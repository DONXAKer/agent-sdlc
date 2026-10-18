"""Тесты scaffold.py (бланк до этапа, механические поля — программа) и intent-sections.py."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools"
SCAFFOLD, SECTIONS = TOOLS / "scaffold.py", TOOLS / "intent-sections.py"
CONST = json.loads((REPO / "sdlc-constants.json").read_text(encoding="utf-8"))

GATES = """# Набор гейтов: t

- **Последнее изменение набора:** 2026-08-01

| Гейт | Вкл | Где отчитывается | Чем реализован |
|---|---|---|---|
| Сборка | да — минимум | этап 6 | `true` |
| Тесты | да — минимум | этап 6 | `true` |
| Scope: файлы вне плана | да — минимум | этап 6 | x |
| Анти-обход тест-гейта | да — минимум | этап 6 | x |
| Ревью независимым агентом | да — минимум | этап 6 | x |
| Секреты в diff | да | этап 6 | x |
| Готовность задачи | да | этап 1 | x |
| Разбор последствий | {axes} | этап 4 | x |
| Бюджет итераций chunk'а | да | этап 5 | 5 попыток |
"""
INTENT = """# Задача: t

- **Контур:** полный
- **Ветка витка:** sdlc/T-1

## Коротко
x

## Приёмочный лист

| id | Пункт | Как проверить |
|----|-------|---|
| claim-1 | Повтор с тем же ключом возвращает первый результат | тест |
| claim-2 [edge] | Пустой ключ отвергается | тест |

## Что придётся тронуть

- ‹path›

## Открытые вопросы

