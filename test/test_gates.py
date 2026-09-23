"""Посевы для эталонных гейт-скриптов (implementations/claude-code/skills/sdlc-verify/tools/gates/).

Каждый тест — пара «дефект известного класса → гейт красный» и «чистый патч → зелёный»:
то самое требование SDLC.md «не бывает так, что гейт есть, но чувствительность его никто не
проверял», применённое к эталонным реализациям, которые методология поставляет проектам.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools"
GATES = TOOLS / "gates"


def run(script, *args):
    proc = subprocess.run([sys.executable, str(GATES / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    return proc.returncode, json.loads(last), proc.stdout


def patch(files):
    """files: {path: (old_lines, new_lines)}; None вместо списка — файла нет (создан/удалён)."""
    out = []
    for path, (old, new) in files.items():
        out.append(f"diff --git a/{path} b/{path}")
        out.append("--- " + ("/dev/null" if old is None else f"a/{path}"))
        out.append("+++ " + ("/dev/null" if new is None else f"b/{path}"))
        old, new = old or [], new or []
        out.append(f"@@ -1,{len(old)} +1,{len(new)} @@")
        out += [f"-{ln}" for ln in old] + [f"+{ln}" for ln in new]
    return "\n".join(out) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d = self.root / ".sdlc" / "S-1"
        d.mkdir(parents=True)
        (d / "plan.md").write_text(
            "# План\n\n## files_to_touch\n| Путь | Что делаем |\n|---|---|\n| src/app.py | правка |\n"
            "| tests/test_app.py | тест |\n\n## Шаги\n", encoding="utf-8")
        (d / "intent.md").write_text(
            "| id | Пункт | Как проверить |\n|---|---|---|\n| claim-1 | a | t |\n| claim-2 [edge] | b | t |\n",
            encoding="utf-8")
        self.d = d

    def tearDown(self):
        self.tmp.cleanup()

    def write_patch(self, files):
        p = self.d / "chunk-1-attempt-1-diff.patch"
        p.write_text(patch(files), encoding="utf-8")
        return p


class CommonTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("sdlc_common", TOOLS / "sdlc_common.py")
        self.c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.c)

    def test_is_test_path(self):
        for p in ["tests/test_app.py", "src/app_test.go", "src/app.test.ts", "spec/x.rb", "src/__tests__/a.js",
                  "src/main/java/FooTest.java", "test_x.py"]:
            self.assertTrue(self.c.is_test_path(p), p)
        for p in ["src/latest_prices.py", "src/contest_rules.ts", "src/app.py", "docs/testing.md"]:
            self.assertFalse(self.c.is_test_path(p), p)

    def test_patch_files_headers_and_hunk_lines(self):
        text = patch({"a.py": (["x"], ["x", "++ counter", "-- sql"]), "gone.py": (["1", "2"], None),
                      "new.py": (None, ["n"])})
        files = {f["path"]: f for f in self.c.patch_files(text)}
        self.assertEqual(set(files), {"a.py", "gone.py", "new.py"})
        self.assertEqual((files["a.py"]["added"], files["a.py"]["removed"]), (3, 1))  # «+++ counter» — строка hunk'а, не заголовок
        self.assertTrue(files["gone.py"]["deleted"])
        self.assertTrue(files["new.py"]["new"])
        st = self.c.diffstat(text)
        self.assertEqual(st["deleted_paths"], ["gone.py"])
        self.assertEqual(st["files"], 3)

    def test_quoted_git_path_is_unquoted(self):
        self.assertEqual(self.c.unquote_git_path('"src/\\320\\275.py"'), "src/н.py")
        text = 'diff --git "a/src/\\320\\275.py" "b/src/\\320\\275.py"\n--- "a/src/\\320\\275.py"\n+++ "b/src/\\320\\275.py"\n@@ -1 +1 @@\n-a\n+b\n'
        self.assertEqual([f["path"] for f in self.c.patch_files(text)], ["src/н.py"])

    def test_missing_tool_requires_tool_name_in_line(self):
        self.assertTrue(self.c.missing_tool("bash: pytest: command not found", "pytest -q"))
        self.assertTrue(self.c.missing_tool("./gradlew: No such file or directory", "./gradlew test"))
        self.assertIsNone(self.c.missing_tool("FileNotFoundError: No such file or directory: 'data.csv'", "pytest -q"))
        self.assertTrue(self.c.missing_tool("", "x", rc=127))


class ScopeTests(Base):
    def test_outside_plan_fails_and_sdlc_is_excluded(self):
        p = self.write_patch({"src/app.py": (["a"], ["b"]), "src/other.py": (["x"], ["y"]),
                              ".sdlc/S-1/chunk-1-journal.md": (None, ["j"])})
        rc, res, _ = run("scope.py", "outside", self.root, "S-1", p)
        self.assertEqual((rc, res["status"]), (1, "fail"))
        self.assertEqual(res["evidence"], ["src/other.py"])

    def test_foreign_change_from_baseline_does_not_fail(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "other.py").write_bytes(b"y\n")
        h = hashlib.sha256(b"y\n").hexdigest()
        (self.d / ".chunk-1-baseline.json").write_text(json.dumps({"src/other.py": h}), encoding="utf-8")
        p = self.write_patch({"src/app.py": (["a"], ["b"]), "src/other.py": (["x"], ["y"])})
        rc, res, _ = run("scope.py", "outside", self.root, "S-1", p)
        self.assertEqual((rc, res["status"]), (0, "pass"))
        self.assertIn("из базы chunk'а", res["evidence"][0])

    def test_unchanged_plan_paths(self):
        p = self.write_patch({"src/app.py": (["a"], ["b"])})
        rc, res, _ = run("scope.py", "unchanged", self.root, "S-1", p)
        self.assertEqual((rc, res["evidence"]), (1, ["tests/test_app.py"]))
        p = self.write_patch({"src/app.py": (["a"], ["b"]), "tests/test_app.py": (["t"], ["t2"])})
        self.assertEqual(run("scope.py", "unchanged", self.root, "S-1", p)[0], 0)


class AntiBypassTests(Base):
    def test_clean_patch_passes(self):
        p = self.write_patch({"tests/test_app.py": (["def test_a():", "    assert 1"],
                                                    ["def test_a():", "    assert 1", "def test_b():", "    assert 2"])})
        rc, res, _ = run("anti_bypass.py", p)
        self.assertEqual((rc, res["status"]), (0, "pass"))

    def test_skip_markers_by_stack_in_test_files(self):
        for line in ["@pytest.mark.skip", "@Disabled", "it.skip('x', () => {})", "xit('x')", "t.Skip()",
                     "#[ignore]", "describe.only('x')"]:
            p = self.write_patch({"tests/test_app.py": (["x"], ["x", line])})
            rc, res, _ = run("anti_bypass.py", p)
            self.assertEqual(rc, 1, line)
            self.assertIn("маркер", res["evidence"][0])

    def test_production_code_is_not_scanned_for_markers(self):
        for line in ["Model.objects.exclude(deleted=True)", "stream.skip(1)", "const pending = []",
                     "if status == 'pending':", "docker build --skip-cache"]:
            p = self.write_patch({"src/app.py": (["x"], ["x", line])})
            rc, res, _ = run("anti_bypass.py", p)
            self.assertEqual((rc, res["status"]), (0, "pass"), line)
        # но в конфиге тестового раннера маркер — обход
        p = self.write_patch({"build.gradle": (["x"], ["x", "test { exclude 'com/acme/**' }"])})
        self.assertEqual(run("anti_bypass.py", p)[0], 1)

    def test_commented_assert(self):
        p = self.write_patch({"tests/test_app.py": (["    assert total == 5"], ["    # assert total == 5"])})
        rc, res, _ = run("anti_bypass.py", p)
        self.assertEqual(rc, 1)
        self.assertIn("комментарий", res["evidence"][0])

    def test_deleted_test_file_and_net_loss(self):
        p = self.write_patch({"tests/test_app.py": (["def test_a():", "    assert 1"], None)})
        rc, res, _ = run("anti_bypass.py", p)
        self.assertEqual(rc, 1)
        self.assertTrue(any("удалён целиком" in e for e in res["evidence"]))
        self.assertTrue(any("нетто-убыль" in e for e in res["evidence"]))
        rc, res, _ = run("anti_bypass.py", p, "--allow-net-loss")
        self.assertFalse(any("нетто-убыль" in e for e in res["evidence"]))
        # удаление продакшн-файла с «test_» в имени — не тестовый файл
        p = self.write_patch({"src/latest_prices.py": (["x"], None)})
        rc, res, _ = run("anti_bypass.py", p)
        self.assertFalse(any("удалён целиком" in e for e in res["evidence"]))


class SecretsTests(Base):
    def test_secret_kinds(self):
        for line in ['AWS_KEY = "AKIAABCDEFGHIJKLMNOP"', "-----BEGIN RSA PRIVATE KEY-----",
                     'token = "ghp_abcdefghijklmnopqrstuvwxyz0123"', 'password = "hunter2hunter2"',
                     "DATABASE_URL=postgres://app:s3cretpass@db/app"]:
            p = self.write_patch({"src/app.py": (["x"], ["x", line])})
            rc, res, _ = run("secrets.py", p)
            self.assertEqual(rc, 1, line)
        p = self.write_patch({"src/app.py": (["x"], ["x", 'password = "hunter2hunter2"  # sdlc:not-a-secret'])})
        self.assertEqual(run("secrets.py", p)[0], 0)
        p = self.write_patch({"src/app.py": (['password = "hunter2hunter2"'], ["x"])})
        self.assertEqual(run("secrets.py", p)[0], 0)


class UntrackedTests(Base):
    def test_untracked_outside_plan(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root, check=True)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text("t\n", encoding="utf-8")
        (self.root / "stray.txt").write_text("x\n", encoding="utf-8")
        (self.root / "noise.log").write_text("x\n", encoding="utf-8")
        rc, res, _ = run("untracked.py", self.root, "S-1")
        self.assertEqual(rc, 1)
        self.assertEqual(res["evidence"], ["stray.txt"])
        (self.root / "stray.txt").unlink()
        rc, res, _ = run("untracked.py", self.root, "S-1")
        self.assertEqual((rc, res["status"]), (0, "pass"))


class TestsClaimsTests(Base):
    def test_claim_ids_required_and_must_exist(self):
        p = self.write_patch({"tests/test_app.py": (["x"], ["x", "def test_claim_1_ok():", "    pass",
                                                            "def test_unlabeled():", "    pass",
                                                            "@Tag(\"claim-9\")", "@Test", "void nine() {}"])})
        rc, res, _ = run("tests_claims.py", self.root, "S-1", p)
        self.assertEqual(rc, 1)
        self.assertTrue(any("test_unlabeled" in e for e in res["evidence"]))
        self.assertTrue(any("claim-9 нет в задаче" in e for e in res["evidence"]))
        p = self.write_patch({"tests/test_app.py": (["x"], ["x", "def test_claim_1_ok():", "    pass",
                                                            "it('claim-2: rejects empty', () => {})"])})
        rc, res, out = run("tests_claims.py", self.root, "S-1", p)
        self.assertEqual((rc, res["status"]), (0, "pass"))
        self.assertIn("claim-2", out)

    def test_junit_tag_after_test_annotation(self):
        p = self.write_patch({"src/test/java/XTest.java": (["x"], ["x", "@Test", "@Tag(\"claim-2\")",
                                                                     "void replayReturnsFirst() {", "}"])})
        rc, res, out = run("tests_claims.py", self.root, "S-1", p)
        self.assertEqual((rc, res["status"]), (0, "pass"), out)
        self.assertIn("replayReturnsFirst", out)


if __name__ == "__main__":
    unittest.main()
