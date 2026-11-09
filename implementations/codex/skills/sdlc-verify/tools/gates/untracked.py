#!/usr/bin/env python3
"""Гейт «Scope: нетракованные файлы» — эталонная реализация.

  python untracked.py <корень> <slug>

История гейта (SDLC.md → «Набор гейтов»): строка набора, закрытая командой
`git status --porcelain`, зеленела при любом состоянии дерева — команда печатает список, но
возвращает 0. Здесь статус считается по СПИСКУ: нетракованный путь вне `.sdlc/**` либо назван
в `files_to_touch` плана (новый файл — часть работы chunk'а), либо роняет гейт. Игнорируемые
git'ом пути (`.gitignore`) в список не входят по построению `--exclude-standard`.
Контракт вывода — см. _common.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ARTIFACTS_DIR, emit, files_to_touch, git_out, read  # noqa: E402

GATE = "Scope: нетракованные файлы"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    root, slug = Path(sys.argv[1]).resolve(), sys.argv[2]
    out = git_out(["ls-files", "--others", "--exclude-standard"], root)
    if out is None:
        return emit(GATE, "skip", [], "git недоступен — исполнить нечем", missing_tool="git")
    planned = set(files_to_touch(read(root / ARTIFACTS_DIR / slug / "plan.md", "")))
    paths = [p for p in out.splitlines() if p.strip() and not p.startswith(ARTIFACTS_DIR + "/")]
    unexplained = [p for p in paths if p not in planned]
    if unexplained:
        return emit(GATE, "fail", unexplained,
                    f"{len(unexplained)} нетракованных путей вне files_to_touch — объяснить или добавить в .gitignore")
    return emit(GATE, "pass", paths, "нетракованных путей вне плана нет"
                + (f" ({len(paths)} новых из плана)" if paths else ""))


if __name__ == "__main__":
    sys.exit(main())
