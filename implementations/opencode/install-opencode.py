#!/usr/bin/env python3
"""Установи комплект Agent-SDLC в .opencode целевого проекта."""

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import sys
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def plain_path(base, name):
    """Отклони ссылки, reparse point и выход за корень до доступа к файлу."""
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError(f'Недопустимый относительный путь: {name}')
    target = base / relative
    for part in (target, *target.parents):
        if not part.is_relative_to(base):
            break
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError(f'Ссылка или reparse point в пути: {part}')
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError(f'Путь выходит за корень: {target}')
    return target


def install(project, check=False):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Корень проекта должен существовать')
    destination = project / '.opencode'
    if not (SOURCE / 'skills/sdlc-run/SKILL.md').is_file():
        raise ValueError('Ресурсы OpenCode отсутствуют. Выполни python tools/distribution.py sync')
    manifest = json.loads(plain_path(SOURCE, 'manifest.json').read_text(encoding='utf-8'))
    changes = []
    for name, digest in manifest['files'].items():
        relative = Path(name)
        if len(relative.parts) < 2 or relative.parts[0] not in ('skills', 'agents', 'commands'):
            raise ValueError(f'Недопустимый ресурс комплекта: {name}')
        source = plain_path(SOURCE, name)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Повреждён комплект: {name}')
        target = plain_path(project, Path('.opencode') / relative)
        if target.exists() and not target.is_file():
            raise ValueError(f'Вместо файла найден каталог: {target}')
        if not target.exists() or target.read_bytes() != source.read_bytes():
            changes.append((source, target))
    if check:
        return not changes
    backup = None
    for source, target in changes:
        plain_path(project, target.relative_to(project))
        if target.exists():
            if backup is None:
                backup = Path(tempfile.mkdtemp(prefix='sdlc-backup-', dir=destination))
            saved = backup / target.relative_to(destination)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f'Обновлено файлов: {len(changes)}')
    if backup:
        print(f'Резервная копия: {backup}')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        return int(not install(args.project, args.check))
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
