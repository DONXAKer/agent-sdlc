"""Ограниченный журнал повторной проверки кандидата. Не вычисляет приёмку.

python verification_runs.py <корень> <slug> <chunk> <attempt> <manifest.json>
Политику verification-policy.json закрепи SHA-256 в плане до prepare.
Каждая запись содержит новый вывод той же команды и отдельный пакет первичных файлов.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import chunk_preconditions
import review_evidence
import snapshot_base
from sdlc_common import ARTIFACTS_DIR, read, run_shell, gate_context, files_to_touch


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def candidate(root, d, known=(), verification=None):
    planned = files_to_touch(read(d / 'plan.md', ''))
    files = snapshot_base.project_files(root, known_paths=set(known) | set(planned), d=d,
                                        git_special=snapshot_base.kind_of(read(d / 'plan.md', '')) == 'git')
    for name in planned:
        if (root / name).is_file() and name not in files:
            raise ValueError('Объявленный путь исключён из кандидата: ' + name)
    tree = {name: {'sha256': snapshot_base.digest(raw), 'mode': mode} for name, (raw, mode) in files.items()}
    for name in ('.sdlc/gates.md', '.sdlc/gate-context.json'):
        path = snapshot_base.safe_path(root, name)
        if path.is_file():
            tree[name] = {'sha256': snapshot_base.digest(path.read_bytes()), 'mode': 0o644}
    return snapshot_base.digest(encoded({'tree': tree, 'verification': verification})), tree


def policy(d, n):
    raw = (d / 'verification-policy.json').read_bytes()
    value = json.loads(raw)
    if (set(value) != {'version', 'fix_id', 'candidate_budget', 'verification_run_limit', 'status'}
            or value['version'] != 1 or value['status'] != 'active'
            or not re.fullmatch(r'[\w.-]+', value['fix_id'])
            or any(type(value[key]) is not int or not 1 <= value[key] <= 10
                   for key in ('candidate_budget', 'verification_run_limit'))):
        raise ValueError('Политика отсутствует, остановлена или содержит недопустимые лимиты')
    receipt = json.loads(chunk_preconditions.receipt_path(d, n).read_bytes())
    plan = chunk_preconditions.artifact(d, receipt['plan_path'])
    if chunk_preconditions.text_digest(plan) != receipt['plan_sha256']:
        raise ValueError('Исторический план изменён')
    pin = re.findall(r'\*\*Политика повторной верификации:\*\*\s*`?([0-9a-f]{64})', read(plan, ''))
    if pin != [snapshot_base.digest(raw)]:
        raise ValueError('Политика не закреплена до prepare или изменена')
    return value, pin[0]


def history(directory):
    records = []
    previous = None
    for path in sorted(directory.glob('run-*.json')):
        raw = path.read_bytes()
        record = json.loads(raw)
        if record['verification_run_id'] != len(records) + 1 or record['previous_sha256'] != previous:
            raise ValueError('Разрыв истории verification_run')
        for name, sha in record['outputs'].items():
            target = snapshot_base.safe_path(directory, name)
            if not target.is_file() or snapshot_base.digest(target.read_bytes()) != sha:
                raise ValueError('Первичный файл прежнего запуска изменён: ' + name)
        records.append(record)
        previous = snapshot_base.digest(raw)
    return records, previous


def source_history(d, config_hash):
    paths = sorted((path for path in d.glob('chunk-*-attempt-*-evidence.json')
                    if re.fullmatch(r'chunk-(\d+)-attempt-(\d+)-evidence.json', path.name)),
                   key=lambda p: tuple(map(int, re.fullmatch(r'chunk-(\d+)-attempt-(\d+)-evidence.json', p.name).groups())))
    result = []
    count = 0
    previous = None
    for path in paths:
        value = json.loads(path.read_bytes()).get('candidate_tracking')
        if not value or value['policy_sha256'] != config_hash:
            raise ValueError('Исходная попытка не имеет закреплённого счётчика кандидата')
        count += int(value['candidate_digest'] != previous)
        if value['candidate_attempt'] != count or snapshot_base.digest(encoded({'tree': value['tree'], 'verification': value['verification']})) != value['candidate_digest']:
            raise ValueError('Счётчик или digest истории кандидатов изменён')
        result.append((path, value))
        previous = value['candidate_digest']
    return result


def track_source(root, d, n, command, layer, context):
    config, config_hash = policy(d, n)
    sources = source_history(d, config_hash)
    known = {name for _, value in sources for name in value['tree']}
    verification = {'command': command, 'layer': layer, 'context': context}
    current, tree = candidate(root, d, known, verification)
    prior = sources[-1][1] if sources else None
    count = (prior['candidate_attempt'] if prior else 0) + int(not prior or prior['candidate_digest'] != current)
    if count > config['candidate_budget']:
        raise ValueError('Исчерпан бюджет исправлений; новый chunk его не обнуляет')
    return {'policy_sha256': config_hash, 'fix_id': config['fix_id'], 'candidate_digest': current,
            'candidate_attempt': count, 'tree': tree, 'verification': verification}


def record(root, d, n, k, manifest):
    config, config_hash = policy(d, n)
    problems = chunk_preconditions.errors(root, d, n)
    if problems:
        raise ValueError('; '.join(problems))
    directory = d / 'verification-runs'
    directory.mkdir(exist_ok=True)
    lock = directory / '.lock'
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError('Другая проверка выполняется; после сбоя проверь незавершённый запуск вручную') from error
    try:
        records, previous = history(directory)
        if any(rec['policy_sha256'] != config_hash for rec in records):
            raise ValueError('Смена chunk не разрешает менять политику того же исправления')
        sources = source_history(d, config_hash)
        source = d / f'chunk-{n}-attempt-{k}-evidence.json'
        if not sources or sources[-1][0] != source:
            raise ValueError('Проверяй последнюю исходную попытку; история кандидатов не обнуляется')
        tracking = sources[-1][1]
        known = {name for _, value in sources for name in value['tree']}
        current, tree = candidate(root, d, known, tracking['verification'])
        if current != tracking['candidate_digest']:
            raise ValueError('Кандидат изменился после исходной попытки')
        attempt = tracking['candidate_attempt']
        if attempt > config['candidate_budget']:
            raise ValueError('Исчерпан бюджет исправлений; новый chunk его не обнуляет')
        same = [rec for rec in records if rec['candidate_attempt'] == attempt]
        if len(same) >= config['verification_run_limit']:
            raise ValueError('Исчерпан предел повторной верификации кандидата')
        if len(same) >= 2 and same[-1]['failure_signature'] and same[-1]['failure_signature'] == same[-2]['failure_signature']:
            raise ValueError('Повтор одинакового отказа; требуется решение человека')
        evidence = json.loads(source.read_bytes())
        verified = subprocess.run([sys.executable, str(Path(__file__).with_name('attempt-evidence.py')),
                                   'verify', str(root), d.name, str(n), str(k)], capture_output=True)
        if verified.returncode:
            raise ValueError('Исходная попытка не соответствует текущему дереву')
        revision = review_evidence.candidate(root, d, n, k)
        if evidence.get('diff_sha256') != revision['diff_sha256']:
            raise ValueError('Патч исходной попытки изменён')
        files = review_evidence.checked(root, d, manifest, revision)
        command = evidence['tests_cmd']
        if tracking['verification'] != {'command': command, 'layer': evidence.get('layer'), 'context': evidence.get('gate_context', {})}:
            raise ValueError('Конфигурация проверки отличается от закреплённого кандидата')
        context = evidence.get('gate_context', {})
        configured = gate_context(root, d, command, n, k, evidence.get('layer'))
        if configured != context:
            raise ValueError('Контекст команды изменился; сохрани новую попытку')
        index = len(records) + 1
        outputs = directory / f'run-{index:04d}'
        outputs.mkdir()
        code, stdout = run_shell(command, root, env={**os.environ, **context})
        (outputs / 'tests.txt').write_bytes(stdout.encode('utf-8', errors='surrogateescape'))
        after, _ = candidate(root, d, known, tracking['verification'])
        if after != current:
            raise ValueError('Команда изменила кандидата; результаты сохранены как незавершённые')
        (outputs / 'manifest.json').write_bytes(encoded(manifest))
        for number, (name, raw) in enumerate(sorted(files.items())):
            (outputs / f'evidence-{number:04d}.bin').write_bytes(raw)
        hashes = {path.relative_to(directory).as_posix(): snapshot_base.digest(path.read_bytes()) for path in outputs.iterdir()}
        value = {'version': 1, 'fix_id': config['fix_id'], 'policy_sha256': config_hash,
                 'verification_run_id': index, 'candidate_digest': current, 'candidate_attempt': attempt,
                 'tree': tree, 'previous_sha256': previous, 'source_attempt': {'chunk': int(n), 'attempt': int(k),
                 'evidence_sha256': snapshot_base.digest(source.read_bytes())},
                 'recorded_at': datetime.now(timezone.utc).isoformat(), 'revision': revision,
                 'tests_exit': code, 'failure_signature': snapshot_base.digest(encoded([code, stdout])) if code else None,
                 'outputs': hashes, 'acceptance': 'requires_review'}
        with (directory / f'run-{index:04d}.json').open('xb') as output:
            output.write(encoded(value))
        return value
    finally:
        lock.rmdir()


if __name__ == '__main__':
    try:
        root, slug, n, k, manifest = sys.argv[1:]
        root = Path(root).resolve()
        d = snapshot_base.safe_path(root, ARTIFACTS_DIR + '/' + slug)
        print(json.dumps(record(root, d, n, k, json.loads(Path(manifest).read_bytes())), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        print(str(error))
        sys.exit(1)
