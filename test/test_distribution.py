"""Регрессии синхронизации и упаковки без изменения исходного checkout."""
import importlib.util
import fnmatch
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
    def test_automation_includes_generated_cursor_changes(self):
        before = self.snapshot()
        source = self.root / 'SDLC.md'
        source.write_bytes(source.read_bytes() + b'\n')
        distribution.sync(self.root)
        changes = [path.as_posix() for path, data in self.snapshot().items()
                   if before.get(path) != data and path.is_relative_to(distribution.CURSOR)]
        workflow = (ROOT / '.github/workflows/distribution.yml').read_text(encoding='utf-8')
        patterns = [line.strip() for line in workflow.split('add-paths: |', 1)[1].split('  release:', 1)[0].splitlines()]
        self.assertTrue(changes)
        for name in changes:
            self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns), name)

    def test_automation_includes_generated_kimi_code_changes(self):
        before = self.snapshot()
        source = self.root / 'SDLC.md'
        source.write_bytes(source.read_bytes() + b'\n')
        distribution.sync(self.root)
        changes = [path.as_posix() for path, data in self.snapshot().items()
                   if before.get(path) != data and path.is_relative_to(distribution.KIMI)]
        workflow = (ROOT / '.github/workflows/distribution.yml').read_text(encoding='utf-8')
        patterns = [line.strip() for line in workflow.split('add-paths: |', 1)[1].split('  release:', 1)[0].splitlines()]
        self.assertTrue(changes)
        for name in changes:
            self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns), name)

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

    def test_kimi_code_archive_installs_without_checkout(self):
        archives = {path.name: path for path in distribution.package(self.root, revision='test-revision')}
        unpacked = Path(self.temp.name) / 'kimi-распаковано'
        with zipfile.ZipFile(archives['agent-sdlc-kimi-code.zip']) as archive:
            self.assertIsNone(archive.testzip())
            archive.extractall(unpacked)
        install = unpacked / 'agent-sdlc-kimi-code/install-kimi-code.py'
        dest = Path(self.temp.name) / 'kimi-установка'
        subprocess.run([sys.executable, str(install), '--project', str(dest)], check=True)
        subprocess.run([sys.executable, str(install), '--project', str(dest), '--check'], check=True)
        subprocess.run([sys.executable, str(dest / '.kimi-code/skills/sdlc-verify/tools/flow-verdict.py'),
                        '--metrics', str(dest)], check=True)

    def test_unowned_templates_survive_with_and_without_record(self):
        for variant in (distribution.CLAUDE, distribution.CODEX, distribution.KIMI):
            for name in ('notes.txt', 'custom.template.md'):
                path = self.root / variant / 'skills/sdlc-verify/templates' / name
                path.write_bytes(b'private notes')
        before = {p: p.read_bytes() for p in self.root.glob('implementations/*/skills/sdlc-verify/templates/*') if p.name in ('notes.txt', 'custom.template.md')}
        distribution.sync(self.root)
        (self.root / distribution.RESOURCE_RECORD).unlink()
        distribution.sync(self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unlisted_files_never_enter_manifest_or_archives(self):
        for variant in (distribution.CLAUDE, distribution.CODEX, distribution.KIMI):
            for name in ('.env', 'skills/sdlc-verify/templates/private.md', 'skills/sdlc-verify/tools/local.py'):
                path = self.root / variant / name
                path.write_bytes(b'LOCAL_SECRET_SENTINEL')
        distribution.sync(self.root)
        for variant in (distribution.CODEX, distribution.KIMI):
            manifest = json.loads((self.root / variant / 'manifest.json').read_text(encoding='utf-8'))
            self.assertNotIn('.env', manifest['files'])
        for archive in distribution.package(self.root, revision='test-revision'):
            with zipfile.ZipFile(archive) as zipped:
                for name in zipped.namelist():
                    self.assertNotIn(b'LOCAL_SECRET_SENTINEL', zipped.read(name))

    def test_standalone_package_excludes_unlisted_files(self):
        for variant, archive_name in ((distribution.CODEX, 'agent-sdlc-codex.zip'),
                                      (distribution.KIMI, 'agent-sdlc-kimi-code.zip')):
            base = self.root / variant
            (base / '.env').write_bytes(b'LOCAL_SECRET_SENTINEL')
            subprocess.run([sys.executable, str(base / 'package.py')], check=True)
            with zipfile.ZipFile(base.parent / archive_name) as archive:
                self.assertNotIn(archive_name[:-4] + '/.env', archive.namelist())
                self.assertIsNone(archive.testzip())

    def test_crlf_sync_survives_lf_checkout(self):
        listing = distribution.inventory(self.root)
        for name in set(listing['codex'] + listing['claude-code'] + listing['kimi-code']):
            path = self.root / name
            if path.exists():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        distribution.sync(self.root)
        for name in listing['kimi-code']:
            path = self.root / name
            if path.exists():
                self.assertNotIn(b'\r\n', path.read_bytes(), name)
        for path in self.root.rglob('*'):
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        self.assertTrue(distribution.sync(self.root, check=True))
        subprocess.run([sys.executable, str(self.root / distribution.CODEX / 'test_package.py')], check=True)
        subprocess.run([sys.executable, str(self.root / distribution.KIMI / 'test_package.py')], check=True)

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
            [str(self.root / distribution.KIMI / 'test_package.py')],
            [str(self.root / distribution.KIMI / 'package.py')],
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

    def install(self, base, dest):
        return subprocess.run([sys.executable, str(base / 'install-codex.py'), '--project', str(dest)],
                              capture_output=True, encoding='utf-8')

    def directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
            if result.returncode:
                self.skipTest('Среда не разрешает ссылки каталогов')

    def test_install_uses_manifest_not_directory_contents(self):
        base = self.root / distribution.CODEX
        extra = base / 'skills/sdlc-run/local.py'
        extra.write_bytes(b'UNLISTED_FILE')
        (base / 'skills/sdlc-run/.env').write_bytes(b'UNLISTED_SECRET')
        dest = Path(self.temp.name) / 'target'
        result = self.install(base, dest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((dest / '.codex/agents').glob('sdlc-*.toml'))), 4)
        self.assertEqual(self.install(base, dest).returncode, 0)
        self.assertFalse((dest / '.agents/skills/sdlc-run/local.py').exists())
        self.assertFalse((dest / '.agents/skills/sdlc-run/.env').exists())
        record = json.loads((dest / '.codex/sdlc-installation.json').read_text(encoding='utf-8'))
        self.assertFalse(any('local.py' in name or '.env' in name for name in record['files']))

    def test_install_rejects_linked_source_directory(self):
        base = self.root / distribution.CODEX
        skill = base / 'skills/sdlc-run'
        real = base / 'original-skill'
        skill.rename(real)
        self.directory_link(skill, real)
        result = self.install(base, Path(self.temp.name) / 'target')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Символьная ссылка', result.stderr)

    def test_stale_parent_link_is_rejected_before_any_write(self):
        base = self.root / distribution.CODEX
        dest = Path(self.temp.name) / 'target'
        victim = dest / 'src/tools/old.py'
        victim.parent.mkdir(parents=True)
        victim.write_bytes(b'KEEP_SOURCE')
        skills = dest / '.agents/skills'
        skills.mkdir(parents=True)
        self.directory_link(skills / 'sdlc-old', dest / 'src')
        record = dest / '.codex/sdlc-installation.json'
        record.parent.mkdir()
        record.write_text(json.dumps({'files': ['.agents/skills/sdlc-old/tools/old.py']}), encoding='utf-8')
        before = record.read_bytes()
        result = self.install(base, dest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Символьная ссылка', result.stderr)
        self.assertEqual(victim.read_bytes(), b'KEEP_SOURCE')
        self.assertEqual(record.read_bytes(), before)
        self.assertFalse((skills / 'sdlc-run').exists())

    def test_package_rejects_stale_resources(self):
        (self.root / 'SDLC.md').write_text('Изменено', encoding='utf-8')
        with self.assertRaises(ValueError):
            distribution.package(self.root, revision='test-revision')


if __name__ == '__main__':
    unittest.main()
