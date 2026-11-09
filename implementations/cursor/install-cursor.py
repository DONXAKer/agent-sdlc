#!/usr/bin/env python3
"""Установи комплект Agent-SDLC в .cursor целевого проекта."""

import argparse
import shutil
import tempfile
import sys
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def install(project, check=False):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Корень проекта должен существовать')
    destination = project / '.cursor'
    if not (SOURCE / 'skills/sdlc-run/SKILL.md').is_file():
        raise ValueError('Ресурсы Cursor отсутствуют. Выполни python tools/distribution.py sync')
    changes = []
    for folder in ('skills', 'agents', 'rules'):
        for source in sorted((SOURCE / folder).rglob('*')):
            if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
                continue
            target = destination / source.relative_to(SOURCE)
            if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(project)):
                raise ValueError(f'Символьная ссылка в цели установки: {target}')
            if target.exists() and not target.is_file():
                raise ValueError(f'Вместо файла найден каталог: {target}')
            if not target.exists() or target.read_bytes() != source.read_bytes():
                changes.append((source, target))
    if check:
        return not changes
    backup = None
    for source, target in changes:
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
