"""Квитанция предусловий: связывает раннюю подготовку с базой и свидетельствами попыток."""

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import snapshot_base
from sdlc_common import profile_gate_errors, task_profile
from sdlc_common import ARTIFACTS_DIR, CHUNK_FILES, artifact_targets, defect_class_catalog, files_to_touch, patch_files, plan_approved, preparation_checks, read, size_explained
from sdlc_common import git_run, planned_paths

PREPARE_VERSION = 2
PROJECT_BLOB_STORE = '.chunk-blobs'


def receipt_path(d, n):
    return d / f'.chunk-{n}-preconditions.json'


def text_digest(path):
    return snapshot_base.digest(path.read_text(encoding='utf-8').encode('utf-8'))


def contour(intent):
    match = re.search(r'\*\*Контур:\*\*[^\S\n]*([^\n]*)', intent or '', re.I)
    value = match.group(1).strip().casefold() if match else ''
    return 'мелкий' if re.fullmatch(r'мелкий(?:\s+—\s+[^/\n]+)?', value) else 'полный'


def artifact(d, name):
    path = Path(name)
    target = d / path
    if path.is_absolute() or '..' in path.parts or not target.resolve().is_relative_to(d.resolve()):
        raise ValueError('Артефакт предусловий должен находиться внутри витка')
    if not target.is_file():
        raise ValueError(f'Артефакт предусловий отсутствует: {name}')
    return target


def publish(path, raw, expected_sha=None):
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='.prepare-') as tmp:
        staged = Path(tmp) / path.name
        staged.write_bytes(raw)
        if expected_sha is not None and snapshot_base.digest(staged.read_bytes()) != expected_sha:
            raise ValueError('Хэш записанного объекта базы chunk отличается')
        staged.replace(path)


def project_blob_store(root):
    return snapshot_base.safe_path(root, f'{ARTIFACTS_DIR}/{PROJECT_BLOB_STORE}')


def normalized_readiness(raw):
    return raw.replace(b'\r\n', b'\n').rstrip(b'\n')


def amendment_record(receipt, before, after, owner, reason):
    return {'version': 1, 'artifact': 'readiness.md', 'prepare_sha256': snapshot_base.digest(receipt),
            'before_sha256': snapshot_base.digest(before), 'after_sha256': snapshot_base.digest(after),
            'owner': owner, 'reason': reason}


def amendment_line(receipt, before, after, owner, reason):
    record = amendment_record(receipt, before, after, owner, reason)
    return 'Поправка готовности: ' + json.dumps(record, ensure_ascii=False, sort_keys=True)


def valid_readiness_amendment(d, n, receipt, before, current):
    if normalized_readiness(before) != normalized_readiness(current):
        return False
    journal = artifact(d, f'chunk-{n}-journal.md').read_text(encoding='utf-8')
    for line in journal.splitlines():
        if not line.startswith('Поправка готовности: '):
            continue
        try:
            record = json.loads(line.removeprefix('Поправка готовности: '))
            owner, reason = record['owner'], record['reason']
            if (isinstance(owner, str) and owner.strip() and isinstance(reason, str) and reason.strip()
                    and record == amendment_record(receipt, before, current, owner, reason)):
                return True
        except (KeyError, ValueError, TypeError):
            continue
    return False


def amend_readiness(root, d, n, replacement, owner, reason):
    if not owner.strip() or not reason.strip():
        raise ValueError('Укажи владельца решения и причину поправки')
    problems = errors(root, d, n)
    if problems:
        raise ValueError('\n'.join(problems))
    receipt = receipt_path(d, n).read_bytes()
    recorded = json.loads(receipt).get('documents', {}).get('readiness.md')
    if not recorded:
        raise ValueError('Исходная квитанция не закрепляет readiness.md')
    before = artifact(d, recorded['path']).read_bytes()
    current = artifact(d, 'readiness.md')
    after = replacement.read_bytes()
    if normalized_readiness(before) != normalized_readiness(after):
        raise ValueError('Допустима поправка переводов строк и пустых строк в конце readiness.md')
    if current.read_bytes() == after:
        return
    journal = artifact(d, f'chunk-{n}-journal.md')
    line = amendment_line(receipt, before, after, owner.strip(), reason.strip())
    with tempfile.TemporaryDirectory(dir=d, prefix='.readiness-') as tmp:
        staged = Path(tmp)
        (staged / 'readiness.md').write_bytes(after)
        raw = journal.read_bytes()
        addition = ('\n\n' + line + '\n').encode('utf-8')
        if line.encode('utf-8') not in raw.splitlines():
            (staged / 'journal.md').write_bytes(raw.rstrip(b'\r\n') + addition)
            (staged / 'journal.md').replace(journal)
        (staged / 'readiness.md').replace(current)


