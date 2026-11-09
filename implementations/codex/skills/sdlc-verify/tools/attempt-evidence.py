#!/usr/bin/env python3
"""attempt-evidence: свидетельства попытки chunk'а производит программа, а не агент.

SDLC.md → «Свидетельства производит программа, а не агент»: патч попытки и запись о прогоне
тестов — улики, по которым этап 6 судит этап 5; пока их пишет тот, чью работу они удостоверяют,
вопрос лишь в том, когда их подделают (измерено: «PASS ✓» без единого запуска тестов).

Режим записи (этап 5, Phase 5 /sdlc-chunk):
  python attempt-evidence.py write <корень> <slug> <n> <k> [--tests-cmd "<команда>"]
                                   [--executor-model <имя>] [--force]
    - `chunk-<n>-attempt-<k>-diff.patch` — `git diff <base_sha>` от базы из плана, без `.sdlc/**`,
      плюс новые нетракованные файлы; байты патча не трогаются (CR/LF как в дереве), пути не
      экранируются (`core.quotepath=false`) — патч применим `git apply` и на CRLF-репозиториях;
    - `chunk-<n>-attempt-<k>-tests.txt` — ПОЛНЫЙ вывод команды «Тесты» из набора (или --tests-cmd);
      на Windows команда идёт через Git Bash, если он есть;
    - `chunk-<n>-attempt-<k>-evidence.json` — base_sha, HEAD, sha256 патча и вывода, команда, код
      возврата, улика отсутствующего инструмента, diffstat (в т.ч. удалённые файлы и счёт по
      файлам — вход гейта «Перезапись файла»), модель исполнителя: `--executor-model` (в сессии —
      самоотчёт исполнителя, под раннером — факт рантайма; источник записывается рядом).
    Файлы попытки не перезаписываются: попытка K уже есть — отказ без --force.

Режим базы (этап 5, Phase 2 /sdlc-chunk — перед первой правкой):
  python attempt-evidence.py baseline <корень> <slug> <n>
    Пишет `.chunk-<n>-baseline.json`: {путь → sha256 байтов} для всех изменённых и нетракованных
    файлов дерева (`git status --porcelain`) — единственный формат, который читает gates/scope.py.

Режим сравнения попыток (этап 5, Phase 0 — детект отсутствия прогресса):
  python attempt-evidence.py compare <корень> <slug> <n> <k1> <k2>
    JSON: identical, similarity 0..1, changed_files.

Режим сверки (этап 6, Phase 0 /sdlc-verify):
  python attempt-evidence.py verify <корень> <slug> <n> <k>
    Перегенерирует diff тем же способом и сравнивает с патчем попытки и evidence.json;
    JSON: diff_matches_tree, patch_matches_evidence, tests_matches_evidence, missing_tool, diffstat.
    Код возврата 1, если перегенерированный diff не совпал с патчем (условие `passed=false`).

Старые формы вызова (`<корень> <slug> <n> <k>`, `--verify`, `--compare`) принимаются.
"""

import argparse
import difflib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import (  # noqa: E402
    ARTIFACTS_DIR, diffstat, git_out, git_run, is_junk_path, missing_tool, read, require_base_sha,
    require_tests_cmd, run_shell, sha256,
)

TOOL = "attempt-evidence.py"
VERSION = 2


def write_bytes_text(path: Path, text: str):
    """Записать текст как есть: без перевода \\n → \\r\\n и без потери \\r."""
    path.write_bytes(text.encode("utf-8", errors="surrogateescape"))


def read_bytes_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def regenerate_diff(root: Path, base_sha: str) -> str:
    """git diff от базы без .sdlc/** плюс новые нетракованные файлы (как добавленные)."""
    rc, diff, err = git_run(["diff", base_sha, "--", ".", f":(exclude){ARTIFACTS_DIR}"], root)
    if rc != 0:
        raise SystemExit(f"git diff {base_sha}: код {rc}\n{err}")
    untracked = git_out(["ls-files", "--others", "--exclude-standard", "--", ".",
                         f":(exclude){ARTIFACTS_DIR}"], root) or ""
    for path in sorted(p for p in untracked.splitlines() if p.strip() and not is_junk_path(p)):
        rc, out, err = git_run(["diff", "--no-index", "--", "/dev/null", path], root)
        if rc not in (0, 1):
            raise SystemExit(f"git diff --no-index {path}: код {rc}\n{err}")
        diff += out
    return diff


