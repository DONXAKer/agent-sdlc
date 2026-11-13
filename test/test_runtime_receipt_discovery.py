"""Отделяй квитанции первичных данных от свидетельств попыток."""
import json
import shutil
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test import test_snapshot_base as fixtures
from test import test_context_evidence as context_fixtures
ROOT = fixtures.ROOT
from test.evidence_fixtures import parser_receipt


class RuntimeReceiptDiscoveryTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot
    write = fixtures.SnapshotTests.write
    prepare = context_fixtures.ContextEvidenceTests.prepare

    def test_runtime_receipt_does_not_change_snapshot_base_in_flow(self):
        self.snapshot()
        pin = self.plan.read_text(encoding='utf-8').split('- **Хэш снимка:**', 1)[1].strip()
        shutil.copytree(ROOT / 'test/flow-verdict-corpus/green/.sdlc/green', self.d, dirs_exist_ok=True)
        shutil.copyfile(ROOT / 'test/flow-verdict-corpus/green/.sdlc/gates.md', self.root / '.sdlc/gates.md')
        self.plan.write_text(self.plan.read_text(encoding='utf-8') +
                             f'\n- **Вид базы:** snapshot\n- **Хэш снимка:** {pin}\n', encoding='utf-8')
        parser_receipt(self.d)
        (self.d / 'chunk-1-attempt-1-review-evidence.json').write_text(
            json.dumps({'version': 1, 'revision': {'head_sha': self.base, 'diff_sha256': '0' * 64}}), encoding='utf-8')
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.d / 'chunk-1-attempt-1-evidence.json').write_text(
            json.dumps({'base': {'kind': 'snapshot', 'id': '0' * 64}}), encoding='utf-8')
        self.assertEqual(self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1').returncode, 1)

    def test_runtime_receipt_does_not_prevent_next_attempt(self):
        self.snapshot()
        self.write()
        (self.d / 'chunk-1-attempt-1-review-evidence.json').write_text(
            json.dumps({'version': 1, 'revision': {'head_sha': self.base, 'diff_sha256': '0' * 64}}), encoding='utf-8')
        self.write(k=2)
        path = self.d / 'chunk-1-attempt-1-evidence.json'
        record = json.loads(path.read_bytes())
        record['base']['id'] = '0' * 64
        path.write_text(json.dumps(record), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 3)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('База отличается от истории попыток', result.stdout)

    def test_runtime_receipt_does_not_prevent_following_chunk(self):
        self.snapshot()
        prepared = self.prepare()
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.write()
        (self.d / 'chunk-1-attempt-1-review-evidence.json').write_text(
            json.dumps({'version': 1, 'revision': {'head_sha': self.base, 'diff_sha256': '0' * 64}}), encoding='utf-8')
        prepared = self.prepare(n=2)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)


if __name__ == '__main__':
    unittest.main()
