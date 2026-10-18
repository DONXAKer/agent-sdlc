"""Посевы для hooks/sdlc-guard.py и tools/review-validate.py."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "implementations" / "claude-code" / "hooks" / "sdlc-guard.py"
VALIDATE = REPO / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools" / "review-validate.py"


def hook(cwd, tool, tool_input, raw=None):
    proc = subprocess.run([sys.executable, str(GUARD)], cwd=cwd,
                          input=raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": tool_input}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def cli(*args):
    return subprocess.run([sys.executable, str(GUARD), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.d = self.root / ".sdlc" / "G-1"
        self.d.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_markers_allows_everything(self):
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "x.py")}), "allow")
        self.assertEqual(hook(self.root, "Bash", {"command": "git commit -m x"}), "allow")
        self.assertEqual(hook(self.root, "Bash", {}, raw="not json"), "allow")  # без маркеров — fail-open безопасен

    def test_scope_via_cli_blocks_writes_outside_plan(self):
        self.assertEqual(cli("--scope", self.root, "G-1", "1", "src/app.py", "tests/**").returncode, 0)
        self.assertEqual(hook(self.root, "Edit", {"file_path": str(self.root / "src" / "app.py")}), "allow")
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "tests" / "t.py")}), "allow")
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.d / "chunk-1-journal.md")}), "allow")
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "src" / "other.py")}), "deny")
        (self.root / "src").mkdir()
        self.assertEqual(hook(self.root / "src", "Write", {"file_path": "other.py"}), "deny")
        self.assertEqual(hook(self.root, "Bash", {"command": "echo hi > src/other.py"}), "allow")
        self.assertEqual(cli("--unscope", self.root, "G-1").returncode, 0)
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "src" / "other.py")}), "allow")

    def test_review_lock_semantics(self):
        self.assertEqual(cli("--lock", self.root, "G-1").returncode, 0)
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "x.py")}), "deny")
        # артефакты витка скилл писать обязан — ответ рецензента, отчёт
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.d / "chunk-1-attempt-1-review.md")}), "allow")
        self.assertEqual(hook(self.root, "Edit", {"file_path": str(self.d / "verification-report-1-attempt-1.md")}), "allow")
        for cmd in ["git commit -m x", "git -C . add -A", "git -c user.name=x commit -m x", "git checkout -- .",
                    "rm -rf build", "sed -i s/a/b/ x.py", "echo x > out.txt", "cat a | tee b", "pip install requests",
                    "echo a | xargs rm", "find . -name x -delete", "python -c \"open('f','w').write('x')\"",
                    "perl -i -pe s/a/b/ x", "powershell Set-Content x.txt"]:
            self.assertEqual(hook(self.root, "Bash", {"command": cmd}), "deny", cmd)
        for cmd in ["git diff HEAD", "git log -3", "grep -rn foo src", "grep -rn \"->\" src/", "python -c 'print(1 > 0)'",
                    "cat file.py", "python -m pytest tests/test_x.py -k claim_3", "ls 2>&1", "ls 2>/dev/null",
                    "make -C test test", "python -m pytest -q 2>/dev/null", "python ~/.claude/hooks/sdlc-guard.py --unlock . G-1"]:
            self.assertEqual(hook(self.root, "Bash", {"command": cmd}), "allow", cmd)
        # нечитаемый ввод при маркерах — deny
        self.assertEqual(hook(self.root, "Bash", {}, raw="not json"), "deny")
        # снятие замка — CLI, не rm
        self.assertEqual(cli("--unlock", self.root, "G-1").returncode, 0)
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "x.py")}), "allow")

    def test_claims_lock_closes_sdlc_for_reading(self):
        (self.d / "intent.md").write_text("# задача\n", encoding="utf-8")
        self.assertEqual(cli("--claims-lock", self.root, "G-1").returncode, 0)
        self.assertEqual(hook(self.root, "Read", {"file_path": str(self.d / "intent.md")}), "deny")
        self.assertEqual(hook(self.root, "Grep", {"pattern": "claim", "path": str(self.root / ".sdlc")}), "deny")
        self.assertEqual(hook(self.root, "Glob", {"pattern": ".sdlc/**/*.md"}), "deny")
        self.assertEqual(hook(self.root, "Bash", {"command": "cat .sdlc/G-1/intent.md"}), "deny")
        self.assertEqual(hook(self.root, "Read", {"file_path": str(self.root / "src" / "app.py")}), "allow")
        self.assertEqual(hook(self.root, "Grep", {"pattern": "claim", "path": str(self.root / "src")}), "allow")
        self.assertEqual(hook(self.root, "Bash", {"command": "git log -3"}), "allow")
        self.assertEqual(cli("--claims-unlock", self.root, "G-1").returncode, 0)
        self.assertEqual(hook(self.root, "Read", {"file_path": str(self.d / "intent.md")}), "allow")

    def test_only_active_loop_markers_apply(self):
        old = self.root / ".sdlc" / "OLD-1"
        old.mkdir()
        (old / ".review-lock").write_text("", encoding="utf-8")  # забытый замок старого витка
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "x.py")}), "deny")
        self.assertEqual(cli("--scope", self.root, "G-1", "1", "src/**").returncode, 0)  # активный виток — G-1
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "src" / "a.py")}), "allow")
        self.assertEqual(hook(self.root, "Write", {"file_path": str(self.root / "x.py")}), "deny")
        out = cli("--status", self.root).stdout
        self.assertIn("активный виток: G-1", out)


class ReviewValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / ".sdlc" / "R-1"
        self.d.mkdir(parents=True)
        (self.d / "intent.md").write_text("| claim-1 | a | t |\n| claim-2 [edge] | b | t |\n", encoding="utf-8")
        (self.d / "chunk-1-attempt-1-diff.patch").write_text(
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n",
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_validate(self, text, path=None, cwd=None):
        f = self.d / "chunk-1-attempt-1-review.md"
        f.write_text(text, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(VALIDATE), str(self.root), "R-1", "1", "1", path or str(f)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
        return proc.returncode, json.loads(proc.stdout)

    def good(self):
        return {"claims": [
            {"id": "claim-1", "status": "passed", "evidence": [{"path": "src/app.py", "anchor": "1"}], "remediation": ""},
            {"id": "claim-2", "status": "failed", "evidence": [{"path": "src/app.py", "anchor": "1"}], "remediation": "чинить"},
        ], "findings": [], "scope": [], "invariants": [], "regressions": [], "retry_instruction": "src/app.py:1 — вернуть guard"}

    def test_valid_answer_and_schema_version_is_substituted(self):
        rc, res = self.run_validate("## 1. Пункты приёмки\n…\n```json\n" + json.dumps(self.good()) + "\n```\n")
        self.assertEqual((rc, res["valid"]), (0, True), res)
        self.assertEqual(res["claims"], {"claim-1": "passed", "claim-2": "failed"})
        self.assertEqual(res["review"]["schema_version"], "agent-sdlc/verify-review/v1")

    def test_relative_path_from_project_root_works_from_other_cwd(self):
        rc, res = self.run_validate("```json\n" + json.dumps(self.good()) + "\n```",
                                    path=".sdlc/R-1/chunk-1-attempt-1-review.md", cwd=str(REPO))
        self.assertEqual((rc, res["valid"]), (0, True), res)

    def test_missing_claim_and_new_id(self):
        r = self.good()
        r["claims"][1]["id"] = "claim-7"
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        self.assertTrue(any("набор id" in e for e in res["errors"]))

    def test_status_vocabulary_matches_schema(self):
        r = self.good()
        r["claims"][0]["status"] = "unverifiable"
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        r["claims"][0]["status"] = "uncertain"
        self.assertEqual(self.run_validate("```json\n" + json.dumps(r) + "\n```")[0], 0)

    def test_findings_kinds_and_all_arrays_validated(self):
        r = self.good()
        r["findings"] = [{"kind": "axis", "summary": "x", "evidence": [{"path": "src/app.py", "anchor": "3"}]}]
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        self.assertTrue(any("kind" in e for e in res["errors"]))
        r["findings"] = []
        r["regressions"] = [{"kind": "regression", "summary": "сломали", "evidence": []}]
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        self.assertTrue(any("regressions[0]" in e and "без evidence" in e for e in res["errors"]))

    def test_status_without_evidence_and_finding_off_patch(self):
        r = self.good()
        r["claims"][0]["evidence"] = []
        r["findings"] = [{"kind": "mismatch", "summary": "x", "evidence": [{"path": "src/nowhere.py", "anchor": "3"}]}]
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        self.assertTrue(any("без единой ссылки" in e for e in res["errors"]))
        self.assertTrue(any("nowhere.py" in e for e in res["errors"]))

    def test_garbage_structure_is_error_list_not_traceback(self):
        for bad in ({"claims": [], "findings": ["free text"]}, {"claims": [{"id": "claim-1", "status": "passed",
                                                                            "evidence": [{"path": "src/app.py", "anchor": "1"}]}],
                                                                 "findings": [{"summary": None}]}, ["list"]):
            rc, res = self.run_validate("```json\n" + json.dumps(bad) + "\n```")
            self.assertEqual((rc, res["valid"]), (1, False))
            self.assertTrue(res["errors"])

    def test_retelling_without_patch_address_is_rejected(self):
        r = self.good()
        for c in r["claims"]:
            c["evidence"] = [{"path": "docs/notes.md", "anchor": "1"}]
        rc, res = self.run_validate("```json\n" + json.dumps(r) + "\n```")
        self.assertEqual(rc, 1)
        self.assertTrue(any("пересказа" in e for e in res["errors"]))

    def test_no_json_block(self):
        rc, res = self.run_validate("## 1. Пункты приёмки\n| claim-1 | ✅ | … |\n")
        self.assertEqual((rc, res["valid"]), (1, False))


if __name__ == "__main__":
    unittest.main()
