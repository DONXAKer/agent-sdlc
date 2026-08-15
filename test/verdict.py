#!/usr/bin/env python3
"""Вердикт e2e-прогона Agent-SDLC: механическая проверка артефактов витка.

Проверяет контракт флоу по файлам .sdlc/<slug>/ — то же, что обещает методология:
нет артефакта — нет шага; решения человека записаны; попытки не затёрты; claims сквозные.
Каждое падение мапится на слабость флоу и попадает в раздел «как улучшить».

Запуск: python3 verdict.py <корень проекта прогона> <slug>
Код возврата: 0 — контракт цел; 1 — есть провалы.
"""

import re
import sys
from pathlib import Path

PLACEHOLDER = "‹"  # ‹


class Verdict:
    def __init__(self):
        self.passed = []
        self.failed = []  # (check, detail, improvement)

    def ok(self, check):
        self.passed.append(check)

    def fail(self, check, detail, improvement):
        self.failed.append((check, detail, improvement))

    def check(self, cond, name, detail, improvement):
        if cond:
            self.ok(name)
        else:
            self.fail(name, detail, improvement)


def read(p: Path):
    return p.read_text(encoding="utf-8") if p.exists() else None


def claims_in(text):
    return set(re.findall(r"\bclaim-\d+\b", text or ""))


