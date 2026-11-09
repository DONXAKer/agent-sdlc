
# Вывод не должен зависеть от системной кодировки Windows и перенаправления в CI.
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

import hashlib, json, shutil, subprocess, sys, tempfile, tomllib
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
for name,digest in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
for p in (root/'agents').glob('*.toml'):
    assert {'name','description','developer_instructions'} <= tomllib.loads(p.read_text(encoding='utf-8')).keys()
assert len(list((root/'skills').glob('*/SKILL.md')))==10
with tempfile.TemporaryDirectory(prefix='sdlc-') as temp:
    dest=Path(temp)/'проект с пробелами'
    command=[sys.executable,str(root/'install-codex.py'),'--project',str(dest)]
    subprocess.run(command,check=True)
    subprocess.run(command+['--check'],check=True)
    subprocess.run(command,check=True)
    installed=dest/'.agents/skills/sdlc-verify'
    subprocess.run([sys.executable,str(installed/'tools/flow-verdict.py'),'--metrics',str(dest)],check=True)
    changed=dest/'.agents/skills/sdlc-run/SKILL.md'
    changed.write_text('Локальная версия',encoding='utf-8')
    assert subprocess.run(command+['--check']).returncode==1
    subprocess.run(command,check=True)
    assert any(p.read_text(encoding='utf-8')=='Локальная версия' for p in (dest/'.codex').glob('sdlc-backup-*/.agents/skills/sdlc-run/SKILL.md'))
print('Проверки комплекта пройдены.')