def paths_for(d: Path, n: str, k: str):
    return (d / f"chunk-{n}-attempt-{k}-diff.patch", d / f"chunk-{n}-attempt-{k}-tests.txt",
            d / f"chunk-{n}-attempt-{k}-evidence.json")


def write_mode(root: Path, slug: str, n: str, k: str, tests_cmd, executor_model, force: bool) -> int:
    d = root / ARTIFACTS_DIR / slug
    base_sha = require_base_sha(read(d / "plan.md", ""))
    tests_cmd = tests_cmd or require_tests_cmd(read(root / ARTIFACTS_DIR / "gates.md", ""))
    patch, tests_out, evidence = paths_for(d, n, k)
    if not force and any(p.exists() for p in (patch, tests_out, evidence)):
        print(f"попытка {k} chunk'а {n} уже имеет свидетельства — они не перезаписываются "
              f"(следующая попытка = следующий номер; --force только осознанно)")
        return 1
    diff = regenerate_diff(root, base_sha)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rc, output = run_shell(tests_cmd, root)
    missing = missing_tool(output, tests_cmd, rc)
    write_bytes_text(patch, diff)
    write_bytes_text(tests_out, output)
    last_line = next((ln for ln in reversed(output.splitlines()) if ln.strip()), "")
    record = {
        "tool": TOOL, "version": VERSION, "generated_at": started,
        "slug": slug, "chunk": int(n), "attempt": int(k),
        "base_sha": base_sha, "head_sha": (git_out(["rev-parse", "HEAD"], root) or "").strip(),
        "diff_sha256": sha256(diff), "diff_empty": not diff.strip(), "diffstat": diffstat(diff),
        "tests_cmd": tests_cmd, "tests_exit": rc, "tests_sha256": sha256(output),
        "tests_last_line": last_line[:200], "missing_tool": missing,
        "executor_model": executor_model or "unknown",
        "executor_model_source": "самоотчёт исполнителя (--executor-model)" if executor_model else "не записана",
    }
    evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: record[key] for key in ("diff_empty", "diffstat", "tests_exit", "missing_tool")},
                     ensure_ascii=False))
    print(f"записано: {patch.name}, {tests_out.name}, {evidence.name}")
    if record["diff_empty"]:
        print("ВНИМАНИЕ: патч пуст — этап не изменил дерево; молчать об этом нельзя (SDLC.md)")
    if missing:
        print(f"ВНИМАНИЕ: оболочка не нашла инструмент команды тестов — кандидат в blocked_env: {missing}")
    return 0