def main(root: Path, slug: str) -> int:
    v = Verdict()
    sdlc = root / ".sdlc"
    d = sdlc / slug

    # --- 1. Артефакты на месте: нет файла — шага не было -------------------
    gates = read(sdlc / "gates.md")
    intent = read(d / "intent.md")
    readiness = read(d / "readiness.md")
    plan = read(d / "plan.md")
    handoff = read(d / "handoff.md")
    journals = sorted(d.glob("chunk-*-journal.md"))
    reports = sorted(d.glob("verification-report-*-attempt-*.md"))
    diffs = sorted(d.glob("chunk-*-attempt-*-diff.patch"))
    tests_out = sorted(d.glob("chunk-*-attempt-*-tests.txt"))

    for name, obj, imp in [
        ("gates.md", gates, "виток стартовал без набора гейтов — усилить предусловие /sdlc-intent"),
        ("intent.md", intent, "этап 1 не оставил задачи"),
        ("readiness.md", readiness,
         "решение о готовности не записано — перенос статуса «Готовность задачи» слеп"),
        ("plan.md", plan, "этап 4 не оставил плана"),
        ("handoff.md", handoff, "виток оборвался без передачи контекста — усилить правило обрыва"),
    ]:
        v.check(obj is not None, f"артефакт {name}", "файла нет", imp)
    v.check(bool(journals), "журнал chunk'а",
            "chunk-N-journal.md нет — место правки и счёт попыток нигде не записаны",
            "журнал должен заводиться с первой попытки")
    v.check(bool(reports), "отчёт(ы) приёмки", "ни одного verification-report-*-attempt-*.md",
            "этап 6 не оставил артефакта")
    v.check(bool(diffs) and bool(tests_out), "diff и вывод тестов попыток",
            "нет файлов attempt-K", "артефакты попыток не сохраняются — детект прогресса слеп")

    # --- 2. Плейсхолдеры ----------------------------------------------------
    for name, obj in [("intent.md", intent), ("plan.md", plan), ("handoff.md", handoff),
                      ("readiness.md", readiness)]:
        if obj is None:
            continue
        # ‹…› в бэктиках — упоминание символа, не дыра
        holes = [ln for ln in obj.splitlines()
                 if PLACEHOLDER in ln and f"`{PLACEHOLDER}" not in ln
                 and "grep" not in ln and not ln.strip().startswith("_")
                 and "«" + PLACEHOLDER not in ln]
        v.check(not holes, f"нет ‹…› в {name}", f"{len(holes)} строк с плейсхолдером",
                "«заполненность артефактов» не выдерживается — включить гейт")

    # --- 3. Решения человека записаны ---------------------------------------
    if plan:
        appr = re.search(r"\*\*Одобрение:\*\*\s*(.+)", plan)
        v.check(bool(appr) and "не одобрен" not in (appr.group(1) if appr else "").lower(),
                "одобрение плана записано", "поле «Одобрение» пусто или «не одобрен»",
                "одобрение живёт в чате, а не в артефакте — чинить /sdlc-plan Phase 5")
    if handoff:
        acc = re.search(r"\*\*Приёмка:\*\*\s*(.+)", handoff)
        v.check(bool(acc), "приёмка записана в handoff", "поля «Приёмка» нет",
                "приёмка человеком не фиксируется — чинить /sdlc-handoff Phase 1")
    if journals:
        j = read(journals[0])
        v.check("Подтвердил:" in j and PLACEHOLDER not in j.split("Подтвердил:")[1][:80],
                "место правки подтверждено в журнале", "секция «Место правки» без подписи",
                "подтверждение места правки не записывается — чинить /sdlc-chunk Phase 2")
    if readiness:
        v.check("Прогон 1" in readiness and "Вердикт прогона 1" in readiness,
                "readiness: прогон 1", "нет секции прогона 1", "чек-лист готовности без протокола")
        v.check("Прогон 2" in readiness and "Вердикт прогона 2" in readiness,
                "readiness: прогон 2", "нет секции прогона 2",
                "второй прогон готовности пропущен — чинить /sdlc-plan Phase 0")

    # --- 4. Попытки: нумерация цела, ничего не затёрто -----------------------
    if journals:
        j = read(journals[0])
        rows = re.findall(r"^\|\s*(\d+)\s*\|", j, flags=re.M)
        attempts = len(rows)
        v.check(attempts >= 1, "в журнале есть строки попыток", "таблица «Попытки» пуста",
                "счёт попыток не ведётся")
        n = re.search(r"chunk-(\d+)-journal", journals[0].name).group(1)
        for k in range(1, attempts + 1):
            v.check((d / f"chunk-{n}-attempt-{k}-diff.patch").exists(),
                    f"diff попытки {k} сохранён", "файла нет",
                    "попытки перезаписывают друг друга — детект прогресса слеп")
        v.check(len(reports) >= 1 and all("attempt" in r.name for r in reports),
                "отчёты приёмки по попыткам", "имена без attempt-K",
                "история возвратов стирается")

    # --- 5. Claims сквозные --------------------------------------------------
    if intent and plan and reports:
        ci, cp = claims_in(intent), claims_in(plan)
        cr = claims_in(read(reports[-1]))
        v.check(ci == cp, "claims intent == plan",
                f"intent {sorted(ci - cp)} vs plan {sorted(cp - ci)}",
                "план потерял или выдумал пункты — сверку claims сделать гейтом")
        v.check(ci == cr, "claims intent == verification",
                f"разница: {sorted(ci ^ cr)}",
                "отчёт приёмки не покрывает лист 1:1 — сверку claims сделать гейтом")

    # --- 6. Гейты: включённые «этап 6» имеют строку в отчёте ------------------
    if gates and reports:
        rep = read(reports[-1])
        enabled = [m.group(1).strip() for m in
                   re.finditer(r"^\|\s*([^|]+?)\s*\|\s*да\s*\|\s*этап 6\s*\|", gates, flags=re.M)]
        # гейт, введённый записью о дефекте этого же витка (этап 7), в отчёт этапа 6 попасть не мог:
        # ловим по журналу набора — «нов…» в переходе или «запись о дефекте» в причине
        introduced_late = set(
            re.findall(r"^\|[^|]+\|\s*([^|]+?)\s*\|\s*[^|]*нов[^|]*\|", gates, flags=re.M)
        ) | set(
            re.findall(r"^\|[^|]+\|\s*([^|]+?)\s*\|[^|]*\|[^|]*запис[ьи] о дефекте[^|]*\|",
                       gates, flags=re.M)
        )
        for g in enabled:
            if g in introduced_late and g not in rep:
                v.ok(f"гейт «{g}» введён после отчёта (петля улучшения) — строка не требуется")
                continue
            v.check(g in rep, f"строка гейта «{g}» в отчёте", "строки нет",
                    "включённый гейт без строки — сверка отчёта с набором должна быть механической")
        v.check("passed" in rep.lower(), "вердикт в отчёте", "поля passed нет", "вердикт не посчитан")

    # --- 7. Scope: .sdlc не в files_to_touch ---------------------------------
    if plan and "## files_to_touch" in plan:
        section = plan.split("## files_to_touch")[1].split("## ")[0]
        table_rows = [ln for ln in section.splitlines() if ln.strip().startswith("|")]
        v.check(not any(".sdlc/" in ln for ln in table_rows),
                "files_to_touch без .sdlc/**", ".sdlc в таблице путей",
                "артефакты процесса попали в scope кода")

    # --- Отчёт ----------------------------------------------------------------
    print(f"# Вердикт e2e: {slug}\n")
    print(f"Пройдено проверок: {len(v.passed)} · Провалено: {len(v.failed)}\n")
    if v.failed:
        print("## Провалы и что улучшить во флоу\n")
        for check, detail, imp in v.failed:
            print(f"- ❌ **{check}** — {detail}\n  → улучшение: {imp}")
    else:
        print("Контракт флоу цел: все артефакты на месте, решения человека записаны, "
              "попытки не затёрты, claims сквозные, гейты отчитались.")
    print("\n## Пройдено\n")
    for c in v.passed:
        print(f"- ✅ {c}")
    return 1 if v.failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), sys.argv[2]))
