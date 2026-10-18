#!/usr/bin/env python3
"""base-check: прогон тестов на базе chunk'а — два вопроса, на которые diff не отвечает.

  python base-check.py <корень> <slug> <n> <k> [--tests-cmd "<команда>"]

1. **Чужие падающие тесты.** Полный прогон команды «Тесты» на чистом дереве `base_sha`
   (временный `git worktree`). Тест, красный на базе, — не регрессия chunk'а, а условие
   «падает тест, причина которого не в текущих изменениях» из SDLC.md → «Когда остановиться
   и спросить». Список печатается как `base_failures`.
2. **Гейт «Тесты красные на базе».** На ту же базу накладываются только hunk'и тестовых файлов
   патча попытки, и команда прогоняется ещё раз. Гейт `pass`, если тестовые hunk'и дали НОВЫЕ
   падения относительно чистой базы (по идентификаторам упавших тестов, а при неизвестном
   раннере — по коду возврата и числу строк-падений); `fail`, если новых падений нет — тесты
   попытки зелёные до правки кода и ничего не доказывают, даже когда база и так красная от
   чужого теста. Тестовых файлов в патче нет — `skip`; инструмент тестов отсутствует —
   `skip` с `missing_tool` (никогда `pass`).
Вывод — JSON последней строкой (контракт gates/_common.py); строкой выше — JSON с
base_exit, base_failures, tests_on_base, new_failures.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "gates"))
from _common import emit  # noqa: E402
from sdlc_common import (  # noqa: E402
    ARTIFACTS_DIR, git_run, is_test_path, missing_tool, read, require_base_sha, require_tests_cmd, run_shell,
)

GATE = "Тесты красные на базе"
FAIL_LINE = re.compile(
    r"^(?:FAILED|ERROR)\s+(\S+)"                  # pytest: FAILED tests/x.py::test_a
    r"|^(?:FAIL|ERROR):\s+(\S+(?:\s+\([^)]+\))?)"  # unittest: FAIL: test_a (mod.Cls)
    r"|^\s*(?:✕|×|✘)\s+(.+)$"                     # jest/vitest
    r"|^(\S+(?:\s*>\s*\S+)*)\s+FAILED\b"          # gradle: Class > method FAILED
    r"|^\s*\[(?:FAIL|ERROR)\]\s+(.+)$"            # dotnet/go
    r"|^--- FAIL:\s+(\S+)", re.M)


def failures_of(output: str):
    ids = set()
    for m in FAIL_LINE.finditer(output):
        ident = next(g for g in m.groups() if g).strip()
        if not ident.startswith("("):  # «FAILED (failures=1)» — итог раннера, не тест
            ids.add(ident)
    return ids


def test_only_patch(patch: str) -> str:
    out, keep = [], False
    for ln in patch.splitlines(keepends=True):
        if ln.startswith("diff --git "):
            m = re.match(r'diff --git (?:"a/(.+?)"|a/(.+?)) (?:"b/(.+)"|b/(.+))$', ln.rstrip("\r\n"))
            target = (m.group(3) or m.group(4)) if m else ""
            keep = is_test_path(target)
        if keep:
            out.append(ln)
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(prog="base-check.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root"); ap.add_argument("slug"); ap.add_argument("n"); ap.add_argument("k")
    ap.add_argument("--tests-cmd")
    try:
        a = ap.parse_args()
    except SystemExit:
        return 2
    root, slug, n, k = Path(a.root).resolve(), a.slug, a.n, a.k
    d = root / ARTIFACTS_DIR / slug
    base = require_base_sha(read(d / "plan.md", ""))
    tests_cmd = a.tests_cmd or require_tests_cmd(read(root / ARTIFACTS_DIR / "gates.md", ""))
    patch_path = d / f"chunk-{n}-attempt-{k}-diff.patch"
    if not patch_path.exists():
        return emit(GATE, "skip", [], f"нет {patch_path.name} — проверять нечего")
    patch_text = patch_path.read_bytes().decode("utf-8", errors="surrogateescape")
    tests_patch = test_only_patch(patch_text)
    if shutil.which("git") is None:
        return emit(GATE, "skip", [], "git недоступен — исполнить нечем", missing_tool="git")
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "base"
        rc, _, err = git_run(["worktree", "add", "--detach", str(wt), base], root)
        if rc != 0:
            return emit(GATE, "skip", [err.strip()[:200]], "worktree на базе не создан — исполнить нечем",
                        missing_tool="git worktree")
        try:
            rc_clean, out_clean = run_shell(tests_cmd, wt)
            tool_missing = missing_tool(out_clean, tests_cmd, rc_clean)
            fails_clean = failures_of(out_clean)
            info = {"base_exit": rc_clean, "base_failures": sorted(fails_clean)[:50],
                    "tests_on_base": None, "new_failures": []}
            if tool_missing:
                print(json.dumps(info, ensure_ascii=False))
                return emit(GATE, "skip", [tool_missing], "инструмент тестов отсутствует в среде — исполнить нечем",
                            missing_tool=tool_missing)
            if not tests_patch.strip():
                print(json.dumps(info, ensure_ascii=False))
                return emit(GATE, "skip", [], "в патче нет тестовых файлов — гейту нечего проверять")
            tp = Path(tmp) / "tests.patch"
            tp.write_bytes(tests_patch.encode("utf-8", errors="surrogateescape"))
            rc_ap, _, err = git_run(["apply", "--3way", str(tp)], wt)
            if rc_ap != 0:
                rc_ap, _, err = git_run(["apply", str(tp)], wt)
            if rc_ap != 0:
                return emit(GATE, "skip", [err.strip()[:200]], "тестовые hunk'и не легли на базу — проверить нечем")
            rc_tests, out_tests = run_shell(tests_cmd, wt)
        finally:
            git_run(["worktree", "remove", "--force", str(wt)], root)
            git_run(["worktree", "prune"], root)
    fails_tests = failures_of(out_tests)
    new_failures = sorted(fails_tests - fails_clean)
    info.update({"tests_on_base": rc_tests, "new_failures": new_failures[:50]})
    print(json.dumps(info, ensure_ascii=False))
    if fails_clean:
        print(f"на чистой базе уже красные ({len(fails_clean)}): чужие падающие тесты, не регрессия chunk'а")
    if rc_tests != 0 and (rc_clean == 0 or new_failures
                          or len(re.findall(r"\b(FAIL|FAILED|ERROR)\b", out_tests)) > len(re.findall(r"\b(FAIL|FAILED|ERROR)\b", out_clean))):
        return emit(GATE, "pass", new_failures or [f"код возврата тестов на базе с тестовыми hunk'ами: {rc_tests}"],
                    "тесты попытки красные на базе — правка кода им нужна")
    return emit(GATE, "fail", ["тестовые hunk'и патча не дали новых падений на базе без правок кода"],
                "новые/изменённые тесты проходят ДО правки — они не доказывают пункты, которые закрывают")


if __name__ == "__main__":
    sys.exit(main())
