"""Установи глобального агента согласования методологии и Runner для Codex."""
import hashlib
import json
import shutil
import stat
import sys
import time
import tomllib
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def checked(base, name):
    target = base / name
    for part in (target, *target.parents):
        if not part.is_relative_to(base):
            break
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError(f'Ссылка в пути глобальной установки: {part}')
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError(f'Путь вне глобального каталога: {target}')
    return target


def main():
    source = Path(__file__).resolve().parent / 'global-agents/sdlc-runner-sync'
    agent = (source / 'agent.toml').read_bytes()
    parsed = tomllib.loads(agent.decode('utf-8'))
    assert parsed['name'] == 'sdlc-runner-sync' and parsed['description'] and parsed['developer_instructions']
    base = Path.home() / '.codex'
    rule = (source / 'global-rule.md').read_text(encoding='utf-8').strip()
    instructions = checked(base, Path('AGENTS.md'))
    old = instructions.read_text(encoding='utf-8') if instructions.exists() else ''
    marker = '## Согласование методологии Agent-SDLC с Runner'
    if marker in old:
        raise ValueError('Правило уже существует. Проверь существующий раздел перед обновлением.')
    desired = {
        Path('agents/sdlc-runner-sync.toml'): agent,
        Path('skills/sdlc-runner-sync/SKILL.md'): (source / 'SKILL.md').read_bytes(),
        Path('AGENTS.md'): (old.rstrip() + '\n\n' + rule + '\n').encode('utf-8'),
    }
    targets = {name: checked(base, name) for name in desired}
    backup = Path('backups') / ('runner-sync-' + str(time.time_ns()))
    for name, target in targets.items():
        if target.exists():
            saved = checked(base, backup / name)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(desired[name])
    for name, target in targets.items():
        assert target.read_bytes() == desired[name]
        print(json.dumps({'file': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}, ensure_ascii=False))
    print('Резервная копия:', base / backup)


if __name__ == '__main__':
    main()
