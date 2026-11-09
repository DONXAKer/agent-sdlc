"""Проверки самостоятельного комплекта Kimi Code."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'implementations/kimi-code'
CODEX = ROOT / 'implementations/codex'


def install(source, project, *args):
    return subprocess.run(
        [sys.executable, str(source / 'install-kimi-code.py'), '--project', str(project), *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )


class KimiCodeTests(unittest.TestCase):
    def test_native_layout_and_shared_resources(self):
        self.assertEqual(len(list((SOURCE / 'skills').glob('*/SKILL.md'))), 10)
        self.assertEqual(len(list((SOURCE / 'agents').glob('*.md'))), 4)
        for source in CODEX.joinpath('skills').rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
                continue
            relative = source.relative_to(CODEX)
            target = SOURCE / relative
            if source.name == 'SKILL.md':
                expected = source.read_text(encoding='utf-8').replace(
                    'Общий контракт Codex', 'Общий контракт Kimi Code',
                ).replace('../sdlc-', '${KIMI_SKILL_DIR}/../sdlc-')
                self.assertEqual(target.read_text(encoding='utf-8'), expected, str(relative))
            else:
                self.assertEqual(source.read_bytes(), target.read_bytes(), str(relative))

    def test_install_check_backup_and_stale_removal(self):
        with tempfile.TemporaryDirectory(prefix='sdlc-kimi-') as temp:
            project = Path(temp) / 'проект с пробелами'
            self.assertNotEqual(install(SOURCE, project, '--check').returncode, 0)
            self.assertFalse(project.exists())
            self.assertEqual(install(SOURCE, project).returncode, 0)
            self.assertEqual(install(SOURCE, project, '--check').returncode, 0)
            changed = project / '.kimi-code/skills/sdlc-run/SKILL.md'
            changed.write_text('Локальная версия', encoding='utf-8')
            stale = project / '.kimi-code/skills/sdlc-old/SKILL.md'
            stale.parent.mkdir()
            stale.write_text('Устаревший навык', encoding='utf-8')
            record = project / '.kimi-code/sdlc-installation.json'
            installed = json.loads(record.read_text(encoding='utf-8'))
            installed['files'].append(stale.relative_to(project).as_posix())
            record.write_text(json.dumps(installed), encoding='utf-8')
            other = project / '.kimi-code/skills/local/SKILL.md'
            other.parent.mkdir()
            other.write_text('Другой навык', encoding='utf-8')
            self.assertNotEqual(install(SOURCE, project, '--check').returncode, 0)
            self.assertEqual(install(SOURCE, project).returncode, 0)
            backups = list(project.glob('.kimi-code/sdlc-backup-*'))
            self.assertTrue(any((path / '.kimi-code/skills/sdlc-run/SKILL.md').is_file() for path in backups))
            self.assertTrue(any((path / '.kimi-code/skills/sdlc-old/SKILL.md').is_file() for path in backups))
            self.assertFalse(stale.exists())
            self.assertEqual(other.read_text(encoding='utf-8'), 'Другой навык')
            self.assertNotIn(stale.relative_to(project).as_posix(), json.loads(record.read_text(encoding='utf-8'))['files'])

    def test_unlisted_source_secret_is_not_installed(self):
        with tempfile.TemporaryDirectory(prefix='sdlc-kimi-') as temp:
            source, project = Path(temp) / 'source', Path(temp) / 'project'
            shutil.copytree(SOURCE, source)
            (source / 'skills/sdlc-run/.env').write_text('LOCAL_SECRET', encoding='utf-8')
            self.assertEqual(install(source, project).returncode, 0)
            self.assertFalse((project / '.kimi-code/skills/sdlc-run/.env').exists())

    def test_corrupted_manifest_resource_blocks_all_writes(self):
        with tempfile.TemporaryDirectory(prefix='sdlc-kimi-') as temp:
            source, project = Path(temp) / 'source', Path(temp) / 'project'
            shutil.copytree(SOURCE, source)
            (source / 'agents/sdlc-reviewer.md').write_bytes(b'changed')
            self.assertNotEqual(install(source, project).returncode, 0)
            self.assertFalse(project.exists())

    def test_junction_or_symlink_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory(prefix='sdlc-kimi-') as temp:
            project, outside = Path(temp) / 'project', Path(temp) / 'outside'
            (project / '.kimi-code').mkdir(parents=True)
            (outside / 'sdlc-run').mkdir(parents=True)
            victim = outside / 'sdlc-run/SKILL.md'
            victim.write_bytes(b'original outside')
            link = project / '.kimi-code/skills'
            if sys.platform == 'win32':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                for check in (True, False):
                    args = ('--check',) if check else ()
                    self.assertNotEqual(install(SOURCE, project, *args).returncode, 0)
                self.assertEqual(victim.read_bytes(), b'original outside')
                self.assertEqual(sorted(path.name for path in (project / '.kimi-code').iterdir()), ['skills'])
            finally:
                if sys.platform == 'win32':
                    link.rmdir()
                else:
                    link.unlink()


if __name__ == '__main__':
    unittest.main()
