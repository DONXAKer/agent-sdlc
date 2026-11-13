"""Протокол условий испытаний и проверки границы restart. Не заменяет приёмку.

python runtime_protocol.py declare <каталог протокола> <spec.json>
python runtime_protocol.py finish <каталог протокола> <observations.json>
python runtime_protocol.py compare <каталог до> <каталог после>
Файлы JSON результата можно передать через review_evidence.py.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import runtime_session
import snapshot_base

ENVIRONMENT = {'os', 'application_version', 'role', 'exampleVersion', 'camera', 'viewport',
               'inset', 'character_position', 'scene_generation'}
OUTCOMES = {'success', 'rule_rejection', 'setup_error', 'input_loss', 'dependency_failure'}


def pin(path):
    return snapshot_base.digest(path.read_bytes())


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Время требует часовой пояс')
    return result


def declare(directory, spec):
    required = {'runId', 'claim_id', 'revision', 'initial_state', 'environment', 'inputs', 'expected',
                'procedure', 'readiness_state', 'material_conditions', 'nonmaterial_reasons', 'preparation', 'restoration', 'restart_required'}
    if set(spec) != required or not re.fullmatch(r'claim-[0-9]+', spec['claim_id']):
        raise ValueError('Неполное объявление испытания или неверный claim-ID')
    if not re.fullmatch(r'[\w.-]+', spec['runId']):
        raise ValueError('Недопустимый runId')
    if (set(spec['revision']) != {'head_sha', 'diff_sha256', 'source_sha256'}
            or not all(re.fullmatch('[0-9a-f]{64}', spec['revision'][key]) for key in ('diff_sha256', 'source_sha256'))):
        raise ValueError('Нет проверяемой ревизии и SHA-256 патча')
    if not spec['revision']['head_sha']:
        raise ValueError('Нет HEAD проверяемой ревизии')
    if not isinstance(spec['environment'], dict) or set(spec['environment']) - ENVIRONMENT:
        raise ValueError('Используй разрешённые поля среды; полное окружение процесса запрещено')
    if not all(spec[key] for key in ('inputs', 'expected', 'procedure', 'readiness_state', 'initial_state', 'material_conditions')):
        raise ValueError('Пустые предусловия, входы, результат или процедура')
    if not isinstance(spec['nonmaterial_reasons'], dict) or any(not reason for reason in spec['nonmaterial_reasons'].values()):
        raise ValueError('Несущественные различия требуют обоснования')
    if type(spec['restart_required']) is not bool:
        raise ValueError('restart_required должен быть bool')
    if set(spec['material_conditions']) & set(spec['nonmaterial_reasons']):
        raise ValueError('Условие одновременно объявлено существенным и несущественным')
    directory.mkdir(parents=True, exist_ok=True)
    runtime_session.write(directory / 'declaration.json', {'version': 1, 'declaredAt': runtime_session.now(), 'spec': spec})


def session(path):
    path = runtime_session.plain(Path(path))
    launch_path, started_path, ended_path = (path / name for name in ('launch.json', 'started.json', 'ended.json'))
    launch = json.loads(launch_path.read_bytes())
    started = json.loads(started_path.read_bytes())
    ended = json.loads(ended_path.read_bytes())
    if (launch['sessionId'] != started['sessionId'] or launch['sessionId'] != ended['sessionId']
            or ended['process'] != started or ended['launch_sha256'] != pin(launch_path)
            or not started['pid'] or not started['instanceId'] or ended['launchError']):
        raise ValueError('Несогласованная квитанция runtime-сеанса')
    if instant(ended['endedAt']) < instant(started['startedAt']):
        raise ValueError('Неверный порядок времени сеанса')
    for name, sha in ended['diagnostics'].items():
        if name.endswith('/') and sha == 'directory':
            if not snapshot_base.safe_path(path / 'diagnostics', name).is_dir():
                raise ValueError('Диагностический каталог отсутствует')
            continue
        primary = snapshot_base.safe_path(path / 'diagnostics', name)
        if not primary.is_file() or pin(primary) != sha:
            raise ValueError('Диагностика сеанса изменена или отсутствует')
    return {'launch': launch, 'started': started, 'ended': ended,
            'sha256': {name: pin(path / name) for name in ('launch.json', 'started.json', 'ended.json')}}


def finish(directory, observations, publish=True):
    declaration_path = directory / 'declaration.json'
    declaration = json.loads(declaration_path.read_bytes())
    spec = declaration['spec']
    if set(observations) != {'revision', 'sessions', 'conditions', 'ready', 'primary', 'outcome'}:
        raise ValueError('Неполная запись наблюдений')
    if observations['revision'] != spec['revision']:
        raise ValueError('Наблюдения относятся к другой ревизии')
    if observations['conditions'] != spec['material_conditions']:
        raise ValueError('Существенные условия изменились; подготовь испытание заново')
    if observations['outcome'] not in OUTCOMES:
        raise ValueError('Неизвестный вид исхода испытания')
    sessions = [session(path) for path in observations['sessions']]
    if not sessions:
        raise ValueError('Нет квитанции запуска runtime')
    ids = [s['started']['instanceId'] for s in sessions]
    if len(ids) != len(set(ids)):
        raise ValueError('Повтор одного экземпляра не доказывает restart')
    for s in sessions:
        if s['launch'].get('revision') != {key: spec['revision'][key] for key in ('head_sha', 'source_sha256')}:
            raise ValueError('Фактические исходники сеанса отличаются от объявленной ревизии')
        if instant(s['started']['startedAt']) < instant(declaration['declaredAt']):
            raise ValueError('Условия объявлены после запуска испытания')
        if s['ended']['source_changes']:
            raise ValueError('Runtime изменил исходники; сначала проверь Scope')
    if spec['restart_required']:
        if len(sessions) < 2:
            raise ValueError('Нет доказанной границы restart')
        for first, second in zip(sessions, sessions[1:]):
            if instant(first['ended']['endedAt']) > instant(second['started']['startedAt']):
                raise ValueError('Первый процесс не завершён до restart')
    by_session = {s['started']['sessionId']: s for s in sessions}
    ready = observations['ready']
    if set(ready) != set(by_session):
        raise ValueError('Нужна наблюдаемая готовность каждого сеанса после подготовки и восстановления')
    for sid, item in ready.items():
        if set(item) != {'path', 'sha256', 'expected', 'conditions_rechecked'} or not item['conditions_rechecked']:
            raise ValueError('Не подтверждена повторная проверка условий')
        path = runtime_session.plain(Path(item['path']))
        if pin(path) != item['sha256']:
            raise ValueError('Файл готовности изменён')
        data = json.loads(path.read_bytes())
        if (data.get('sessionId') != sid or item['expected'] != spec['readiness_state'] or data.get('state') != spec['readiness_state']
                or not instant(by_session[sid]['started']['startedAt']) <= instant(data['observedAt']) <= instant(by_session[sid]['ended']['endedAt'])):
            raise ValueError('Среда не готова или готовность относится к другому сеансу')
    if not observations['primary']:
        raise ValueError('Нет первичных файлов испытания')
    if {item.get('sessionId') for item in observations['primary']} != set(by_session):
        raise ValueError('Нужны первичные файлы каждого сеанса, включая состояние после restart')
    for item in observations['primary']:
        if set(item) != {'path', 'sha256', 'sessionId', 'observedAt'} or item['sessionId'] not in by_session:
            raise ValueError('Файл не связан с runtime-сеансом')
        path = runtime_session.plain(Path(item['path']))
        s = by_session[item['sessionId']]
        if not path.is_file() or pin(path) != item['sha256']:
            raise ValueError('Первичный файл отсутствует или подменён')
        if not instant(s['started']['startedAt']) <= instant(item['observedAt']) <= instant(s['ended']['endedAt']):
            raise ValueError('Первичный файл вне границ сеанса')
    result = {'version': 1, 'declaration_sha256': pin(declaration_path), 'spec': spec,
              'observations': observations, 'sessions': sessions, 'recordedAt': runtime_session.now(),
              'acceptance': 'requires_review'}
    if publish:
        runtime_session.write(directory / 'result.json', result)
    return result


def compare(before, after):
    specifications = []
    for directory in (before, after):
        saved = json.loads((directory / 'result.json').read_bytes())
        current = finish(directory, saved['observations'], publish=False)
        if any(saved[key] != current[key] for key in ('declaration_sha256', 'spec', 'sessions')):
            raise ValueError('Протокол или квитанции сеансов изменились после finish')
        specifications.append(saved['spec'])
    a, b = specifications
    changed = [key for key in ('claim_id', 'material_conditions', 'inputs', 'procedure', 'readiness_state', 'expected', 'initial_state') if a[key] != b[key]]
    for key in a['environment'].keys() | b['environment'].keys():
        if a['environment'].get(key) != b['environment'].get(key):
            if key not in a['nonmaterial_reasons'] or key not in b['nonmaterial_reasons']:
                changed.append('environment.' + key)
    return {'comparable': not changed, 'differences': changed}


if __name__ == '__main__':
    try:
        mode, directory, source = sys.argv[1:]
        directory = Path(directory).resolve()
        if mode == 'declare':
            declare(directory, json.loads(Path(source).read_bytes()))
        elif mode == 'finish':
            print(json.dumps(finish(directory, json.loads(Path(source).read_bytes())), ensure_ascii=False))
        elif mode == 'compare':
            result = compare(directory, Path(source))
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(int(not result['comparable']))
        else:
            raise ValueError('Неизвестная команда')
    except (ValueError, OSError, KeyError) as error:
        print(str(error))
        sys.exit(1)
