"""Упакуй только файлы, перечисленные в проверенном манифесте Codex."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
entries = {}
for name, digest in manifest['files'].items():
    path = root / name
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Недопустимый путь комплекта: {name}')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f'Повреждён комплект: {name}')
    entries[name] = data
entries['manifest.json'] = (root / 'manifest.json').read_bytes()
archive = root.parent / 'agent-sdlc-codex.zip'
with zipfile.ZipFile(archive, 'w') as output:
    for name, data in sorted(entries.items()):
        info = zipfile.ZipInfo('agent-sdlc-codex/' + name, (2020, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        output.writestr(info, data)
print(archive)
