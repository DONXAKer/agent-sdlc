"""Проверки явных целей внутри каталога артефактов."""

import unittest
import test_snapshot_base as fixtures
from sdlc_common import artifact_targets
from sdlc_common import artifact_observed
import git_operation
from test_guard_and_review import hook


class ArtifactTargetsTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot
    write = fixtures.SnapshotTests.write

    def declare(self, kind):
        text = self.plan.read_text(encoding='utf-8').replace('snapshot', kind)
        self.plan.write_text(text + '| .sdlc/gates.md | изменить команду |\n', encoding='utf-8')

    def test_targets_and_neighbours_in_both_bases(self):
        for kind in ('git', 'snapshot'):
            with self.subTest(kind=kind):
                if kind == 'snapshot':
                    self.tearDown()
                    self.setUp()
                self.declare(kind)
                target = self.root / '.sdlc/gates.md'
                self.git('add', '-f', '.sdlc/gates.md')
                self.git('commit', '-qm', 'Сохранить набор')
                self.base = self.git('rev-parse', 'HEAD').stdout.strip()
                self.plan.write_text(self.plan.read_text(encoding='utf-8').replace(
                    self.git('rev-parse', 'HEAD~1').stdout.strip(), self.base), encoding='utf-8')
                if kind == 'snapshot':
                    self.snapshot()
                target.write_text('| Тесты | да | этап 6 | `git --version` |\n', encoding='utf-8')
                (self.root / '.sdlc/config').mkdir()
                neighbour = self.root / '.sdlc/config/neighbour.txt'
                neighbour.write_text('лишняя правка', encoding='utf-8')
                patch = self.write()
                raw = patch.read_text(encoding='utf-8')
                self.assertIn('a/.sdlc/gates.md', raw)
                self.assertIn('.sdlc/config/neighbour.txt', raw)
                self.assertNotIn('T-1/plan.md', raw)
                result = self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('neighbour.txt', result.stdout)
                neighbour.unlink()
                result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 2)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                patch = self.d / 'chunk-1-attempt-2-diff.patch'
                result = self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = self.tool('gates/scope.py', 'unchanged', self.root, 'T-1', patch)
                self.assertIn('app.py', result.stdout)
                self.assertNotIn('gates.md', result.stdout)

    def test_process_file_is_not_target(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8') +
                             '| .sdlc/T-1/plan.md | подмена |\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            artifact_targets(self.root, self.d)

    def test_target_change_invalidates_snapshot(self):
        self.declare('snapshot')
        self.snapshot()
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace(
            '.sdlc/gates.md', '.sdlc/other.md'), encoding='utf-8')
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Цели .sdlc изменились', result.stdout)

    def test_git_trees_include_target_and_detect_index_substitution(self):
        self.declare('git')
        self.git('add', '-f', '.sdlc/gates.md')
        admitted = git_operation.indexed_tree(self.root, self.d)
        self.assertIn('.sdlc/gates.md', admitted)
        (self.root / '.sdlc/gates.md').write_text('подмена', encoding='utf-8')
        self.git('add', '-f', '.sdlc/gates.md')
        self.assertNotEqual(admitted, git_operation.indexed_tree(self.root, self.d))
        self.git('commit', '-qm', 'Подмена цели')
        self.assertNotEqual(admitted, git_operation.committed_tree(self.root, 'HEAD', self.d))

    def test_review_lock_blocks_target_but_allows_report(self):
        self.declare('git')
        (self.d / '.review-lock').write_text('lock', encoding='utf-8')
        self.assertEqual(hook(self.root, 'Write', {'file_path': str(self.root / '.sdlc/gates.md')}), 'deny')
        self.assertEqual(hook(self.root, 'Write', {'file_path': str(self.d / 'review.md')}), 'allow')

    def test_process_markers_are_excluded_when_target_exists(self):
        self.declare('git')
        for name in ('.sdlc/.active', '.sdlc/OTHER/.scope.json', '.sdlc/OTHER/.review-lock',
                     '.sdlc/OTHER/git-operation-admission.json', '.sdlc/OTHER/plan.md'):
            self.assertFalse(artifact_observed(name, ['.sdlc/gates.md'], self.d), name)
        self.assertTrue(artifact_observed('.sdlc/config/extra.json', ['.sdlc/gates.md'], self.d))
        self.assertTrue(artifact_observed('.sdlc/.custom.json', ['.sdlc/gates.md'], self.d))
        self.assertTrue(artifact_observed('.sdlc/.config/value.json', ['.sdlc/gates.md'], self.d))


if __name__ == '__main__':
    unittest.main()