- нет открытых вопросов
"""


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / "f").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"], cwd=self.root, check=True)
        self.d = self.root / ".sdlc" / "T-1"
        self.d.mkdir(parents=True)
        (self.root / ".sdlc" / "gates.md").write_text(GATES.format(axes="да"), encoding="utf-8")
        (self.d / "intent.md").write_text(INTENT, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_stage_fills_its_mechanics(self):
        # раскладка каждого этапа обязана заполнить свои механические поля (код 0), иначе шаблон разошёлся
        for stage, extra in [("plan", []), ("exploration-report", []), ("clarification-report", []),
                             ("readiness", []), ("chunk-journal", ["--chunk", "2"]),
                             ("verification-report", ["--chunk", "2", "--attempt", "3"]), ("handoff", [])]:
            proc = run(SCAFFOLD, self.root, stage, "T-1", *extra)
            self.assertEqual(proc.returncode, 0, f"{stage}: {proc.stdout}{proc.stderr}")
            self.assertNotIn("ВНИМАНИЕ", proc.stdout, stage)

    def test_plan_gets_base_sha_claims_and_axes(self):
        proc = run(SCAFFOLD, self.root, "plan", "T-1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        plan = (self.d / "plan.md").read_text(encoding="utf-8")
        self.assertRegex(plan, r"\*\*База:\*\* [0-9a-f]{40}")
        self.assertIn("отчёт разведки нет", plan)
        self.assertIn("| claim-1 | ‹тест / место в коде› |", plan)
        self.assertIn("| claim-2 | ‹тест / место в коде› |", plan)
        for ax in CONST["axes"]:
            self.assertIn(f"| {ax} |", plan)
        self.assertIn("**Одобрение:** ‹имя› · ‹дата›", plan)  # поле решения человека не тронуто
        self.assertIn("# План: T-1", plan)
        self.assertEqual(run(SCAFFOLD, self.root, "plan", "T-1").returncode, 1)

    def test_plan_without_axes_gate_collapses_section(self):
        (self.root / ".sdlc" / "gates.md").write_text(GATES.format(axes="нет"), encoding="utf-8")
        run(SCAFFOLD, self.root, "plan", "T-1")
        plan = (self.d / "plan.md").read_text(encoding="utf-8")
        self.assertIn("⏭ — гейт в долге", plan)
        self.assertNotIn("| Безопасность |", plan)
        self.assertIn("## Контекст-пакет", plan)

    def test_journal_keeps_generic_attempt_number(self):
        run(SCAFFOLD, self.root, "plan", "T-1")
        plan_p = self.d / "plan.md"
        plan_p.write_text(plan_p.read_text(encoding="utf-8").replace("**Одобрение:** ‹имя› · ‹дата›",
                                                                     "**Одобрение:** Тест · 2026-08-02"), encoding="utf-8")
        proc = run(SCAFFOLD, self.root, "chunk-journal", "T-1", "--chunk", "2")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        j = (self.d / "chunk-2-journal.md").read_text(encoding="utf-8")
        self.assertIn("# Журнал chunk'а 2", j)
        self.assertIn("**Бюджет попыток:** 5", j)
        self.assertIn("одобрение от 2026-08-02", j)
        self.assertIn("**Подтвердил:** ‹имя› · ‹дата›", j)
        self.assertIn("chunk-2-attempt-‹K›-diff.patch", j)              # ‹K› — обобщённый номер попытки
        self.assertIn("verification-report-2-attempt-‹K›.md", j)
        self.assertNotIn("attempt-1-diff", j)
        proc = run(SCAFFOLD, self.root, "verification-report", "T-1", "--chunk", "2", "--attempt", "3")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        r = (self.d / "verification-report-2-attempt-3.md").read_text(encoding="utf-8")
        for g in ["Сборка", "Тесты", "Scope: файлы вне плана", "Анти-обход тест-гейта",
                  "Ревью независимым агентом", "Секреты в diff"]:
            self.assertIn(f"| {g} | ‹✅/❌/⏭› |", r)
        self.assertNotIn("| Готовность задачи | ‹✅/❌/⏭› |", r)
        self.assertIn("| Готовность задачи | 1 | ‹✅/❌/⏭› | `readiness.md` |", r)
        self.assertIn("| Бюджет итераций chunk'а | 5 |", r)
        self.assertIn("| claim-1 | Повтор с тем же ключом… |", r)
        self.assertIn("**Попытка:** 3 из 5", r)
        self.assertIn("chunk-2-attempt-3-diff.patch", r)
        self.assertIn("от 2026-08-01 — совпадает с журналом набора: ‹да/нет›", r)

    def test_handoff_numbers_loops_and_counts_attempts_like_flow_verdict(self):
        run(SCAFFOLD, self.root, "plan", "T-1")
        (self.d / "chunk-1-journal.md").write_text(
            "# Журнал chunk'а 1\n\n## Попытки\n\n| K | Дата | Что | Что | Итог |\n|---|---|---|---|---|\n"
            "| 1 | 2026-08-01 | первая | н/п | retry |\n| 2 | 01.08.2026 | вторая | x | retry |\n| 3 |  | третья | y | passed |\n"
            "| 4 | ‹дата› | образец | ‹…› | ‹…› |\n", encoding="utf-8")
        self.assertEqual(run(SCAFFOLD, self.root, "handoff", "T-1").returncode, 0)
        self.assertEqual(run(SCAFFOLD, self.root, "handoff", "T-1").returncode, 0)   # без --run: номер выводится сам
        self.assertEqual(run(SCAFFOLD, self.root, "handoff", "T-1", "--run", "7").returncode, 0)
        h = (self.d / "handoff.md").read_text(encoding="utf-8")
        self.assertEqual([m for m in re.findall(r"^## Виток (\d+)", h, re.M)], ["1", "2", "7"])
        self.assertIn("slug: T-1", h)
        self.assertIn("branch: sdlc/T-1", h)
        self.assertIn("attempts: 3", h)  # как attempt_nums: строка-образец с ‹дата› не попытка, дата не обязательна
        self.assertIn("**Приёмка:** принял ‹имя› · ‹дата›", h)

    def test_bad_argv_is_exit_2(self):
        self.assertEqual(run(SCAFFOLD, self.root, "chunk-journal", "T-1", "--chunk").returncode, 2)
        self.assertEqual(run(SCAFFOLD, self.root, "nope", "T-1").returncode, 2)


class IntentSectionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / ".sdlc" / "T-1"
        self.d.mkdir(parents=True)
        (self.d / "intent.md").write_text(INTENT, encoding="utf-8")
        self.assertEqual(run(SECTIONS, "--snapshot", self.root, "T-1").returncode, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        (self.d / "intent.md").write_text(text, encoding="utf-8")

    def check(self):
        proc = run(SECTIONS, "--check", self.root, "T-1")
        return proc.returncode, json.loads(proc.stdout)

    def test_legal_edits(self):
        self.write(INTENT.replace("- ‹path›", "- src/app.py — правка").replace(
            "- нет открытых вопросов", "- [x] **[блокирующий]** TTL? → 24 часа"))
        rc, res = self.check()
        self.assertEqual((rc, res["legal"]), (0, True), res)
        self.assertEqual(sorted(res["allowed"]), ["Открытые вопросы", "Что придётся тронуть"])

    def test_silent_rewrite_of_claims_is_illegal(self):
        self.write(INTENT.replace("Пустой ключ отвергается", "Пустой ключ принимается"))
        rc, res = self.check()
        self.assertEqual((rc, res["illegal"]), (1, ["Приёмочный лист"]))

    def test_added_claim_row_is_legal_edit_three(self):
        # человек дописал пункт по находке гейта этапа 4 — старые строки не тронуты
        self.write(INTENT.replace("| claim-2 [edge] | Пустой ключ отвергается | тест |",
                                  "| claim-2 [edge] | Пустой ключ отвергается | тест |\n| claim-3 [edge] | Повтор при снижении лимита | тест |"))
        rc, res = self.check()
        self.assertEqual((rc, res["legal"], res["additive_claims"]), (0, True, ["Приёмочный лист"]), res)
        # дописал и одновременно переписал старый — уже не дополнение
        self.write(INTENT.replace("| claim-2 [edge] | Пустой ключ отвергается | тест |",
                                  "| claim-2 [edge] | Пустой ключ принимается | тест |\n| claim-3 [edge] | новый | тест |"))
        rc, res = self.check()
        self.assertEqual((rc, res["illegal"]), (1, ["Приёмочный лист"]))

    def test_rewording_needs_a_new_approval_each_time(self):
        self.write(INTENT.replace("Пустой ключ отвергается", "Пустой ключ отвергается с 400 (уточнено с одобрения Тест · 2026-08-02)"))
        rc, res = self.check()
        self.assertEqual((rc, res["approved_rewording"]), (0, ["Приёмочный лист"]))
        # снимок заново (этап 4 после одобрения плана) — следующее переписывание без новой записи незаконно
        self.assertEqual(run(SECTIONS, "--snapshot", self.root, "T-1").returncode, 0)
        self.write((self.d / "intent.md").read_text(encoding="utf-8").replace("Повтор с тем же ключом", "Повтор с любым ключом"))
        rc, res = self.check()
        self.assertEqual((rc, res["illegal"]), (1, ["Приёмочный лист"]))


if __name__ == "__main__":
    unittest.main()
