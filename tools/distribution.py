"""Синхронизация и воспроизводимая упаковка установочных ресурсов SDLC."""

# Вывод не должен зависеть от системной кодировки Windows и перенаправления в CI.
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = Path('implementations/claude-code')
CODEX = Path('implementations/codex')
CURSOR = Path('implementations/cursor')
TEMPLATES = {
    'intent': ('intent',), 'gates': ('intent',),
    'readiness': ('intent', 'plan'), 'exploration-report': ('explore',),
    'clarification-report': ('ask',), 'plan': ('plan',),
    'acceptance-checks': ('plan', 'verify'), 'chunk-journal': ('chunk',),
    'verification-report': ('verify',), 'handoff': ('handoff',),
}
RESOURCE_RECORD = CODEX / 'generated-resources.json'


def canonical_bytes(path):
    """Приведи распространяемый UTF-8 текст к LF до копирования и хэширования."""
    return path.read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8')


def inventory(root):
    data = json.loads((root / 'distribution-files.json').read_text(encoding='utf-8'))
    for paths in data.values():
        if len(paths) != len(set(paths)):
            raise ValueError('Повтор пути в списке распространения')
        for name in paths:
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise ValueError(f'Недопустимый путь комплекта: {name}')
    return data


def selected_files(root, paths, overrides=None):
    root = root.resolve()
    result = {}
    overrides = overrides or {}
    for name in paths:
        path = Path(name)
        target = root / path
        if not target.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(root)):
            raise ValueError(f'Символьная ссылка в комплекте: {path}')
        result[path] = overrides[path] if path in overrides else canonical_bytes(target)
    return result


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def generated(root):
    desired = {}
    for name, skills in TEMPLATES.items():
        source = Path('templates') / (name + '.template.md')
        for skill in skills:
            desired[CLAUDE / 'skills' / ('sdlc-' + skill) / 'templates' / source.name] = canonical_bytes(root / source)
    tools = CLAUDE / 'skills/sdlc-verify/tools'
    desired[tools / 'sdlc-constants.json'] = canonical_bytes(root / 'sdlc-constants.json')
    contract = Path('implementations/runner-contract')
    desired[tools / 'state_contract.py'] = canonical_bytes(root / contract / 'tools/state_contract.py')
    for name in ('agent-sdlc-state-v1.schema.json', 'verify-review-v1.schema.json'):
        desired[tools / 'state_contract/schemas' / name] = canonical_bytes(root / contract / 'schemas' / name)
    # Инструменты Claude Code остаются первоисточником общей реализации.
    tool_files = selected_files(root, inventory(root)['shared_tools'], desired)
    tool_files.update({p: data for p, data in desired.items() if p.is_relative_to(tools)})
    for path, data in tool_files.items():
        desired[CODEX / 'skills/sdlc-verify/tools' / path.relative_to(tools)] = data
    for name in TEMPLATES:
        path = Path('templates') / (name + '.template.md')
        desired[CODEX / 'skills/sdlc-verify/templates' / path.name] = canonical_bytes(root / path)
    desired[CODEX / 'skills/sdlc-run/references/SDLC.md'] = canonical_bytes(root / 'SDLC.md')
    # Cursor использует те же ресурсы. Меняются только обращения к среде и вызовы навыков.
    for path, data in selected_files(root, [name for name in inventory(root)['codex'] if Path(name).is_relative_to(CODEX / 'skills')], desired).items():
        if path.name == 'SKILL.md':
            data = data.decode('utf-8').replace('Codex', 'Cursor').replace('$sdlc-', '/sdlc-').encode('utf-8')
        desired[CURSOR / path.relative_to(CODEX)] = data
    return desired


def sync(root=ROOT, check=False):
    root = root.resolve()
    desired = generated(root)
    owned = set()
    if (root / RESOURCE_RECORD).exists():
        owned = {Path(p) for p in json.loads((root / RESOURCE_RECORD).read_text(encoding='utf-8'))}
    for path in owned:
        if '..' in path.parts or path.is_absolute() or not any(path.is_relative_to(p / 'skills') for p in (CLAUDE, CODEX, CURSOR)):
            raise ValueError(f'Недопустимый путь ресурса: {path}')
    stale = owned - desired.keys()
    desired[RESOURCE_RECORD] = encoded(sorted(p.as_posix() for p in desired))
    codex_files = selected_files(root, inventory(root)['codex'], desired)
    for path in stale:
        codex_files.pop(path, None)
    codex_files.update({p: data for p, data in desired.items() if p.is_relative_to(CODEX)})
    # Запиши те же LF-байты, которые получит чистый checkout.
    desired.update({p: data for p, data in codex_files.items() if p != CODEX / 'manifest.json'})
    manifest_path = CODEX / 'manifest.json'
    manifest = json.loads((root / manifest_path).read_text(encoding='utf-8'))
    manifest.pop('revision', None)
    manifest['files'] = {p.relative_to(CODEX).as_posix(): hashlib.sha256(data).hexdigest()
                         for p, data in sorted(codex_files.items()) if p != manifest_path}
    desired[manifest_path] = encoded(manifest)
    changes = [p for p, data in desired.items() if not (root / p).is_file() or (root / p).read_bytes() != data]
    removed = [p for p in stale if (root / p).exists()]
    if check:
        for path in changes + removed:
            print(f'РАСХОЖДЕНИЕ: {path}')
        return not (changes or removed)
    # Проверь цели до записи и удаления. Не переходи через символьные ссылки.
    for path in changes + removed:
        target = root / path
        if not target.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(root)):
            raise ValueError(f'Недопустимая цель: {target}')
    for path in changes:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(desired[path])
    for path in removed:
        (root / path).unlink()
    print(f'Обновлено: {len(changes)}; удалено: {len(removed)}')
    return True


def package(root=ROOT, revision=None):
    root = root.resolve()
    if not sync(root, check=True):
        raise ValueError('Установочные ресурсы устарели. Выполни команду sync.')
    if revision is None:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dest = root / 'dist'
    dest.mkdir(exist_ok=True)
    version = json.loads((root / CODEX / 'manifest.json').read_text(encoding='utf-8'))['version']
    archives = []
    for variant, base in (('codex', CODEX), ('claude-code', CLAUDE)):
        payload = selected_files(root, inventory(root)[variant])
        if variant == 'codex':
            entries = {p.relative_to(base).as_posix(): data for p, data in payload.items()}
        else:
            entries = {p.as_posix(): data for p, data in payload.items()}
            for name in ('install.sh', 'junk-names.sh', 'SDLC.md'):
                entries[name] = canonical_bytes(root / name)
        entries['build.json'] = encoded({'revision': revision, 'version': version,
                                        'sha256': {n: hashlib.sha256(b).hexdigest() for n, b in sorted(entries.items())}})
        archive = dest / f'agent-sdlc-{variant}.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(f'agent-sdlc-{variant}/{name}', (2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, data)
        archives.append(archive)
    (dest / 'SHA256SUMS').write_bytes(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in archives).encode('ascii'))
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('sync', 'check', 'package'))
    args = parser.parse_args()
    if args.command == 'package':
        package()
    else:
        return int(not sync(check=args.command == 'check'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
