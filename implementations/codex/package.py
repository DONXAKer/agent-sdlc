from pathlib import Path
import zipfile
root=Path(__file__).resolve().parent
archive=root.parent/'agent-sdlc-codex.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            z.write(p,Path('agent-sdlc-codex')/p.relative_to(root))
print(archive)
