"""Проверки установки комплекта Cursor и синхронизации ресурсов."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'implementations/cursor'
spec = importlib.util.spec_from_file_location('install_cursor', SOURCE / 'install-cursor.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CursorTests(unittest.TestCase):
    def test_junction_or_symlink_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            project, outside = Path(temp) / 'project', Path(temp) / 'outside'
            (project / '.cursor').mkdir(parents=True)
            (outside / 'sdlc-run').mkdir(parents=True)
            victim = outside / 'sdlc-run/SKILL.md'
            victim.write_bytes(b'original outside')
            link = project / '.cursor/skills'
            if sys.platform == 'win32':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                for check in (True, False):
                    with self.assertRaises(ValueError):
                        installer.install(project, check=check)
                self.assertEqual(victim.read_bytes(), b'original outside')
                self.assertEqual(sorted(p.name for p in (project / '.cursor').iterdir()), ['skills'])
            finally:
                if sys.platform == 'win32':
                    link.rmdir()
                else:
                    link.unlink()

    def test_unlisted_source_secret_is_not_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            source, project = Path(temp) / 'source', Path(temp) / 'project'
            shutil.copytree(SOURCE, source)
            project.mkdir()
            (source / 'skills/sdlc-run/.env').write_text('LOCAL_SECRET', encoding='utf-8')
            with patch.object(installer, 'SOURCE', source):
                installer.install(project)
                self.assertTrue(installer.install(project, check=True))
            self.assertFalse((project / '.cursor/skills/sdlc-run/.env').exists())

    def test_corrupted_manifest_resource_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            source, project = Path(temp) / 'source', Path(temp) / 'project'
            shutil.copytree(SOURCE, source)
            project.mkdir()
            (source / 'rules/agent-sdlc.mdc').write_bytes(b'changed')
            with patch.object(installer, 'SOURCE', source), self.assertRaises(ValueError):
                installer.install(project)
            self.assertEqual(list(project.iterdir()), [])

    def test_install_check_and_backup_preserve_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.assertFalse(installer.install(project, check=True))
            self.assertEqual(list(project.iterdir()), [])
            self.assertTrue(installer.install(project))
            self.assertTrue(installer.install(project, check=True))
            target = project / '.cursor/rules/agent-sdlc.mdc'
            target.write_text('Локальная версия', encoding='utf-8')
            other = project / '.cursor/rules/local.mdc'
            other.write_text('Другое правило', encoding='utf-8')
            self.assertFalse(installer.install(project, check=True))
            installer.install(project)
            self.assertEqual(target.read_bytes(), (SOURCE / 'rules/agent-sdlc.mdc').read_bytes())
            backups = list((project / '.cursor').glob('sdlc-backup-*/rules/agent-sdlc.mdc'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding='utf-8'), 'Локальная версия')
            self.assertEqual(other.read_text(encoding='utf-8'), 'Другое правило')

    def test_native_layout_and_shared_resources(self):
        self.assertEqual(len(list((SOURCE / 'skills').glob('*/SKILL.md'))), 10)
        self.assertEqual(len(list((SOURCE / 'agents').glob('*.md'))), 4)
        for source in (ROOT / 'implementations/codex/skills').rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts or source.name == 'SKILL.md' or source.suffix == '.pyc':
                continue
            relative = source.relative_to(ROOT / 'implementations/codex')
            self.assertEqual(source.read_bytes(), (SOURCE / relative).read_bytes(), str(relative))
        for skill in (SOURCE / 'skills').glob('*/SKILL.md'):
            self.assertNotIn('Общий контракт Codex', skill.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
