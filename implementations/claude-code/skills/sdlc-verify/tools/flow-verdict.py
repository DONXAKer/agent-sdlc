#!/usr/bin/env python3
"""flow-verdict: механическая проверка артефактов витка Agent-SDLC.

Исполнение гейтов «Сверка отчёта с набором» и механической части «Заполненности артефактов»,
а также вердикт e2e-прогона: нет артефакта — нет шага; решения человека записаны; попытки
не затёрты; claims сквозные. Каждое падение мапится на слабость флоу.

Запуск:
  python3 flow-verdict.py [--at verify|handoff] <корень проекта> <slug>
    --at handoff (умолчание) — полный контракт завершённого витка;
    --at verify — середина витка, этап 6: без проверок handoff'а.
  python3 flow-verdict.py --print budget|attempts|action|approved <корень> <slug>
    печатает одно значение для оркестраторов (раннер e2e) — единственный парсер артефактов,
    чтобы bash не разбирал те же форматы вторым языком.
Код возврата: 0 — контракт цел / значение напечатано; 1 — есть провалы; 2 — неверный вызов.
"""

import re
import sys
from pathlib import Path

PLACEHOLDER = "‹"

# Обязательный минимум гейтов — дословно из SDLC.md → «Набор гейтов проекта»
MANDATORY = [
    "Сборка", "Тесты", "Scope: файлы вне плана", "Анти-обход тест-гейта",
    "Ревью независимым агентом",
]


def natsorted(paths):
    """Сортировка версионированных имён: attempt-10 позже attempt-9, не раньше attempt-2."""
    return sorted(paths, key=lambda p: [int(t) if t.isdigit() else t
                                        for t in re.split(r"(\d+)", p.name)])


def read(p: Path):
    return p.read_text(encoding="utf-8") if p.exists() else None


def strip_literal_spans(line: str) -> str:
    """Убрать код в бэктиках и цитаты в «ёлочках» — там ‹…› законно упоминается как символ."""
    line = re.sub(r"`[^`]*`", "", line)
    line = re.sub(r"«[^»]*»", "", line)
    return line


def holes_in(text: str):
    """Строки с настоящими незаполненными ‹…› (вне кода и цитат)."""
    return [ln for ln in text.splitlines() if PLACEHOLDER in strip_literal_spans(ln)]


def md_sections(text: str):
    """Разбивка по заголовкам второго уровня; ### остаётся внутри своей секции."""
    return re.split(r"^## ", text, flags=re.M)


def claims_in(text):
    return set(re.findall(r"\bclaim-\d+\b", text or ""))


def gate_row_in(report: str, gate: str) -> bool:
    """Строка таблицы, начинающаяся с имени гейта, — а не упоминание в прозе."""
    return re.search(rf"^\|\s*{re.escape(gate)}\s*\|", report, flags=re.M) is not None


