"""Исполнение и сверка именованного гейта Verify для конкретной попытки."""

import json
import os
import re
import tempfile
import time
from pathlib import Path

import snapshot_base
from sdlc_common import ARTIFACTS_DIR, MANDATORY_GATES, command_of, enabled_gates, gate_context, read, run_shell

PATCH_GATES = {'Scope: файлы вне плана', 'Анти-обход тест-гейта'}
OTHER_RESULTS = (set(MANDATORY_GATES) - PATCH_GATES) | {'Сверка отчёта с набором'}


def needs_receipt(name, command):
    return name in PATCH_GATES or (bool(command) and name not in OTHER_RESULTS)


def gate_command(root, name):
    gates = read(root / ARTIFACTS_DIR / 'gates.md', '')
    commands = [command_of(how) for gate, _, how in enabled_gates(gates, 'этап 6') if gate == name]
    if len(commands) != 1 or not commands[0]:
        raise ValueError(f'Гейт «{name}»: нужна одна включённая команда этапа 6 из gates.md')
    return commands[0]


def configured_context(root, d, name, command, n, k, layer, pending=False):
    if not all(re.fullmatch(r'[1-9][0-9]*', str(value)) for value in (n, k)):
        raise ValueError('Недопустимый номер chunk или попытки')
    inputs = gate_context(root, d, command, n, k, layer, allow_pending_patch=pending)
    if name in PATCH_GATES:
        patch = d / f'chunk-{n}-attempt-{k}-diff.patch'
        if str(patch) not in inputs.values() or re.search(r'chunk-\d+-attempt-\d+-diff\.patch', command):
            raise ValueError(f'Гейт «{name}»: объяви вход patch текущей попытки в gate-context.json и убери фиксированный путь')
    return inputs


def identity(d, n, k, name, command):
    prefix = f'chunk-{n}-attempt-{k}'
    patch = snapshot_base.safe_path(d, prefix + '-diff.patch')
    evidence = snapshot_base.safe_path(d, prefix + '-evidence.json')
    if any(not path.is_file() or path.is_symlink() for path in (patch, evidence)):
        raise ValueError('Патч и evidence попытки отсутствуют либо не являются обычными файлами')
    patch_hash = snapshot_base.digest(patch.read_bytes())
    raw = evidence.read_bytes()
    source = json.loads(raw)
    if (not isinstance(source, dict) or source.get('tool') != 'attempt-evidence.py'
            or source.get('slug') != d.name or source.get('chunk') != n or source.get('attempt') != k
            or source.get('diff_sha256') != patch_hash):
        raise ValueError('Патч и evidence не подтверждают текущую попытку')
    base = source.get('base', {'kind': 'git', 'id': source.get('base_sha')})
    return {'version': 1, 'tool': 'attempt-evidence.py run-gate', 'slug': d.name, 'base': base,
            'chunk': n, 'attempt': k, 'gate': name, 'command': command,
            'patch_sha256': patch_hash, 'evidence_sha256': snapshot_base.digest(raw)}


def directory(d, n, k, name):
    slug = snapshot_base.digest(name.encode('utf-8'))
    relative = f'.chunk-{n}-gate-runs/attempt-{k}/{slug}'
    path = snapshot_base.safe_path(d, relative)
    if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)):
        raise ValueError('Каталог квитанций гейта не может быть ссылкой')
    return path


def portable_context(d, n, k, inputs):
    patch = d / f'chunk-{n}-attempt-{k}-diff.patch'
    return {key: patch.name if value == str(patch) else value for key, value in inputs.items()}


def run(root, d, n, k, name, command, context, layer, current_revision):
    n, k = int(n), int(k)
    record = identity(d, n, k, name, command)
    revision = record['base'], record['patch_sha256']
    if current_revision() != revision:
        raise ValueError(f'Гейт «{name}»: рабочее дерево отличается от патча текущей попытки')
    target = directory(d, n, k, name)
    target.mkdir(parents=True, exist_ok=True)
    code, output = run_shell(command, root, env={**os.environ, **context})
    if identity(d, n, k, name, command) != record:
        raise ValueError(f'Гейт «{name}» изменил патч или evidence попытки')
    if current_revision() != revision:
        raise ValueError(f'Гейт «{name}» изменил рабочее дерево; квитанция не выпущена')
    raw = output.encode('utf-8', errors='surrogateescape')
    record.update(exit_code=code, output_sha256=snapshot_base.digest(raw), layer=layer,
                  context=portable_context(d, n, k, context))
    with tempfile.TemporaryDirectory(dir=target, prefix='.run-') as tmp:
        staging = Path(tmp)
        (staging / 'output.txt').write_bytes(raw)
        (staging / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        published = target / f'run-{time.time_ns():020d}-{staging.name.removeprefix(".run-")}'
        staging.rename(published)
    return code, output, published / 'receipt.json'


def errors(root, d, n, k, name):
    try:
        command = gate_command(root, name)
        runs = sorted(directory(d, n, k, name).glob('run-*'))
        if not runs:
            raise ValueError('нет квитанции запуска команды')
        latest = runs[-1]
        receipt, output = latest / 'receipt.json', latest / 'output.txt'
        if any(path.is_symlink() for path in (latest, receipt, output)):
            raise ValueError('квитанция и вывод команды должны быть обычными файлами')
        data = json.loads(receipt.read_bytes())
        raw = output.read_bytes()
        expected = identity(d, n, k, name, command)
        if not isinstance(data, dict) or any(data.get(key) != value for key, value in expected.items()):
            raise ValueError('квитанция относится к другому гейту, команде или патчу попытки')
        context = configured_context(root, d, name, command, n, k, data.get('layer'))
        if data.get('context') != portable_context(d, n, k, context):
            raise ValueError('Входы команды отличаются от зафиксированных при запуске')
        if type(data.get('exit_code')) is not int or data['exit_code'] != 0:
            raise ValueError(f'код выхода команды: {data.get("exit_code")!r}')
        if data.get('output_sha256') != snapshot_base.digest(raw):
            raise ValueError('SHA-256 вывода команды не совпадает')
        last = raw.rstrip().rsplit(b'\n', 1)[-1]
        try:
            result = json.loads(last)
        except (ValueError, UnicodeError):
            result = None
        if isinstance(result, dict) and 'gate' in result and result.get('status') != 'pass':
            raise ValueError(f'вывод гейт-скрипта: {result.get("status")!r}')
    except (OSError, TypeError, ValueError) as error:
        return [f'Гейт «{name}», chunk {n}, попытка {k}: {error}']
    return []
