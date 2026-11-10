"""Прежний алгоритм полного diff для побайтного контроля и измерения стоимости записи."""

import shutil
import tempfile
from pathlib import Path

import snapshot_base
from sdlc_common import git_run


def full_snapshot_diff(root, files):
    current = snapshot_base.project_files(root, files)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)

        def git(*args):
            rc, out, err = git_run(['-c', 'core.autocrlf=false', '-c', 'core.filemode=true',
                                    '-c', 'core.attributesFile=', *args], tree)
            if rc:
                raise ValueError(err)
            return out

        git('init', '-q')
        (tree / '.git/info/attributes').write_text('* -text -filter -ident -working-tree-encoding\n', encoding='utf-8')
        snapshot_base.materialize(files, tree)
        git('add', '--all', '--force')
        before = git('write-tree').strip()
        for child in tree.iterdir():
            if child.name != '.git':
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        snapshot_base.materialize(current, tree)
        git('add', '--all', '--force')
        after = git('write-tree').strip()
        return git('diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames', '--no-color',
                   '--src-prefix=a/', '--dst-prefix=b/', '--output-indicator-new=+',
                   '--output-indicator-old=-', '--output-indicator-context= ', before, after, '--')