def parse_date(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- --print для оркестраторов

def do_print(what: str, root: Path, slug: str) -> int:
    d = root / ".sdlc" / slug
    journals = natsorted(d.glob("chunk-*-journal.md"))
    j = read(journals[-1]) if journals else None
    if what == "budget":
        m = re.search(r"Бюджет попыток:\*?\*?[^0-9]*(\d+)", j or "")
        print(m.group(1) if m else "3")
        return 0
    if what == "attempts":
        print(len(re.findall(r"^\|\s*\d+\s*\|", j or "", flags=re.M)))
        return 0
    if what == "action":
        reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
        r = read(reports[-1]) if reports else ""
        # якорь в конец строки отвергает шаблонную заготовку «continue / retry / escalate»
        m = re.search(r"\*\*action:\*\*\s*(continue|retry|escalate)\s*$", r or "", flags=re.M)
        print(m.group(1) if m else "none")
        return 0
    if what == "approved":
        plan = read(d / "plan.md") or ""
        line = next((ln for ln in plan.splitlines() if "**Одобрение:**" in ln), "")
        print("yes" if re.search(r"20\d\d", line) else "no")
        return 0
    print(__doc__)
    return 2


# ---------------------------------------------------------------- вердикт

class Verdict:
    def __init__(self):
        self.passed = []
        self.failed = []  # (check, detail, improvement)

    def ok(self, check):
        self.passed.append(check)

    def fail(self, check, detail, improvement):
        self.failed.append((check, detail, improvement))

    def check(self, cond, name, detail, improvement):
        (self.ok if cond else lambda n: self.fail(n, detail, improvement))(name)


def main(root: Path, slug: str, at: str = "handoff") -> int:
    v = Verdict()
    full = at == "handoff"
    sdlc = root / ".sdlc"
    d = sdlc / slug

    gates = read(sdlc / "gates.md")
    intent = read(d / "intent.md")
    readiness = read(d / "readiness.md")
    plan = read(d / "plan.md")
    handoff = read(d / "handoff.md")
    journals = natsorted(d.glob("chunk-*-journal.md"))
    journal_texts = {p: read(p) for p in journals}
    reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
    last_report = read(reports[-1]) if reports else None
    diffs = natsorted(d.glob("chunk-*-attempt-*-diff.patch"))
    tests_out = natsorted(d.glob("chunk-*-attempt-*-tests.txt"))

    # --- 1. Артефакты на месте и непусты: нет файла (или он пуст) — шага не было -----------
    for name, obj, imp in [
        ("gates.md", gates, "виток стартовал без набора гейтов — усилить предусловие /sdlc-intent"),
        ("intent.md", intent, "этап 1 не оставил задачи"),
        ("readiness.md", readiness,
         "решение о готовности не записано — перенос статуса «Готовность задачи» слеп"),
        ("plan.md", plan, "этап 4 не оставил плана"),
    ] + ([("handoff.md", handoff,
           "виток оборвался без передачи контекста — усилить правило обрыва")] if full else []):
        v.check(bool(obj), f"артефакт {name}", "файла нет или он пуст", imp)
    v.check(bool(journals), "журнал chunk'а",
            "chunk-N-journal.md нет — место правки и счёт попыток нигде не записаны",
            "журнал должен заводиться с первой попытки")
    v.check(bool(reports), "отчёт(ы) приёмки", "ни одного verification-report-*-attempt-*.md",
            "этап 6 не оставил артефакта")
    v.check(bool(diffs) and bool(tests_out), "diff и вывод тестов попыток",
            "нет файлов attempt-K", "артефакты попыток не сохраняются — детект прогресса слеп")

    # --- 2. Плейсхолдеры — по всем артефактам витка -----------------------------------------
    scan = {p.name: read(p) for p in natsorted(d.glob("*.md"))}
    scan["gates.md"] = gates
    for name, text in scan.items():
        if not text:
            continue
        holes = holes_in(text)
        detail = (f"{len(holes)} строк с плейсхолдером, первая: {holes[0].strip()[:60]}"
                  if holes else "")
        v.check(not holes, f"нет ‹…› в {name}", detail,
                "«заполненность артефактов» не выдерживается")

    # --- 3. Решения человека записаны --------------------------------------------------------
    if plan:
        line = next((ln for ln in plan.splitlines() if "**Одобрение:**" in ln), "")
        v.check(bool(re.search(r"20\d\d", line)),
                "одобрение плана записано", "в строке «Одобрение» нет даты",
                "одобрение живёт в чате, а не в артефакте — чинить /sdlc-plan Phase 5")
    if handoff and full:
        v.check(bool(re.search(r"\*\*Приёмка:\*\*", handoff)),
                "приёмка записана в handoff", "поля «Приёмка» нет",
                "приёмка человеком не фиксируется — чинить /sdlc-handoff Phase 1")
    for p in journals:
        j = journal_texts[p] or ""
        sig = j.split("Подтвердил:")[1][:80] if "Подтвердил:" in j else ""
        v.check(bool(sig) and PLACEHOLDER not in strip_literal_spans(sig),
                f"место правки подтверждено ({p.name})", "секция «Место правки» без подписи",
                "подтверждение места правки не записывается — чинить /sdlc-chunk Phase 2")
    if readiness:
        for n in (1, 2):
            m = re.search(rf"\*\*Вердикт прогона {n}:\*\*\s*(.+)", readiness)
            ok_val = bool(m) and m.group(1).strip().startswith("готова")
            v.check(ok_val, f"readiness: прогон {n} — «готова»",
                    "вердикт отсутствует или не «готова»" if not m
                    else f"вердикт: {m.group(1).strip()[:50]}",
                    "виток идёт с непринятой готовностью — чинить предусловия этапов 2/4")

    # --- 4. Попытки: нумерация цела, ничего не затёрто — по каждому chunk'у ------------------
    for p in journals:
        j = journal_texts[p] or ""
        n = re.search(r"chunk-(\d+)-journal", p.name).group(1)
        rows = re.findall(r"^\|\s*(\d+)\s*\|", j, flags=re.M)
        v.check(bool(rows), f"в журнале chunk-{n} есть строки попыток",
                "таблица «Попытки» пуста", "счёт попыток не ведётся")
        for k in range(1, len(rows) + 1):
            v.check((d / f"chunk-{n}-attempt-{k}-diff.patch").exists(),
                    f"diff chunk-{n} попытки {k} сохранён", "файла нет",
                    "попытки перезаписывают друг друга — детект прогресса слеп")
    if journals:
        v.check(len(reports) >= 1 and all("attempt" in r.name for r in reports),
                "отчёты приёмки по попыткам", "имена без attempt-K",
                "история возвратов стирается")

    # --- 5. Claims сквозные + структура листа -------------------------------------------------
    if intent and plan and last_report:
        ci, cp, cr = claims_in(intent), claims_in(plan), claims_in(last_report)
        v.check(ci == cp, "claims intent == plan",
                f"intent {sorted(ci - cp)} vs plan {sorted(cp - ci)}",
                "план потерял или выдумал пункты")
        v.check(ci == cr, "claims intent == verification",
                f"разница: {sorted(ci ^ cr)}",
                "отчёт приёмки не покрывает лист 1:1")
    if intent:
        # структура листа: держится и после правок этапов 2–3 (прогон 2 проверяет то же)
        rows = [ln for ln in intent.splitlines() if re.match(r"^\|\s*claim-\d+\s*\|", ln)]
        cells_ok = all(len([c for c in r.split("|") if c.strip()]) >= 3 for r in rows)
        small = bool(re.search(r"\*\*Контур:\*\*\s*мелкий", intent))
        edge = sum("[edge]" in r for r in rows)
        v.check(bool(rows), "в листе есть строки claim-N", "таблица листа пуста или не по форме",
                "лист не в канонической форме — сверки слепнут")
        v.check(cells_ok, "у каждого claim заполнено «Как проверить»",
                "есть строка листа без процедуры проверки",
                "структура листа сломана правкой после прогона 1")
        if not small:
            v.check(len(rows) >= 3 and edge >= 2, "полный контур: ≥3 пунктов, ≥2 [edge]",
                    f"пунктов {len(rows)}, [edge] {edge}",
                    "правка листа после прогона 1 уронила структуру ниже минимума")

    # --- 6. Гейты: минимум включён; включённые «этап 6» имеют строку в отчёте -----------------
    if gates:
        for g in MANDATORY:
            v.check(bool(re.search(rf"^\|\s*{re.escape(g)}\s*\|\s*да\s*\|", gates, flags=re.M)),
                    f"минимум: «{g}» включён", "строка не «да»",
                    "обязательный минимум выключен среди витка — набор не собран")
    if gates and last_report:
        enabled = [m.group(1).strip() for m in
                   re.finditer(r"^\|\s*([^|]+?)\s*\|\s*да\s*\|\s*этап 6\s*\|", gates, flags=re.M)]
        # «гейт введён после отчёта» выводится из дат: строка журнала о включении датирована
        # позже, чем «Набор гейтов: от ‹дата›» в шапке отчёта
        report_gates_date = parse_date(
            next((ln for ln in last_report.splitlines() if "Набор гейтов:" in ln), ""))
        enabled_rows = {}  # гейт -> (последняя дата включения, причина)
        for m in re.finditer(
                r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|[^|]*→\s*да\s*\|\s*([^|]*)\|",
                gates, flags=re.M):
            g, date, reason = m.group(2).strip(), m.group(1), m.group(3)
            if date >= enabled_rows.get(g, ("", ""))[0]:
                enabled_rows[g] = (date, reason)
        for g in enabled:
            date, reason = enabled_rows.get(g, ("", ""))
            # позже даты набора из отчёта — введён после; та же дата различима только по причине:
            # запись ссылается на текущий виток (его записи о дефектах заводит этап 7)
            late = bool(report_gates_date) and (
                date > report_gates_date
                or (date == report_gates_date and slug in reason))
            if late and not gate_row_in(last_report, g):
                v.ok(f"гейт «{g}» включён после отчёта ({date}) — строка не требуется")
                continue
            v.check(gate_row_in(last_report, g), f"строка гейта «{g}» в отчёте", "строки нет",
                    "включённый гейт без строки — сверка отчёта с набором должна быть механической")
        v.check(bool(re.search(r"\*\*passed:\*\*", last_report)), "вердикт в отчёте",
                "поля **passed:** нет", "вердикт не посчитан")

    # --- 7. Scope: .sdlc не в files_to_touch ---------------------------------------------------
    if plan:
        section = next((s for s in md_sections(plan) if s.startswith("files_to_touch")), "")
        table_rows = [ln for ln in section.splitlines() if ln.strip().startswith("|")]
        v.check(not any(".sdlc/" in ln for ln in table_rows),
                "files_to_touch без .sdlc/**", ".sdlc в таблице путей",
                "артефакты процесса попали в scope кода")

    # --- Отчёт ----------------------------------------------------------------
    print(f"# Вердикт флоу: {slug} (--at {at})\n")
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
    args = sys.argv[1:]
    at, mode = "handoff", None
    pos = []
    i = 0
    while i < len(args):
        if args[i] == "--at" and i + 1 < len(args):
            at = args[i + 1]; i += 2
        elif args[i] == "--print" and i + 1 < len(args):
            mode = args[i + 1]; i += 2
        else:
            pos.append(args[i]); i += 1
    if at not in ("verify", "handoff") or len(pos) != 2:
        print(__doc__)
        sys.exit(2)
    if mode:
        sys.exit(do_print(mode, Path(pos[0]), pos[1]))
    sys.exit(main(Path(pos[0]), pos[1], at))
