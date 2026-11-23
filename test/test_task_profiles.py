"""Проверь выбор пилота, обязательные гейты и совместимость базового маршрута."""

from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'implementations/claude-code/skills/sdlc-verify/tools'))
from sdlc_common import MANDATORY_GATES, preparation_checks, profile_gate_errors, task_profile
sys.path.insert(0, str(ROOT))
from test import test_snapshot_base as fixtures
from test.test_issue_regressions import flow
from tempfile import TemporaryDirectory
from chunk_preconditions import receipt_path


class TaskProfiles(unittest.TestCase):
    def test_bugfix_and_legacy_acceptance_rows(self):
        row = '| claim-1 | Пустой запрос отклонён | Проверить код ответа |'
        readiness = '**Вердикт прогона 1:** готова\n**Вердикт прогона 2:** готова'
        pilot = '**Профиль задачи:** bugfix\n' + row
        for intent in (pilot, pilot + ' performance |'):
            errors = [name for ok, name, _ in preparation_checks(intent, 'claim-1', readiness) if not ok]
            self.assertEqual(errors, [])
        legacy = '\n'.join((row, row.replace('claim-1', 'claim-2 [edge]'), row.replace('claim-1', 'claim-3 [edge]')))
        self.assertTrue(all(ok for ok, _, _ in preparation_checks(legacy, 'claim-1 claim-2 claim-3', readiness)))
        self.assertFalse(all(ok for ok, _, _ in preparation_checks(row, 'claim-1', readiness)))

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            paths = ['module/handler.py', 'module/validator.py', 'module/test_input.py']
            patch = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n' for p in paths)
            (directory / 'chunk-1-attempt-1-diff.patch').write_text(patch, encoding='utf-8')
            compact = '**Контур:** мелкий\n'
            self.assertTrue(flow.small_scope_errors(directory, compact + row))
            receipt_path(directory, 1).write_text(json.dumps({'contour': 'мелкий', 'task_profile': 'bugfix'}), encoding='utf-8')
            self.assertEqual(flow.small_scope_errors(directory, compact + pilot), [])

    def test_only_explicit_profile_replaces_quota(self):
        intent = '| claim-1 | результат | наблюдение |'
        for marker, quota in [('', True), ('**Профиль задачи:** feature', False)]:
            checks = preparation_checks(intent + '\n' + marker, '', '')
            self.assertEqual(any('≥3' in label for _, label, _ in checks), quota)

    def test_ambiguous_profile_is_rejected(self):
        intent = '**Профиль задачи:** feature\n**Профиль задачи:** bugfix'
        self.assertIsNone(task_profile(intent))
        self.assertIn(False, [ok for ok, label, _ in preparation_checks(intent, '', '')
                              if label == 'профиль задачи распознан'])

    def test_bugfix_requires_both_gates(self):
        intent = '**Профиль задачи:** bugfix'
        errors = profile_gate_errors(intent, '')
        self.assertEqual(len(errors), 2)
        gates = '\n'.join('| ' + name + ' | да | этап 6 | `check` |'
                          for name in ('Сверка тестов с claims', 'Тесты красные на базе'))
        self.assertEqual(profile_gate_errors(intent, gates), [])
        self.assertEqual(profile_gate_errors('', ''), [])


class RunnerLiteTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot

    def test_resume_and_reject_changed_candidate(self):
        rows = ['| Гейт | Вкл | Где отчитывается | Чем реализован |', '|---|---|---|---|']
        for name in MANDATORY_GATES:
            command = 'git version' if name in ('Scope: файлы вне плана', 'Анти-обход тест-гейта') else 'git --version'
            rows.append(f'| {name} | да | этап 6 | `{command}` |')
        (self.d.parent / 'gates.md').write_text('\n'.join(rows), encoding='utf-8')
        context = {'version': 1, 'commands': [
            {'command': 'git version', 'inputs': {'PATCH': 'patch', 'LAYER': 'layer'}},
            {'command': 'git --version', 'inputs': {'LAYER': 'layer'}},
        ]}
        (self.d.parent / 'gate-context.json').write_text(json.dumps(context), encoding='utf-8')
        intent = self.d / 'intent.md'
        text = intent.read_text(encoding='utf-8') + '\n**Профиль задачи:** docs-config\n'
        intent.write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
        self.snapshot()
        (self.d / 'locator.md').write_text('app.py', encoding='utf-8')
        (self.d / 'chunk-1-journal.md').write_text('Начало работы', encoding='utf-8')
        for _ in range(2):
            result = self.tool('runner-lite.py', 'prepare', self.root, 'T-1', 1, '--locator', 'locator.md')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = self.root / 'app.py'
        source.write_bytes(b'answer = 2\n')
        def check(*flags):
            return self.tool('runner-lite.py', 'check', self.root, 'T-1', 1, 1, '--executor-model', 'test',
                             '--layer', 'integration', *flags)
        for _ in range(2):
            result = check()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Воспроизведи остановку после записи патча и до публикации evidence.
        evidence = self.d / 'chunk-1-attempt-1-evidence.json'
        evidence.unlink()
        result = check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(evidence.read_bytes())['layer'], 'integration')
        gate_directory = next(self.d.glob('.chunk-1-gate-runs/attempt-1/*'))
        receipt = sorted(gate_directory.glob('run-*/receipt.json'))[-1]
        receipt.unlink()
        result = check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--resume-gates', result.stderr)
        result = check('--resume-gates')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source.write_bytes(b'answer = 3\n')
        self.assertNotEqual(check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
