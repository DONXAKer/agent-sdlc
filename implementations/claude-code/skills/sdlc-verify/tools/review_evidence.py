"""Побайтовый пакет первичных доказательств. Не вычисляет результат приёмки.

Подготовка: python review_evidence.py <корень> <slug> <N> <K> <manifest.json>
Manifest: {"files": [{"path": "qa/result.json", "claims": ["claim-1"],
"kind": "observation|stdout|screenshot|sql_trace|fixture", "source": "команда или источник",
"sha256": "64 hex", "revision": {"head_sha": "...", "diff_sha256": "..."}}]}.
Ревизия закрепляет HEAD и байты патча попытки. Старую ревизию сначала проверь заново.
Передавай ZIP вместе с квитанцией. ZIP сохраняет исходные CRLF/LF без Git-нормализации.
"""

import json
import re
import sys
import zipfile
from pathlib import Path

import snapshot_base
from sdlc_common import claims_in, git_out

KINDS = {'observation': {'.json'}, 'stdout': {'.txt', '.log'},
         'screenshot': {'.png', '.jpg', '.jpeg', '.webp'}, 'sql_trace': {'.sql', '.json', '.txt'},
         'fixture': {'.json', '.lua', '.luau', '.csv'}}


def candidate(root, d, n, k):
    return {'head_sha': (git_out(['rev-parse', 'HEAD'], root) or '').strip(),
            'diff_sha256': snapshot_base.digest((d / f'chunk-{n}-attempt-{k}-diff.patch').read_bytes())}


def paths(d, n, k):
    stem = f'chunk-{n}-attempt-{k}-review-evidence'
    return d / (stem + '.zip'), d / (stem + '.json')


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
        if (item['kind'] not in KINDS or path.suffix.lower() not in KINDS[item['kind']]
                or re.search(r'(?:^|[/_.-])(?:review|narrative|handoff|verification-report|conversation)(?:[/_.-]|$)', name, re.I)):
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


def prepare(root, d, n, k, manifest):
    revision = candidate(root, d, n, k)
    files = checked(root, d, manifest, revision)
    package, receipt = paths(d, n, k)
    if package.exists() or receipt.exists():
        raise ValueError('Пакет попытки уже существует; сохрани новую попытку')
    with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        for name, raw in files.items():
            output.writestr('files/' + name, raw)
    rec = {'version': 1, 'slug': d.name, 'chunk': int(n), 'attempt': int(k),
           'revision': revision, 'package_sha256': snapshot_base.digest(package.read_bytes())}
    with receipt.open('x', encoding='utf-8') as output:
        json.dump(rec, output, ensure_ascii=False, indent=2)
    return verify(root, d, n, k)


def verify(root, d, n, k):
    package, receipt = paths(d, n, k)
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
    return set(files)


if __name__ == '__main__':
    try:
        root, slug, n, k, source = sys.argv[1:]
        root = Path(root).resolve()
        result = prepare(root, root / '.sdlc' / slug, n, k, json.loads(Path(source).read_bytes()))
        print(json.dumps({'files': sorted(result)}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