def create(root, d, n, locator, current_diff, capture_baseline, profile=None):
    target = receipt_path(d, n)
    if target.exists() or list(d.glob(f'chunk-{n}-attempt-*')):
        raise ValueError('Квитанция уже существует или попытки начаты; поздняя запись не доказывает порядок')
    plan = read(d / 'plan.md', '')
    intent, readiness = read(d / 'intent.md', ''), read(d / 'readiness.md', '')
    problems = [f'{name}: {detail}' for ok, name, detail in
                preparation_checks(intent, plan, readiness, contour(intent) == 'мелкий') if not ok]
    catalog = defect_class_catalog(read(root / ARTIFACTS_DIR / 'gates.md', ''))
    problems.extend(profile_gate_errors(intent, read(root / ARTIFACTS_DIR / 'gates.md', '')))
    problems.extend(f'Классы дефектов проекта: {error}' for error in catalog['errors'])
    if problems:
        raise ValueError('; '.join(problems))
    planned = set(files_to_touch(plan))
    if not plan_approved(plan):
        raise ValueError('Проверь поле «Одобрение» до prepare')
    deferred = re.search(r"Уходит следующим chunk.ам:\*\*[^\n]*claim-\d+", plan, re.I)
    if len(planned) > CHUNK_FILES and not deferred and not size_explained(plan):
        raise ValueError('Раздели план или укажи: почему этот chunk больше ориентира')
    targets = artifact_targets(root, d)
    if not planned:
        raise ValueError('План не содержит files_to_touch; предусловия не доказаны')
    base, _ = snapshot_base.load(root, d)
    locator_path = artifact(d, locator)
    if not locator_path.read_bytes().strip():
        raise ValueError('Отчёт locator пуст')
    journal = artifact(d, f'chunk-{n}-journal.md')
    if not journal.read_bytes().strip():
        raise ValueError('Журнал chunk пуст; создай его до prepare и первой попытки')
    if int(n) == 1:
        touched = {record['path'] for record in patch_files(current_diff)}
        if touched & planned:
            raise ValueError('До квитанции уже изменены пути плана; порядок не доказан. Сохрани отклонение в журнале')
    else:
        previous = [path for path in d.glob(f'chunk-{int(n) - 1}-attempt-*-evidence.json')
                    if re.fullmatch(rf'chunk-{int(n) - 1}-attempt-\d+-evidence\.json', path.name)]
        if not previous:
            raise ValueError('Нет evidence предыдущего chunk; порядок подготовки не доказан')
        previous.sort(key=lambda path: int(path.name.split('-')[3]))
        rec = json.loads(previous[-1].read_text(encoding='utf-8'))
        previous_base = rec.get('base', {'kind': 'git', 'id': rec.get('base_sha')})
        patch = previous[-1].with_name(previous[-1].name.replace('-evidence.json', '-diff.patch'))
        if (previous_base != base or snapshot_base.digest(current_diff.encode('utf-8', errors='surrogateescape')) != rec.get('diff_sha256')
                or not patch.is_file() or snapshot_base.digest(patch.read_bytes()) != rec.get('diff_sha256')):
            raise ValueError('Дерево отличается от предыдущей попытки; поздняя подготовка не доказывает порядок')
    original_paths = load_tree(root, d, 1) if int(n) > 1 and (d / '.chunk-1-tree.json').is_file() else None
    path_operations = list(planned_paths(plan))
    new_paths = {name for name, operation in path_operations if operation == '[new]'}
    for name, operation in path_operations:
        if any(char in name for char in '*?['):
            raise ValueError(f'Укажи точный путь плана: {name}')
        path = snapshot_base.safe_path(root, name, follow=False)
        exists_as_file = path.is_file() or path.is_symlink()
        if not exists_as_file and path.is_dir() and base['kind'] == 'git':
            rc, index, _ = git_run(['--literal-pathspecs', 'ls-files', '--stage', '-z', '--', name], root)
            exists_as_file = rc == 0 and any(
                row.startswith('160000 ') and row.partition('\t')[2] == name for row in index.split('\0'))
        present = name in original_paths if original_paths is not None else exists_as_file
        if operation == '[new]':
            if present or (original_paths is None and path.exists()):
                raise ValueError(f'Новый путь плана уже занят: {name}')
        elif not present and not exists_as_file:
            if name in new_paths:
                raise ValueError(f'Конфликт строк плана для {name}: путь отсутствует, указаны [new] и {operation}. '
                                 'Укажи [new] для создания, а последовательность правок опиши в chunk.')
            raise ValueError(f'Путь плана не существует: {name}. Для создания укажи [new].')
    baseline = d / f'.chunk-{n}-baseline.json'
    if not baseline.exists():
        capture_baseline()
    original = json.loads(baseline.read_text(encoding='utf-8'))
    for name, digest in original.items():
        path = snapshot_base.safe_path(root, name)
        if not path.is_file() or snapshot_base.digest(path.read_bytes()) != digest:
            raise ValueError('Dirty-база изменилась после baseline; предусловия не доказаны')
    record = {'version': PREPARE_VERSION, 'tool': 'attempt-evidence.py prepare', 'status': 'ready', 'artifact_targets': targets,
              'profile': profile,
              'task_profile': task_profile(intent),
              'slug': d.name, 'chunk': int(n), 'base': base,
              'plan_sha256': text_digest(d / 'plan.md'),
              'locator': {'path': locator, 'sha256': text_digest(locator_path)},
              'baseline_sha256': text_digest(baseline),
              'initial_diff_sha256': snapshot_base.digest(current_diff.encode('utf-8', errors='surrogateescape')),
              'contour': contour(read(d / 'intent.md', '')),
              'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    plan_archive = d / f'.chunk-{n}-plan.md'
    if plan_archive.exists() and text_digest(plan_archive) != record['plan_sha256']:
        raise ValueError('Историческая редакция плана уже существует и отличается')
    if not plan_archive.exists():
        publish(plan_archive, (d / 'plan.md').read_bytes())
    record['plan_path'] = plan_archive.name
    record['documents'] = {}
    for name in ('intent.md', 'readiness.md'):
        archive = d / f'.chunk-{n}-{name}'
        raw = (d / name).read_bytes()
        if archive.exists() and archive.read_bytes() != raw:
            raise ValueError('Историческая редакция документа уже существует и отличается')
        if not archive.exists():
            publish(archive, raw)
        record['documents'][name] = {'path': archive.name, 'sha256': snapshot_base.digest(raw)}
    tree = {}
    blobs = project_blob_store(root)
    blobs.mkdir(exist_ok=True)
    for name, (data, mode) in snapshot_base.project_files(root, git_special=base['kind'] == 'git', d=d).items():
        digest = snapshot_base.digest(data)
        blob = blobs / digest
        if blob.exists() or blob.is_symlink():
            if artifact(blobs, digest).read_bytes() != data:
                raise ValueError('Байты базы chunk изменены')
        else:
            publish(blob, data, digest)
        tree[name] = {'sha256': digest, 'mode': mode}
    tree_path = d / f'.chunk-{n}-tree.json'
    raw = (json.dumps(tree, sort_keys=True) + '\n').encode('utf-8')
    if tree_path.exists() and tree_path.read_bytes() != raw:
        raise ValueError('Историческая база chunk уже существует и отличается')
    if not tree_path.exists():
        publish(tree_path, raw)
    record['tree'] = {'path': tree_path.name, 'sha256': snapshot_base.digest(raw), 'storage': 'project'}
    journal_archive = d / f'.chunk-{n}-initial-journal.md'
    if journal_archive.exists() and text_digest(journal_archive) != text_digest(journal):
        raise ValueError('Начальная редакция журнала уже существует и отличается')
    if not journal_archive.exists():
        publish(journal_archive, journal.read_bytes())
    record['journal'] = {'path': journal_archive.name, 'sha256': text_digest(journal_archive)}
    publish(target, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return record


def errors(root, d, n, expected_hash=None, historical=False):
    """Не используй время файлов как доказательство. Проверяй связи и неизменность входов."""
    target = receipt_path(d, n)
    if not target.is_file():
        return ['Нет программной квитанции предусловий; подпись не доказывает порядок locator/baseline']
    try:
        raw = target.read_bytes()
        rec = json.loads(raw)
        if expected_hash is not None and snapshot_base.digest(raw) != expected_hash:
            return ['Квитанция изменена после записи evidence']
        if (rec.get('tool') != 'attempt-evidence.py prepare' or rec.get('status') != 'ready'
                or rec.get('slug') != d.name or rec.get('chunk') != int(n)):
            return ['Недопустимая квитанция предусловий']
        version = rec.get('version')
        if type(version) is not int or version not in (1, PREPARE_VERSION):
            return [f'Неподдерживаемая версия квитанции prepare: {version!r}; ожидается v{PREPARE_VERSION}']
        if version == 1 and 'contour' not in rec:
            return [f'Квитанция prepare v1 без contour несовместима с профилем v{PREPARE_VERSION}; '
                    'проверь архив исходным комплектом, выпустившим квитанцию']
        if rec.get('contour') not in ('мелкий', 'полный'):
            return [f'Квитанция prepare v{version}: недопустимое поле contour']
        base, _ = snapshot_base.load(root, d)
        if rec.get('artifact_targets', []) != artifact_targets(root, d):
            return ['Цели .sdlc изменились после prepare']
        archived = artifact(d, rec['plan_path'])
        if rec.get('plan_sha256') != text_digest(archived):
            return ['Историческая редакция плана изменена']
        if rec.get('base') != base or (not historical and rec.get('plan_sha256') != text_digest(d / 'plan.md')):
            return ['База или план изменились после квитанции']
        if 'documents' in rec:
            for name in ('intent.md', 'readiness.md'):
                saved = rec['documents'][name]
                raw = artifact(d, saved['path']).read_bytes()
                if snapshot_base.digest(raw) != saved['sha256']:
                    return ['Историческая редакция документа изменена']
                if name == 'intent.md' and rec.get('task_profile') != task_profile(raw.decode('utf-8')):
                    return ['Профиль квитанции отличается от закреплённого intent']
                if not historical:
                    current = (d / name).read_bytes()
                    if current != raw and (name != 'readiness.md' or not valid_readiness_amendment(
                            d, n, target.read_bytes(), raw, current)):
                        return ['Intent или readiness изменились после квитанции; требуется новая подготовка']
        if 'tree' in rec:
            load_tree(root, d, n)
        initial_journal = rec.get('journal')
        if not isinstance(initial_journal, dict):
            return ['Нет начальной редакции журнала в квитанции; порядок его создания не доказан']
        archived_journal = artifact(d, initial_journal['path'])
        if (initial_journal['path'] != f'.chunk-{n}-initial-journal.md'
                or not archived_journal.read_bytes().strip()
                or text_digest(archived_journal) != initial_journal.get('sha256')):
            return ['Начальная редакция журнала изменена или пуста']
        if not artifact(d, f'chunk-{n}-journal.md').read_bytes().strip():
            return ['Журнал chunk пуст']
        locator = rec['locator']
        if text_digest(artifact(d, locator['path'])) != locator['sha256']:
            return ['Отчёт locator изменился после квитанции']
        baseline = artifact(d, f'.chunk-{n}-baseline.json')
        if text_digest(baseline) != rec.get('baseline_sha256'):
            return ['Baseline изменился после квитанции']
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return [f'Квитанция предусловий не подтверждена: {error}']
    return []


def load_tree(root, d, n):
    """Прочитай исходные байты chunk по закреплённому manifest."""
    rec = json.loads(receipt_path(d, n).read_bytes())
    tree = rec['tree']
    storage = tree.get('storage', 'cycle')
    if storage not in ('cycle', 'project'):
        raise ValueError('Недопустимое хранилище базы chunk')
    blobs = project_blob_store(root) if storage == 'project' else d
    raw = artifact(d, tree['path']).read_bytes()
    if snapshot_base.digest(raw) != tree['sha256']:
        raise ValueError('Manifest базы chunk изменён')
    result = {}
    for name, item in json.loads(raw).items():
        sha, mode = item['sha256'], item['mode']
        snapshot_base.safe_path(root, name, follow=mode != 0o120000)
        if not re.fullmatch('[0-9a-f]{64}', sha) or mode not in (0o644, 0o755, 0o120000, 0o160000):
            raise ValueError('Недопустимая запись базы chunk')
        data = artifact(blobs, sha if storage == 'project' else '.chunk-blobs/' + sha).read_bytes()
        if snapshot_base.digest(data) != sha:
            raise ValueError('Байты базы chunk изменены')
        result[name] = (data, mode)
    return result
