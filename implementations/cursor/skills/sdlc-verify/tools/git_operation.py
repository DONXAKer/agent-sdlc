"""Допуск Git-операции и программная проверка её результата. Сам Git не изменяет.

python git_operation.py prepare|postcheck <корень> <slug> <N> <K>
До prepare создай git-operation.json: authorization (ссылка на явное решение человека),
deferred ({claim-ID: commit|parents|ancestry|paths}), expected ({parents: [SHA], ancestry: [SHA], paths: [путь]}).
В intent соответствующая строка содержит [git-result:тип]. Обычные и ручные проверки не откладываются.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import snapshot_base
import review_evidence
from sdlc_common import claims_in, enabled_gates, git_out, git_run, is_junk_path, files_to_touch

KINDS = {'commit', 'parents', 'ancestry', 'paths'}


def json_file(path):
    return json.loads(path.read_bytes())


def tree_record(root, mode, oid):
    if mode == '160000':
        return {'oid': oid, 'mode': int(mode, 8)}
    if mode not in ('100644', '100755', '120000'):
        raise ValueError('Недопустимый режим объекта Git')
    rc, content, error = git_run(['cat-file', 'blob', oid], root)
    if rc:
        raise ValueError('Не удалось прочитать blob кандидата: ' + error)
    return {'sha256': snapshot_base.digest(content.encode('utf-8', errors='surrogateescape')), 'mode': int(mode, 8)}


def indexed_tree(root):
    result = {}
    rc, output, error = git_run(['ls-files', '--stage', '-z'], root)
    if rc:
        raise ValueError('Не удалось прочитать index: ' + error)
    for entry in output.split('\0'):
        if not entry:
            continue
        info, name = entry.split('\t', 1)
        mode, oid, stage = info.split()
        if stage != '0':
            raise ValueError('Index содержит незавершённое слияние')
        if not name.startswith('.sdlc/') and not is_junk_path(name):
            result[name] = tree_record(root, mode, oid)
    return result


def committed_tree(root, head):
    tree = {}
    rc, output, error = git_run(['ls-tree', '-r', '-z', '--full-tree', head], root)
    if rc:
        raise ValueError('Не удалось прочитать дерево результата: ' + error)
    for entry in output.split('\0'):
        if not entry:
            continue
        info, name = entry.split('\t', 1)
        mode, kind, oid = info.split()
        if name.startswith('.sdlc/') or is_junk_path(name):
            continue
        if kind not in ('blob', 'commit'):
            raise ValueError('Недопустимый объект результата Git-операции')
        tree[name] = tree_record(root, mode, oid)
    return tree


def admission_errors(config, intent, review, gates, report):
    errors = []
    if not isinstance(config.get('authorization'), str) or not config['authorization'].strip():
        errors.append('Git-операция требует отдельное явное разрешение человека')
    deferred = config.get('deferred', {})
    if not isinstance(deferred, dict) or not deferred or not set(deferred.values()) <= KINDS:
        return errors + ['Отложи только commit, parents, ancestry или paths']
    if not set(deferred) <= claims_in(intent):
        errors.append('Неизвестные отложенные claim-ID')
    for cid, kind in deferred.items():
        row = next((line for line in intent.splitlines() if re.match(rf'^\|\s*{re.escape(cid)}\b', line)), '')
        if '[git-result:' + kind + ']' not in row or '[manual]' in row:
            errors.append(cid + ': отложенный критерий не объявлен результатом Git-операции')
    results = {item['id']: item['status'] for item in review.get('claims', [])}
    if set(results) != claims_in(intent):
        errors.append('Независимое ревью не покрывает весь intent')
    for cid, status in results.items():
        if status != ('uncertain' if cid in deferred else 'passed'):
            errors.append(cid + ': функциональная или ручная проверка не завершена')
    if any(review.get(field) for field in ('findings', 'scope', 'invariants', 'regressions')):
        errors.append('Независимое ревью содержит блокирующие находки')
    for name, _, _ in enabled_gates(gates):
        row = next((line for line in report.splitlines() if re.match(rf'^\|\s*{re.escape(name)}\s*\|', line)), '')
        if '✅' not in row or any(glyph in row for glyph in ('❌', '⚠', '⏭')):
            errors.append(name + ': проверка кандидата не завершена')
    if not re.search(r'\*\*passed:\*\*\s*false\b', report):
        errors.append('До postcheck окончательный passed должен оставаться false')
    expected = config.get('expected', {})
    for kind in set(deferred.values()) - {'commit'}:
        values = expected.get(kind)
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            errors.append(kind + ': нет ожидаемого результата')
    return errors


def prepare(root, d, n, k):
    spec_flow = importlib.util.spec_from_file_location('operation_flow', Path(__file__).with_name('flow-verdict.py'))
    flow = importlib.util.module_from_spec(spec_flow)
    spec_flow.loader.exec_module(flow)
    if flow.check_flow(root, d.name, 'verify'):
        raise ValueError('Кандидат не прошёл механическую проверку флоу')
    config = json_file(d / 'git-operation.json')
    report = d / f'verification-report-{n}-attempt-{k}.md'
    review_path = d / f'chunk-{n}-attempt-{k}-review.md'
    spec = importlib.util.spec_from_file_location('operation_review', Path(__file__).with_name('review-validate.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    review, error = module.extract_json(review_path.read_text(encoding='utf-8'))
    if error:
        raise ValueError(error)
    intent = (d / 'intent.md').read_text(encoding='utf-8')
    patch = d / f'chunk-{n}-attempt-{k}-diff.patch'
    spec_evidence = importlib.util.spec_from_file_location('operation_attempt', Path(__file__).with_name('attempt-evidence.py'))
    evidence = importlib.util.module_from_spec(spec_evidence)
    spec_evidence.loader.exec_module(evidence)
    base, snapshot = snapshot_base.load(root, d)
    current = (evidence.regenerate_diff(root, base['id']) if snapshot is None else
               snapshot_base.snapshot_diff(root, snapshot))
    if snapshot_base.digest(current.encode('utf-8', errors='surrogateescape')) != snapshot_base.digest(patch.read_bytes()):
        raise ValueError('Дерево кандидата отличается от проверенного патча')
    from sdlc_common import diffstat
    errors, _ = module.validate(review, claims_in(intent), set(diffstat(patch.read_text(encoding='utf-8'))['paths']),
                                review_evidence.verify(root, d, n, k), module.artifact_aliases(d.name))
    errors += admission_errors(config, intent, review, (d.parent / 'gates.md').read_text(encoding='utf-8'),
                               report.read_text(encoding='utf-8'))
    if errors:
        raise ValueError('; '.join(errors))
    planned = set(files_to_touch((d / 'plan.md').read_text(encoding='utf-8')))
    staged = set((git_out(['diff', '--cached', '--name-only', 'HEAD'], root) or '').splitlines())
    untracked = set((git_out(['ls-files', '--others', '--exclude-standard'], root) or '').splitlines())
    if staged - planned or untracked & planned or git_run(['diff', '--quiet', '--exit-code', '--', *sorted(planned)], root)[0]:
        raise ValueError('До допуска подготовь index: только область плана, без незаписанных правок кандидата')
    rec = {'version': 1, 'slug': d.name, 'chunk': int(n), 'attempt': int(k),
           'head_sha': (git_out(['rev-parse', 'HEAD'], root) or '').strip(),
           'config_sha256': snapshot_base.digest((d / 'git-operation.json').read_bytes()),
           'inputs': {path.name: snapshot_base.digest(path.read_bytes()) for path in
                      (report, review_path, patch, d / 'intent.md', d / 'plan.md', d.parent / 'gates.md')},
           'config': config, 'status': 'admitted', 'passed': False}
    rec['candidate_tree'] = indexed_tree(root)
    target = d / 'git-operation-admission.json'
    if target.exists():
        if json_file(target) != rec:
            raise ValueError('Входы существующего Git-допуска изменились; не используй прежнее разрешение для нового кандидата')
        return rec
    with target.open('x', encoding='utf-8') as output:
        json.dump(rec, output, ensure_ascii=False, indent=2)
    return rec


def postcheck(root, d):
    admission = d / 'git-operation-admission.json'
    rec = json_file(admission)
    if rec['config_sha256'] != snapshot_base.digest((d / 'git-operation.json').read_bytes()):
        raise ValueError('Условия операции изменены после допуска')
    for name, digest in rec['inputs'].items():
        path = d.parent / name if name == 'gates.md' else d / name
        if snapshot_base.digest(path.read_bytes()) != digest:
            raise ValueError('Вход допуска изменён: ' + name)
    head = (git_out(['rev-parse', 'HEAD'], root) or '').strip()
    parents = (git_out(['show', '-s', '--format=%P', head], root) or '').split()
    paths = (git_out(['diff-tree', '--root', '--first-parent', '-m', '--no-commit-id', '--name-only', '-r', head], root) or '').splitlines()
    expected = rec['config'].get('expected', {})
    checks = {'commit': bool(head) and head != rec['head_sha'],
              'candidate_tree': committed_tree(root, head) == rec['candidate_tree'],
              'parents': parents == expected.get('parents'),
              'paths': sorted(paths) == sorted(expected.get('paths', [])),
              'ancestry': all(git_run(['merge-base', '--is-ancestor', sha, head], root)[0] == 0
                              for sha in expected.get('ancestry', []))}
    passed = checks['commit'] and checks['candidate_tree'] and all(checks[kind] for kind in rec['config']['deferred'].values())
    result = {'version': 1, 'slug': d.name, 'head_sha': head, 'parents': parents, 'paths': paths,
              'admission_sha256': snapshot_base.digest(admission.read_bytes()), 'checks': checks, 'passed': passed}
    target = d / ('git-operation-postcheck-' + head + '.json')
    with target.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


def completion_errors(root, d):
    if not (d / 'git-operation.json').exists():
        return []
    try:
        admission = d / 'git-operation-admission.json'
        original = json_file(admission)
        if original.get('config_sha256') != snapshot_base.digest((d / 'git-operation.json').read_bytes()):
            return ['Условия Git-операции изменены после допуска']
        head = (git_out(['rev-parse', 'HEAD'], root) or '').strip()
        rec = json_file(d / ('git-operation-postcheck-' + head + '.json'))
        if (rec.get('version') != 1 or rec.get('slug') != d.name or rec.get('head_sha') != head
                or rec.get('admission_sha256') != snapshot_base.digest(admission.read_bytes())
                or rec.get('passed') is not True):
            return ['Git-postcheck не подтверждён; задача остаётся открытой']
        if committed_tree(root, head) != original.get('candidate_tree'):
            return ['Дерево Git-результата отличается от принятого кандидата']
        reports = sorted(d.glob('verification-report-*-attempt-*.md'),
                         key=lambda p: tuple(map(int, re.findall(r'\d+', p.name))))
        if not reports:
            return ['Нет окончательного отчёта после Git-postcheck']
        final = reports[-1]
        n, k = map(int, re.findall(r'\d+', final.name))
        if (n, k) <= (original['chunk'], original['attempt']):
            return ['Нет нового окончательного ревью после Git-postcheck']
        if not re.search(r'\*\*passed:\*\*\s*true\b', final.read_text(encoding='utf-8')):
            return ['Окончательная приёмка после Git-postcheck не завершена']
        spec = importlib.util.spec_from_file_location('final_operation_review', Path(__file__).with_name('review-validate.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        review, error = module.extract_json((d / f'chunk-{n}-attempt-{k}-review.md').read_text(encoding='utf-8'))
        if error or not isinstance(review, dict):
            return ['Нет корректного окончательного независимого ревью']
        expected_claims = claims_in((d / 'intent.md').read_text(encoding='utf-8'))
        problems, _ = module.validate(review, expected_claims, set(rec.get('paths', [])),
                                      review_evidence.verify(root, d, n, k), module.artifact_aliases(d.name))
        if problems:
            return ['Окончательное независимое ревью не прошло валидацию']
        if ({item['id'] for item in review.get('claims', [])} != expected_claims
                or any(item.get('status') != 'passed' for item in review.get('claims', []))
                or any(review.get(field) for field in ('findings', 'scope', 'invariants', 'regressions'))):
            return ['Окончательное ревью оставило непроверенные пункты или находки']
    except (OSError, ValueError, KeyError, TypeError):
        return ['Нет успешного postcheck Git-операции; задача остаётся открытой']
    return []


if __name__ == '__main__':
    try:
        mode, root, slug, *numbers = sys.argv[1:]
        root = Path(root).resolve()
        d = root / '.sdlc' / slug
        if mode == 'prepare':
            result = prepare(root, d, *numbers)
        elif mode == 'postcheck':
            result = postcheck(root, d)
        else:
            raise ValueError('Режим должен быть prepare или postcheck')
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get('status') == 'admitted' or result.get('passed') else 1)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
