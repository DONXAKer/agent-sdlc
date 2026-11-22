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


class TaskProfiles(unittest.TestCase):
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
        def check():
            return self.tool('runner-lite.py', 'check', self.root, 'T-1', 1, 1, '--executor-model', 'test',
                             '--layer', 'integration')
        for _ in range(2):
            result = check()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Воспроизведи остановку после записи патча и до публикации evidence.
        evidence = self.d / 'chunk-1-attempt-1-evidence.json'
        evidence.unlink()
        result = check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(evidence.read_bytes())['layer'], 'integration')
        source.write_bytes(b'answer = 3\n')
        self.assertNotEqual(check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
