"""Положительные и отрицательные контроли машинного словаря артефактов."""

import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'implementations/claude-code/skills/sdlc-verify/tools'))
from sdlc_common import (CONSTANTS, axis_outcome_kind, gate_switch, enabled,
                         readiness_ok, preparation_checks, defect_class_catalog, defect_class_name)


class ArtifactDictionaryTests(unittest.TestCase):
    def test_gate_history_uses_the_same_switch_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'test/flow-verdict-corpus/gate-enabled-after-report/.sdlc', root / '.sdlc')
            gates = root / '.sdlc/gates.md'
            gates.write_text(gates.read_text(encoding='utf-8').replace('нет → да', 'нет → **ДА**'), encoding='utf-8')
            result = subprocess.run([sys.executable,
                str(ROOT / 'implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py'),
                '--at', 'verify', str(root), 'gate-enabled-after-report'], capture_output=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_flow_checks_normalized_gates_and_axis_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'test/flow-verdict-corpus/axes-quoted-reason/.sdlc', root / '.sdlc')
            gates = root / '.sdlc/gates.md'
            gates.write_text(gates.read_text(encoding='utf-8').replace('| да', '| **ДА**'), encoding='utf-8')
            plan = root / '.sdlc/axes-quoted-reason/plan.md'
            text = plan.read_text(encoding='utf-8').replace('н/п —', 'Н/П —').replace('шаг 2 | claim-1', 'шаг 2 | CLAIM-1')
            plan.write_text(text, encoding='utf-8')
            command = [sys.executable, str(ROOT / 'implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py'),
                       '--at', 'verify', str(root), 'axes-quoted-reason']
            result = subprocess.run(command, capture_output=True, encoding='utf-8')
            self.assertIn('✅ разбор последствий: адресаты исходов существуют', result.stdout)
            self.assertIn('✅ минимум: «Сборка» включён', result.stdout)
            self.assertIn('✅ строка гейта «Сборка» в отчёте', result.stdout)
            plan.write_text(text.replace('шаг 2 | CLAIM-1', 'шаг 2 | CLAIM-99'), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('claim-99 нет в задаче', result.stdout)
            plan.write_text(text, encoding='utf-8')
            report = root / '.sdlc/axes-quoted-reason/verification-report-1-attempt-1.md'
            report.write_text('\n'.join(line for line in report.read_text(encoding='utf-8').splitlines()
                                        if not line.startswith('| Сборка |')), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('строка гейта «Сборка» в отчёте', result.stdout)

    def test_gate_switch_normalizes_only_documented_forms(self):
        for value in ('да', '**ДА**', '  да  —  минимум ', 'Да – минимум'):
            self.assertEqual(gate_switch(value), 'да')
            self.assertTrue(enabled(value))
        for value in ('нет', '`НЕТ` — причина'):
            self.assertEqual(gate_switch(value), 'нет')
            self.assertFalse(enabled(value))
        for value in ('да/нет', 'да или нет', 'давайте', 'нет?', '', 'да —'):
            self.assertIsNone(gate_switch(value), value)

    def test_each_axis_kind_has_an_independent_positive_control(self):
        examples = {
            'claim-1, claim-2': 'claim-N',
            'Инвариант — число записей не меняется': 'инвариант',
            'гейт «Сборка»': 'гейт «имя»',
            'риск': 'риск',
            'следующий виток: вопрос 2': 'следующий виток',
            'Н/П — публичный контракт не меняется': 'н/п — почему',
        }
        self.assertEqual(set(examples.values()), set(CONSTANTS['axis_outcomes']))
        for value, expected in examples.items():
            self.assertEqual(axis_outcome_kind(value), expected, value)
        for value in ('другое claim-1', 'рискованный', 'н/п', 'гейт', 'следующий витоков', ''):
            self.assertIsNone(axis_outcome_kind(value), value)

    def test_readiness_dictionary_and_diagnostics(self):
        for value in ('готова', 'ГОТОВА', '**готова** — проверки завершены', '✅ готова', 'готова.'):
            self.assertTrue(readiness_ok(f'**Вердикт прогона 1:** {value}', 1), value)
        for value in CONSTANTS['readiness_verdicts']['rejected_prose_examples'] + ['не готова', 'готова / не готова']:
            self.assertFalse(readiness_ok(f'**Вердикт прогона 1:** {value}', 1), value)
        detail = preparation_checks('', '', '**Вердикт прогона 1:** почти готова')[-2][2]
        self.assertIn('почти готова', detail)
        self.assertIn('ожидается: готова', detail)

    def test_defect_dictionary_rejects_unknown_and_accepts_declared_alias(self):
        catalog = defect_class_catalog()
        for name in CONSTANTS['defect_classes']:
            self.assertEqual(defect_class_name(name, catalog), (name, None))
        self.assertEqual(defect_class_name('сломанная сборка.', catalog), ('сломанная сборка', None))
        self.assertEqual(defect_class_name('**СЛОМАННАЯ СБОРКА**', catalog), ('сломанная сборка', None))
        self.assertIn('ожидается', defect_class_name('неизвестный класс', catalog)[1])
        project = defect_class_catalog('## Классы дефектов проекта\n- `ошибка_формата` — 2026-11-16\n')
        self.assertEqual(defect_class_name('**ОШИБКА_ФОРМАТА**', project), ('ошибка_формата', None))
        self.assertTrue(defect_class_name('ошибкаформата', project)[1])


if __name__ == '__main__':
    unittest.main()