def baseline_mode(root: Path, slug: str, n: str) -> int:
    import hashlib
    d = root / ARTIFACTS_DIR / slug
    status = git_out(["status", "--porcelain", "--untracked-files=all", "--", ".", f":(exclude){ARTIFACTS_DIR}"], root)
    if status is None:
        raise SystemExit("git status не выполнился — база chunk'а не снята")
    base = {}
    for ln in status.splitlines():
        path = ln[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        p = root / path
        if p.is_file():
            base[path] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = d / f".chunk-{n}-baseline.json"
    target.write_text(json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": target.name, "dirty_files": sorted(base)}, ensure_ascii=False))
    if base:
        print(f"дерево грязное: {len(base)} чужих незакоммиченных файлов — назови их человеку до правок")
    return 0


def normalize_patch(text: str) -> str:
    return "\n".join(ln.rstrip() for ln in text.splitlines()
                     if not ln.startswith(("index ", "similarity index", "@@")))


def compare_mode(root: Path, slug: str, n: str, k1: str, k2: str) -> int:
    d = root / ARTIFACTS_DIR / slug
    p1, p2 = d / f"chunk-{n}-attempt-{k1}-diff.patch", d / f"chunk-{n}-attempt-{k2}-diff.patch"
    for p in (p1, p2):
        if not p.exists():
            print(json.dumps({"error": f"нет {p.name}"}, ensure_ascii=False))
            return 2
    a, b = normalize_patch(read_bytes_text(p1)), normalize_patch(read_bytes_text(p2))
    ratio = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines()).ratio()
    fa, fb = set(diffstat(a)["paths"]), set(diffstat(b)["paths"])
    print(json.dumps({"attempts": [int(k1), int(k2)], "identical": sha256(a) == sha256(b),
                      "similarity": round(ratio, 3), "changed_files": sorted(fa ^ fb),
                      "files_k1": sorted(fa), "files_k2": sorted(fb)}, ensure_ascii=False, indent=2))
    return 0


def verify_mode(root: Path, slug: str, n: str, k: str) -> int:
    d = root / ARTIFACTS_DIR / slug
    base_sha = require_base_sha(read(d / "plan.md", ""))
    patch, tests_out, evidence = paths_for(d, n, k)
    if not patch.exists():
        print(json.dumps({"error": f"нет {patch.name} — верификация не начинается"}, ensure_ascii=False))
        return 2
    current = regenerate_diff(root, base_sha)
    patch_text = read_bytes_text(patch)
    patch_hash = sha256(patch_text)
    rec = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else None
    result = {
        "diff_matches_tree": sha256(current) == patch_hash,
        "evidence_present": rec is not None,
        "patch_matches_evidence": rec is not None and rec.get("diff_sha256") == patch_hash,
        "tests_matches_evidence": (rec is not None and tests_out.exists()
                                   and rec.get("tests_sha256") == sha256(read_bytes_text(tests_out))),
        "missing_tool": rec.get("missing_tool") if rec else None,
        "tests_exit": rec.get("tests_exit") if rec else None,
        "executor_model": rec.get("executor_model") if rec else None,
        "diffstat": diffstat(current),
        "produced_by": (rec or {}).get("tool", "не программой — файлы написал агент, свидетельством не считаются"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["diff_matches_tree"] else 1


def main() -> int:
    argv = sys.argv[1:]
    # старые формы: `--verify …`, `--compare …`, `<root> <slug> <n> <k>` без подкоманды
    if argv and argv[0] == "--verify":
        argv = ["verify"] + argv[1:]
    elif argv and argv[0] == "--compare":
        argv = ["compare"] + argv[1:]
    elif argv and argv[0] not in ("write", "baseline", "compare", "verify", "-h", "--help"):
        argv = ["write"] + argv
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    w = sub.add_parser("write")
    w.add_argument("root"); w.add_argument("slug"); w.add_argument("n"); w.add_argument("k")
    w.add_argument("--tests-cmd"); w.add_argument("--executor-model"); w.add_argument("--force", action="store_true")
    b = sub.add_parser("baseline")
    b.add_argument("root"); b.add_argument("slug"); b.add_argument("n")
    c = sub.add_parser("compare")
    c.add_argument("root"); c.add_argument("slug"); c.add_argument("n"); c.add_argument("k1"); c.add_argument("k2")
    v = sub.add_parser("verify")
    v.add_argument("root"); v.add_argument("slug"); v.add_argument("n"); v.add_argument("k")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    root = Path(a.root).resolve()
    if a.mode == "write":
        return write_mode(root, a.slug, a.n, a.k, a.tests_cmd, a.executor_model, a.force)
    if a.mode == "baseline":
        return baseline_mode(root, a.slug, a.n)
    if a.mode == "compare":
        return compare_mode(root, a.slug, a.n, a.k1, a.k2)
    return verify_mode(root, a.slug, a.n, a.k)


if __name__ == "__main__":
    sys.exit(main())
