"""Локальный запуск с отдельным cwd и квитанциями происхождения диагностики.

python runtime_session.py <корень исходников> <хранилище вне исходников> <exe> [args...]
Аргументы и окружение процесса в квитанцию не попадают. Это наблюдение, а не песочница.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import snapshot_base
from sdlc_common import git_out


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                raise ValueError('Ссылка в runtime-пути: ' + str(part))
    return path.resolve()


def write(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        checked = plain(path)
        if not checked.is_relative_to(directory):
            raise ValueError('Диагностика вышла за каталог сеанса')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = snapshot_base.digest(path.read_bytes())
        elif path.is_dir():
            result[path.relative_to(directory).as_posix() + '/'] = 'directory'
    return result


def source_tree(root, known=()):
    return {name: {'sha256': snapshot_base.digest(raw), 'mode': mode}
            for name, (raw, mode) in snapshot_base.project_files(root, known_paths=known, git_special=True).items()}


def tree_digest(tree):
    return snapshot_base.digest(json.dumps(tree, sort_keys=True).encode('utf-8'))


def launch(root, storage, executable, args=(), document=None, timeout=None, diagnostic_roots=()):
    # Явно выбранные корни сначала канонизируются; вложенные пути сеанса остаются без ссылок.
    root, storage = plain(Path(root).resolve()), plain(Path(storage).resolve())
    if storage.is_relative_to(root) or root.is_relative_to(storage):
        raise ValueError('Хранилище runtime должно находиться отдельно от исходников')
    found = shutil.which(str(executable))
    invocation = Path(os.path.abspath(found or executable))
    exe = plain(invocation.resolve())
    target = plain(Path(document).resolve()) if document else None
    if target is not None and (not target.is_relative_to(root) or not target.is_file()):
        raise ValueError('Локальный документ должен существовать внутри исходников')
    before = source_tree(root)
    external_roots = [plain(Path(path).resolve()) for path in diagnostic_roots]
    if any(path.is_relative_to(root) or root.is_relative_to(path) for path in external_roots):
        raise ValueError('Внешние каталоги диагностики должны быть отделены от исходников')
    external_before = {str(plain(path)) for base in external_roots if base.is_dir() for path in base.rglob('*') if path.is_file()}
    storage.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    session = storage / session_id
    session.mkdir()
    working = session / 'working'
    diagnostics, temporary = session / 'diagnostics', session / 'temporary'
    for path in (working, diagnostics, temporary):
        path.mkdir()
    record = {'version': 1, 'sessionId': session_id, 'executable': str(exe),
              'executableInvocation': str(invocation),
              'workingDirectory': str(working), 'sourceRoot': str(root),
              'document': str(target) if target else None,
              'document_sha256': snapshot_base.digest(target.read_bytes()) if target else None,
              'diagnosticsDirectory': str(diagnostics), 'temporaryDirectory': str(temporary),
              'requestedAt': now(), 'source_before': before}
    record['allowedDiagnosticRoots'] = [str(path) for path in external_roots]
    record['revision'] = {'head_sha': (git_out(['rev-parse', 'HEAD'], root) or '').strip(),
                          'source_sha256': tree_digest(before)}
    write(session / 'launch.json', record)
    environment = {**os.environ, 'SDLC_RUNTIME_SESSION_ID': session_id,
                   'SDLC_RUNTIME_DIAGNOSTICS': str(diagnostics), 'TMP': str(temporary),
                   'TEMP': str(temporary), 'TMPDIR': str(temporary)}
    started = None
    error = None
    code = None
    timed_out = False
    process = None
    with (diagnostics / 'stdout.txt').open('xb') as stdout, (diagnostics / 'stderr.txt').open('xb') as stderr:
        try:
            actual_args = args(record) if callable(args) else args
            process = subprocess.Popen([str(invocation), *map(str, actual_args)], cwd=working, env=environment,
                                       stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            started = {'sessionId': session_id, 'instanceId': session_id, 'pid': process.pid,
                       'startedAt': now(), 'executable': str(exe)}
            write(session / 'started.json', started)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.terminate()
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait(timeout=5)
        except OSError as failure:
            error = type(failure).__name__
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait(timeout=5)
    after = source_tree(root, before)
    changes = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    external = []
    captured = set()
    for base in external_roots:
        for path in sorted(base.rglob('*')):
            checked = plain(path)
            if not path.is_file() or str(checked) in external_before or checked in captured:
                continue
            raw = checked.read_bytes()
            if session_id.encode('ascii') not in raw[:65536]:
                continue
            sha = snapshot_base.digest(raw)
            copied = diagnostics / ('external-' + snapshot_base.digest(str(checked).encode('utf-8')) + '.log')
            with copied.open('xb') as output:
                output.write(raw)
            captured.add(checked)
            external.append({'source_path': str(checked), 'sha256': sha, 'path': copied.name,
                             'origin': 'new_file_with_unique_session_marker'})
    result = {'version': 1, 'sessionId': session_id, 'process': started, 'endedAt': now(),
              'exitCode': code, 'launchError': error, 'timedOut': timed_out,
              'diagnostics': inventory(diagnostics), 'workingFiles': inventory(working),
              'temporaryFiles': inventory(temporary), 'source_changes': changes,
              'external_diagnostics': external,
              'isolation': 'violated' if changes else 'observed_clean',
              'launch_sha256': snapshot_base.digest((session / 'launch.json').read_bytes())}
    write(session / 'ended.json', result)
    return session, result


def cleanup_temporary(storage, session_id):
    storage = plain(Path(storage).resolve())
    if not __import__('re').fullmatch('[0-9a-f]{32}', session_id):
        raise ValueError('Недопустимый sessionId')
    session = plain(storage / session_id)
    launch_record = json.loads((session / 'launch.json').read_bytes())
    ended = json.loads((session / 'ended.json').read_bytes())
    if (launch_record['sessionId'] != session_id or ended['sessionId'] != session_id
            or snapshot_base.digest((session / 'launch.json').read_bytes()) != ended['launch_sha256']):
        raise ValueError('Происхождение временных данных не подтверждено')
    temporary = plain(session / 'temporary')
    if launch_record['temporaryDirectory'] != str(temporary) or temporary.parent != session:
        raise ValueError('Временный путь отличается от квитанции')
    if inventory(temporary) != ended['temporaryFiles']:
        raise ValueError('После завершения появились чужие временные данные; очистка запрещена')
    shutil.rmtree(temporary)


if __name__ == '__main__':
    try:
        session, result = launch(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4:])
        print(json.dumps({'session': str(session), **result}, ensure_ascii=False))
        sys.exit(int(result['exitCode'] != 0 or result['isolation'] != 'observed_clean'))
    except (OSError, ValueError, IndexError) as error:
        print(str(error))
        sys.exit(1)
