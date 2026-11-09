"""Проверки установки комплекта Cursor и синхронизации ресурсов."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'implementations/cursor'
spec = importlib.util.spec_from_file_location('install_cursor', SOURCE / 'install-cursor.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CursorTests(unittest.TestCase):
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
