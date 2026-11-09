"""Регрессии синхронизации и упаковки без изменения исходного checkout."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('distribution', ROOT / 'tools/distribution.py')
distribution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribution)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sdlc-distribution-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'проект с пробелами'
        self.root.mkdir()
        for name in ('templates', 'implementations'):
            shutil.copytree(ROOT / name, self.root / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.zip'))
        for name in ('sdlc-constants.json', 'SDLC.md', 'install.sh', 'junk-names.sh', 'distribution-files.json'):
            shutil.copy2(ROOT / name, self.root / name)
        distribution.sync(self.root)

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_check_is_read_only_and_sync_is_idempotent(self):
        before = self.snapshot()
        self.assertTrue(distribution.sync(self.root, check=True))
        distribution.sync(self.root)
        self.assertEqual(before, self.snapshot())

    def test_sources_propagate_without_changing_instructions(self):
        skill = self.root / distribution.CODEX / 'skills/sdlc-run/SKILL.md'
        original = skill.read_bytes()
        for name in ('templates/intent.template.md', 'SDLC.md',
                     'implementations/claude-code/skills/sdlc-verify/tools/sdlc_common.py',
                     'implementations/runner-contract/tools/state_contract.py'):
            source = self.root / name
            source.write_bytes(source.read_bytes() + b'\n')
        constants = self.root / 'sdlc-constants.json'
        constants.write_bytes(constants.read_bytes() + b'\n')
        before = self.snapshot()
        self.assertFalse(distribution.sync(self.root, check=True))
        self.assertEqual(before, self.snapshot())
        distribution.sync(self.root)
        self.assertTrue(distribution.sync(self.root, check=True))
        self.assertEqual(original, skill.read_bytes())

    def test_removed_source_removes_owned_copy_only(self):
        tool = Path('skills/sdlc-verify/tools/temporary-resource.py')
        (self.root / distribution.CLAUDE / tool).write_text('# Ресурс\n', encoding='utf-8')
        listing = json.loads((self.root / 'distribution-files.json').read_text(encoding='utf-8'))
        listing['shared_tools'].append((distribution.CLAUDE / tool).as_posix())
        listing['codex'].append((distribution.CODEX / tool).as_posix())
        (self.root / 'distribution-files.json').write_text(json.dumps(listing), encoding='utf-8')
        distribution.sync(self.root)
        (self.root / distribution.CLAUDE / tool).unlink()
        listing['shared_tools'].remove((distribution.CLAUDE / tool).as_posix())
        listing['codex'].remove((distribution.CODEX / tool).as_posix())
        (self.root / 'distribution-files.json').write_text(json.dumps(listing), encoding='utf-8')
        unrelated = self.root / distribution.CODEX / 'skills/sdlc-run/references/local.txt'
        unrelated.write_text('Локальные данные', encoding='utf-8')
        distribution.sync(self.root)
        self.assertFalse((self.root / distribution.CODEX / tool).exists())
        self.assertTrue(unrelated.exists())

    def test_archives_are_reproducible_and_codex_installs_without_checkout(self):
        archives = distribution.package(self.root, revision='test-revision')
        first = {p.name: p.read_bytes() for p in archives}
        distribution.package(self.root, revision='test-revision')
        self.assertEqual(first, {p.name: p.read_bytes() for p in archives})
        unpacked = Path(self.temp.name) / 'распаковано'
        with zipfile.ZipFile(archives[0]) as archive:
            self.assertIsNone(archive.testzip())
            archive.extractall(unpacked)
        install = unpacked / 'agent-sdlc-codex/install-codex.py'
        dest = Path(self.temp.name) / 'установка'
        subprocess.run([sys.executable, str(install), '--project', str(dest)], check=True)
        subprocess.run([sys.executable, str(install), '--project', str(dest), '--check'], check=True)
        subprocess.run([sys.executable, str(dest / '.agents/skills/sdlc-verify/tools/flow-verdict.py'),
                        '--metrics', str(dest)], check=True)

    def test_unowned_templates_survive_with_and_without_record(self):
        for variant in (distribution.CLAUDE, distribution.CODEX):
            for name in ('notes.txt', 'custom.template.md'):
                path = self.root / variant / 'skills/sdlc-verify/templates' / name
                path.write_bytes(b'private notes')
        before = {p: p.read_bytes() for p in self.root.glob('implementations/*/skills/sdlc-verify/templates/*') if p.name in ('notes.txt', 'custom.template.md')}
        distribution.sync(self.root)
        (self.root / distribution.RESOURCE_RECORD).unlink()
        distribution.sync(self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unlisted_files_never_enter_manifest_or_archives(self):
        for variant in (distribution.CLAUDE, distribution.CODEX):
            for name in ('.env', 'skills/sdlc-verify/templates/private.md', 'skills/sdlc-verify/tools/local.py'):
                path = self.root / variant / name
                path.write_bytes(b'LOCAL_SECRET_SENTINEL')
        distribution.sync(self.root)
        manifest = json.loads((self.root / distribution.CODEX / 'manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('.env', manifest['files'])
        for archive in distribution.package(self.root, revision='test-revision'):
            with zipfile.ZipFile(archive) as zipped:
                for name in zipped.namelist():
                    self.assertNotIn(b'LOCAL_SECRET_SENTINEL', zipped.read(name))

    def test_standalone_package_excludes_unlisted_files(self):
        base = self.root / distribution.CODEX
        (base / '.env').write_bytes(b'LOCAL_SECRET_SENTINEL')
        subprocess.run([sys.executable, str(base / 'package.py')], check=True)
        with zipfile.ZipFile(base.parent / 'agent-sdlc-codex.zip') as archive:
            self.assertNotIn('agent-sdlc-codex/.env', archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_crlf_sync_survives_lf_checkout(self):
        listing = distribution.inventory(self.root)
        for name in set(listing['codex'] + listing['claude-code']):
            path = self.root / name
            if path.exists():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        distribution.sync(self.root)
        for path in self.root.rglob('*'):
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        self.assertTrue(distribution.sync(self.root, check=True))
        subprocess.run([sys.executable, str(self.root / distribution.CODEX / 'test_package.py')], check=True)

    def test_windows_legacy_stdout_encoding(self):
        env = dict(os.environ, PYTHONUTF8='0', PYTHONIOENCODING='cp1252:strict')
        tools = self.root / 'tools'
        tools.mkdir()
        shutil.copy2(ROOT / 'tools/distribution.py', tools / 'distribution.py')
        commands = [
            [str(tools / 'distribution.py'), 'sync'],
            [str(tools / 'distribution.py'), 'check'],
            [str(self.root / distribution.CODEX / 'test_package.py')],
            [str(self.root / distribution.CODEX / 'package.py')],
        ]
        for args in commands:
            result = subprocess.run([sys.executable, *args], env=env,
                                    capture_output=True, encoding='utf-8', errors='strict')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('UnicodeEncodeError', result.stderr)

    def test_symlink_above_root_is_not_a_bundle_resource(self):
        # macOS размещает временные каталоги под системной ссылкой /var.
        ancestor = self.root.parent.parent
        original = Path.is_symlink
        def is_symlink(path):
            return path == ancestor or original(path)
        with patch.object(Path, 'is_symlink', is_symlink):
            self.assertTrue(distribution.sync(self.root, check=True))
            (self.root / 'SDLC.md').write_text('Изменён канон', encoding='utf-8')
            distribution.sync(self.root)
            distribution.package(self.root, revision='test-revision')

    def test_package_rejects_stale_resources(self):
        (self.root / 'SDLC.md').write_text('Изменено', encoding='utf-8')
        with self.assertRaises(ValueError):
            distribution.package(self.root, revision='test-revision')


if __name__ == '__main__':
    unittest.main()
