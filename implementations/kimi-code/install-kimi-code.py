"""Установка самостоятельного комплекта Agent-SDLC для Kimi Code. Требуется Python 3.11+."""
import argparse
import hashlib
import json
import shutil
import stat
import sys
import time
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def plain_path(base, name):
    """Проверь весь путь внутри корня до чтения, записи или удаления."""
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Недопустимый относительный путь: ' + str(name))
    target = base / relative
    for part in (target, *target.parents):
        if not part.is_relative_to(base):
            break
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Символьная ссылка в пути: ' + str(part))
    if not target.resolve().is_relative_to(base):
        raise ValueError('Путь выходит за корень: ' + str(target))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--user', action='store_true')
    scope.add_argument('--project', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    args = parser.parse_args()
    if args.doctor:
        missing = [name for name in ('git', 'kimi') if not shutil.which(name)]
        print('Python:', sys.version.split()[0])
        print('Не найдены: ' + ', '.join(missing) if missing else 'Git и Kimi Code доступны. Проверь авторизацию Kimi Code и инструменты проекта.')
        return int(bool(missing) or sys.version_info < (3, 11))
    if not args.user and not args.project:
        parser.print_help()
        return 2
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    verified = {}
    for name, digest in manifest['files'].items():
        item = plain_path(source, name)
        if not item.is_file() or hashlib.sha256(item.read_bytes()).hexdigest() != digest:
            raise ValueError('Повреждён комплект: ' + name)
        verified[Path(name)] = item
    base = (Path.home() if args.user else args.project).resolve()
    targets = {'skills': base / '.kimi-code/skills', 'agents': base / '.kimi-code/agents'}
    record = plain_path(base, '.kimi-code/sdlc-installation.json')
    old = json.loads(record.read_text(encoding='utf-8')) if record.is_file() else {'files': []}
    owned = []
    changes = []
    for relative, item in verified.items():
        if relative.parts[0] not in targets or len(relative.parts) < 2:
            continue
        dest = targets[relative.parts[0]] / Path(*relative.parts[1:])
        dest = plain_path(base, dest.relative_to(base))
        if dest.exists() and not dest.is_file():
            raise ValueError('Вместо файла найден каталог: ' + str(dest))
        owned.append(dest.relative_to(base).as_posix())
        if not dest.is_file() or dest.read_bytes() != item.read_bytes():
            changes.append((item, dest))
    stale = []
    for name in old['files']:
        dest = plain_path(base, name)
        if not any(dest.is_relative_to(target) and len(dest.relative_to(target).parts) >= (2 if kind == 'skills' else 1) for kind, target in targets.items()):
            raise ValueError('Недопустимый путь в манифесте установки')
        if name not in owned and dest.is_file():
            stale.append(dest)
    if args.check:
        print('Расхождений:', len(changes) + len(stale))
        return int(bool(changes or stale))
    backup = plain_path(base, '.kimi-code/sdlc-backup-' + str(time.time_ns()))
    for item, dest in changes:
        if dest.exists():
            saved = plain_path(base, (backup / dest.relative_to(base)).relative_to(base))
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
    for dest in stale:
        saved = plain_path(base, (backup / dest.relative_to(base)).relative_to(base))
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, saved)
        dest.unlink()
    record.parent.mkdir(parents=True, exist_ok=True)
    value = json.dumps({'version': manifest['version'], 'files': sorted(owned)}, ensure_ascii=False, indent=2)
    if not record.exists() or record.read_text(encoding='utf-8') != value:
        record.write_text(value, encoding='utf-8')
    print('Установлено файлов:', len(changes))
    print('Открой Kimi Code в проекте и вызови /skill:sdlc-run <slug> с явным корнем проекта.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
