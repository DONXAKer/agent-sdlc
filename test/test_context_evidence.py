"""Свидетельства долга и программная готовность chunk: положительные и отрицательные контроли."""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test import test_snapshot_base as fixtures
import chunk_preconditions
import sdlc_common


class ContextEvidenceTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot
    write = fixtures.SnapshotTests.write

    def prepare(self, n=1, env=None):
        locator = self.d / f'locator-{n}.md'
        locator.write_text('Точка правки: app.py, answer.\n', encoding='utf-8')
        return self.tool('attempt-evidence.py', 'prepare', self.root, 'T-1', n, '--locator', locator.name, env=env)

    def test_early_receipt_accepts_git_and_dirty_snapshot(self):
        for kind in ('git', 'snapshot'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'git':
                    self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
                else:
                    (self.root / 'app.py').write_bytes(b'answer = 10\r\n')
                    self.snapshot()
                result = self.prepare()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                (self.root / 'app.py').write_bytes(b'answer = 20\r\n')
                self.write()
                rec = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_text(encoding='utf-8'))
                self.assertEqual(rec['preconditions']['errors'], [])
                self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1, rec['preconditions']['receipt_sha256']), [])

    def test_late_prepare_rejects_changed_paths_and_preserves_deviation(self):
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn('порядок не доказан', result.stdout)
        journal = self.d / 'chunk-1-journal.md'
        journal.write_text('**Подтвердил:** Оператор\nОтклонение: locator вызван после правок.\n', encoding='utf-8')
        self.write()
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Нет программной квитанции', result.stdout)
        self.assertIn('Отклонение:', journal.read_text(encoding='utf-8'))
        self.assertEqual(self.prepare().returncode, 1)

    def test_receipt_detects_changed_locator_baseline_plan_and_pin(self):
        self.snapshot()
        self.assertEqual(self.prepare().returncode, 0)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        rec = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_text(encoding='utf-8'))
        for path in (self.d / 'locator-1.md', self.d / '.chunk-1-baseline.json', self.plan,
                     chunk_preconditions.receipt_path(self.d, 1)):
            before = path.read_bytes()
            path.write_bytes(before + b' ')
            self.assertTrue(chunk_preconditions.errors(self.root, self.d, 1, rec['preconditions']['receipt_sha256']), path.name)
            path.write_bytes(before)

    def test_next_chunk_requires_unchanged_previous_evidence(self):
        self.snapshot()
        self.assertEqual(self.prepare().returncode, 0)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        (self.root / 'app.py').write_bytes(b'answer = 3\r\n')
        self.assertEqual(self.prepare(2).returncode, 1)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        result = self.prepare(2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_git_diff_preferences_cannot_hide_late_preparation(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        (self.d / 'locator.md').write_text('Точка правки: app.py.\n', encoding='utf-8')
        env = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='diff.noprefix', GIT_CONFIG_VALUE_0='true')
        result = self.tool('attempt-evidence.py', 'prepare', self.root, 'T-1', 1, '--locator', 'locator.md', env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn('порядок не доказан', result.stdout)

    def test_approved_next_plan_preserves_historical_receipt(self):
        self.snapshot()
        self.assertEqual(self.prepare().returncode, 0)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        previous = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_text(encoding='utf-8'))
        self.plan.write_text(self.plan.read_text(encoding='utf-8') +
                             '\n- **Одобрение:** Оператор · 2026-11-09\nНовая согласованная редакция.\n', encoding='utf-8')
        result = self.prepare(2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(chunk_preconditions.errors(self.root, self.d, 1))
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1,
                         previous['preconditions']['receipt_sha256'], historical=True), [])
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 2), [])
        archived = self.d / '.chunk-1-plan.md'
        archived.write_bytes(archived.read_bytes() + b' ')
        self.assertTrue(chunk_preconditions.errors(self.root, self.d, 1, historical=True))

    def test_two_git_chunks_use_same_diff_format_under_preferences(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        env = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='diff.noprefix', GIT_CONFIG_VALUE_0='true')
        self.assertEqual(self.prepare(env=env).returncode, 0)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 1, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 1, env=env).returncode, 0)
        result = self.prepare(2, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_staged_rename_cannot_hide_late_preparation(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        self.git('mv', 'app.py', 'moved.py')
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn('порядок не доказан', result.stdout)

    def gates(self, row):
        minimum = '\n'.join(f'| {name} | да — минимум | этап 6 | `true` |' for name in sdlc_common.MANDATORY_GATES)
        return ('| Гейт | Вкл | Где отчитывается | Чем реализован |\n|---|---|---|---|\n' + minimum +
                '\n| Дополнительная проверка | нет | этап 6 | н/п |\n\n## Долг\n\n'
                '| Гейт | Где должен стоять | Как закрывается | Дата | Кто | Область | Свидетельство / условие пересмотра |\n'
                '|---|---|---|---|---|---|---|\n' + row)

    def test_debt_current_foreign_portable_and_legacy(self):
        (self.d / 'manual.md').write_text('Проверка выполнена.\n', encoding='utf-8')
        prefix = '| Дополнительная проверка | этап 6 | проверяет руками: результат сохранён | 2026-11-09 | Оператор |'
        def problems(row):
            return sdlc_common.gate_configuration_errors(self.gates(row), self.root, 'T-1')
        self.assertEqual(problems(prefix + ' виток:T-1 | artifact:manual.md |'), [])
        (self.root / '.sdlc/gates.md').write_text(self.gates(prefix + ' виток:T-1 | artifact:manual.md |'), encoding='utf-8')
        self.assertEqual(self.tool('gates-preflight.py', self.root, '--builtin-review').returncode, 1)
        self.assertEqual(self.tool('gates-preflight.py', self.root, '--builtin-review', '--slug', 'T-1').returncode, 0)
        self.assertEqual(self.tool('gates-preflight.py', self.root, '--builtin-review', '--slug', 'next').returncode, 1)
        self.assertTrue(problems(prefix + ' виток:previous | artifact:manual.md |'))
        self.assertTrue(problems(prefix + ' виток:T-1 | artifact:missing.md |'))
        self.assertTrue(problems(prefix + ' виток:T-1 | artifact:../previous/manual.md |'))
        self.assertTrue(problems(prefix))
        risk = '| Дополнительная проверка | этап 6 | риск принят: профиль не использует эту возможность | 2026-11-09 | Оператор | проект |'
        self.assertEqual(problems(risk + ' пересмотр: изменение профиля |'), [])
        self.assertTrue(problems(risk + ' безусловно |'))
        self.assertTrue(problems(risk + ' пересмотр: использовать artifact:previous.md |'))

    def test_flow_checks_current_debt_scope_and_legacy_receipt(self):
        source = Path(__file__).resolve().parent / 'flow-verdict-corpus/green/.sdlc'
        shutil.copytree(source, self.root / '.sdlc', dirs_exist_ok=True)
        d = self.root / '.sdlc/green'
        gates = self.root / '.sdlc/gates.md'
        original = gates.read_text(encoding='utf-8').replace('\n\n## Долг',
            '\n| Дополнительная проверка | нет | этап 6 | н/п |\n\n## Долг', 1)
        header = ('\n\n| Гейт | Где должен стоять | Как закрывается | Дата | Кто | Область | Свидетельство / условие пересмотра |\n'
                  '|---|---|---|---|---|---|---|\n')
        (d / 'manual.md').write_text('Проверка выполнена.\n', encoding='utf-8')
        current = '| Дополнительная проверка | этап 6 | проверяет руками: выполнено | 2026-11-09 | Оператор | виток:green | artifact:manual.md |'
        gates.write_text(original + header + current, encoding='utf-8')
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gates.write_text(original + header + current.replace('виток:green', 'виток:previous'), encoding='utf-8')
        self.assertEqual(self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green').returncode, 1)
        risk = '| Дополнительная проверка | этап 6 | риск принят: возможность не используется | 2026-11-09 | Оператор | проект | пересмотр: изменение профиля |'
        gates.write_text(original + header + risk, encoding='utf-8')
        self.assertEqual(self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green').returncode, 0)
        chunk_preconditions.receipt_path(d, 1).unlink()
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'green')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Нет программной квитанции', result.stdout)


if __name__ == '__main__':
    unittest.main()
