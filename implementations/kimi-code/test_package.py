import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
for name, digest in manifest['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name

skills = list((root / 'skills').glob('*/SKILL.md'))
assert len(skills) == 10
for skill in skills:
    text = skill.read_text(encoding='utf-8')
    assert text.startswith('---\n')
    assert 'description:' in text.split('---', 2)[1]
    assert '${KIMI_SKILL_DIR}/../sdlc-run/references/SDLC.md' in text
    assert '${KIMI_SKILL_DIR}/../sdlc-verify/tools' in text
    assert 'Общий контракт Codex' not in text

expected_tools = {
    'sdlc-claims': ['Read', 'Grep', 'Glob'],
    'sdlc-locator': ['Read', 'Grep', 'Glob'],
    'sdlc-reviewer': ['Read', 'Grep', 'Glob', 'Bash'],
    'sdlc-methodologist': ['Read', 'Grep', 'Glob', 'Bash', 'WebSearch', 'FetchURL'],
}
agents = {path.stem: path for path in (root / 'agents').glob('*.md')}
assert set(agents) == set(expected_tools)
for name, path in agents.items():
    frontmatter = path.read_text(encoding='utf-8').split('---', 2)[1]
    assert 'description:' in frontmatter and 'whenToUse:' in frontmatter
    assert 'subagents: []' in frontmatter
    tools = [line.strip()[2:] for line in frontmatter.splitlines() if line.startswith('  - ')]
    assert tools == expected_tools[name], (name, tools)

with tempfile.TemporaryDirectory(prefix='sdlc-kimi-') as temp:
    dest = Path(temp) / 'проект с пробелами'
    command = [sys.executable, str(root / 'install-kimi-code.py'), '--project', str(dest)]
    subprocess.run(command, check=True)
    subprocess.run(command + ['--check'], check=True)
    subprocess.run(command, check=True)
    assert len(list((dest / '.kimi-code/agents').glob('sdlc-*.md'))) == 4
    installed = dest / '.kimi-code/skills/sdlc-verify'
    subprocess.run([sys.executable, str(installed / 'tools/flow-verdict.py'), '--metrics', str(dest)], check=True)
    changed = dest / '.kimi-code/skills/sdlc-run/SKILL.md'
    changed.write_text('Локальная версия', encoding='utf-8')
    other = dest / '.kimi-code/skills/local/SKILL.md'
    other.parent.mkdir()
    other.write_text('Другой навык', encoding='utf-8')
    assert subprocess.run(command + ['--check']).returncode == 1
    subprocess.run(command, check=True)
    assert any(p.read_text(encoding='utf-8') == 'Локальная версия' for p in (dest / '.kimi-code').glob('sdlc-backup-*/.kimi-code/skills/sdlc-run/SKILL.md'))
    assert other.read_text(encoding='utf-8') == 'Другой навык'

print('Проверки комплекта Kimi Code пройдены.')
