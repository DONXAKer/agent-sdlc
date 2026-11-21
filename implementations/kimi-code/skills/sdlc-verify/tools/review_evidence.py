"""Побайтовый пакет первичных доказательств. Не вычисляет результат приёмки.

Подготовка: python review_evidence.py <корень> <slug> <N> <K> <manifest.json>
Дополнение до ревью: python review_evidence.py --extend <корень> <slug> <N> <K> <полный-manifest.json>
Контекст reviewer: python review_evidence.py --context <корень> <slug> <N> <K>
evidencePath берётся из manifest.path; archiveMember указывает отдельное транспортное имя ZIP.
Manifest: {"files": [{"path": "qa/result.json", "claims": ["claim-1"],
"kind": "observation|stdout|screenshot|video|sql_trace|fixture", "source": "команда или источник",
"sha256": "64 hex", "revision": {"head_sha": "...", "diff_sha256": "..."}}]}.
Ревизия закрепляет HEAD и байты патча попытки. Старую ревизию сначала проверь заново.
Передавай ZIP вместе с квитанцией. ZIP сохраняет исходные CRLF/LF без Git-нормализации.
"""

import json
import re
import sys
import zipfile
import tempfile
import subprocess
from pathlib import Path

import snapshot_base
from sdlc_common import claims_in, git_out

KINDS = {'observation': {'.json'}, 'stdout': {'.txt', '.log'},
         'video': {'.webm'}, 'screenshot': {'.png', '.jpg', '.jpeg', '.webp'}, 'sql_trace': {'.sql', '.json', '.txt'},
         'fixture': {'.json', '.lua', '.luau', '.csv'}}


def candidate(root, d, n, k):
    return {'head_sha': (git_out(['rev-parse', 'HEAD'], root) or '').strip(),
            'diff_sha256': snapshot_base.digest((d / f'chunk-{n}-attempt-{k}-diff.patch').read_bytes())}


def edition(d, n, k):
    current = d
    number = 1
    while True:
        following = d / f'chunk-{n}-attempt-{k}-review-evidence-v{number + 1}'
        if not following.exists():
            return current, following
        if not following.is_dir() or following.is_symlink():
            raise ValueError('Некорректный каталог редакции пакета')
        current = following
        number += 1


def paths(d, n, k, storage=None):
    d = storage or edition(d, n, k)[0]
    stem = f'chunk-{n}-attempt-{k}-review-evidence'
    return d / (stem + '.zip'), d / (stem + '.json')


def context_path(d, n, k, storage=None):
    d = storage or edition(d, n, k)[0]
    return d / f'chunk-{n}-attempt-{k}-review-evidence-context.json'


def reviewer_context(root, d, n, k):
    """Получить проверенную карту адресов до первого ответа reviewer, не изменяя пакет."""
    if not paths(d, n, k)[0].is_file():
        raise ValueError('Первичный пакет отсутствует; контекст reviewer не подтверждён')
    context = verify(root, d, n, k, with_context=True)
    saved = context_path(d, n, k)
    if saved.exists() and json.loads(saved.read_bytes()) != context:
        raise ValueError('Карта адресов reviewer отличается от проверенного пакета')
    return context


