"""Регрессии метрик, одобрения, размера, журнала, контура и стоимости snapshot diff."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test import test_snapshot_base as fixtures
from test.evidence_fixtures import parser_receipt
import snapshot_base
import chunk_preconditions
from test.snapshot_diff_reference import full_snapshot_diff

spec = importlib.util.spec_from_file_location('issue_flow', fixtures.TOOLS / 'flow-verdict.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class ParserRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_metrics_ignore_absence_and_count_distinct_cycles(self):
        def metrics(classes):
            for n, value in enumerate(classes):
                d = self.root / '.sdlc' / str(n)
                d.mkdir(parents=True, exist_ok=True)
                (d / 'intent.md').write_text('Задача', encoding='utf-8')
                (d / 'handoff.md').write_text(value, encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(flow.do_metrics(self.root), 0)
            return output.getvalue()
        absent = '- **Имя:** нет\n- **Класс:** н/п\n'
        real = '- **Имя:** Ошибка\n- **Класс:** неверная граница\n'
        self.assertIn('Класс»: 0 · повторившихся на ≥2 витках: 0', metrics([absent, absent]))
        self.assertIn('Класс»: 1 · повторившихся на ≥2 витках: 1', metrics([absent + real, real]))
        result = metrics([real + real, absent])
        self.assertIn('повторившихся на ≥2 витках: 0', result)
        self.assertNotIn('ПОВТОР «н/п»', result)

    def test_delegated_approval_and_negative_controls(self):
        prefix = '- **Одобрение:** '
        positive = ('Пользователь поручил вести цель через $sdlc-run, задача задаёт scope, '
                    'техническая приёмка делегирована; отдельное повторное согласование не требуется.')
        self.assertTrue(flow.plan_approved(prefix + positive))
        self.assertTrue(flow.plan_approved(prefix + 'Пользователь делегировал выполнение текущего плана через AGENTS.md.'))
        for value in ('', 'не одобрен — вернуться после 2026-11-10',
                      'ожидается решение к 2026-11-10', 'делегировано, основание неизвестно',
                      'Пользователь не поручил работу через $sdlc-run',
                      'Пользователь поручил изучить AGENTS.md.',
                      'Пользователь разрешил только чтение AGENTS.md.',
                      'Пользователь поручил выполнить аудит AGENTS.md.',
                      'Пользователь поручил выполнить анализ AGENTS.md.',
                      positive + ' Ожидается решение человека.',
                      'Пользователь поручил работу через $sdlc-run?'):
            self.assertFalse(flow.plan_approved(prefix + value), value)

    def test_size_marker_case_and_limits_with_missing_reason(self):
        source = ROOT / 'test/flow-verdict-corpus/chunk-oversize'
        shutil.copytree(source, self.root, dirs_exist_ok=True)
        slug = 'chunk-oversize'
        d = self.root / '.sdlc' / slug
        plan = d / 'plan.md'
        original = plan.read_text(encoding='utf-8')
        for marker in ('почему этот chunk больше ориентира', 'Почему этот chunk больше ориентира', None):
            plan.write_text(original + ('\n' + marker + ': связанные изменения.\n' if marker else ''), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                flow.main(self.root, slug, 'verify')
            self.assertEqual('размер chunk-1 в ориентире или объяснён**' in output.getvalue(), marker is None)
            self.assertNotIn('размер chunk-2 в ориентире или объяснён**', output.getvalue())

    def test_small_scope_uses_union_and_keeps_historical_deviation(self):
        d = self.root / '.sdlc/T-1'
        d.mkdir(parents=True)
        def write(paths, attempt=1):
            (d / f'chunk-1-attempt-{attempt}-diff.patch').write_text(''.join(
                f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-a\n+b\n' for p in paths), encoding='utf-8')
        small = '**Контур:** мелкий'
        write(['app.py'])
        self.assertEqual(flow.small_scope_errors(d, small), [])
        write(['README.md', 'config.toml'])
        self.assertEqual(flow.small_scope_errors(d, small), [])
        write(['app.py', 'test_app.py', 'README.md'])
        self.assertTrue(flow.small_scope_errors(d, small))
        self.assertEqual(flow.small_scope_errors(d, '**Контур:** полный'), [])
        (d / '.chunk-1-preconditions.json').write_text(json.dumps({'contour': 'мелкий'}), encoding='utf-8')
        self.assertTrue(flow.small_scope_errors(d, '**Контур:** полный'))
        write(['app.py'])
        write(['test_app.py'], 2)
        self.assertTrue(flow.small_scope_errors(d, small))

    def test_approval_does_not_hide_intent_placeholder(self):
        source = ROOT / 'test/flow-verdict-corpus/green'
        shutil.copytree(source, self.root, dirs_exist_ok=True)
        d = self.root / '.sdlc/green'
        plan = d / 'plan.md'
        plan.write_text(re.sub(r'(?m)^.*\*\*Одобрение:\*\*.*$',
                              '- **Одобрение:** Пользователь поручил вести цель через $sdlc-run; '
                              'отдельное повторное согласование не требуется.', plan.read_text(encoding='utf-8')),
                        encoding='utf-8')
        parser_receipt(d)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.main(self.root, 'green', 'verify'), 0)
        intent = d / 'intent.md'
        intent.write_text(intent.read_text(encoding='utf-8') + '\nПробел: ‹уточнить›\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.main(self.root, 'green', 'verify'), 1)
        self.assertIn('нет ‹…› в intent.md**', output.getvalue())
        self.assertNotIn('одобрение плана записано**', output.getvalue())

    def test_mode_binary_and_rename_are_paths_of_small_scope(self):
        d = self.root / '.sdlc/T-1'
        d.mkdir(parents=True)
        small = '**Контур:** мелкий'
        bodies = [
            'diff --git a/tool.py b/tool.py\nold mode 100644\nnew mode 100755\n',
            'diff --git a/data.bin b/data.bin\nGIT binary patch\nliteral 1\nA\n',
            'diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n',
        ]
        for body in bodies:
            with self.subTest(body=body):
                (d / 'chunk-1-attempt-1-diff.patch').write_text(body +
                    'diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n', encoding='utf-8')
                self.assertTrue(flow.small_scope_errors(d, small))
        self.assertEqual(chunk_preconditions.contour('**Контур:** мелкий / полный'), 'полный')

    def test_contour_parser_is_shared_with_claim_structure(self):
        shutil.copytree(ROOT / 'test/flow-verdict-corpus/green', self.root, dirs_exist_ok=True)
        d = self.root / '.sdlc/green'
        intent = d / 'intent.md'
        text = re.sub(r'(?m)^\|\s*claim-[23]\b[^\n]*\n?', '', intent.read_text(encoding='utf-8'))
        intent.write_text(text + '\n**Контур:** мелкий / полный\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.main(self.root, 'green', 'verify'), 1)
        self.assertIn('полный контур: ≥3 пунктов, ≥2 [edge]**', output.getvalue())

    def test_timely_switch_to_full_keeps_earlier_small_scope_valid(self):
        d = self.root / '.sdlc/T-1'
        d.mkdir(parents=True)
        (d / '.chunk-1-preconditions.json').write_text(json.dumps({'contour': 'мелкий'}), encoding='utf-8')
        (d / '.chunk-2-preconditions.json').write_text(json.dumps({'contour': 'полный'}), encoding='utf-8')
        first = 'diff --git a/app.py b/app.py\nold mode 100644\nnew mode 100755\n'
        second = first + 'diff --git a/test_app.py b/test_app.py\nnew file mode 100644\n'
        (d / 'chunk-1-attempt-1-diff.patch').write_text(first, encoding='utf-8')
        (d / 'chunk-2-attempt-1-diff.patch').write_text(second, encoding='utf-8')
        self.assertEqual(flow.small_scope_errors(d, '**Контур:** полный'), [])
        (d / 'chunk-1-attempt-1-diff.patch').write_text(second, encoding='utf-8')
        self.assertTrue(flow.small_scope_errors(d, '**Контур:** полный'))

    def test_unknown_configuration_requires_review_bound_to_patch(self):
        d = self.root / '.sdlc/T-1'
        d.mkdir(parents=True)
        small = '**Контур:** мелкий'
        patch_path = d / 'chunk-1-attempt-1-diff.patch'
        def write(paths):
            patch_path.write_text(''.join(f'diff --git a/{p} b/{p}\nold mode 100644\nnew mode 100755\n' for p in paths), encoding='utf-8')
        write(['settings.properties', 'local.properties', 'settings.xml', '.env.local'])
        self.assertEqual(flow.small_scope_errors(d, small), [])
        write(['settings.custom', 'README.md'])
        self.assertTrue(flow.small_scope_errors(d, small))
        review = d / 'chunk-1-attempt-1-review.md'
        review.write_text(f'**SHA-256 патча классификации:** {snapshot_base.digest(patch_path.read_bytes())}\n'
                          '**Классификацию подтвердил:** Рецензент\n**Источник классификации:** независимый рецензент\n'
                          '| settings.custom | конфигурация | Пары ключей и значений; исполняемых команд нет. |\n', encoding='utf-8')
        self.assertEqual(flow.small_scope_errors(d, small), [])
        accepted = review.read_text(encoding='utf-8')
        for signer in ('', 'не подтверждено', 'нет', 'н/п', 'ожидается решение', '‹имя›'):
            review.write_text(accepted.replace('подтвердил:** Рецензент', 'подтвердил:** ' + signer), encoding='utf-8')
            self.assertTrue(flow.small_scope_errors(d, small), signer)
        review.write_text(accepted, encoding='utf-8')
        write(['settings.custom', 'README.md', 'app.py'])
        self.assertTrue(flow.small_scope_errors(d, small))
        write(['docs/setup.html', 'docs/usage.html'])
        review.write_text(f'**SHA-256 патча классификации:** {snapshot_base.digest(patch_path.read_bytes())}\n'
                          '**Классификацию подтвердил:** Рецензент\n**Источник классификации:** независимый рецензент\n'
                          '| docs/setup.html | документация | Статическая справка без скриптов. |\n'
                          '| docs/usage.html | документация | Статическая справка без скриптов. |\n', encoding='utf-8')
        self.assertEqual(flow.small_scope_errors(d, small), [])


class JournalRegressions(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot
    write = fixtures.SnapshotTests.write

    def test_missing_late_and_early_journal(self):
        self.snapshot()
        (self.d / 'locator.md').write_text('Точка правки: app.py.\n', encoding='utf-8')
        args = ('prepare', self.root, 'T-1', 1, '--locator', 'locator.md')
        self.assertEqual(self.tool('attempt-evidence.py', *args).returncode, 1)
        self.assertFalse(chunk_preconditions.receipt_path(self.d, 1).exists())
        journal = self.d / 'chunk-1-journal.md'
        journal.write_text('**Подтвердил:** Оператор\n', encoding='utf-8')
        result = self.tool('attempt-evidence.py', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        record = json.loads((self.d / 'chunk-1-attempt-1-evidence.json').read_text(encoding='utf-8'))
        journal.write_text(journal.read_text(encoding='utf-8') + '\n| 1 | Сегодня | Проверено |\n', encoding='utf-8')
        os.utime(journal, (1, 1))
        self.assertEqual(chunk_preconditions.errors(self.root, self.d, 1, record['preconditions']['receipt_sha256']), [])
        archived = self.d / '.chunk-1-initial-journal.md'
        archived.write_bytes(archived.read_bytes() + b'changed')
        self.assertTrue(chunk_preconditions.errors(self.root, self.d, 1))

    def test_late_journal_cannot_repair_old_receipt_or_attempt(self):
        self.snapshot()
        self.write()
        journal = self.d / 'chunk-1-journal.md'
        journal.write_text('Отклонение: журнал создан после попытки.\n', encoding='utf-8')
        (self.d / 'locator.md').write_text('Точка правки: app.py.\n', encoding='utf-8')
        self.assertEqual(self.tool('attempt-evidence.py', 'prepare', self.root, 'T-1', 1,
                                   '--locator', 'locator.md').returncode, 1)
        parser_receipt(self.d)
        receipt = chunk_preconditions.receipt_path(self.d, 1)
        rec = json.loads(receipt.read_text(encoding='utf-8'))
        del rec['journal']
        receipt.write_text(json.dumps(rec), encoding='utf-8')
        self.assertIn('порядок его создания не доказан', '; '.join(chunk_preconditions.errors(self.root, self.d, 1)))
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('порядок его создания не доказан', result.stdout)
        self.assertIn('Отклонение:', journal.read_text(encoding='utf-8'))


class SparseSnapshotRegressions(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot

    def test_patch_equal_and_only_changed_paths_materialized(self):
        (self.root / 'old.bin').write_bytes(b'\x00old\xff')
        (self.root / 'keep.bin').write_bytes(b'\x00keep\xff' * 1000)
        self.snapshot()
        _, files = snapshot_base.load(self.root, self.d)
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        (self.root / 'old.bin').unlink()
        (self.root / 'new.bin').write_bytes(b'\x00new\xff')
        expected = full_snapshot_diff(self.root, files)
        calls = []
        original = snapshot_base.materialize
        def counted(records, destination):
            calls.append(set(records))
            original(records, destination)
        with patch.object(snapshot_base, 'materialize', side_effect=counted):
            actual = snapshot_base.snapshot_diff(self.root, files)
        self.assertEqual(actual.encode('utf-8', errors='surrogateescape'), expected.encode('utf-8', errors='surrogateescape'))
        self.assertEqual(calls, [{'app.py', 'old.bin'}, {'app.py', 'new.bin'}])
        self.assertIn('GIT binary patch', actual)

    def test_unchanged_tree_does_not_materialize_files(self):
        self.snapshot()
        _, files = snapshot_base.load(self.root, self.d)
        with patch.object(snapshot_base, 'materialize', wraps=snapshot_base.materialize) as materialize:
            self.assertEqual(snapshot_base.snapshot_diff(self.root, files), '')
        self.assertTrue(all(not call.args[0] for call in materialize.call_args_list))

    def test_unchanged_attributes_preserve_binary_and_nested_diff(self):
        (self.root / '.gitattributes').write_text('*.txt -diff\n', encoding='utf-8')
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / '.gitattributes').write_text('*.txt diff\n', encoding='utf-8')
        for path in (self.root / 'data.txt', nested / 'data.txt'):
            path.write_bytes(b'before\r\n')
        self.snapshot()
        _, files = snapshot_base.load(self.root, self.d)
        for path in (self.root / 'data.txt', nested / 'data.txt'):
            path.write_bytes(b'after\r\n')
        expected = full_snapshot_diff(self.root, files)
        with patch.object(snapshot_base, 'materialize', wraps=snapshot_base.materialize) as materialize:
            actual = snapshot_base.snapshot_diff(self.root, files)
        self.assertEqual(actual, expected)
        self.assertIn('GIT binary patch', actual)
        self.assertIn('-before\r\n+after\r\n', actual)
        self.assertTrue(all('.gitattributes' not in call.args[0] and 'nested/.gitattributes' not in call.args[0]
                            for call in materialize.call_args_list))

    def test_unchanged_blob_keeps_abbreviated_git_hash_unique(self):
        (self.root / 'keep.bin').write_bytes(b'20738')
        (self.root / 'data.txt').write_bytes(b'old')
        self.snapshot()
        _, files = snapshot_base.load(self.root, self.d)
        (self.root / 'data.txt').write_bytes(b'37901')
        actual = snapshot_base.snapshot_diff(self.root, files)
        self.assertEqual(actual, full_snapshot_diff(self.root, files))
        self.assertIn('index 489ce0f..65ba8cac', actual)

    def test_unchanged_blob_still_validated(self):
        (self.root / 'keep.bin').write_bytes(b'unchanged')
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        manifest = json.loads((self.d / 'base-snapshot/manifest.json').read_bytes())
        blob = self.d / 'base-snapshot/blobs' / manifest['files']['keep.bin']['sha256']
        blob.write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'Байты снимка изменены: keep.bin'):
            snapshot_base.load(self.root, self.d)

    @unittest.skipIf(os.name == 'nt', 'Windows не сохраняет executable bit; требуется POSIX')
    def test_mode_only_patch_equals_full_algorithm(self):
        self.snapshot()
        _, files = snapshot_base.load(self.root, self.d)
        (self.root / 'app.py').chmod(0o755)
        actual = snapshot_base.snapshot_diff(self.root, files)
        self.assertEqual(actual, full_snapshot_diff(self.root, files))
        self.assertIn('old mode 100644\nnew mode 100755', actual)


if __name__ == '__main__':
    unittest.main()
