"""Квитанция предусловий: связывает раннюю подготовку с базой и свидетельствами попыток."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import snapshot_base
from sdlc_common import files_to_touch, patch_files, read, preparation_checks


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


def create(root, d, n, locator, current_diff, capture_baseline):
    target = receipt_path(d, n)
    if target.exists() or list(d.glob(f'chunk-{n}-attempt-*')):
        raise ValueError('Квитанция уже существует или попытки начаты; поздняя запись не доказывает порядок')
    plan = read(d / 'plan.md', '')
    intent, readiness = read(d / 'intent.md', ''), read(d / 'readiness.md', '')
    problems = [f'{name}: {detail}' for ok, name, detail in
                preparation_checks(intent, plan, readiness, contour(intent) == 'мелкий') if not ok]
    if problems:
        raise ValueError('; '.join(problems))
    planned = set(files_to_touch(plan))
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
        previous = list(d.glob(f'chunk-{int(n) - 1}-attempt-*-evidence.json'))
        if not previous:
            raise ValueError('Нет evidence предыдущего chunk; порядок подготовки не доказан')
        previous.sort(key=lambda path: int(path.name.split('-')[3]))
        rec = json.loads(previous[-1].read_text(encoding='utf-8'))
        previous_base = rec.get('base', {'kind': 'git', 'id': rec.get('base_sha')})
        patch = previous[-1].with_name(previous[-1].name.replace('-evidence.json', '-diff.patch'))
        if (previous_base != base or snapshot_base.digest(current_diff.encode('utf-8', errors='surrogateescape')) != rec.get('diff_sha256')
                or not patch.is_file() or snapshot_base.digest(patch.read_bytes()) != rec.get('diff_sha256')):
            raise ValueError('Дерево отличается от предыдущей попытки; поздняя подготовка не доказывает порядок')
    baseline = d / f'.chunk-{n}-baseline.json'
    if not baseline.exists():
        capture_baseline()
    original = json.loads(baseline.read_text(encoding='utf-8'))
    for name, digest in original.items():
        path = snapshot_base.safe_path(root, name)
        if not path.is_file() or snapshot_base.digest(path.read_bytes()) != digest:
            raise ValueError('Dirty-база изменилась после baseline; предусловия не доказаны')
    record = {'version': 1, 'tool': 'attempt-evidence.py prepare', 'status': 'ready',
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
        with plan_archive.open('xb') as output:
            output.write((d / 'plan.md').read_bytes())
    record['plan_path'] = plan_archive.name
    record['documents'] = {}
    for name in ('intent.md', 'readiness.md'):
        archive = d / f'.chunk-{n}-{name}'
        raw = (d / name).read_bytes()
        if archive.exists() and archive.read_bytes() != raw:
            raise ValueError('Историческая редакция документа уже существует и отличается')
        if not archive.exists():
            archive.write_bytes(raw)
        record['documents'][name] = {'path': archive.name, 'sha256': snapshot_base.digest(raw)}
    tree = {}
    blobs = d / '.chunk-blobs'
    blobs.mkdir(exist_ok=True)
    for name, (data, mode) in snapshot_base.project_files(root, git_special=base['kind'] == 'git').items():
        digest = snapshot_base.digest(data)
        blob = blobs / digest
        if blob.exists() and blob.read_bytes() != data:
            raise ValueError('Байты базы chunk изменены')
        if not blob.exists():
            blob.write_bytes(data)
        tree[name] = {'sha256': digest, 'mode': mode}
    tree_path = d / f'.chunk-{n}-tree.json'
    raw = (json.dumps(tree, sort_keys=True) + '\n').encode('utf-8')
    if tree_path.exists() and tree_path.read_bytes() != raw:
        raise ValueError('Историческая база chunk уже существует и отличается')
    if not tree_path.exists():
        tree_path.write_bytes(raw)
    record['tree'] = {'path': tree_path.name, 'sha256': snapshot_base.digest(raw)}
    journal_archive = d / f'.chunk-{n}-initial-journal.md'
    if journal_archive.exists() and text_digest(journal_archive) != text_digest(journal):
        raise ValueError('Начальная редакция журнала уже существует и отличается')
    if not journal_archive.exists():
        with journal_archive.open('xb') as output:
            output.write(journal.read_bytes())
    record['journal'] = {'path': journal_archive.name, 'sha256': text_digest(journal_archive)}
    with target.open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
        output.write('\n')
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
        if (rec.get('version') != 1 or rec.get('tool') != 'attempt-evidence.py prepare' or rec.get('status') != 'ready'
                or rec.get('slug') != d.name or rec.get('chunk') != int(n)
                or rec.get('contour') not in ('мелкий', 'полный')):
            return ['Недопустимая квитанция предусловий']
        base, _ = snapshot_base.load(root, d)
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
                if not historical and (d / name).read_bytes() != raw:
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
    raw = artifact(d, tree['path']).read_bytes()
    if snapshot_base.digest(raw) != tree['sha256']:
        raise ValueError('Manifest базы chunk изменён')
    result = {}
    for name, item in json.loads(raw).items():
        sha, mode = item['sha256'], item['mode']
        snapshot_base.safe_path(root, name, follow=mode != 0o120000)
        if not re.fullmatch('[0-9a-f]{64}', sha) or mode not in (0o644, 0o755, 0o120000, 0o160000):
            raise ValueError('Недопустимая запись базы chunk')
        data = artifact(d, '.chunk-blobs/' + sha).read_bytes()
        if snapshot_base.digest(data) != sha:
            raise ValueError('Байты базы chunk изменены')
        result[name] = (data, mode)
    return result
