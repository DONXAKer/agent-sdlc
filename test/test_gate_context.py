"""Контекст команды не должен зависеть от окружения родительского процесса."""

import json
import unittest
import test_snapshot_base as fixtures
from sdlc_common import gate_context


class GateContextTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool

    def contract(self, inputs, command='git --version'):
        (self.root / '.sdlc/gate-context.json').write_text(json.dumps({
            'version': 1, 'commands': [{'command': command, 'inputs': inputs}]}), encoding='utf-8')

    def test_context_comes_from_explicit_values(self):
        self.contract({'TASK': 'slug', 'PATCH': 'patch', 'LAYER': 'layer'})
        (self.d / 'chunk-2-attempt-3-diff.patch').write_text('патч', encoding='utf-8')
        value = gate_context(self.root, self.d, 'git --version', 2, 3, 'unit')
        self.assertEqual(value['TASK'], 'T-1')
        self.assertEqual(value['PATCH'], str(self.d / 'chunk-2-attempt-3-diff.patch'))
        self.assertEqual(value['LAYER'], 'unit')

    def test_missing_layer_does_not_create_attempt(self):
        self.contract({'LAYER': 'layer'})
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('отсутствует подтверждённый вход layer', result.stdout)
        self.assertEqual(list(self.d.glob('chunk-*-attempt-*')), [])

    def test_unknown_source_and_missing_patch_are_configuration_errors(self):
        for inputs in ({'VALUE': 'guess'}, {'PATCH': 'patch'}):
            self.contract(inputs)
            with self.assertRaises(ValueError):
                gate_context(self.root, self.d, 'git --version')

    def test_verify_gate_receives_values_and_preflight_does_not_run_it(self):
        script = self.root / 'check.py'
        script.write_text("import os\nfrom pathlib import Path\n"
                          "assert os.environ['TASK'] == 'T-1'\n"
                          "assert os.environ['LAYER'] == 'integration'\n"
                          "Path('executed.txt').write_text(os.environ['PATCH'])\n", encoding='utf-8')
        command = 'python check.py'
        self.contract({'TASK': 'slug', 'PATCH': 'patch', 'LAYER': 'layer'}, command)
        (self.d / 'chunk-2-attempt-4-diff.patch').write_text('патч', encoding='utf-8')
        args = (self.root, 'T-1', 2, 4, '--command', command, '--layer', 'integration')
        result = self.tool('attempt-evidence.py', 'preflight', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'executed.txt').exists())
        result = self.tool('attempt-evidence.py', 'run-gate', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / 'executed.txt').read_text().endswith('chunk-2-attempt-4-diff.patch'))

    def test_verify_rejects_missing_patch_and_invalid_attempt(self):
        self.contract({'PATCH': 'patch'})
        for n, k in ((1, 1), ('../escape', 1), (1, '0')):
            with self.assertRaises(ValueError):
                gate_context(self.root, self.d, 'git --version', n, k)


if __name__ == '__main__':
    unittest.main()
