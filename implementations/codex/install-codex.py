"""Установка переносимого комплекта Agent-SDLC. Требуется Python 3.11+."""

# Вывод не должен зависеть от системной кодировки Windows и перенаправления в CI.
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--user', action='store_true')
    scope.add_argument('--project', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    args = parser.parse_args()
    if args.doctor:
        missing = [name for name in ('git', 'codex') if not shutil.which(name)]
        print('Python:', sys.version.split()[0])
        print('Не найдены: ' + ', '.join(missing) if missing else 'Git и Codex доступны. Проверь авторизацию Codex и инструменты проекта.')
        return int(bool(missing) or sys.version_info < (3,11))
    if not args.user and not args.project:
        parser.print_help(); return 2
    source = Path(__file__).resolve().parent
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items():
        item = (source/name).resolve()
        if not item.is_relative_to(source) or not item.is_file() or hashlib.sha256(item.read_bytes()).hexdigest() != digest:
            raise ValueError('Повреждён комплект: '+name)
    base = Path.home() if args.user else args.project.resolve()
    targets = {'skills':base/'.agents/skills', 'agents':base/'.codex/agents'}
    record = base/'.codex/sdlc-installation.json'
    old = json.loads(record.read_text(encoding='utf-8')) if record.is_file() else {'files':[]}
    owned = []
    changes=[]
    for kind, target in targets.items():
        for item in (source/kind).rglob('*'):
            if not item.is_file(): continue
            dest=target/item.relative_to(source/kind)
            owned.append(dest.relative_to(base).as_posix())
            if any(p.is_symlink() for p in [dest,*dest.parents]):
                raise ValueError('Символьная ссылка в пути установки: '+str(dest))
            if not dest.is_file() or dest.read_bytes()!=item.read_bytes(): changes.append((item,dest))
    stale=[]
    for name in old['files']:
        dest=base/name
        if not dest.resolve().is_relative_to(base.resolve()) or not any(dest.is_relative_to(t) for t in targets.values()):
            raise ValueError('Недопустимый путь в манифесте установки')
        if name not in owned and dest.is_file(): stale.append(dest)
    if args.check:
        print('Расхождений:',len(changes)+len(stale)); return int(bool(changes or stale))
    backup=base/'.codex'/('sdlc-backup-'+str(time.time_ns()))
    for item,dest in changes:
        if dest.exists():
            saved=backup/dest.relative_to(base); saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(dest,saved)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(item,dest)
    for dest in stale:
        if dest.is_symlink(): raise ValueError('Символьная ссылка: '+str(dest))
        saved=backup/dest.relative_to(base); saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(dest,saved); dest.unlink()
    record.parent.mkdir(parents=True,exist_ok=True)
    value=json.dumps({'version':manifest['version'],'files':sorted(owned)},ensure_ascii=False,indent=2)
    if not record.exists() or record.read_text(encoding='utf-8')!=value: record.write_text(value,encoding='utf-8')
    print('Установлено файлов:',len(changes))
    print('Открой Codex в проекте и вызови $sdlc-run <slug> <задача>.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
