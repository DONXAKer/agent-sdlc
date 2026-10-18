"""Exercise real CLI precedence and evidence failures, not a parser imitation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_state_contract import valid_state

TOOL = Path(__file__).resolve().parents[1] / 'implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py'


class StateFirstVerdictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / '.sdlc/SCHED-101'
        self.run.mkdir(parents=True)
        (self.run / 'verification-report-1-attempt-1.md').write_text('**passed:** false\n❌ stale', encoding='utf-8', newline='\n')
        (self.root / 'scheduler').mkdir()
        (self.root / 'scheduler/core.py').write_text('def cancel(): pass\n', encoding='utf-8', newline='\n')
        self.state = valid_state()

    def symlink(self, link, target):
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows не разрешает создание симлинков без соответствующего права")
            raise

    def invoke(self, *args):
        (self.run / 'state.json').write_text(json.dumps(self.state))
        return self.cli(*args)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(TOOL), '--at', 'verify', *args,
                               str(self.root), 'SCHED-101'], capture_output=True, text=True, encoding='utf-8')

    def test_valid_state_ignores_stale_markdown(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn('state-first', result.stdout)

    def test_corrupt_state_never_falls_back(self):
        (self.run / 'state.json').write_text('{')
        result = self.cli()
        self.assertEqual(1, result.returncode)
        self.assertIn('state-first', result.stderr)

    def test_canonical_state_cannot_be_read_outside_project(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'state.json'
            target.write_text(json.dumps(self.state))
            self.symlink(self.run / 'state.json', target)
            result = self.cli()
            self.assertEqual(1, result.returncode)
            self.assertIn('state.json выходит за корень', result.stderr)

    def test_mandatory_gates_cannot_be_moved_to_early_stage(self):
        self.state['gates'][0]['stage'] = 1
        result = self.invoke()
        self.assertEqual(1, result.returncode)
        self.assertIn('минимальные гейты', result.stderr)

    def test_missing_mismatched_or_escaping_evidence_rejected(self):
        ref = self.state['verification']['review']['claims'][0]['evidence'][0]
        for path in ('missing.py', '../outside', '/etc/passwd'):
            with self.subTest(path=path):
                ref['path'] = path
                self.assertEqual(1, self.invoke().returncode)
        ref['path'] = 'scheduler/core.py'
        self.state['gates'][0]['evidence'] = [{'path': 'scheduler/core.py', 'sha256': '0' * 64}]
        self.assertEqual(1, self.invoke().returncode)

    def test_artifact_relative_hash_and_symlink_escape(self):
        artifact = self.run / 'tests.txt'
        artifact.write_text('ok')
        self.state['gates'][0]['evidence'] = [{'path': 'artifact:tests.txt', 'sha256': hashlib.sha256(b'ok').hexdigest()}]
        self.assertEqual(0, self.invoke().returncode)
        artifact.unlink()
        self.symlink(artifact, self.root.parent / 'outside-evidence')
        self.assertEqual(1, self.invoke().returncode)

    def test_installed_bundle_without_repository_is_portable(self):
        import shutil
        bundle = self.root / 'installed/tools'
        bundle.mkdir(parents=True)
        shutil.copy2(TOOL, bundle / TOOL.name)
        for name in ('sdlc_common.py', 'sdlc-constants.json'):
            shutil.copy2(TOOL.parent / name, bundle / name)
        # Resolve canonical source from repository rather than rely on any generated copy.
        contract = Path(__file__).resolve().parents[1] / 'implementations/runner-contract'
        shutil.copy2(contract / 'tools/state_contract.py', bundle / 'state_contract.py')
        shutil.copytree(contract / 'schemas', bundle / 'state_contract/schemas')
        (self.run / 'state.json').write_text(json.dumps(self.state))
        result = subprocess.run([sys.executable, str(bundle / TOOL.name), '--at', 'verify',
                                 str(self.root), 'SCHED-101'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_state_print_action_and_unsupported_projection(self):
        result = self.invoke('--print', 'action')
        self.assertEqual(0, result.returncode)
        self.assertEqual('continue', result.stdout.strip())
        self.assertEqual(1, self.invoke('--print', 'approved').returncode)


if __name__ == '__main__':
    unittest.main()
