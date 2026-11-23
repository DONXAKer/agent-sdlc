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
import gate_evidence
import snapshot_base
import review_evidence
import git_operation
from sdlc_common import diffstat, preparation_checks, readiness_ok

spec = importlib.util.spec_from_file_location('primary_review', fixtures.fixtures.TOOLS / 'review-validate.py')
review_validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_validator)


class OpenIssueTests(fixtures.ContextEvidenceTests):
    def test_auxiliary_step_connects_existing_claim_without_closing_it(self):
        intent = (self.d / 'intent.md').read_text(encoding='utf-8')
        readiness = (self.d / 'readiness.md').read_text(encoding='utf-8')
        plan = ('### Шаг 1 — восстановить сборку\n- файл: build.toml\n- закрывает: нет\n'
                '- обеспечивает проверку: claim-1\n- проверка: компиляция тестов\n'
                '### Шаг 2 — проверить экспорт\n- файл: tests/test_export.py\n'
                '- закрывает: claim-1\n- зависит от: 1\n')
        verdict = lambda text: {name: ok for ok, name, _ in preparation_checks(intent, text, readiness, True)}
        self.assertTrue(all(verdict(plan).values()))
        self.assertTrue(all(verdict(plan.replace('закрывает: нет', 'закрывает: нет  ')).values()))
        self.assertFalse(verdict(plan.replace('проверку: claim-1', 'проверку: claim-99'))['claims intent == plan'])
        step = 'вспомогательные шаги обеспечивают проверку'
        self.assertFalse(verdict(plan.split('### Шаг 2')[0])[step])
        self.assertFalse(verdict(plan.replace('закрывает: нет', 'закрывает: claim-1'))[step])
        deferred = plan.split('### Шаг 2')[0] + '- **Уходит следующим chunk\'ам:** claim-1, chunk 2\n'
        self.assertTrue(verdict(deferred)[step])

    def test_prepare_receipt_versions_preserve_historical_files(self):
        self.snapshot()
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = chunk_preconditions.receipt_path(self.d, 1)
        record = json.loads(path.read_bytes())
        self.assertEqual(record['version'], 2)
        self.assertEqual(record['contour'], 'мелкий')
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1), [])
        record['version'] = 1
        path.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1), [])
        del record['contour']
        old = json.dumps(record).encode('utf-8')
        path.write_bytes(old)
        self.assertIn('v1 без contour несовместима', chunk_preconditions.errors(self.root, self.d, 1)[0])
        self.assertEqual(path.read_bytes(), old)

    def test_snapshot_prepare_reports_two_comparisons(self):
        (self.root / 'app.py').write_bytes(b'answer = 7\r\n')
        self.snapshot()
        late = self.root / 'new.py'
        late.write_text('value = 1\n', encoding='utf-8')
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('от Git HEAD: 2', result.stdout)
        self.assertIn('относительно снимка: 1 путей: new.py', result.stdout)
        self.assertNotIn('чужих', result.stdout)
        record = json.loads(chunk_preconditions.receipt_path(self.d, 1).read_bytes())
        self.assertEqual(record['base']['kind'], 'snapshot')
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1), [])

    def test_prepare_refuses_invalid_defect_class_before_recording_receipt(self):
        self.snapshot()
        gates = self.d.parent / 'gates.md'
        with gates.open('a', encoding='utf-8') as output:
            output.write('\n## Классы дефектов проекта\n\n- класс без даты\n')
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Классы дефектов проекта', result.stdout)
        self.assertIn('- класс без даты', result.stdout)
        self.assertFalse(chunk_preconditions.receipt_path(self.d, 1).exists())

    def test_retry_blocks_verify_route_until_same_report_is_corrected(self):
        source = ROOT / 'test/flow-verdict-corpus/green/.sdlc'
        archive = self.root / '.sdlc/green'
        shutil.copytree(source / 'green', archive)
        shutil.copyfile(source / 'gates.md', archive.parent / 'gates.md')
        report = archive / 'verification-report-1-attempt-1.md'
        text = report.read_text(encoding='utf-8').replace('**passed:** true', '**passed:** false').replace('**action:** continue', '**action:** retry')
        for value in ('', 'н/п / н/п', 'н/п/н/п', 'TODO', '‹что делать›'):
            report.write_text(text + f'\n- **retry_instruction:** {value}\n', encoding='utf-8')
            result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('Заполни retry_instruction', result.stdout)
            result = self.tool('flow-verdict.py', '--print', 'action', self.root, 'green')
            self.assertEqual(result.returncode, 1)
            self.assertIn(report.name, result.stderr)
        report.write_text(text + '\n- **retry_instruction:** Сохрани сортировку экспортированных строк.\n', encoding='utf-8')
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.tool('flow-verdict.py', '--print', 'action', self.root, 'green')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'retry'))
        self.assertEqual(list(archive.glob('verification-report-*.md')), [report])

    def test_metrics_separate_accepted_aborted_and_missing_handoff(self):
        for slug in ('accepted', 'aborted', 'missing', 'unconfirmed'):
            d = self.d.parent / slug
            d.mkdir()
            (d / 'intent.md').write_text('Задача\n', encoding='utf-8')
            report = d / 'verification-report-1-attempt-1.md'
            report.write_text('- **passed:** true\n', encoding='utf-8')
            if slug != 'unconfirmed':
                flow.verify_receipt(d, report, 0)
            if slug != 'missing':
                verdict = 'aborted' if slug == 'aborted' else 'passed'
                handoff = ('## Виток 1\n- **Приёмка:** принял Оператор · 2026-08-01\n\n'
                           '## Состояние (машиночитаемое)\n\n```yaml\n'
                           f'chunk: 1\nattempts: 1\nverdict: {verdict}\n```\n')
                (d / 'handoff.md').write_text(handoff, encoding='utf-8')
        result = self.tool('flow-verdict.py', '--metrics', self.root)
        # T-1 из общей фикстуры тоже ещё без handoff.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('успешно завершено: 1 · незавершённых: 4', result.stdout)
        self.assertIn('Незавершённых с handoff: 2 · без handoff: 2', result.stdout)

    def test_terminal_escalation_transfers_same_slug_before_retro(self):
        skill = (ROOT / 'implementations/codex/skills/sdlc-run/SKILL.md').read_text(encoding='utf-8')
        self.assertRegex(skill, r'`passed: false` и `action: escalate`[^\n]*`\$sdlc-handoff` для этого slug')
        self.assertIn('Ретроспективу запускай после сохранения handoff', skill)
        self.assertIn('При `action: retry` продолжай текущий chunk', skill)

    def test_handoff_checks_review_pass_for_each_historical_attempt(self):
        source = ROOT / 'test/flow-verdict-corpus/handoff-green/.sdlc'
        slug = 'handoff-green'
        d = self.root / '.sdlc' / slug
        shutil.copytree(source / slug, d)
        shutil.copyfile(source / 'gates.md', d.parent / 'gates.md')
        for name in ('verification-report-1-attempt-1.md', 'chunk-1-attempt-1-diff.patch', 'chunk-1-attempt-1-tests.txt'):
            shutil.copyfile(d / name, d / name.replace('attempt-1', 'attempt-2'))
        journal = d / 'chunk-1-journal.md'
        with journal.open('a', encoding='utf-8') as output:
            output.write('\n| 2 | 2026-08-01 | Изменена проверка | Свидетельство | passed=true |\n')
        first = d / 'verification-report-1-attempt-1.md'
        body = first.read_text(encoding='utf-8').replace('**passed:** true', '**passed:** false').replace(
            '**action:** continue', '**action:** retry') + '\n- **retry_instruction:** Исправь ошибку в проверке.\n'
        first.write_text(body + '- **По каким условиям упал:** блокирующее замечание независимого ревью\n', encoding='utf-8')
        checked = self.tool('flow-verdict.py', '--at', 'handoff', self.root, slug)
        self.assertEqual(checked.returncode, 1)
        self.assertIn('Отчёт называет независимое ревью причиной', checked.stdout)
        first.write_text(body.replace('| Тесты | ✅', '| Тесты | ❌') +
                         '- **По каким условиям упал:** Тесты вернули отказ, Ревью независимым агентом прошло.\n', encoding='utf-8')
        checked = self.tool('flow-verdict.py', '--at', 'handoff', self.root, slug)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        patch = d / 'chunk-1-attempt-1-diff.patch'
        patch.write_text('diff --git a/example.txt b/example.txt\n--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-before\n+after\n', encoding='utf-8')
        ref = [{'path': diffstat(patch.read_text(encoding='utf-8'))['paths'][0], 'anchor': 'путь'}]
        answer = d / 'review-raw.json'
        answer.write_text(json.dumps({'claims': [{'id': f'claim-{n}', 'status': 'passed', 'evidence': ref} for n in (1, 2, 3)],
                                     'scope': [{'kind': 'scope', 'summary': 'Нарушение области правок', 'evidence': ref}]}), encoding='utf-8')
        result = self.tool('review-validate.py', self.root, slug, 1, 1, answer)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checked = self.tool('flow-verdict.py', '--at', 'handoff', self.root, slug)
        self.assertEqual(checked.returncode, 1)
        self.assertIn(f'статус независимого ревью ({first.name})', checked.stdout)
        self.assertIn('Квитанция этой попытки подтверждает ответ с блокирующей находкой', checked.stdout)
        first.write_text(first.read_text(encoding='utf-8').replace('| Ревью независимым агентом | ✅', '| Ревью независимым агентом | ❌'), encoding='utf-8')
        checked = self.tool('flow-verdict.py', '--at', 'handoff', self.root, slug)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_verify_requires_current_patch_and_project_gate_receipts(self):
        source = ROOT / 'test/flow-verdict-corpus/green/.sdlc'
        for path in (source / 'green').glob('*.md'):
            if not path.name.startswith('.'):
                shutil.copyfile(path, self.d / path.name)
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('1234567', self.base).replace('example.txt', 'app.py'), encoding='utf-8')
        path = self.root / 'gate_fixture.py'
        path.write_text('import os\nimport sys\nfrom pathlib import Path\n'
                        'kind = sys.argv[1]\n'
                        'if kind in ("scope", "anti"):\n'
                        '    print(Path(os.environ["FIXTURE_PATCH"]).read_bytes().decode())\n'
                        'if kind == "rewrite":\n'
                        '    Path("app.py").write_bytes(b"answer = 999\\r\\n")\n'
                        'if kind == "fail":\n'
                        '    raise SystemExit(3)\n'
                        'print(kind)\n', encoding='utf-8')
        scope, anti = 'Scope: файлы вне плана', 'Анти-обход тест-гейта'
        project = 'Проектные инварианты как ассерты'
        commands = {name: f'"{sys.executable}" gate_fixture.py {mode}'
                    for name, mode in ((scope, 'scope'), (anti, 'anti'), (project, 'project'))}
        contract = {'version': 1, 'commands': [{'command': commands[name], 'inputs': {'FIXTURE_PATCH': 'patch'}}
                                                for name in (scope, anti)]}
        (self.d.parent / 'gate-context.json').write_text(json.dumps(contract), encoding='utf-8')
        gates = (source / 'gates.md').read_text(encoding='utf-8')
        gates = gates.replace('встроенная реализация', f'`{commands[scope]}`', 1).replace('встроенная реализация', f'`{commands[anti]}`', 1)
        gates = gates.replace('\n## Долг', f'| {project} | да | этап 6 | `{commands[project]}` |\n\n## Долг')
        (self.d.parent / 'gates.md').write_text(gates, encoding='utf-8')
        report = self.d / 'verification-report-1-attempt-1.md'
        body = report.read_text(encoding='utf-8').replace('\nстрок неприменимости нет', f'| {project} | ✅ | Отметка |\n\nстрок неприменимости нет')
        report.unlink()
        ready = self.prepare()
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        report.write_text(body, encoding='utf-8')

        def verify():
            return self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1')

        def run_gate(name, attempt):
            result = self.tool('attempt-evidence.py', 'run-gate', self.root, 'T-1', 1, attempt, '--gate', name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        missing = verify()
        self.assertEqual(missing.returncode, 1)
        for name in commands:
            self.assertIn(f'Гейт «{name}», chunk 1, попытка 1: нет квитанции', missing.stdout)
        for name in commands:
            run_gate(name, 1)
        result = verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with (self.d / 'chunk-1-journal.md').open('a', encoding='utf-8') as journal:
            journal.write('\n| 2 | 2026-08-01 | Правка | Исправлено | passed=true |\n')
        (self.root / 'app.py').write_bytes(b'answer = 3\r\n')
        self.write(2)
        (self.d / 'verification-report-1-attempt-2.md').write_text(body.replace('attempt-1', 'attempt-2'), encoding='utf-8')
        for name in (scope, anti):
            shutil.copytree(gate_evidence.directory(self.d, 1, 1, name), gate_evidence.directory(self.d, 1, 2, name))
        stale = verify()
        self.assertEqual(stale.returncode, 1)
        for name in (scope, anti):
            self.assertIn(f'Гейт «{name}», chunk 1, попытка 2: квитанция относится', stale.stdout)
            self.assertIn('+answer = 3', run_gate(name, 2).stdout)
        project_runs = gate_evidence.directory(self.d, 1, 2, project)
        app = self.root / 'app.py'
        app.write_bytes(b'answer = 999\r\n')
        result = self.tool('attempt-evidence.py', 'run-gate', self.root, 'T-1', 1, 2, '--gate', project)
        self.assertEqual(result.returncode, 1)
        self.assertIn('рабочее дерево отличается', result.stdout)
        self.assertEqual(list(project_runs.glob('run-*')), [])
        app.write_bytes(b'answer = 3\r\n')

        gates_path = self.d.parent / 'gates.md'
        command = commands[project]
        gates_path.write_text(gates.replace(command, command.removesuffix('project') + 'rewrite'), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'run-gate', self.root, 'T-1', 1, 2, '--gate', project)
        self.assertEqual(result.returncode, 1)
        self.assertIn('изменил рабочее дерево', result.stdout)
        self.assertEqual(list(project_runs.glob('run-*')), [])
        app.write_bytes(b'answer = 3\r\n')

        gates_path.write_text(gates.replace(command, command.removesuffix('project') + 'fail'), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'run-gate', self.root, 'T-1', 1, 2, '--gate', project)
        self.assertEqual(result.returncode, 3)
        self.assertIn('код выхода команды: 3', verify().stdout)

        gates_path.write_text(gates, encoding='utf-8')
        run_gate(project, 2)
        result = verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        output = sorted(project_runs.glob('run-*/output.txt'))[-1]
        output.write_bytes(output.read_bytes() + b'changed')
        tampered = verify()
        self.assertEqual(tampered.returncode, 1)
        self.assertIn('SHA-256 вывода команды не совпадает', tampered.stdout)

        command = commands[scope] + ' .sdlc/T-1/chunk-1-attempt-1-diff.patch'
        contract['commands'][0]['command'] = command
        (self.d.parent / 'gate-context.json').write_text(json.dumps(contract), encoding='utf-8')
        (self.d.parent / 'gates.md').write_text(gates.replace(commands[scope], command), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'run-gate', self.root, 'T-1', 1, 2, '--gate', scope)
        self.assertEqual(result.returncode, 1)
        self.assertIn('убери фиксированный путь', result.stdout)

    def test_chunk_commands_reuse_verified_evidence_tool(self):
        skill = (ROOT / 'implementations/claude-code/skills/sdlc-chunk/SKILL.md').read_text(encoding='utf-8')
        body = skill.split('Зафиксируй путь совместимой копии как `<attempt-evidence>`', 1)[1]
        self.assertNotIn('python ~/.claude/skills/sdlc-verify/tools/attempt-evidence.py', body)
        for mode in ('compare', 'snapshot', 'baseline', 'prepare', 'write'):
            self.assertIn(f'python <attempt-evidence> {mode}', body)

    def test_verify_acceptance_rows_are_unique_and_known_in_both_contours(self):
        for contour, claims in (('полный', ('claim-1', 'claim-2')), ('мелкий', ('claim-1',))):
            with self.subTest(contour=contour):
                rows = ['## 1. Пункты приёмки'] + [f'| {cid} | Поведение | ⚠ | нет результата процедуры | чинить |'
                                                  for cid in claims]
                self.assertEqual(flow.acceptance_claim_errors('\n'.join(rows), set(claims)), [])
                rows.insert(2, '| claim-1 | Повтор | ✅ | app.py | н/п |')
                rows.append('| claim-99 | Несогласованный сценарий | ✅ | app.py | н/п |')
                problems = flow.acceptance_claim_errors('\n'.join(rows), set(claims))
                self.assertIn('повторные ID: claim-1', problems)
                self.assertIn('неизвестные ID: claim-99', problems)
                self.assertFalse(any('⚠' in problem for problem in problems))

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
- **Класс:** пропуск приёмочного листа
- **Повтор:** нет
- **Что проскочило:** Пропущенная ветка.
- **Какой гейт должен был поймать и почему не поймал:** Тесты — не покрывает класс
- **Действие:** проверка
- **Кто утвердил:** Оператор · явный ответ в текущем витке
- **Где реализовано:** app.py:answer
- **Чем закреплено:** test_answer.py:test_branch, FAIL до исправления, PASS после
'''
        for spelling in (source, f'`{source}`', f'`{source}`.'):
            self.assertEqual(flow.defect_record_errors(self.d, block.replace(source, spelling)), [])
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
        gate_commands = {'Scope: файлы вне плана': 'git apply --stat "$FIXTURE_PATCH"',
                         'Анти-обход тест-гейта': 'git apply --numstat "$FIXTURE_PATCH"'}
        gates = (source / 'gates.md').read_text(encoding='utf-8')
        for command in gate_commands.values():
            gates = gates.replace('встроенная реализация', f'`{command}`', 1)
        (self.d.parent / 'gates.md').write_text(gates, encoding='utf-8')
        (self.d.parent / 'gate-context.json').write_text(json.dumps({'version': 1, 'commands': [
            {'command': command, 'inputs': {'FIXTURE_PATCH': 'patch'}} for command in gate_commands.values()
        ]}), encoding='utf-8')
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
        for name in gate_commands:
            result = self.tool('attempt-evidence.py', 'run-gate', self.root, self.d.name, 1, 1, '--gate', name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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


class ReadinessVerdictDictionaryTests(unittest.TestCase):
    """#72: вердикт прогона — только словарные формы; контракт совпадает с Runner readinessVerdict."""

    LINE = '**Вердикт прогона 1:** {}'

    def test_accepted_dictionary_forms(self):
        for value in ('готова', 'готова.', 'готова!', '✅ готова', 'Готова', 'ГОТОВА',
                      'готова — закрыть вопрос 2', 'готова – пояснение', 'готова - пояснение',
                      '  готова  ', '**готова**', '`готова`', 'готова   —   пояснение'):
            self.assertTrue(readiness_ok(self.LINE.format(value), 1), value)

    def test_rejected_free_prose_and_weakened_forms(self):
        for value in ('не готова', 'не готова — причины', 'готова для разведки',
                      'готова к утверждённому плану', 'готова к плану — вопросов нет',
                      'почти готова', 'частично готова', 'условно готова', 'готова с оговорками',
                      'задача готова', 'готова / не готова', '‹готова / не готова›',
                      '', 'Не вычислен', 'готова; готова к плану'):
            self.assertFalse(readiness_ok(self.LINE.format(value), 1), value)

    def test_missing_verdict_line_is_not_ready(self):
        self.assertFalse(readiness_ok('', 1))
        self.assertFalse(readiness_ok('## Прогон 1\n- **Дата:** 2026-11-16\n', 1))


class MethodologyPracticeTests(unittest.TestCase):
    """#79–#82: общие определения PDSA, вариации, андон/Кано и переноса уроков."""

    def setUp(self):
        self.constants = json.loads((ROOT / 'sdlc-constants.json').read_text(encoding='utf-8'))
        self.sdlc = (ROOT / 'SDLC.md').read_text(encoding='utf-8')
        self.sdlc += '\n' + (ROOT / 'process-quality.md').read_text(encoding='utf-8')
        self.guided = (ROOT / 'profiles/guided.md').read_text(encoding='utf-8')

    def test_prediction_calibration_has_three_outcomes_and_does_not_change_verdict(self):
        self.assertEqual(self.constants['prediction_outcomes'], {
            'fulfilled': 'сбывшийся', 'not_fulfilled': 'несбывшийся', 'unmeasured': 'не измерено'})
        for text in (self.sdlc, self.guided):
            self.assertIn('`passed=true`', text)
            self.assertIn('`passed=false`', text)
            self.assertIn('той же редакции требований и кода', text)
            self.assertIn('не меняет `passed` и `action`', text)
        self.assertIn('сбывшиеся / (сбывшиеся + несбывшиеся)', self.guided)
        self.assertIn('Пустой знаменатель означает `не измерено`', self.guided)
        self.assertIn('Прогноз без проверки не считай ни сбывшимся, ни несбывшимся', self.guided)

    def test_stability_uses_explicit_p_chart_minimum_and_recorded_series(self):
        self.assertEqual(self.constants['stability_states'], ['годна', 'не годна', 'нестабильна'])
        chart = self.constants['p_chart']
        self.assertEqual(chart, {'minimum_n': 20, 'subgroup_size': 1,
                                 'control_limit_sigmas': 3, 'one_side_run_length': 8})
        self.assertIn('**20 измеримых прогонов**', self.sdlc)
        self.assertIn('`nᵢ=1`', self.sdlc)
        self.assertIn('`p̄ ± 3 · √(p̄ · (1 − p̄) / nᵢ)`', self.sdlc)
        self.assertIn('Восемь точек подряд', self.sdlc)
        self.assertIn('`blocked_env` и андон не входят в знаменатель', self.sdlc)
        self.assertIn('зелёный после красного не сохраняется', self.sdlc)
        self.assertIn('При меньшем `n` состояние стабильности не присваивай', self.sdlc)
        self.assertNotIn('Контрольные карты и Six Sigma здесь не применяются', self.sdlc)

    def test_andon_and_kano_preserve_scope_gates_and_human_attention(self):
        self.assertEqual(self.constants['andon'], {
            'action': 'andon', 'outcome': 'честная остановка',
            'consumes_repair_budget': False, 'model_failure': False})
        self.assertEqual(self.constants['kano_classes']['values'], ['must-be', 'performance', 'delighters'])
        intent = (ROOT / 'templates/intent.template.md').read_text(encoding='utf-8')
        handoff = (ROOT / 'templates/handoff.template.md').read_text(encoding='utf-8')
        self.assertNotIn('| Класс Кано |', intent)
        self.assertIn('Предложения ИИ добавляй в приёмку только после явного одобрения', intent)
        self.assertIn('**честная остановка**', handoff)
        self.assertIn('не расходует бюджет ремонтов, не является отказом модели', handoff)
        example_handoff = (ROOT / 'example/handoff.md').read_text(encoding='utf-8')
        self.assertIn("виток продолжается\n  следующей попыткой chunk'а 1\n- **Андон:** н/п", example_handoff)
        self.assertIn('Нельзя скрывать исключения `must-be` ради снижения метрики human-required', self.sdlc)
        self.assertIn('не устанавливает `passed=true`, не обходит гейты', self.sdlc)
        self.assertIn('не расширяет приёмку и scope', self.sdlc)

    def test_a3_requires_systemic_cause_and_yokoten_on_unrelated_task(self):
        template = (ROOT / 'templates/retro-a3.template.md').read_text(encoding='utf-8')
        for heading in ('## Фон', '## Текущее состояние', '## Цель',
                        '## Анализ: пять почему до системной причины',
                        '## Контрмеры и план', '## Проверка переноса (ёкотэн)', '## Реестр уроков'):
            self.assertIn(heading, template)
        for n in range(1, 6):
            self.assertIn(f'**Почему {n}:**', template)
        self.assertIn('класс дефекта → системная корневая причина → контрмера', template)
        self.assertIn('Несвязанная задача', template)
        self.assertIn('открыт / перенесён', template)
        self.assertNotIn('legacy-no-change', template)
        self.assertNotIn('legacy-verify-text', template)
        self.assertIn('**Первый персистентный отчёт серии:**', template)
        self.assertIn('## Отклонённые находки', template)
        self.assertIn('Добавь подтверждённые открытые уроки из проверенных отчётов', template)
        self.assertIn('не открывай его заново без нового дефекта', template)
        self.assertIn('не закрывает автоматически прежние задачи', template)
        for name in ('codex', 'claude-code'):
            skill = (ROOT / f'implementations/{name}/skills/sdlc-retro/SKILL.md').read_text(encoding='utf-8')
            self.assertIn('retro-a3.template.md', skill)
            self.assertIn('несвязанной задаче', skill)
            self.assertIn('системная корневая', skill)
            self.assertIn('подтверждённые открытые уроки из проверенных отчётов', skill)
            self.assertIn('не открывай', skill.casefold())

    def test_readme_links_only_documented_practices(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        section = readme.split('## Какие подходы применяются', 1)[1].split('## Реализации', 1)[0]
        for label in ('PDSA', 'SoPK', 'вариации', 'андон', 'A3', 'пять почему', 'Ёкотэн', 'Кано',
                      'Fagan', 'Синго', 'Теория ограничений', 'ISO/IEC 25010'):
            self.assertIn(label, section)
        self.assertIn('(templates/retro-a3.template.md)', section)
        self.assertIn('(process-quality.md#стабильность-вердиктов-по-сериям)', section)
        self.assertIn('Six Sigma и формальное соответствие ISO 9001/CMMI проект не заявляет', section)


class BlockedEnvExternalCriterionTests(unittest.TestCase):
    """#100: blocked_env разрешён для обязательного критерия на подтверждённо недоступной внешней среде."""

    def setUp(self):
        self.sdlc = (ROOT / 'SDLC.md').read_text(encoding='utf-8')

    def test_mandatory_external_criterion_allows_blocked_env_with_full_diagnostics(self):
        for fragment in ('Ошибка кода и отсутствие среды требуют разных действий',
                         'Обязательный claim требует внешней среды',
                         'Сохрани диагностику среды',
                         'Укажи непроверенный claim или гейт, первичное свидетельство и условие продолжения',
                         'Для открытого claim сохрани ⚠ и passed=false',
                         'Остальные проверки, включая ревью, scope и инварианты, успешны'):
            self.assertIn(fragment, self.sdlc)


class PlanChecksVsProjectGatesTests(unittest.TestCase):
    """#83: проверки плана и проектные гейты — независимые обязательства; слабое звено роняет вердикт."""

    def setUp(self):
        self.sdlc = (ROOT / 'SDLC.md').read_text(encoding='utf-8')
        self.plan = (ROOT / 'templates/plan.template.md').read_text(encoding='utf-8')
        self.report = (ROOT / 'templates/verification-report.template.md').read_text(encoding='utf-8')
        self.example = (ROOT / 'example/plan-checks-and-gates.md').read_text(encoding='utf-8')

    def test_contract_states_two_independent_obligations_and_weak_link_verdict(self):
        for fragment in ('два независимых обязательства',
                         'зелёный гейт не подтверждает пункт и не отменяет его процедуру',
                         'Итог определяется слабым звеном',
                         'красная либо отсутствующая проверка плана',
                         'остаётся\nв `⚠`, опровергнутый — `❌`',
                         'example/plan-checks-and-gates.md'):
            self.assertIn(fragment, self.sdlc)
        self.assertIn('Проверки плана и проектные гейты', self.sdlc)
        self.assertIn('реальную команду проверки с каталогом её запуска', self.sdlc)

    def test_templates_pin_command_environment_and_result(self):
        self.assertIn('‹команда проекта› из ‹каталог запуска от корня›', self.plan)
        for fragment in ('фактически выполненную команду, код возврата и счёт',
                         'каталог её запуска',
                         'версия инструмента'):
            self.assertIn(fragment, self.report)

    def test_example_with_two_stacks_shows_commands_and_acceptance_reasons(self):
        for fragment in ('ledger-svc', 'cabinet-web',
                         './tools/gw :ledger-svc:test', 'npm run validate --workspace cabinet-web',
                         'passed: false', 'passed: true', '11 из 12', '12/12',
                         'Зелёный гейт другого стека пункт не подтверждает'):
            self.assertIn(fragment, self.example)


class TerminalEvidenceCheckSeparationTests(unittest.TestCase):
    """#84: терминальная проверка версии инструмента исключена из промпта управляемого Chunk."""

    def setUp(self):
        self.sdlc = (ROOT / 'SDLC.md').read_text(encoding='utf-8')

    def test_version_check_block_wrapped_in_runner_skip(self):
        block = ('<!-- runner:skip -->\n'
                 'До старта Chunk выполни `attempt-evidence.py --version` и проверь JSON `capabilities`.\n'
                 'Для snapshot-плана обязательны возможности `snapshot` и `prepare`. '
                 'Старую копию обнови штатным установщиком текущей реализации либо явно выбери '
                 'совместимую копию из проверенной поставки и повтори проверку.\n'
                 'Все последующие вызовы инструмента в этом Chunk выполняй той же проверенной копией.\n'
                 'При отказе проверки остановись с диагностикой. '
                 'Не заменяй snapshot-план Git-diff и не переходи к непроверенной копии инструмента.\n'
                 '<!-- /runner:skip -->')
        self.assertIn(block, self.sdlc)

    def test_managed_chunk_receives_runner_check_state(self):
        for fragment in ('Runner проверяет версию инструмента свидетельств `attempt-evidence.py` '
                         'и передаёт её состояние.',
                         'Получи состояние от Runner и переходи к изменению кода '
                         'без локального вызова скрипта.'):
            self.assertIn(fragment, self.sdlc)


if __name__ == '__main__':
    unittest.main()
