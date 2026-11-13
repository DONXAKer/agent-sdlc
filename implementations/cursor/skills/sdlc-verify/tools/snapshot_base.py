"""Явная база рабочего дерева: исходные байты, закреплённый manifest и проверяемый diff."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import zlib
from pathlib import Path, PurePosixPath

from sdlc_common import ARTIFACTS_DIR, git_run, is_junk_path, read, require_base_sha, artifact_targets, artifact_observed


def digest(data):
    return hashlib.sha256(data).hexdigest()


def kind_of(plan):
    matches = re.findall(r'^[- ]*\*\*Вид базы:\*\*[^\S\n]*(.*)$', plan or '', re.M)
    if len(matches) > 1:
        raise ValueError('В плане указан более одного вида базы')
    kind = matches[0].strip().strip('`') if matches else 'git'
    if kind not in ('git', 'snapshot'):
        raise ValueError('Вид базы должен быть git или snapshot')
    if kind == 'git' and re.search(r'\*\*Хэш снимка:', plan or ''):
        raise ValueError('Закреплённый хэш снимка несовместим с Git-режимом; создай новый виток')
    return kind


def project_files(root, known_paths=(), git_special=False, d=None):
    rc, out, err = git_run(['ls-files', '-z', '--cached', '--others', '--exclude-standard'], root)
    if rc:
        raise ValueError(f'Не удалось получить файлы дерева: {err}')
    targets = artifact_targets(root, d) if d else []
    names = set(out.split('\0')) | set(known_paths) | set(targets)
    for name in targets:
        parent = (root / name).parent
        if parent.is_dir():
            names.update(str(path.relative_to(root)).replace('\\', '/') for path in parent.iterdir() if path.is_file())
    if targets:
        names.update(str(path.relative_to(root)).replace('\\', '/')
                     for path in (root / ARTIFACTS_DIR).rglob('*') if path.is_file())
    result = {}
    for name in sorted(names - {''}):
        if (name.startswith(ARTIFACTS_DIR + '/') and not artifact_observed(name, targets, d)) or is_junk_path(name):
            continue
        candidate = root / name
        if git_special and candidate.is_symlink():
            safe_path(root, str(Path(name).parent).replace('\\', '/'))
            result[name] = (os.readlink(candidate).encode('utf-8', errors='surrogateescape'), 0o120000)
            continue
        path = safe_path(root, name)
        if git_special and path.is_dir():
            rc, index, err = git_run(['ls-files', '--stage', '--', name], root)
            match = re.match(r'160000 ([0-9a-f]+) 0\t', index)
            if rc or not match:
                raise ValueError(f'Не удалось прочитать gitlink: {name}')
            rc, directory, _ = git_run(['-C', str(path), 'rev-parse', '--show-toplevel'], root)
            initialized = rc == 0 and Path(directory.strip()).resolve() == path.resolve()
            oid = match.group(1)
            dirty = ''
            if initialized:
                rc, output, err = git_run(['-C', str(path), 'rev-parse', 'HEAD'], root)
                if rc:
                    raise ValueError(f'Не удалось прочитать submodule: {name}')
                oid = output.strip()
                rc, output, err = git_run(['-C', str(path), 'status', '--porcelain'], root)
                if rc:
                    raise ValueError(f'Не удалось проверить submodule: {name}')
                dirty = '-dirty' if output.strip() else ''
            result[name] = (f'Subproject commit {oid}{dirty}\n'.encode('ascii'), 0o160000)
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
            raise ValueError(f'Снимок не поддерживает символьные ссылки: {name}')
        if path.is_dir():
            raise ValueError(f'Снимок не поддерживает submodule: {name}')
        if path.exists():
            result[name] = (path.read_bytes(), 0o755 if os.name != 'nt' and path.stat().st_mode & 0o111 else 0o644)
    return result


def safe_path(root, name, follow=True):
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name or '.git' in parts.parts:
        raise ValueError(f'Недопустимый путь снимка: {name}')
    target = root / name
    resolved = target.resolve() if follow else target.parent.resolve() / target.name
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f'Путь снимка выходит за корень: {name}')
    return target


def create(root, d):
    plan_path = d / 'plan.md'
    plan = read(plan_path, '')
    if kind_of(plan) != 'snapshot':
        raise ValueError('До снимка укажи в плане **Вид базы:** snapshot')
    target = d / 'base-snapshot'
    if target.exists() or list(d.glob('chunk-*-attempt-*')) or re.search(r'\*\*Хэш снимка:', plan):
        raise ValueError('База уже создана или попытки начаты; создай новый виток')
    files = project_files(root, d=d)
    manifest = {'version': 1, 'kind': 'snapshot', 'files': {}, 'artifact_targets': artifact_targets(root, d)}
    # Временный каталог находится рядом: rename публикует только полный снимок.
    with tempfile.TemporaryDirectory(dir=d, prefix='.snapshot-') as tmp:
        staging = Path(tmp) / 'base'
        (staging / 'blobs').mkdir(parents=True)
        for name, (data, mode) in files.items():
            sha = digest(data)
            (staging / 'blobs' / sha).write_bytes(data)
            manifest['files'][name] = {'sha256': sha, 'mode': mode}
        raw = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('utf-8')
        (staging / 'manifest.json').write_bytes(raw)
        staging.rename(target)
    pin = digest(raw)
    plan_path.write_text(plan.rstrip() + f'\n\n- **Хэш снимка:** `{pin}`\n', encoding='utf-8')
    return pin


def load(root, d):
    plan = read(d / 'plan.md', '')
    kind = kind_of(plan)
    if kind == 'git':
        return {'kind': 'git', 'id': require_base_sha(plan)}, None
    pin = re.search(r'\*\*Хэш снимка:\*\*\s*`?([0-9a-f]{64})', plan)
    if not pin:
        raise ValueError('Хэш снимка отсутствует; сначала выполни snapshot до первой правки')
    directory = d / 'base-snapshot'
    try:
        raw = (directory / 'manifest.json').read_bytes()
        if digest(raw) != pin.group(1):
            raise ValueError('Manifest снимка изменён')
        manifest = json.loads(raw)
        if manifest.get('artifact_targets', []) != artifact_targets(root, d):
            raise ValueError('Цели .sdlc изменились после снимка; создай новый виток')
        if manifest.get('version') != 1 or manifest.get('kind') != 'snapshot' or not isinstance(manifest.get('files'), dict):
            raise ValueError('Недопустимый формат снимка')
        files = {}
        for name, record in manifest['files'].items():
            safe_path(root, name)
            sha, mode = record['sha256'], record['mode']
            if not re.fullmatch('[0-9a-f]{64}', sha) or mode not in (0o644, 0o755):
                raise ValueError(f'Недопустимая запись снимка: {name}')
            data = (directory / 'blobs' / sha).read_bytes()
            if digest(data) != sha:
                raise ValueError(f'Байты снимка изменены: {name}')
            files[name] = (data, mode)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f'Снимок повреждён или потерян: {error}') from error
    return {'kind': 'snapshot', 'id': pin.group(1)}, files


def materialize(files, destination):
    for name, (data, mode) in files.items():
        path = safe_path(destination, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode if mode in (0o644, 0o755) else 0o644)


def snapshot_diff(root, files, current_files=None, git_special=False, d=None):
    """Сравни сырые байты через отдельный Git index без фильтров рабочего проекта."""
    # Новое правило ignore не удаляет ранее сохранённый файл из сравнения.
    current = project_files(root, files, git_special=git_special, d=d) if current_files is None else current_files
    # Чтение и проверка полной базы остаются в load. Сокращаем только временную запись.
    changed = {name for name in files.keys() | current.keys() if files.get(name) != current.get(name)}
    before_files = {name: files[name] for name in changed if name in files}
    after_files = {name: current[name] for name in changed if name in current}
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        def git(*args, input=None):
            rc, out, err = git_run(['-c', 'core.autocrlf=false', '-c', 'core.filemode=true',
                                    '-c', 'core.attributesFile=', *args], tree, input=input)
            if rc:
                raise ValueError(f'Не удалось сравнить снимок: {err}')
            return out
        git('init', '-q')
        object_format = git('rev-parse', '--show-object-format').strip()
        def write_tree(records):
            entries = []
            for name, (data, mode) in records.items():
                raw = f'blob {len(data)}\0'.encode('ascii') + data
                oid = hashlib.new(object_format, raw).hexdigest()
                target = tree / '.git/objects' / oid[:2] / oid[2:]
                if not target.exists():
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(zlib.compress(raw))
                git_mode = f'100{mode:o}' if mode in (0o644, 0o755) else f'{mode:o}'
                entries.append(f'{git_mode} {oid}\t'.encode('ascii') + name.encode('utf-8', errors='surrogateescape') + b'\0')
            git('read-tree', '--empty')
            git('update-index', '-z', '--index-info', input=b''.join(entries))
            return git('write-tree').strip()
        # .gitattributes исходного проекта не должны преобразовать исходные байты.
        attributes = tree / '.git/info/attributes'
        attributes.write_text('* -text -filter -ident -working-tree-encoding\n', encoding='utf-8')
        materialize(before_files, tree)
        # Полные Git-объекты сохраняют прежние сокращения SHA, включая коллизии префиксов.
        # Байты неизменённых файлов пишутся только как служебные сжатые blobs, без рабочей копии.
        before = write_tree(files)
        for child in tree.iterdir():
            if child.name != '.git':
                if not child.resolve().is_relative_to(tree.resolve()):
                    raise ValueError('Временный путь вышел за каталог сравнения')
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        materialize(after_files, tree)
        after = write_tree(current)
        # Получи diff-атрибут из полного after index. Зафиксируй его для каждого пути:
        # Git diff не обязан читать отсутствующие в рабочей копии .gitattributes из index.
        names = b''.join(name.encode('utf-8', errors='surrogateescape') + b'\0' for name in sorted(changed))
        resolved = git('check-attr', '--cached', '-z', 'diff', '--stdin', input=names).split('\0')
        with attributes.open('a', encoding='utf-8') as output:
            for index in range(0, len(resolved) - 1, 3):
                name, _, value = resolved[index:index + 3]
                pattern = '/' + ''.join('\\' + c if c in '*?[]\\' else c for c in name)
                setting = {'set': 'diff', 'unset': '-diff', 'unspecified': '!diff'}.get(value, 'diff=' + value)
                output.write(json.dumps(pattern, ensure_ascii=False) + ' ' + setting + '\n')
        return git('diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames', '--no-color',
                   '--src-prefix=a/', '--dst-prefix=b/', '--output-indicator-new=+',
                   '--output-indicator-old=-', '--output-indicator-context= ', before, after, '--')
