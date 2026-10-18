"""Посевы для base-check.py: тест, зелёный до правки, — не доказательство."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools"
TOOL, EVIDENCE = TOOLS / "base-check.py", TOOLS / "attempt-evidence.py"
PY = Path(sys.executable).as_posix()


def git(args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def tests_file(*bodies):
    return ("import unittest\nfrom app import add\n\nclass T(unittest.TestCase):\n"
            + "".join(f"    def {name}(self):\n        {body}\n" for name, body in bodies))


class BaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(["init", "-q", "-b", "main"], self.root)
        (self.root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_app.py").write_text(
            tests_file(("test_claim_1_zero", "self.assertEqual(add(0, 0), 0)")), encoding="utf-8")
        (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git(["add", "-A"], self.root)
        git(["commit", "-qm", "base"], self.root)
        d = self.root / ".sdlc" / "B-1"
        d.mkdir(parents=True)
        self.tests_cmd = f'"{PY}" -m unittest discover -s tests -t .'
        (self.root / ".sdlc" / "gates.md").write_text(
            f"| Гейт | Вкл | Где отчитывается | Чем реализован |\n|---|---|---|---|\n"
            f"| Тесты | да — минимум | этап 6 | `{self.tests_cmd}` |\n", encoding="utf-8")
        self.set_base()
        self.d = d

    def tearDown(self):
        self.tmp.cleanup()

    def set_base(self):
        base = git(["rev-parse", "HEAD"], self.root).strip()
        (self.root / ".sdlc" / "B-1" / "plan.md").write_text(f"# План\n\n- **База:** {base}\n", encoding="utf-8")

    def snapshot(self):
        proc = subprocess.run([sys.executable, str(EVIDENCE), "write", str(self.root), "B-1", "1", "1", "--force"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def run_tool(self, *extra):
        proc = subprocess.run([sys.executable, str(TOOL), str(self.root), "B-1", "1", "1", *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
        return proc.returncode, json.loads(lines[-1]), json.loads(lines[-2]) if len(lines) > 1 else {}, proc.stdout

    def fix_code(self):
        (self.root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    def test_real_test_is_red_on_base(self):
        self.fix_code()
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(0, 0), 0)"),
            ("test_claim_2_sum", "self.assertEqual(add(2, 3), 5)")), encoding="utf-8")
        self.snapshot()
        rc, gate, info, out = self.run_tool()
        self.assertEqual((rc, gate["status"]), (0, "pass"), out)
        self.assertEqual(info["base_exit"], 0)
        self.assertIn("test_claim_2_sum", " ".join(info["new_failures"]))

    def test_tautological_test_is_green_on_base_and_fails_gate(self):
        self.fix_code()
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(0, 0), 0)"),
            ("test_claim_2_sum", "self.assertTrue(add(2, 3) is not None)")), encoding="utf-8")
        self.snapshot()
        rc, gate, info, out = self.run_tool()
        self.assertEqual((rc, gate["status"]), (1, "fail"), out)

    def test_tautology_behind_red_base_still_fails(self):
        # база уже красная от чужого теста; тавтологический тест попытки не даёт НОВОГО падения
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(1, 1), 2)")), encoding="utf-8")
        git(["commit", "-qam", "red base"], self.root)
        self.set_base()
        self.fix_code()
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(1, 1), 2)"),
            ("test_claim_2_sum", "self.assertTrue(add(2, 3) is not None)")), encoding="utf-8")
        self.snapshot()
        rc, gate, info, out = self.run_tool()
        self.assertNotEqual(info["base_exit"], 0)
        self.assertTrue(info["base_failures"], out)
        self.assertEqual((rc, gate["status"]), (1, "fail"), out)
        # а настоящий тест на той же красной базе даёт новое падение → pass
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(1, 1), 2)"),
            ("test_claim_2_sum", "self.assertEqual(add(2, 3), 5)")), encoding="utf-8")
        self.snapshot()
        rc, gate, info, out = self.run_tool()
        self.assertEqual((rc, gate["status"]), (0, "pass"), out)
        self.assertIn("чужие падающие тесты", out)

    def test_no_test_files_in_patch_is_skip(self):
        self.fix_code()
        self.snapshot()
        rc, gate, info, out = self.run_tool()
        self.assertEqual((rc, gate["status"]), (3, "skip"), out)

    def test_missing_tool_is_skip_not_pass(self):
        self.fix_code()
        (self.root / "tests" / "test_app.py").write_text(tests_file(
            ("test_claim_1_zero", "self.assertEqual(add(0, 0), 0)"),
            ("test_claim_2_sum", "self.assertEqual(add(2, 3), 5)")), encoding="utf-8")
        self.snapshot()
        rc, gate, info, out = self.run_tool("--tests-cmd", "nonexistent_tool_xyz test")
        self.assertEqual((rc, gate["status"]), (3, "skip"), out)
        self.assertTrue(gate["missing_tool"])

    def test_bad_argv_is_exit_2(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(self.root), "B-1", "1", "1", "--tests-cmd"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
