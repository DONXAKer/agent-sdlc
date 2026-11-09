"""Явная база рабочего дерева: исходные байты, закреплённый manifest и проверяемый diff."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from sdlc_common import ARTIFACTS_DIR, git_run, is_junk_path, read, require_base_sha


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


def project_files(root, known_paths=()):
    rc, out, err = git_run(['ls-files', '-z', '--cached', '--others', '--exclude-standard'], root)
    if rc:
        raise ValueError(f'Не удалось получить файлы дерева: {err}')
    result = {}
    for name in sorted((set(out.split('\0')) | set(known_paths)) - {''}):
        if name.startswith(ARTIFACTS_DIR + '/') or is_junk_path(name):
            continue
        path = safe_path(root, name)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
            raise ValueError(f'Снимок не поддерживает символьные ссылки: {name}')
        if path.is_dir():
            raise ValueError(f'Снимок не поддерживает submodule: {name}')
        if path.exists():
            result[name] = (path.read_bytes(), 0o755 if os.name != 'nt' and path.stat().st_mode & 0o111 else 0o644)
    return result


def safe_path(root, name):
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name or '.git' in parts.parts:
        raise ValueError(f'Недопустимый путь снимка: {name}')
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
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
    files = project_files(root)
    manifest = {'version': 1, 'kind': 'snapshot', 'files': {}}
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
        path.chmod(mode)


def snapshot_diff(root, files):
    """Сравни сырые байты через отдельный Git index без фильтров рабочего проекта."""
    # Новое правило ignore не удаляет ранее сохранённый файл из сравнения.
    current = project_files(root, files)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        def git(*args):
            rc, out, err = git_run(['-c', 'core.autocrlf=false', '-c', 'core.filemode=true',
                                    '-c', 'core.attributesFile=', *args], tree)
            if rc:
                raise ValueError(f'Не удалось сравнить снимок: {err}')
            return out
        git('init', '-q')
        # .gitattributes исходного проекта не должны преобразовать исходные байты.
        attributes = tree / '.git/info/attributes'
        attributes.write_text('* -text -filter -ident -working-tree-encoding\n', encoding='utf-8')
        materialize(files, tree)
        git('add', '--all', '--force')
        before = git('write-tree').strip()
        for child in tree.iterdir():
            if child.name != '.git':
                if not child.resolve().is_relative_to(tree.resolve()):
                    raise ValueError('Временный путь вышел за каталог сравнения')
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        materialize(current, tree)
        git('add', '--all', '--force')
        after = git('write-tree').strip()
        return git('diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames', '--no-color',
                   '--src-prefix=a/', '--dst-prefix=b/', '--output-indicator-new=+',
                   '--output-indicator-old=-', '--output-indicator-context= ', before, after, '--')