def checked(root, d, manifest, revision):
    if not isinstance(manifest, dict) or set(manifest) != {'files'} or not isinstance(manifest['files'], list):
        raise ValueError('Manifest должен содержать только массив files')
    known = claims_in((d / 'intent.md').read_text(encoding='utf-8'))
    result = {}
    for item in manifest['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'claims', 'kind', 'source', 'sha256', 'revision'}:
            raise ValueError('Недопустимые поля первичного доказательства')
        name = item['path']
        if not isinstance(name, str):
            raise ValueError('Путь доказательства должен быть строкой')
        path = snapshot_base.safe_path(root, name)
        if name in result or path.is_symlink() or not path.is_file():
            raise ValueError('Доказательство отсутствует, повторено или является ссылкой')
        if item['kind'] not in KINDS or path.suffix.lower() not in KINDS[item['kind']]:
            raise ValueError('Тип или расширение первичного доказательства не поддерживается')
        if re.search(r'(?:^|[/_.-])(?:review|narrative|handoff|verification-report|conversation)(?:[/_.-]|$)', name, re.I):
            raise ValueError('Рассказ, прежнее ревью и история не являются первичным доказательством')
        if not isinstance(item['source'], str) or not item['source'].strip():
            raise ValueError('Нет источника получения доказательства')
        ids = item['claims']
        if not isinstance(ids, list) or not ids or not all(isinstance(cid, str) and cid in known for cid in ids):
            raise ValueError('Доказательство требует существующие claim-ID')
        if item['revision'] != revision:
            raise ValueError('Чужая ревизия: проверь применимость и получи новое первичное доказательство')
        raw = path.read_bytes()
        if path.suffix.lower() == '.json':
            try:
                value = json.loads(raw)
            except ValueError as error:
                raise ValueError('Первичный JSON не разбирается') from error
            messages = value.get('messages') if isinstance(value, dict) else None
            conversation = (isinstance(messages, list) and any(isinstance(message, dict)
                            and message.get('role') in ('user', 'assistant', 'system', 'developer')
                            and 'content' in message for message in messages))
            if isinstance(value, dict) and (value.get('schema_version') == 'agent-sdlc/verify-review/v1'
                    or {'claims', 'findings'} <= value.keys() or conversation
                    or any(key in value for key in ('narrative', 'conversation'))):
                raise ValueError('Содержимое является рассказом, историей диалога или прежним ревью')
        if snapshot_base.digest(raw) != item['sha256']:
            raise ValueError('SHA-256 первичного доказательства не совпадает: ' + name)
        result[name] = raw
    return result


def extend(root, d, n, k, manifest):
    """Дополни неизменённый пакет перед ревью. Опубликуй новую редакцию целиком."""
    if ((d / '.review-lock').exists() or (d / f'.review-validation-{n}-attempt-{k}.json').exists()
            or any(d.glob(f'chunk-{n}-attempt-{k}-*review*.md'))
            or (d / f'verification-report-{n}-attempt-{k}.md').exists()):
        raise ValueError('Ревью уже началось; пакет этой проверки закреплён')
    command = [sys.executable, str(Path(__file__).with_name('attempt-evidence.py')),
               'verify', str(root), d.name, str(n), str(k)]
    run = subprocess.run(command, capture_output=True)
    if run.returncode or not json.loads(run.stdout)['patch_matches_evidence']:
        raise ValueError('Рабочее дерево или патч отличается от закреплённого кандидата')
    current, following = edition(d, n, k)
    package, _ = paths(d, n, k, current)
    verify(root, d, n, k, storage=current)
    with zipfile.ZipFile(package) as archive:
        previous = json.loads(archive.read('manifest.json'))['files']
    checked(root, d, manifest, candidate(root, d, n, k))
    if len(manifest['files']) <= len(previous) or any(item not in manifest['files'] for item in previous):
        raise ValueError('Сохрани все прежние записи и добавь пропущенные файлы')
    with tempfile.TemporaryDirectory(dir=d) as temp:
        stage = Path(temp) / 'package'
        stage.mkdir()
        result = prepare(root, d, n, k, manifest, storage=stage)
        stage.rename(following)
    return result


def prepare(root, d, n, k, manifest, storage=None):
    revision = candidate(root, d, n, k)
    files = checked(root, d, manifest, revision)
    package, receipt = paths(d, n, k, storage)
    if package.exists() or receipt.exists() or context_path(d, n, k, storage).exists():
        raise ValueError('Пакет попытки уже существует. Для дополнения до ревью используй --extend')
    with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        for name, raw in files.items():
            output.writestr('files/' + name, raw)
    rec = {'version': 1, 'slug': d.name, 'chunk': int(n), 'attempt': int(k),
           'revision': revision, 'package_sha256': snapshot_base.digest(package.read_bytes())}
    with receipt.open('x', encoding='utf-8') as output:
        json.dump(rec, output, ensure_ascii=False, indent=2)
    context = verify(root, d, n, k, with_context=True, storage=storage)
    with context_path(d, n, k, storage).open('x', encoding='utf-8') as output:
        json.dump(context, output, ensure_ascii=False, indent=2)
    return {item['evidencePath'] for item in context['files']}


def verify(root, d, n, k, with_context=False, storage=None):
    package, receipt = paths(d, n, k, storage)
    if not package.exists() and not receipt.exists():
        return set()
    rec = json.loads(receipt.read_bytes())
    if (rec.get('version') != 1 or rec.get('slug') != d.name or rec.get('chunk') != int(n)
            or rec.get('attempt') != int(k) or rec.get('revision') != candidate(root, d, n, k)
            or rec.get('package_sha256') != snapshot_base.digest(package.read_bytes())):
        raise ValueError('Квитанция пакета не соответствует попытке или переданным байтам')
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        files = checked(root, d, manifest, rec['revision'])
        if set(archive.namelist()) != {'manifest.json'} | {'files/' + name for name in files}:
            raise ValueError('Пакет содержит незаявленные файлы')
        for name, raw in files.items():
            if archive.read('files/' + name) != raw:
                raise ValueError('Переданные байты отличаются от исходного доказательства')
    if with_context:
        by_name = {item['path']: item for item in manifest['files']}
        return {'version': 1, 'revision': rec['revision'], 'package_sha256': rec['package_sha256'],
                'files': [{'evidencePath': name, 'archiveMember': 'files/' + name,
                           'sha256': by_name[name]['sha256'], 'claims': by_name[name]['claims']}
                          for name in sorted(files)]}
    return set(files)


if __name__ == '__main__':
    try:
        argv = sys.argv[1:]
        context_only = bool(argv and argv[0] == '--context')
        extend_only = bool(argv and argv[0] == '--extend')
        if extend_only:
            argv = argv[1:]
        if context_only:
            root, slug, n, k = argv[1:]
        else:
            root, slug, n, k, source = argv
        root = Path(root).resolve()
        d = root / '.sdlc' / slug
        if context_only:
            print(json.dumps(reviewer_context(root, d, n, k), ensure_ascii=False))
        else:
            operation = extend if extend_only else prepare
            result = operation(root, d, n, k, json.loads(Path(source).read_bytes()))
            print(json.dumps({'files': sorted(result), 'package': str(paths(d, n, k)[0]), 'reviewerContext': reviewer_context(root, d, n, k)}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
