"""Регрессии issue №6: единая неизменяемая dirty-база и сохранение Git-режима."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'implementations/claude-code/skills/sdlc-verify/tools'
sys.path.insert(0, str(TOOLS))
import snapshot_base


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Тест')
        self.git('config', 'core.autocrlf', 'false')
        (self.root / 'app.py').write_bytes(b'answer = 1\r\n')
        (self.root / '.gitignore').write_text('*.log\n.sdlc/\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'Исходная фикстура')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()
        self.d = self.root / '.sdlc/T-1'
        self.d.mkdir(parents=True)
        self.plan = self.d / 'plan.md'
        self.plan.write_text(f'- **База:** {self.base}\n- **Вид базы:** snapshot\n\n'
                             '## files_to_touch\n| Путь | Что делаем |\n|---|---|\n| app.py | правка |\n',
                             encoding='utf-8')
        (self.root / '.sdlc/gates.md').write_text('| Тесты | да | этап 6 | `git --version` |', encoding='utf-8')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, check=True, capture_output=True,
                              text=True, encoding='utf-8')

    def tool(self, name, *args, env=None):
        return subprocess.run([sys.executable, str(TOOLS / name), *map(str, args)], cwd=self.root,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)

    def snapshot(self):
        result = self.tool('attempt-evidence.py', 'snapshot', self.root, 'T-1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write(self, k=1, extra=()):
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, k, *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.d / f'chunk-1-attempt-{k}-diff.patch'

    def test_dirty_layers_scope_untracked_and_crlf_roundtrip(self):
        (self.root / 'app.py').write_bytes(b'answer = 10\r\n')
        (self.root / 'foreign.txt').write_bytes(b'foreign\r\n')
        (self.root / 'ignored.log').write_bytes(b'noise')
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 11\r\n')
        patch = self.write()
        raw = patch.read_bytes()
        self.assertIn(b'-answer = 10\r\n+answer = 11\r\n', raw)
        self.assertNotIn(b'-answer = 1\r\n', raw)
        self.assertNotIn(b'foreign.txt', raw)
        self.assertNotIn(b'ignored.log', raw)
        for tool, args in [('attempt-evidence.py', ('verify', self.root, 'T-1', 1, 1)),
                           ('gates/scope.py', ('outside', self.root, 'T-1', patch)),
                           ('gates/untracked.py', (self.root, 'T-1'))]:
            result = self.tool(tool, *args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, files = snapshot_base.load(self.root, self.d)
        target = self.root / '.sdlc/apply'
        target.mkdir()
        snapshot_base.materialize(files, target)
        self.git('init', '-q', cwd=target)
        self.git('apply', '--binary', str(patch), cwd=target)
        self.assertEqual((target / 'app.py').read_bytes(), b'answer = 11\r\n')
        (self.root / 'foreign.txt').write_bytes(b'changed\r\n')
        patch = self.write(2)
        self.assertIn(b'foreign.txt', patch.read_bytes())
        self.assertEqual(self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch).returncode, 1)
        self.assertEqual(self.tool('gates/untracked.py', self.root, 'T-1').returncode, 1)

    def test_missing_or_modified_snapshot_rejects_verification(self):
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        self.write()
        manifest = self.d / 'base-snapshot/manifest.json'
        raw = manifest.read_bytes()
        manifest.write_bytes(raw + b' ')
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 1).returncode, 1)
        manifest.write_bytes(raw)
        data = json.loads(raw)
        blob = self.d / 'base-snapshot/blobs' / data['files']['app.py']['sha256']
        original = blob.read_bytes()
        blob.write_bytes(b'corrupted')
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 1).returncode, 1)
        blob.write_bytes(original)
        blob.unlink()
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 1).returncode, 1)

    def test_explicit_selection_and_history_cannot_be_replaced(self):
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot', 'git'), encoding='utf-8')
        self.assertEqual(self.tool('attempt-evidence.py', 'snapshot', self.root, 'T-1').returncode, 1)
        (self.root / 'app.py').write_bytes(b'answer = 10\r\n')
        patch = self.write()
        self.assertIn(b'-answer = 1\r\n', patch.read_bytes())
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('git\n', 'snapshot\n'), encoding='utf-8')
        self.assertEqual(self.tool('attempt-evidence.py', 'snapshot', self.root, 'T-1').returncode, 1)

    def test_attempts_keep_same_snapshot_and_previous_bytes(self):
        self.snapshot()
        (self.root / 'app.py').write_bytes(b'answer = 2\r\n')
        first = self.write()
        first_bytes = first.read_bytes()
        self.assertEqual(self.tool('attempt-evidence.py', 'snapshot', self.root, 'T-1').returncode, 1)
        (self.root / 'app.py').write_bytes(b'answer = 3\r\n')
        self.write(2)
        self.assertEqual(first.read_bytes(), first_bytes)
        records = [json.loads((self.d / f'chunk-1-attempt-{k}-evidence.json').read_text(encoding='utf-8')) for k in (1, 2)]
        self.assertEqual(records[0]['base'], records[1]['base'])
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('snapshot\n', 'git\n'), encoding='utf-8')
        self.assertEqual(self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 3).returncode, 1)
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 2).returncode, 1)
        patch = self.d / 'chunk-1-attempt-2-diff.patch'
        self.assertEqual(self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch).returncode, 1)
        self.assertEqual(self.tool('base-check.py', self.root, 'T-1', 1, 2).returncode, 1)
        self.assertEqual(self.tool('gates/untracked.py', self.root, 'T-1').returncode, 1)

    def test_binary_untracked_addition_and_deletion(self):
        (self.root / 'old.bin').write_bytes(b'\x00old\xff')
        self.snapshot()
        (self.root / 'old.bin').unlink()
        (self.root / 'new.bin').write_bytes(b'\x00new\xff')
        raw = self.write().read_bytes()
        self.assertIn(b'GIT binary patch', raw)
        self.assertIn(b'deleted file mode', raw)
        self.assertIn(b'new file mode', raw)
        self.assertEqual(self.tool('attempt-evidence.py', 'verify', self.root, 'T-1', 1, 1).returncode, 0)

    def test_flow_and_scope_reject_missing_or_replaced_base(self):
        self.snapshot()
        plan = self.plan.read_text(encoding='utf-8')
        pin = plan.split('- **Хэш снимка:**', 1)[1].strip()
        shutil.copytree(ROOT / 'test/flow-verdict-corpus/green/.sdlc/green', self.d, dirs_exist_ok=True)
        shutil.copyfile(ROOT / 'test/flow-verdict-corpus/green/.sdlc/gates.md', self.root / '.sdlc/gates.md')
        self.plan.write_text(self.plan.read_text(encoding='utf-8') +
                             f'\n- **Вид базы:** snapshot\n- **Хэш снимка:** {pin}\n', encoding='utf-8')
        result = self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = self.d / 'chunk-1-attempt-1-evidence.json'
        evidence.write_text(json.dumps({'base': {'kind': 'snapshot', 'id': '0' * 64}}), encoding='utf-8')
        self.assertEqual(self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1').returncode, 1)
        manifest = self.d / 'base-snapshot/manifest.json'
        manifest.unlink()
        self.assertEqual(self.tool('flow-verdict.py', '--at', 'verify', self.root, 'T-1').returncode, 1)
        patch = self.d / 'chunk-1-attempt-1-diff.patch'
        self.assertEqual(self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch).returncode, 1)
        self.assertEqual(self.tool('base-check.py', self.root, 'T-1', 1, 1).returncode, 1)

    def test_attributes_do_not_normalize_snapshot_bytes(self):
        (self.root / '.gitattributes').write_text('*.txt text eol=lf\n', encoding='utf-8')
        (self.root / 'data.txt').write_bytes(b'before\r\n')
        self.snapshot()
        (self.root / 'data.txt').write_bytes(b'after\r\n')
        patch = self.write().read_bytes()
        self.assertIn(b'-before\r\n+after\r\n', patch)

    def test_new_ignore_rule_keeps_original_untracked_bytes(self):
        foreign = self.root / 'foreign.txt'
        foreign.write_bytes(b'original\r\n')
        self.snapshot()
        ignore = self.root / '.gitignore'
        ignore.write_text(ignore.read_text(encoding='utf-8') + 'foreign.txt\n', encoding='utf-8')
        self.assertNotIn(b'diff --git a/foreign.txt', self.write().read_bytes())
        foreign.write_bytes(b'changed\r\n')
        self.assertIn(b'diff --git a/foreign.txt', self.write(2).read_bytes())
        foreign.unlink()
        self.assertIn(b'deleted file mode', self.write(3).read_bytes())

    def test_git_format_config_does_not_hide_outside_scope(self):
        self.snapshot()
        (self.root / 'outside.txt').write_text('outside\n', encoding='utf-8')
        env = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='diff.noprefix',
                   GIT_CONFIG_VALUE_0='true', GIT_CONFIG_KEY_1='color.ui', GIT_CONFIG_VALUE_1='always')
        result = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 1, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        patch = self.d / 'chunk-1-attempt-1-diff.patch'
        self.assertIn(b'diff --git a/outside.txt b/outside.txt', patch.read_bytes())
        self.assertEqual(self.tool('gates/scope.py', 'outside', self.root, 'T-1', patch).returncode, 1)

    def test_base_check_uses_dirty_snapshot_not_git_commit(self):
        tests = self.root / 'tests'
        tests.mkdir()
        script = tests / 'test_app.py'
        script.write_text('import unittest\nimport app\nclass Test(unittest.TestCase):\n'
                          ' def test_answer(self): self.assertEqual(app.answer, 10)\n', encoding='utf-8')
        (self.root / 'app.py').write_text('answer = 10\n', encoding='utf-8')
        self.snapshot()
        (self.root / 'app.py').write_text('answer = 11\n', encoding='utf-8')
        script.write_text(script.read_text(encoding='utf-8').replace('10)', '11)'), encoding='utf-8')
        command = f'"{Path(sys.executable).as_posix()}" -m unittest discover -s tests'
        self.write(extra=('--tests-cmd', command))
        result = self.tool('base-check.py', self.root, 'T-1', 1, 1, '--tests-cmd', command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        info = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"base_exit"'))
        self.assertEqual(info['base_exit'], 0)
        self.assertNotEqual(info['tests_on_base'], 0)


if __name__ == '__main__':
    unittest.main()
