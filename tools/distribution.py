import argparse, hashlib, json, shutil, subprocess, tempfile, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sync():
    subprocess.run(['bash','-s'],input=(ROOT/'sync-templates.sh').read_text(encoding='utf-8').encode('utf-8'),cwd=ROOT,check=True)
    codex=ROOT/'implementations/codex'
    source=ROOT/'implementations/claude-code/skills/sdlc-verify'
    for folder in ('tools','templates'):
        target=codex/'skills/sdlc-verify'/folder
        shutil.rmtree(target)
        shutil.copytree(source/folder,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for p in (ROOT/'templates').glob('*'):
        if p.is_file(): shutil.copy2(p,codex/'skills/sdlc-verify/templates'/p.name)
    shutil.copy2(ROOT/'SDLC.md',codex/'skills/sdlc-run/references/SDLC.md')
    manifest=json.loads((codex/'manifest.json').read_text(encoding='utf-8'))
    manifest.pop('revision',None)
    manifest['files']={p.relative_to(codex).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(codex.rglob('*')) if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    (codex/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def package():
    dest=ROOT/'dist'; dest.mkdir(exist_ok=True)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for variant in ('codex','claude-code'):
        base=ROOT/'implementations'/variant
        files={p.relative_to(base).as_posix():p for p in base.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
        if variant=='claude-code':
            files={('implementations/claude-code/'+name):p for name,p in files.items()}
            files.update({name:ROOT/name for name in ('install.sh','junk-names.sh','SDLC.md')})
        with zipfile.ZipFile(dest/('agent-sdlc-'+variant+'.zip'),'w',zipfile.ZIP_DEFLATED) as z:
            entries={name:p.read_bytes() for name,p in files.items()}
            entries['build.json']=json.dumps({'revision':revision,'version':json.loads((ROOT/'implementations/codex/manifest.json').read_text())['version'],'sha256':{n:hashlib.sha256(b).hexdigest() for n,b in entries.items()}},sort_keys=True).encode()
            for name,data in sorted(entries.items()):
                info=zipfile.ZipInfo('agent-sdlc-'+variant+'/'+name,(2020,1,1,0,0,0)); info.external_attr=0o100755<<16; info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,data)
    (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(dest.glob('*.zip'))))
if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['sync','package']); args=parser.parse_args()
    sync() if args.command=='sync' else package()
