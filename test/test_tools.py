"""Тесты инструментов реализации claude-code: gates-preflight.py и attempt-evidence.py.

Правило SDLC.md → «Когда дефект проскочил»: проверка без кейса — принятый риск, записанный
чужим словом. Каждая ветка инструментов ниже имеет кейс, который красный до правки.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools"
PREFLIGHT = TOOLS / "gates-preflight.py"
EVIDENCE = TOOLS / "attempt-evidence.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


GATES = """# Набор гейтов: тест

- **Последнее изменение набора:** 2026-08-01

| Гейт | Вкл | Где отчитывается | Чем реализован |
|---|---|---|---|
| Сборка | да — минимум | этап 6 | `git --version` |
| Тесты | да — минимум | этап 6 | `{tests}` |
| Scope: файлы вне плана | да — минимум | этап 6 | встроенная реализация |
| Анти-обход тест-гейта | да — минимум | этап 6 | встроенная реализация |
| Ревью независимым агентом | да — минимум | этап 6 | агент sdlc-reviewer |
| Секреты в diff | нет | этап 6 | `definitely-missing-tool-xyz scan` |
| Проверка предусловий публикации | да | этап 7 | `definitely-missing-tool-xyz publish` |
"""


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".sdlc").mkdir()
        self.mod = load(PREFLIGHT, "gates_preflight")

    def tearDown(self):
        self.tmp.cleanup()

    def write_gates(self, tests):
        (self.root / ".sdlc" / "gates.md").write_text(GATES.format(tests=tests), encoding="utf-8")

    def test_all_executable(self):
        self.write_gates("git status")
        rows = self.mod.preflight(self.root, "этап 6")
        self.assertEqual({r["status"] for r in rows if r["command"]}, {"ok"})
        self.assertNotIn("Секреты в diff", [r["gate"] for r in rows])
        self.assertNotIn("Проверка предусловий публикации", [r["gate"] for r in rows])

    def test_missing_tool_is_blocked_env_candidate(self):
        self.write_gates("definitely-missing-tool-xyz test")
        rows = self.mod.preflight(self.root, "этап 6")
        missing = [r for r in rows if r["status"] == "missing"]
        self.assertEqual([r["gate"] for r in missing], ["Тесты"])
        self.assertEqual(missing[0]["token"], "definitely-missing-tool-xyz")

    def test_relative_path_command(self):
        (self.root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        self.write_gates("./gradlew test")
        rows = self.mod.preflight(self.root, "этап 6")
        self.assertEqual([r["status"] for r in rows if r["gate"] == "Тесты"], ["ok"])

    def test_interpreter_script_path_is_checked(self):
        # `python` есть в PATH, но скрипт относителен к чужому каталогу — это missing, не ok
        self.write_gates("python tools/gates/scope.py outside . s p")
        rows = self.mod.preflight(self.root, "этап 6")
        row = next(r for r in rows if r["gate"] == "Тесты")
        self.assertEqual(row["status"], "missing")
        self.assertIn("скрипт", row["detail"])
        # существующий скрипт от корня — ok; абсолютный путь — ok; `-m модуль` — ok
        (self.root / "run_tests.py").write_text("", encoding="utf-8")
        self.write_gates("python run_tests.py")
        self.assertEqual(next(r for r in self.mod.preflight(self.root, "этап 6") if r["gate"] == "Тесты")["status"], "ok")
        self.write_gates(f"python {EVIDENCE.as_posix()} verify . s 1 1")
        self.assertEqual(next(r for r in self.mod.preflight(self.root, "этап 6") if r["gate"] == "Тесты")["status"], "ok")
        self.write_gates("python -m unittest discover")
        self.assertEqual(next(r for r in self.mod.preflight(self.root, "этап 6") if r["gate"] == "Тесты")["status"], "ok")

    def test_no_backtick_command_is_reported_not_green(self):
        self.write_gates("git status")
        text = (self.root / ".sdlc" / "gates.md").read_text(encoding="utf-8")
        text = text.replace("| `git --version` |", "| проза без кавычек |")
        (self.root / ".sdlc" / "gates.md").write_text(text, encoding="utf-8")
        rows = self.mod.preflight(self.root, "этап 6")
        self.assertEqual([r["status"] for r in rows if r["gate"] == "Сборка"], ["no-command"])

    def test_template_hints_do_not_trigger_blocked_env(self):
        # канонический шаблон набора со всеми «да» не должен давать blocked_env из-за подсказок
        tpl = (REPO / "templates" / "gates.template.md").read_text(encoding="utf-8")
        tpl = tpl.replace("‹да/нет›", "да").replace("| ‹проект›", "| t")
        (self.root / ".sdlc" / "gates.md").write_text(tpl, encoding="utf-8")
        rows = self.mod.preflight(self.root, "этап 6")
        self.assertEqual([r for r in rows if r["status"] == "missing"], [])

    def test_cli_exit_codes(self):
        self.write_gates("definitely-missing-tool-xyz test")
        proc = subprocess.run([sys.executable, str(PREFLIGHT), str(self.root)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("blocked_env", proc.stdout)
        self.write_gates("git status")
        proc = subprocess.run([sys.executable, str(PREFLIGHT), str(self.root)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0)
        proc = subprocess.run([sys.executable, str(PREFLIGHT), str(self.root), "--stage"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 2)  # неверный вызов — код 2, не traceback


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(["init", "-q", "-b", "main"], self.root)
        git(["config", "core.autocrlf", "false"], self.root)  # байты дерева — как есть, без нормализации
        (self.root / "app.py").write_text("print('v1')\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        git(["add", "-A"], self.root)
        git(["commit", "-qm", "base"], self.root)
        self.base = git(["rev-parse", "HEAD"], self.root).strip()
        d = self.root / ".sdlc" / "T-1"
        d.mkdir(parents=True)
        (self.root / ".sdlc" / "gates.md").write_text(GATES.format(tests="git --version"), encoding="utf-8")
        (d / "plan.md").write_text(f"# План\n\n- **База:** {self.base}\n", encoding="utf-8")
        self.d = d

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(EVIDENCE), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", cwd=self.root)

    def evidence(self, n=1, k=1):
        return json.loads((self.d / f"chunk-{n}-attempt-{k}-evidence.json").read_text(encoding="utf-8"))

    def test_write_then_verify_roundtrip(self):
        (self.root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (self.root / "new.py").write_text("x = 1\n", encoding="utf-8")       # нетракованный
        (self.root / "debug.log").write_text("noise\n", encoding="utf-8")    # игнорируемый
        (self.d / "chunk-1-journal.md").write_text("# журнал\n", encoding="utf-8")
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1", "--executor-model", "sonnet")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        patch = (self.d / "chunk-1-attempt-1-diff.patch").read_text(encoding="utf-8")
        self.assertIn("v2", patch)
        self.assertIn("new.py", patch)
        self.assertNotIn("debug.log", patch)
        self.assertNotIn("chunk-1-journal", patch)
        rec = self.evidence()
        self.assertEqual(rec["tests_exit"], 0)
        self.assertIsNone(rec["missing_tool"])
        self.assertEqual(rec["diffstat"]["files"], 2)
        self.assertEqual(rec["executor_model"], "sonnet")
        self.assertIn("самоотчёт", rec["executor_model_source"])
        self.assertIn("git version", (self.d / "chunk-1-attempt-1-tests.txt").read_text(encoding="utf-8"))
        proc = self.run_tool("verify", str(self.root), "T-1", "1", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        res = json.loads(proc.stdout)
        self.assertTrue(res["diff_matches_tree"] and res["patch_matches_evidence"] and res["tests_matches_evidence"], res)
        (self.d / "chunk-1-journal.md").write_text("# журнал\n| 1 | … |\n", encoding="utf-8")
        self.assertEqual(self.run_tool("verify", str(self.root), "T-1", "1", "1").returncode, 0)
        (self.root / "app.py").write_text("print('v3')\n", encoding="utf-8")
        proc = self.run_tool("verify", str(self.root), "T-1", "1", "1")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["diff_matches_tree"])
        # старая форма вызова без подкоманды по-прежнему принимается
        self.assertEqual(self.run_tool("--verify", str(self.root), "T-1", "1", "1").returncode, 1)

    def test_tests_hash_survives_crlf_output(self):
        # cmd.exe/PowerShell печатают CRLF; хэш обязан сходиться после записи и чтения
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1", "--tests-cmd",
                             "printf 'a\\r\\nb\\r\\n'" if sys.platform != "win32" else "printf 'a\\r\\nb\\r\\n'")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        raw = (self.d / "chunk-1-attempt-1-tests.txt").read_bytes()
        self.assertIn(b"\r\n", raw)
        res = json.loads(self.run_tool("verify", str(self.root), "T-1", "1", "1").stdout)
        self.assertTrue(res["tests_matches_evidence"], res)

    def test_crlf_file_patch_is_byte_exact_and_applies(self):
        (self.root / "win.txt").write_bytes(b"one\r\ntwo\r\n")
        git(["add", "-A"], self.root)
        git(["commit", "-qm", "crlf"], self.root)
        base = git(["rev-parse", "HEAD"], self.root).strip()
        (self.d / "plan.md").write_text(f"# План\n\n- **База:** {base}\n", encoding="utf-8")
        (self.root / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1").returncode, 0)
        patch = self.d / "chunk-1-attempt-1-diff.patch"
        self.assertIn(b"+three\r\n", patch.read_bytes())
        git(["checkout", "--", "win.txt"], self.root)
        self.assertEqual(subprocess.run(["git", "apply", "--check", str(patch)], cwd=self.root).returncode, 0)

    def test_non_ascii_paths_are_kept(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "новый.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.evidence()
        self.assertIn("src/новый.py", rec["diffstat"]["paths"])
        self.assertEqual(rec["diffstat"]["files"], 2)

    def test_attempt_files_are_never_overwritten(self):
        (self.root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1").returncode, 0)
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("не перезаписываются", proc.stdout)
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "2").returncode, 0)

    def test_missing_tool_and_empty_diff_are_flagged(self):
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1", "--tests-cmd", "definitely-missing-tool-xyz --run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.evidence()
        self.assertTrue(rec["missing_tool"])
        self.assertTrue(rec["diff_empty"])
        self.assertIn("blocked_env", proc.stdout)
        self.assertIn("патч пуст", proc.stdout)

    def test_test_log_mentioning_missing_file_is_not_missing_tool(self):
        # «No such file or directory» в логе тестов — не отсутствующий инструмент команды
        cmd = f"{Path(sys.executable).as_posix()} -c \"print('FileNotFoundError: [Errno 2] No such file or directory: data.csv'); raise SystemExit(1)\""
        proc = self.run_tool("write", str(self.root), "T-1", "1", "1", "--tests-cmd", cmd)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = self.evidence()
        self.assertEqual(rec["tests_exit"], 1)
        self.assertIsNone(rec["missing_tool"])

    def test_cache_junk_is_not_evidence_even_without_gitignore(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "app.cpython-314.pyc").write_bytes(bytes([0, 1]))
        (self.root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1").returncode, 0)
        self.assertEqual(self.evidence()["diffstat"]["paths"], ["app.py"])

    def test_deleted_file_is_in_diffstat(self):
        (self.root / "app.py").unlink()
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1").returncode, 0)
        st = self.evidence()["diffstat"]
        self.assertEqual(st["deleted_paths"], ["app.py"])
        self.assertTrue(st["per_file"]["app.py"]["deleted_entirely"])
        self.assertEqual(st["files"], 1)

    def test_baseline_mode(self):
        (self.root / "app.py").write_text("print('dirty')\n", encoding="utf-8")
        (self.root / "stray.txt").write_text("x\n", encoding="utf-8")
        proc = self.run_tool("baseline", str(self.root), "T-1", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        base = json.loads((self.d / ".chunk-1-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(base), ["app.py", "stray.txt"])
        self.assertRegex(base["app.py"], r"^[0-9a-f]{64}$")
        self.assertIn("дерево грязное", proc.stdout)

    def test_compare_attempts_reports_identity_and_similarity(self):
        (self.root / "app.py").write_text("print('v2')\n", encoding="utf-8")
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1").returncode, 0)
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "2").returncode, 0)
        res = json.loads(self.run_tool("compare", str(self.root), "T-1", "1", "1", "2").stdout)
        self.assertTrue(res["identical"])
        self.assertEqual(res["similarity"], 1.0)
        (self.root / "app.py").write_text("print('v2')\nprint('more')\n", encoding="utf-8")
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "3").returncode, 0)
        res = json.loads(self.run_tool("compare", str(self.root), "T-1", "1", "2", "3").stdout)
        self.assertFalse(res["identical"])
        self.assertLess(res["similarity"], 1.0)

    def test_agent_written_files_are_not_evidence(self):
        (self.d / "chunk-1-attempt-1-diff.patch").write_text("", encoding="utf-8")
        (self.d / "chunk-1-attempt-1-tests.txt").write_text("PASS ✓ Case 1 (5ms)\n", encoding="utf-8")
        res = json.loads(self.run_tool("verify", str(self.root), "T-1", "1", "1").stdout)
        self.assertFalse(res["evidence_present"])
        self.assertIn("не программой", res["produced_by"])

    def test_bad_argv_is_exit_2(self):
        self.assertEqual(self.run_tool("write", str(self.root), "T-1", "1", "1", "--tests-cmd").returncode, 2)


if __name__ == "__main__":
    unittest.main()
