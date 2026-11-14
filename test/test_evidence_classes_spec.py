"""Канонические адреса evidence, классы дефектов и наблюдательные метрики."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test import test_snapshot_base as fixtures
sys.path.insert(0, str(fixtures.TOOLS))
import review_evidence
import snapshot_base
import sdlc_common

spec = importlib.util.spec_from_file_location('evidence_classes_flow', fixtures.TOOLS / 'flow-verdict.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class EvidencePathTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool

    def test_diff_only_review_does_not_require_primary_package(self):
        (self.d / 'chunk-1-attempt-1-diff.patch').write_bytes(
            b'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n')
        response = self.d / 'raw.md'
        response.write_bytes(json.dumps({'claims': [{'id': 'claim-1', 'status': 'passed',
            'evidence': [{'path': 'app.py', 'anchor': '1'}]}], 'findings': []}).encode('utf-8'))
        self.assertEqual(review_evidence.verify(self.root, self.d, 1, 1), set())
        result = self.tool('review-validate.py', self.root, 'T-1', 1, 1, response)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def primary(self, name='qa/result.json'):
        (self.d / 'chunk-1-attempt-1-diff.patch').write_bytes(
            b'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-answer=1\n+answer=2\n')
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"value":2}\r\n')
        manifest = {'files': [{'path': name, 'kind': 'observation', 'claims': ['claim-1'],
                              'source': 'Независимый помощник', 'sha256': snapshot_base.digest(path.read_bytes()),
                              'revision': review_evidence.candidate(self.root, self.d, 1, 1)}]}
        self.assertEqual(review_evidence.prepare(self.root, self.d, 1, 1, manifest), {name})
        return manifest

    def answer(self, proof):
        refs = [{'path': 'app.py', 'anchor': 'answer'}, {'path': proof, 'anchor': 'value'}]
        return {'claims': [{'id': 'claim-1', 'status': 'passed', 'evidence': refs}],
                'findings': [{'kind': 'uncovered_behavior', 'summary': 'Непокрытая ветка', 'evidence': refs}],
                'scope': [], 'invariants': [], 'regressions': [], 'retry_instruction': 'Добавить проверку ветки'}

    def test_context_before_first_reply_and_retained_rejection(self):
        manifest = self.primary()
        package, receipt = review_evidence.paths(self.d, 1, 1)
        original = package.read_bytes(), receipt.read_bytes()
        context = review_evidence.reviewer_context(self.root, self.d, 1, 1)
        item = context['files'][0]
        self.assertEqual(item['evidencePath'], 'qa/result.json')
        self.assertEqual(item['archiveMember'], 'files/qa/result.json')
        self.assertEqual(item['sha256'], manifest['files'][0]['sha256'])
        self.assertEqual(context['revision'], manifest['files'][0]['revision'])
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(archive.read(item['archiveMember']), (self.root / item['evidencePath']).read_bytes())
        response = self.d / 'raw.md'
        initial = self.answer(item['archiveMember'])
        raw = (json.dumps(initial, ensure_ascii=False, indent=2) + '\r\n').encode('utf-8')
        response.write_bytes(raw)
        rejected = self.tool('review-validate.py', self.root, 'T-1', 1, 1, response)
        self.assertEqual(rejected.returncode, 1)
        result = json.loads(rejected.stdout)
        retained = self.d / result['rejected_review_path']
        self.assertEqual(retained.read_bytes(), raw)
        corrected = self.answer(item['evidencePath'])
        response.write_bytes(json.dumps(corrected, ensure_ascii=False).encode('utf-8'))
        accepted = self.tool('review-validate.py', self.root, 'T-1', 1, 1, response)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        normalized = json.loads(accepted.stdout)['review']
        self.assertEqual(normalized['claims'][0]['status'], initial['claims'][0]['status'])
        self.assertEqual(normalized['findings'][0]['summary'], initial['findings'][0]['summary'])
        self.assertEqual(retained.read_bytes(), raw)
        self.assertEqual((package.read_bytes(), receipt.read_bytes()), original)

    def test_whitelist_context_tamper_revision_and_legacy_package(self):
        self.primary()
        saved = review_evidence.context_path(self.d, 1, 1)
        original = saved.read_bytes()
        changed = json.loads(original)
        changed['files'][0]['evidencePath'] = 'files/qa/result.json'
        saved.write_bytes(json.dumps(changed).encode('utf-8'))
        with self.assertRaisesRegex(ValueError, 'Карта адресов'):
            review_evidence.reviewer_context(self.root, self.d, 1, 1)
        saved.write_bytes(original)
        for proof in ('files/qa/result.json', '../qa/result.json', 'qa/undeclared.json'):
            response = self.d / 'raw.md'
            response.write_bytes(json.dumps(self.answer(proof)).encode('utf-8'))
            self.assertEqual(self.tool('review-validate.py', self.root, 'T-1', 1, 1, response).returncode, 1)
        package, receipt = review_evidence.paths(self.d, 1, 1)
        before = package.read_bytes(), receipt.read_bytes()
        saved.unlink()
        legacy_context = review_evidence.reviewer_context(self.root, self.d, 1, 1)
        self.assertEqual(legacy_context['files'][0]['evidencePath'], 'qa/result.json')
        self.assertFalse(saved.exists())
        self.assertEqual((package.read_bytes(), receipt.read_bytes()), before)
        patch = self.d / 'chunk-1-attempt-1-diff.patch'
        patch.write_bytes(patch.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            review_evidence.reviewer_context(self.root, self.d, 1, 1)

    def test_declared_files_prefix_is_a_literal_canonical_path(self):
        self.primary('files/qa/result.json')
        item = review_evidence.reviewer_context(self.root, self.d, 1, 1)['files'][0]
        self.assertEqual(item['evidencePath'], 'files/qa/result.json')
        self.assertEqual(item['archiveMember'], 'files/files/qa/result.json')
        response = self.d / 'raw.md'
        response.write_bytes(json.dumps(self.answer(item['evidencePath'])).encode('utf-8'))
        result = self.tool('review-validate.py', self.root, 'T-1', 1, 1, response)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_relative_root_named_context_and_explicit_context_flag(self):
        root = self.root / 'context'
        d = root / '.sdlc/T-1'
        d.mkdir(parents=True)
        (d / 'intent.md').write_text('| claim-1 | Ответ | Проверка |\n', encoding='utf-8')
        (d / 'chunk-1-attempt-1-diff.patch').write_bytes(b'diff placeholder')
        primary = root / 'qa/result.json'
        primary.parent.mkdir()
        primary.write_bytes(b'{"value":2}')
        manifest = self.root / 'manifest.json'
        manifest.write_bytes(json.dumps({'files': [{'path': 'qa/result.json', 'claims': ['claim-1'],
            'kind': 'observation', 'source': 'Помощник', 'sha256': snapshot_base.digest(primary.read_bytes()),
            'revision': review_evidence.candidate(root, d, 1, 1)}]}).encode('utf-8'))
        result = self.tool('review_evidence.py', 'context', 'T-1', 1, 1, manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mapped = self.tool('review_evidence.py', '--context', root, 'T-1', 1, 1)
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        self.assertEqual(json.loads(mapped.stdout)['files'][0]['evidencePath'], 'qa/result.json')


class DefectClassTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.sdlc').mkdir()

    def metrics(self, classes, gates=''):
        (self.root / '.sdlc/gates.md').write_text(gates, encoding='utf-8')
        before = {}
        for n, value in enumerate(classes):
            d = self.root / '.sdlc' / str(n)
            d.mkdir(exist_ok=True)
            (d / 'intent.md').write_text('Нейтральная задача', encoding='utf-8')
            path = d / 'handoff.md'
            path.write_text('- **Класс:** ' + value + '\n', encoding='utf-8')
            before[path] = path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.do_metrics(self.root), 0)
        self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
        return output.getvalue()

    def test_explicit_historical_alias_groups_without_rewriting(self):
        result = self.metrics(['пропуск приёмочного листа', 'пропуск приёмочного листа.'])
        self.assertIn('Класс»: 1 · повторившихся на ≥2 витках: 1', result)
        self.assertIn('ПОВТОР «пропуск приёмочного листа»', result)

    def test_project_punctuation_unknown_and_absence_remain_distinct(self):
        gates = '## Классы дефектов проекта\n- `канал` — 2026-01-01\n- `канал.` — 2026-01-01\n'
        result = self.metrics(['канал', 'канал.', 'неизвестный', 'н/п', 'нет', 'none', 'n/a', '-'], gates)
        self.assertIn('Класс»: 3 · повторившихся на ≥2 витках: 0', result)
        self.assertIn('Неизвестный класс дефекта «неизвестный»', result)

    def test_unknown_historical_punctuation_is_not_normalized(self):
        result = self.metrics(['новый', '_новый_', 'новый.', '**н/п**'])
        self.assertIn('Класс»: 3 · повторившихся на ≥2 витках: 0', result)
        self.assertIn('Неизвестный класс дефекта «_новый_»', result)

    def test_new_handoff_validates_canonical_project_and_unknown_classes(self):
        d = self.root / '.sdlc' / 'cycle'
        d.mkdir()
        gates = self.root / '.sdlc/gates.md'
        gates.write_text('## Классы дефектов проекта\n- `канал.` — 2026-01-01\n', encoding='utf-8')
        def block(name):
            return '\n'.join('- **' + key + ':** ' + value for key, value in {
                'Имя': 'Ошибка', 'Класс': name, 'Повтор': 'нет', 'Что проскочило': 'Непроверенная ветка',
                'Какой гейт должен был поймать и почему не поймал': 'Тесты — ветки не было',
                'Действие': 'конструкция', 'Кто утвердил': 'Оператор', 'Где реализовано': 'app.py:check',
                'Чем закреплено': 'test_check.py:test_branch'}.items())
        for name in ('пропуск приёмочного листа', 'пропуск приёмочного листа.', 'канал.'):
            self.assertEqual(flow.defect_record_errors(d, block(name)), [])
        self.assertTrue(any('Неизвестный класс' in error for error in flow.defect_record_errors(d, block('другой'))))
        gates.write_text('## Классы дефектов проекта\n- `пропуск приёмочного листа.` — 2026-01-01\n', encoding='utf-8')
        self.assertTrue(any('Неоднозначное' in error for error in flow.defect_record_errors(d, block('пропуск приёмочного листа'))))


if __name__ == '__main__':
    unittest.main()
