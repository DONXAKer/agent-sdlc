"""Положительные и отрицательные контроли ранней подготовки, доказательств и завершения."""

import contextlib
import copy
import importlib.util
import io
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test import test_context_evidence as fixtures
from test.test_issue_regressions import flow
import chunk_preconditions
import snapshot_base
import review_evidence
import git_operation
from sdlc_common import diffstat, preparation_checks

spec = importlib.util.spec_from_file_location('primary_review', fixtures.fixtures.TOOLS / 'review-validate.py')
review_validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_validator)


class OpenIssueTests(fixtures.ContextEvidenceTests):
    def test_prepare_rejects_missing_mapping_and_invalid_forms_in_both_modes(self):
        for kind in ('git', 'snapshot'):
            for broken in ('mapping', 'unknown', 'list', 'missing_verdict', 'negative_verdict', 'abbreviated'):
                with self.subTest(kind=kind, broken=broken):
                    self.setUp()
                    if kind == 'git':
                        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
                    else:
                        self.snapshot()
                    if broken == 'mapping':
                        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('claim-1', 'claim-2'), encoding='utf-8')
                    elif broken == 'unknown':
                        self.plan.write_text(self.plan.read_text(encoding='utf-8') + '\nclaim-99\n', encoding='utf-8')
                    elif broken == 'list':
                        (self.d / 'intent.md').write_text('**Контур:** мелкий\n- claim-1: Ответ, проверить ответ.\n', encoding='utf-8')
                    elif broken == 'abbreviated':
                        (self.d / 'intent.md').write_text('**Контур:** мелкий\n| claim-1 | Ответ | Проверить |\n| claim-2 | Второй | Проверить |\n', encoding='utf-8')
                        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('claim-1.', 'claim-1/2.'), encoding='utf-8')
                    else:
                        (self.d / 'readiness.md').write_text('**Вердикт прогона 1:** готова\n' +
                            ('**Вердикт прогона 2:** не готова\n' if broken == 'negative_verdict' else ''), encoding='utf-8')
                    result = self.prepare()
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertFalse(chunk_preconditions.receipt_path(self.d, 1).exists())

    def test_prepared_documents_cannot_change_and_full_plan_allows_partial_steps(self):
        (self.d / 'intent.md').write_text('**Контур:** мелкий\n| claim-1 | Ответ | Проверить |\n| claim-2 | Другой | Проверить |\n', encoding='utf-8')
        self.plan.write_text(self.plan.read_text(encoding='utf-8') + '\nШаг 1: claim-1.\nШаг 2: claim-2.\n', encoding='utf-8')
        self.snapshot()
        prepared = self.prepare()
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        before = chunk_preconditions.receipt_path(self.d, 1).read_bytes()
        for name in ('intent.md', 'readiness.md'):
            path = self.d / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            self.assertTrue(chunk_preconditions.errors(self.root, self.d, 1))
            path.write_bytes(original)
        self.assertEqual(chunk_preconditions.receipt_path(self.d, 1).read_bytes(), before)

    def test_chunk_size_excludes_foreign_baseline_and_measures_next_chunk(self):
        for kind in ('git', 'snapshot'):
            with self.subTest(kind=kind):
                self.setUp()
                self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', kind).replace(
                    '| app.py | правка |', '| app.py | правка |\n| second.py | правка |'), encoding='utf-8')
                for n in range(26):
                    (self.root / f'foreign-{n}.txt').write_text('Чужая строка\n' * 20, encoding='utf-8')
                if kind == 'snapshot':
                    self.snapshot()
                result = self.prepare()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
                self.write()
                rec = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_bytes())
                self.assertEqual(rec['chunk_diff']['diffstat']['files'], 1)
                self.assertEqual(rec['chunk_diff']['diffstat']['added'], 1)
                self.assertEqual(rec['chunk_diff']['diffstat']['deleted'], 1)
                self.assertEqual(self.prepare(2).returncode, 0)
                first = (self.d / 'chunk-1-attempt-1-chunk.patch').read_bytes()
                (self.root / 'app.py').write_bytes(b'answer = 1\r\n')
                (self.root / 'second.py').write_bytes(b'answer = 3\r\n')
                result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 2, 1)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rec = json.loads((self.d / 'chunk-2-attempt-1-evidence.json').read_bytes())
                self.assertEqual(rec['chunk_diff']['diffstat']['files'], 2)
                self.assertEqual((self.d / 'chunk-1-attempt-1-chunk.patch').read_bytes(), first)
                (self.root / 'foreign-0.txt').write_bytes(b'changed')
                self.write(2)
                patch = self.d / 'chunk-1-attempt-2-diff.patch'
                self.assertEqual(self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch).returncode, 1)

    def test_real_chunk_overflow_and_corrupt_patch_are_rejected(self):
        self.snapshot()
        self.assertEqual(self.prepare().returncode, 0)
        (self.root / 'app.py').write_text('answer = 2\n' * 401, encoding='utf-8')
        self.write()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            flow.main(self.root, 'T-1', 'verify')
        self.assertIn('**размер chunk-1 в ориентире или объяснён**', output.getvalue())
        patch = self.d / 'chunk-1-attempt-1-chunk.patch'
        patch.write_bytes(patch.read_bytes() + b'changed')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            flow.main(self.root, 'T-1', 'verify')
        self.assertIn('Патч chunk изменён', output.getvalue())

    def test_luau_pending_literals_comments_and_real_protections(self):
        cases = [
            ('qa_check.luau', ['local result = {nativeInput="pending"}', 'assert(result.nativeInput == "pending")'], 0),
            ('qa_check.luau', ['-- pending', 'local text = [=[pending]=]', 'assert(true)'], 0),
            ('qa_spec.rb', ['pending "ожидание"'], 1),
            ('qa_spec.rb', ['xit "пример" do'], 1),
        ]
        patch = self.d / 'probe.patch'
        for name, lines, expected in cases:
            patch.write_text(f'diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n' +
                             ''.join('+' + line + '\n' for line in lines), encoding='utf-8')
            result = self.tool('gates/anti_bypass.py', patch, '--test-path', name)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        patch.write_text('diff --git a/qa_check.luau b/qa_check.luau\n--- a/qa_check.luau\n+++ b/qa_check.luau\n@@ -1,2 +1 @@\n-assert(true)\n assert(true)\n', encoding='utf-8')
        self.assertEqual(self.tool('gates/anti_bypass.py', patch, '--test-path', '*.luau').returncode, 1)
        patch.write_text('diff --git a/qa_check.luau b/qa_check.luau\ndeleted file mode 100644\n--- a/qa_check.luau\n+++ /dev/null\n@@ -1 +0,0 @@\n-assert(true)\n', encoding='utf-8')
        self.assertEqual(self.tool('gates/anti_bypass.py', patch, '--test-path', '*.luau').returncode, 1)

    def primary(self):
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        path = self.d / 'result.json'
        path.write_bytes(b'{"value": 2}\r\n')
        return {'files': [{'path': '.sdlc/T-1/result.json', 'claims': ['claim-1'], 'kind': 'observation',
                           'source': 'Снимок проверяемого процесса', 'sha256': snapshot_base.digest(path.read_bytes()),
                           'revision': review_evidence.candidate(self.root, self.d, 1, 1)}]}

    def test_primary_package_roundtrip_and_negative_controls(self):
        manifest = self.primary()
        name = manifest['files'][0]['path']
        for mutation in ('hash', 'missing', 'revision', 'escape', 'narrative'):
            changed = copy.deepcopy(manifest)
            item = changed['files'][0]
            if mutation == 'hash':
                item['sha256'] = '0' * 64
            elif mutation == 'missing':
                item['path'] = 'missing.json'
            elif mutation == 'revision':
                item['revision']['head_sha'] = '0' * 40
            elif mutation == 'escape':
                item['path'] = '../outside.json'
            else:
                item['kind'] = 'narrative'
            with self.assertRaises((ValueError, OSError)):
                review_evidence.prepare(self.root, self.d, 1, 1, changed)
        source = self.root / name
        original = source.read_bytes()
        disguised = copy.deepcopy(manifest)
        source.write_text(json.dumps({'schema_version': 'agent-sdlc/verify-review/v1', 'claims': [], 'findings': []}), encoding='utf-8')
        disguised['files'][0]['sha256'] = snapshot_base.digest(source.read_bytes())
        with self.assertRaises(ValueError):
            review_evidence.prepare(self.root, self.d, 1, 1, disguised)
        source.write_text(json.dumps({'messages': ['event started'], 'state': 'ready'}), encoding='utf-8')
        disguised['files'][0]['sha256'] = snapshot_base.digest(source.read_bytes())
        self.assertIn(name, review_evidence.checked(self.root, self.d, disguised,
                                                  review_evidence.candidate(self.root, self.d, 1, 1)))
        source.write_bytes(original)
        self.assertEqual(review_evidence.prepare(self.root, self.d, 1, 1, manifest), {name})
        self.assertEqual(review_evidence.verify(self.root, self.d, 1, 1), {name})
        path = self.root / name
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'\r\n', b'\n'))
        with self.assertRaises(ValueError):
            review_evidence.verify(self.root, self.d, 1, 1)
        path.write_bytes(raw)
        package, _ = review_evidence.paths(self.d, 1, 1)
        package.write_bytes(package.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            review_evidence.verify(self.root, self.d, 1, 1)

    def test_finding_requires_object_address_and_pinned_additional_proof(self):
        ref = lambda path: {'path': path, 'anchor': 'проверяемый символ'}
        review = {'claims': [{'id': 'claim-1', 'status': 'passed', 'evidence': [ref('app.py')]}],
                  'findings': [{'kind': 'uncovered_behavior', 'summary': 'Ошибка',
                                'evidence': [ref('app.py'), ref('.sdlc/T-1/result.json')]}]}
        validate = lambda value, pins=(): review_validator.validate(copy.deepcopy(value), {'claim-1'}, {'app.py'}, pins)[0]
        self.assertTrue(validate(review))
        self.assertEqual(validate(review, {'.sdlc/T-1/result.json'}), [])
        review['findings'][0]['evidence'] = [ref('.sdlc/T-1/result.json')]
        self.assertTrue(validate(review, {'.sdlc/T-1/result.json'}))
        review['findings'][0]['evidence'] = [ref('plan.md')]
        self.assertEqual(validate(review), [])
        review['findings'][0]['evidence'] = [ref('.sdlc/T-1/plan.md')]
        errors, _ = review_validator.validate(copy.deepcopy(review), {'claim-1'}, {'app.py'},
                                              artifact_paths=review_validator.artifact_aliases('T-1'))
        self.assertEqual(errors, [])

    def test_verify_receipts_bind_attempt_report_and_final_machine_result(self):
        report = self.d / 'verification-report-1-attempt-1.md'
        report.write_text('**passed:** true\n', encoding='utf-8')
        self.assertFalse(flow.confirmed_verify(self.d, report))
        flow.verify_receipt(self.d, report, 1)
        self.assertFalse(flow.confirmed_verify(self.d, report))
        second = self.d / 'verification-report-2-attempt-1.md'
        second.write_bytes(report.read_bytes())
        flow.verify_receipt(self.d, second, 0)
        self.assertTrue(flow.confirmed_verify(self.d, second))
        self.assertFalse(flow.confirmed_verify(self.d, report))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.do_metrics(self.root), 0)
        self.assertIn('Подтверждённый verify с первой попытки: 0/1', output.getvalue())
        self.assertIn('Подтверждённый успешный verify: 1/1', output.getvalue())
        second.write_bytes(second.read_bytes() + b' ')
        self.assertFalse(flow.confirmed_verify(self.d, second))
        second.write_bytes(report.read_bytes())
        receipt = self.d / 'verify-result-2-attempt-1-run-1.json'
        rec = json.loads(receipt.read_bytes())
        rec['attempt'] = 2
        receipt.write_text(json.dumps(rec), encoding='utf-8')
        self.assertFalse(flow.confirmed_verify(self.d, second))
        flow.verify_receipt(self.d, second, 0)
        self.assertTrue(flow.confirmed_verify(self.d, second))
        flow.verify_receipt(self.d, second, 1)
        self.assertFalse(flow.confirmed_verify(self.d, second))
        self.assertEqual(len(list(self.d.glob('verify-result-*.json'))), 4)

    def test_handoff_requires_complete_record_for_confirmed_uncovered_behavior(self):
        review = self.d / 'chunk-1-attempt-1-review.md'
        reference = {'path': 'app.py', 'anchor': 'answer'}
        review.write_text(json.dumps({'claims': [{'id': 'claim-1', 'status': 'passed', 'evidence': [reference]}],
            'findings': [{'kind': 'uncovered_behavior', 'summary': 'Неучтённое поведение', 'evidence': [reference]}]}), encoding='utf-8')
        (self.d / 'chunk-1-attempt-1-diff.patch').write_text('diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n', encoding='utf-8')
        self.assertTrue(flow.defect_record_errors(self.d, '- **Имя:** нет\n- **Действие:** н/п\n'))
        source = review.name + '#' + snapshot_base.digest('Неучтённое поведение'.encode('utf-8'))[:16]
        block = f'''- **Имя:** Неучтённое поведение
- **Источник дефекта:** {source}
- **Класс:** неверная граница
- **Повтор:** нет
- **Что проскочило:** Пропущенная ветка.
- **Какой гейт должен был поймать и почему не поймал:** Тесты — не покрывает класс
- **Действие:** проверка
- **Кто утвердил:** Оператор · явный ответ в текущем витке
- **Где реализовано:** app.py:answer
- **Чем закреплено:** test_answer.py:test_branch, FAIL до исправления, PASS после
'''
        self.assertEqual(flow.defect_record_errors(self.d, block), [])
        for field in ('Класс', 'Действие', 'Кто утвердил', 'Чем закреплено'):
            bad = '\n'.join(line for line in block.splitlines() if not line.startswith('- **' + field + ':'))
            self.assertTrue(flow.defect_record_errors(self.d, bad))
        review.unlink()
        (self.d / 'verification-report-1-attempt-1.md').write_text('**passed:** false\nОшибка формата отчёта.\n', encoding='utf-8')
        self.assertEqual(flow.defect_record_errors(self.d, '- **Имя:** нет\n'), [])

    def test_git_admission_defers_only_git_results_and_preserves_permissions(self):
        config = {'authorization': 'Явное решение пользователя о commit', 'deferred': {'claim-2': 'parents'},
                  'expected': {'parents': ['a' * 40]}}
        intent = '| claim-1 | Поведение | Проверить |\n| claim-2 [git-result:parents] | Родители | Проверить Git |\n'
        review = {'claims': [{'id': 'claim-1', 'status': 'passed'}, {'id': 'claim-2', 'status': 'uncertain'}]}
        gates = '| Тесты | да | этап 6 | `tests` |\n'
        report = '| Тесты | ✅ | Проверено |\n**passed:** false\n'
        self.assertEqual(git_operation.admission_errors(config, intent, review, gates, report), [])
        changed = copy.deepcopy(config)
        changed['authorization'] = ''
        self.assertTrue(git_operation.admission_errors(changed, intent, review, gates, report))
        for changed_intent in (intent.replace('[git-result:parents]', ''), intent.replace('[git-result:parents]', '[manual]')):
            self.assertTrue(git_operation.admission_errors(config, changed_intent, review, gates, report))
        review['claims'][0]['status'] = 'uncertain'
        self.assertTrue(git_operation.admission_errors(config, intent, review, gates, report))
        (self.d / 'git-operation.json').write_text('{}', encoding='utf-8')
        self.assertTrue(git_operation.completion_errors(self.root, self.d))

    def test_git_operation_checks_staged_candidate_and_actual_result(self):
        source = ROOT / 'test/flow-verdict-corpus/green/.sdlc'
        for path in (source / 'green').glob('*.md'):
            if not path.name.startswith('.'):
                shutil.copy(path, self.d / path.name)
        shutil.copy(source / 'gates.md', self.d.parent / 'gates.md')
        report = self.d / 'verification-report-1-attempt-1.md'
        body = report.read_text(encoding='utf-8').replace('**passed:** true', '**passed:** false')
        report.unlink()
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('1234567', self.base).replace('example.txt', 'app.py'), encoding='utf-8')
        intent = self.d / 'intent.md'
        intent.write_text(intent.read_text(encoding='utf-8').replace('| claim-3', '| claim-3 [git-result:parents]'), encoding='utf-8')
        (self.root / 'foreign.txt').write_bytes(b'foreign baseline\r\n')
        self.assertEqual(self.prepare().returncode, 0)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        report.write_text(body, encoding='utf-8')
        reference = {'path': 'app.py', 'anchor': 'answer'}
        review = {'claims': [{'id': f'claim-{n}', 'status': 'uncertain' if n == 3 else 'passed',
                             'evidence': [reference]} for n in (1, 2, 3)]}
        review_path = self.d / 'chunk-1-attempt-1-review.md'
        review_path.write_text(json.dumps(review), encoding='utf-8')
        config = {'authorization': 'Явное решение пользователя о commit', 'deferred': {'claim-3': 'parents'},
                  'expected': {'parents': [self.base]}}
        (self.d / 'git-operation.json').write_text(json.dumps(config), encoding='utf-8')
        self.git('add', 'app.py')
        (self.root / 'app.py').write_bytes(b'answer = 3\r\n')
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                git_operation.prepare(self.root, self.d, 1, 1)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            admitted = git_operation.prepare(self.root, self.d, 1, 1)
        self.assertFalse(admitted['passed'], output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(git_operation.prepare(self.root, self.d, 1, 1), admitted)
        self.assertNotIn('foreign.txt', admitted['candidate_tree'])
        self.assertFalse(git_operation.postcheck(self.root, self.d)['passed'])
        self.git('commit', '-qm', 'Проверяемый результат')
        result = git_operation.postcheck(self.root, self.d)
        self.assertTrue(result['passed'], result)
        self.assertTrue(git_operation.completion_errors(self.root, self.d))
        final = self.d / 'verification-report-1-attempt-2.md'
        final.write_text('**passed:** false\n', encoding='utf-8')
        self.assertTrue(git_operation.completion_errors(self.root, self.d))
        final.write_text('**passed:** true\n', encoding='utf-8')
        final_review = self.d / 'chunk-1-attempt-2-review.md'
        final_review.write_text(json.dumps({'claims': [{'id': f'claim-{n}', 'status': 'passed'} for n in (1, 2, 3)]}), encoding='utf-8')
        self.assertTrue(git_operation.completion_errors(self.root, self.d))
        for item in review['claims']:
            item['status'] = 'passed'
        final_review.write_text(json.dumps(review), encoding='utf-8')
        self.assertEqual(git_operation.completion_errors(self.root, self.d), [])
        (self.root / 'app.py').write_bytes(b'answer = 99\r\n')
        self.git('add', 'app.py')
        self.git('commit', '-qm', 'Другой результат')
        result = git_operation.postcheck(self.root, self.d)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['candidate_tree'])

    def test_validated_review_history_survives_new_claims_and_rejects_tampering(self):
        manifest = self.primary()
        review_evidence.prepare(self.root, self.d, 1, 1, manifest)
        path = self.d / 'chunk-1-attempt-1-review.md'
        refs = [{'path': 'app.py', 'anchor': 'answer'}, {'path': '.sdlc/T-1/result.json', 'anchor': 'value'}]
        path.write_text(json.dumps({'claims': [{'id': 'claim-1', 'status': 'passed', 'evidence': refs}],
            'findings': [{'kind': 'uncovered_behavior', 'summary': 'Историческая находка', 'evidence': refs}]}), encoding='utf-8')
        result = self.tool('review-validate.py', self.root, 'T-1', 1, 1, path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intent = self.d / 'intent.md'
        intent.write_text(intent.read_text(encoding='utf-8') + '| claim-2 | Новый | Проверить |\n', encoding='utf-8')
        self.assertIn('Нет обязательных записей', ';'.join(flow.defect_record_errors(self.d, '- **Имя:** нет\n')))
        original = path.read_bytes()
        path.write_bytes(b'{"findings":null}')
        self.assertIn('История подтверждённого ревью повреждена', ';'.join(flow.defect_record_errors(self.d, '- **Имя:** нет\n')))
        path.write_bytes(original)
        path.unlink()
        self.assertIn('История подтверждённого ревью повреждена', ';'.join(flow.defect_record_errors(self.d, '- **Имя:** нет\n')))

    def test_metrics_do_not_infer_first_attempt_from_missing_history(self):
        report = self.d / 'verification-report-2-attempt-1.md'
        report.write_text('**passed:** true\n', encoding='utf-8')
        flow.verify_receipt(self.d, report, 0)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            flow.do_metrics(self.root)
        self.assertIn('Подтверждённый verify с первой попытки: 0/1', output.getvalue())
        first = self.d / 'verification-report-1-attempt-1.md'
        first.write_bytes(report.read_bytes())
        flow.verify_receipt(self.d, first, 0)
        flow.verify_receipt(self.d, first, 0)
        receipt = self.d / 'verify-result-1-attempt-1-run-1.json'
        record = json.loads(receipt.read_bytes())
        record['slug'] = 'OTHER'
        receipt.write_text(json.dumps(record), encoding='utf-8')
        self.assertTrue(flow.confirmed_verify(self.d, first))
        self.assertFalse(flow.first_verify_succeeded(self.d, first))

    def test_git_prepare_supports_unchanged_gitlink(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        (self.root / 'vendor/module').mkdir(parents=True)
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.base + ',vendor/module')
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        rec = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_bytes())
        self.assertEqual(rec['chunk_diff']['diffstat']['files'], 1)

    def test_corpus_receipt_pin_survives_git_text_normalization(self):
        d = ROOT / 'test/flow-verdict-corpus/snapshot-valid/.sdlc/snapshot-valid'
        raw = (d / '.chunk-1-preconditions.json').read_bytes()
        saved = json.loads((d / 'chunk-1-attempt-1-evidence.json').read_bytes())
        self.assertEqual(saved['preconditions']['receipt_sha256'], snapshot_base.digest(raw))
        canonical = raw.replace(b'\r\n', b'\n')
        self.assertEqual(saved['preconditions']['receipt_sha256'], snapshot_base.digest(canonical))


if __name__ == '__main__':
    unittest.main()
