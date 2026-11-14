"""Регрессии пути подтверждённого ревью и обязательного входа гейта секретов."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get('SDLC_TEST_TOOLS', ROOT / 'implementations/claude-code/skills/sdlc-verify/tools'))
sys.path.insert(0, str(TOOLS))
import review_evidence
import snapshot_base
from sdlc_common import diffstat


def run(script, *args, cwd=None):
    return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, encoding='utf-8')


class ReviewPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        shutil.copytree(ROOT / 'test/flow-verdict-corpus/handoff-green', self.root, dirs_exist_ok=True)
        self.slug = 'handoff-green'
        self.d = self.root / '.sdlc' / self.slug
        (self.d / 'chunk-1-attempt-1-diff.patch').write_bytes(
            b'diff --git a/example.txt b/example.txt\n--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-before\n+after\n')
        for args in (('init', '-q'), ('config', 'user.email', 'fixture@example.invalid'),
                     ('config', 'user.name', 'Фикстура'), ('add', '.'), ('commit', '-qm', 'Исходная фикстура')):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        primary = self.root / 'qa/primary.json'
        primary.parent.mkdir()
        primary.write_bytes(b'{"value":7}')
        review_evidence.prepare(self.root, self.d, 1, 1, {'files': [{
            'path': 'qa/primary.json', 'claims': ['claim-1'], 'kind': 'observation',
            'source': 'Контрольный помощник', 'sha256': snapshot_base.digest(primary.read_bytes()),
            'revision': review_evidence.candidate(self.root, self.d, 1, 1)}]})
        self.receipt = self.d / '.review-validation-1-attempt-1.json'
        self.canonical = self.d / 'chunk-1-attempt-1-review.md'

    def answer(self, newline='\n'):
        paths = diffstat((self.d / 'chunk-1-attempt-1-diff.patch').read_text(encoding='utf-8'))['paths']
        evidence = [{'path': paths[0], 'anchor': '1'}, {'path': 'qa/primary.json', 'anchor': 'value'}]
        value = {'claims': [{'id': f'claim-{n}', 'status': 'passed', 'evidence': evidence} for n in (1, 2, 3)],
                 'findings': [], 'scope': [], 'invariants': [], 'regressions': [], 'retry_instruction': ''}
        return ('Сырой ответ.\n```json\n' + json.dumps(value, ensure_ascii=False, indent=2) + '\n```\n').replace('\n', newline).encode('utf-8')

    def validate(self, source, cwd=None):
        return run(TOOLS / 'review-validate.py', self.root, self.slug, 1, 1, source, cwd=cwd)

    def handoff(self):
        return run(TOOLS / 'flow-verdict.py', '--at', 'handoff', self.root, self.slug)

    def test_absolute_custom_crlf_answer_passes_validate_and_handoff(self):
        source = self.root / 'сырой ответ.md'
        raw = self.answer('\r\n')
        source.write_bytes(raw)
        validated = self.validate(source)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertEqual(self.canonical.read_bytes(), raw)
        self.assertEqual(source.read_bytes(), raw)
        receipt = json.loads(self.receipt.read_bytes())
        self.assertEqual(receipt['review_path'], self.canonical.name)
        self.assertEqual(receipt['review_sha256'], snapshot_base.digest(raw))
        result = self.handoff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        original = self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns, self.canonical.stat().st_mtime_ns
        self.assertEqual(self.validate(source).returncode, 0)
        self.assertEqual((self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns, self.canonical.stat().st_mtime_ns), original)

    def test_relative_lf_answer_from_cwd_and_project_root(self):
        source = self.root / 'answers' / 'response.md'
        source.parent.mkdir()
        source.write_bytes(self.answer())
        validated = self.validate(source.name, cwd=source.parent)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertEqual(self.canonical.read_bytes(), source.read_bytes())
        repeated = self.validate('answers/response.md', cwd=ROOT)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        result = self.handoff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_changed_bytes_source_path_missing_answer_and_receipt_path_fail(self):
        source = self.root / 'custom.md'
        raw = self.answer()
        source.write_bytes(raw)
        self.assertEqual(self.validate(source).returncode, 0)
        receipt_bytes = self.receipt.read_bytes()
        alternate = self.root / 'alternate.md'
        alternate.write_bytes(raw)
        self.assertEqual(self.validate(alternate).returncode, 1)
        source.write_bytes(raw + b'\n')
        self.assertEqual(self.validate(source).returncode, 1)
        self.assertEqual(self.canonical.read_bytes(), raw)
        self.assertEqual(self.receipt.read_bytes(), receipt_bytes)
        source.unlink()
        self.assertEqual(self.validate(source).returncode, 1)
        record = json.loads(receipt_bytes)
        record['review_path'] = '../alternate.md'
        self.receipt.write_bytes(json.dumps(record).encode('utf-8'))
        result = self.handoff()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Путь подтверждённого ответа', result.stdout)
        self.receipt.write_bytes(receipt_bytes)
        self.canonical.unlink()
        result = self.handoff()
        self.assertEqual(result.returncode, 1)
        self.assertIn('История подтверждённого ревью повреждена', result.stdout)

    def test_legacy_receipt_keeps_canonical_path_and_original_bytes(self):
        self.canonical.write_bytes(self.answer())
        self.assertEqual(self.validate(self.canonical).returncode, 0)
        record = json.loads(self.receipt.read_bytes())
        record['version'] = 1
        record.pop('review_path')
        record.pop('source_review_path')
        self.receipt.write_bytes(json.dumps(record).encode('utf-8'))
        original = self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns
        self.assertEqual(self.validate(self.canonical).returncode, 0)
        self.assertEqual((self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns), original)
        result = self.handoff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        patch_path = self.d / 'chunk-1-attempt-1-diff.patch'
        patch_path.write_bytes(patch_path.read_bytes() + b'\n')
        self.assertEqual(self.validate(self.canonical).returncode, 1)
        self.assertEqual(self.handoff().returncode, 1)


class SecretsInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_distributed_gates_missing_directory_clean_empty_and_secret(self):
        variants = ('claude-code', 'codex', 'cursor', 'kimi-code', 'opencode')
        clean = self.root / 'clean.patch'
        clean.write_bytes(b'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+value=7\n')
        empty = self.root / 'empty.patch'
        empty.write_bytes(b'')
        secret = self.root / 'secret.patch'
        secret.write_bytes(clean.read_bytes().replace(b'+value=7', b'+password="synthetic-fixture-value"'))  # sdlc:not-a-secret
        for variant in variants:
            script = ROOT / f'implementations/{variant}/skills/sdlc-verify/tools/gates/secrets.py'
            if os.environ.get('SDLC_TEST_TOOLS'):
                script = TOOLS / 'gates/secrets.py'
            for path, code, status in ((self.root / 'missing.patch', 1, 'fail'), (self.root, 1, 'fail'),
                                        (clean, 0, 'pass'), (empty, 0, 'pass'), (secret, 1, 'fail')):
                with self.subTest(variant=variant, path=path.name):
                    result = run(script, path)
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    record = json.loads(result.stdout.splitlines()[-1])
                    self.assertEqual(record['status'], status)
                    self.assertEqual(record['gate'], 'Секреты в diff')
                    self.assertIsNone(record['missing_tool'])

    def test_permission_error_is_structured_failure_without_content(self):
        spec = importlib.util.spec_from_file_location('secrets_input_gate', TOOLS / 'gates/secrets.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import contextlib
        import io
        output = io.StringIO()
        with patch.object(sys, 'argv', ['secrets.py', 'denied.patch']), patch.object(Path, 'read_bytes', side_effect=PermissionError('Содержимое нельзя раскрывать')):
            with contextlib.redirect_stdout(output):
                code = module.main()
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('PermissionError', result['detail'])
        self.assertNotIn('Содержимое нельзя раскрывать', output.getvalue())


if __name__ == '__main__':
    unittest.main()
