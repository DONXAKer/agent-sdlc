#!/usr/bin/env python3
"""flow-verdict: механическая проверка артефактов витка Agent-SDLC.

Исполнение гейтов «Сверка отчёта с набором» и механической части «Заполненности артефактов»,
а также вердикт e2e-прогона: нет артефакта — нет шага; решения человека записаны; попытки
не затёрты; claims сквозные. Каждое падение мапится на слабость флоу.

Запуск:
  python3 flow-verdict.py [--at verify|handoff] <корень проекта> <slug>
    --at handoff (умолчание) — полный контракт завершённого витка;
    --at verify — середина витка, этап 6: без проверок handoff'а.
  python3 flow-verdict.py --print budget|attempts|action|approved|chunk|readiness2|reports|next_chunk|all <корень> <slug>
    печатает одно значение для оркестраторов (раннер e2e) — единственный парсер артефактов;
    `all` печатает все значения разом строками key=value (один субпроцесс на итерацию раннера);
    и --print, и вердикт зовут ОДНИ И ТЕ ЖЕ хелперы разбора, форматы не расходятся.
Код возврата: 0 — контракт цел / значение напечатано; 1 — есть провалы; 2 — неверный вызов.
"""

import re
import sys
from pathlib import Path

PLACEHOLDER = "‹"

# Канонический минимум — жёсткий пол, дословно из SDLC.md → «Набор гейтов проекта».
# mandatory_gates() всегда добавляет его к пометкам набора (union): пометка «да — минимум»
# расширяет минимум, но снятие пометки от проверки не освобождает.
MANDATORY_FALLBACK = [
    "Сборка", "Тесты", "Scope: файлы вне плана", "Анти-обход тест-гейта",
    "Ревью независимым агентом",
]
DEFAULT_BUDGET = "3"  # норма SDLC.md → этап 6; шаблон журнала обязан совпадать

ATTEMPT_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.M)
# [^0-9\n‹] — не перескакивать на цифры следующих строк и не принимать ‹плейсхолдер› за значение
BUDGET_RE = re.compile(r"Бюджет попыток\*{0,2}:\*{0,2}[^0-9\n‹]*(\d+)")
# принять «retry — причина…» и «retry / см. отчёт», отвергнуть заготовку-триплет
# «continue / retry / escalate» (слэш + другое action-слово)
ACTION_RE = re.compile(
    r"\*\*action:\*\*\s*(continue|retry|escalate)\b(?!\s*/\s*(?:continue|retry|escalate))", re.M)
# «да» с человеческим комментарием («да (с 2026-08)», «да, см. журнал») — тоже включён:
# жёсткое «ровно да» молча выводило бы такой гейт из-под сверки. (?!\w) отсекает слова на «да…»
ENABLED_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*да(?!\w)[^|]*\|\s*этап 6\s*\|", re.M)
MANDATORY_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*да — минимум(?!\w)[^|]*\|", re.M)


def natsorted(paths):
    """attempt-10 позже attempt-9, а не раньше attempt-2."""
    return sorted(paths, key=lambda p: [int(t) if t.isdigit() else t
                                        for t in re.split(r"(\d+)", p.name)])


def read(p: Path):
    return p.read_text(encoding="utf-8") if p.exists() else None


# ------------------------------------------------------------------ разбор артефактов (единый)

def strip_literal_spans(line: str) -> str:
    """Убрать код в бэктиках и цитаты в «ёлочках», где ‹…› — упоминание символа.

    Осознанное ограничение: настоящая дыра ВНУТРИ «ёлочек» («…‹причина›…») механически
    неотличима от легитимной цитаты формы («Использовать ‹символ›» в подсказках шаблонов) —
    проверено на живых артефактах. Скрипт выбирает отсутствие ложных провалов; дыры внутри
    цитат остаются ручной части гейта «Заполненность артефактов» (верификатор + рецензент).
    """
    line = re.sub(r"`[^`]*`", "", line)
    line = re.sub(r"«[^»]*»", "", line)
    return line


def holes_in(text: str):
    """Строки с настоящими ‹…›: вне кода, вне цитат-упоминаний, вне fenced-блоков.

    Осознанные слепые зоны механики: ‹…› внутри «ёлочек» и внутри fenced-блоков ```…```.
    Дыры там остаются ручной части гейта (норма в SDLC.md): верификатор этапа 6 пробегает
    все артефакты витка, рецензент — свои четыре входных."""
    holes, fenced = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if PLACEHOLDER in strip_literal_spans(ln):
            holes.append(ln)
    return holes


def approval_line(plan: str) -> str:
    return next((ln for ln in (plan or "").splitlines() if "**Одобрение:**" in ln), "")


def plan_approved(plan: str) -> bool:
    """Одобрение — значащая часть строки до « / »-альтернативы шаблона.

    «Иван · 2026-08-15 / **не одобрен — …**» (не вычищенный хвост заготовки) — одобрен;
    «не одобрен — вернуться после 2026-08-20» — НЕ одобрен, дата в причине не спасает;
    «ожидается решение к 2026-08-20» — НЕ одобрен: слово ожидания сильнее даты;
    «Тест-Оператор · 2026-08-15 · источник: файл ответов» — одобрен.
    """
    raw = approval_line(plan).split("**Одобрение:**", 1)[-1]
    # отрезаем только хвост заготовки «/ не одобрен — …» (болд необязателен, пробелы любые);
    # слэш внутри значения («Иван / Оператор») не трогаем
    value = re.split(r"\s*/\s*\*{0,2}не одобрен", raw)[0]
    # решение ещё не принято: отрицание или слово ожидания в значении — не одобрение,
    # даже если рядом стоит дата; сомнение трактуется как «не одобрен» (ложный провал громкий,
    # ложное «одобрен» пропускало бы неодобренный план молча)
    if re.search(r"(?<!\w)не\s|ожида|отложен|позже|вернуться|tbd|\?", value, re.I):
        return False
    return bool(re.search(r"20\d\d", value)) or "источник: файл ответов" in value


def attempt_nums(journal: str):
    """Строки таблицы «Попытки». Шаблонная заготовка (‹дата› во второй ячейке) — не попытка;
    реальная строка с остаточным ‹…› в хвостовой ячейке ИЗ СЧЁТА НЕ выпадает — иначе один
    незаполненный столбец каскадом ронял бы непрерывность и детекты раннера (дыру и так
    ловит скан заполненности)."""
    nums = []
    for ln in (journal or "").splitlines():
        m = ATTEMPT_ROW.match(ln)
        if not m:
            continue
        cells = ln.split("|")
        date_cell = cells[2] if len(cells) > 2 else ""
        if PLACEHOLDER in strip_literal_spans(date_cell):
            continue  # заготовка шаблона
        nums.append(int(m.group(1)))
    return nums


def budget_of(journal: str) -> str:
    m = BUDGET_RE.search(journal or "")
    return m.group(1) if m else DEFAULT_BUDGET


def action_of(report: str) -> str:
    """Последнее записанное значение — то же правило, что у readiness_verdict:
    артефакт может содержать историю, действует свежая запись."""
    vals = ACTION_RE.findall(report or "")
    return vals[-1] if vals else "none"


def readiness_verdict(readiness: str, n: int) -> str:
    """Последнее записанное значение вердикта прогона n (протокол может содержать историю)."""
    vals = re.findall(rf"\*\*Вердикт прогона {n}:\*\*\s*(.+)", readiness or "")
    return vals[-1].strip() if vals else ""


def readiness_ok(readiness: str, n: int) -> bool:
    v = readiness_verdict(readiness, n).lower()
    # нормализация оформления: болд/код и лишние пробелы не должны прятать отрицание
    # («**не** готова», «не  готова» — это отказ, а не «готова»)
    v = re.sub(r"[*_`]", "", v)
    v = re.sub(r"\s+", " ", v)
    # граница слова: «✅ готова» — ок; «неготова» (слитно), «не готова…» и заготовка
    # «готова / не готова — ‹…›» — нет
    return bool(re.search(r"(?<!\w)готова", v)) and "не готова" not in v


def journal_chunk_num(p: Path):
    m = re.search(r"chunk-(\d+)-journal", p.name)
    return m.group(1) if m else None


def plan_more_chunks(plan: str, cur: str) -> bool:
    """План оставляет пункты chunk'ам с номером больше текущего?

    Читает строку плана «Уходит следующим chunk'ам»: «нет — …» или отсутствие
    упоминаний chunk-N с номером выше текущего означает, что план закрыт."""
    line = next((ln for ln in (plan or "").splitlines()
                 if "Уходит следующим chunk'ам" in ln), "")
    nums = [int(x) for x in re.findall(r"chunk-(\d+)", line)]
    try:
        cur_n = int(cur)
    except (TypeError, ValueError):
        cur_n = 0
    return any(x > cur_n for x in nums)


def gate_row_in(report: str, gate: str) -> bool:
    return re.search(rf"^\|\s*{re.escape(gate)}\s*\|", report, flags=re.M) is not None


def mandatory_gates(gates: str):
    """Минимум = канонический фолбэк ∪ пометки набора: пометка расширяет минимум,
    но снятая с канонической строки пометка НЕ выводит её из-под проверки —
    иначе проверка самореферентна (список из проверяемого же файла)."""
    marked = [g.strip() for g in MANDATORY_ROW.findall(gates or "")]
    return sorted(set(marked) | set(MANDATORY_FALLBACK))


def md_sections(text: str):
    return re.split(r"^## ", text or "", flags=re.M)


def claims_in(text):
    return set(re.findall(r"\bclaim-\d+\b", text or ""))


# ---------------------------------------------------------------- --print для оркестраторов

def do_print(what: str, root: Path, slug: str) -> int:
    d = root / ".sdlc" / slug
    journals = natsorted(d.glob("chunk-*-journal.md"))
    j = read(journals[-1]) if journals else None
    reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
    chunk = (journal_chunk_num(journals[-1]) if journals else None) or "1"
    vals = {
        "budget": lambda: budget_of(j),
        "attempts": lambda: len(attempt_nums(j)),
        "chunk": lambda: chunk,
        "action": lambda: action_of(read(reports[-1]) if reports else ""),
        "approved": lambda: "yes" if plan_approved(read(d / "plan.md") or "") else "no",
        "readiness2": lambda: "yes" if readiness_ok(read(d / "readiness.md") or "", 2) else "no",
        # число отчётов приёмки: раннер НЕ глобит имена сам — формат имён знает только этот файл
        "reports": lambda: len(reports),
        # план оставляет пункты следующим chunk'ам? (маршрут continue: chunk или handoff)
        "next_chunk": lambda: "yes" if plan_more_chunks(read(d / "plan.md") or "", chunk) else "no",
    }
    if what == "all":
        for k in sorted(vals):
            print(f"{k}={vals[k]()}")
        return 0
    if what not in vals:
        print(__doc__)
        return 2
    print(vals[what]())
    return 0


# ---------------------------------------------------------------- вердикт

class Verdict:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, check):
        self.passed.append(check)

    def fail(self, check, detail, improvement):
        self.failed.append((check, detail, improvement))

    def check(self, cond, name, detail, improvement):
        if cond:
            self.ok(name)
        else:
            self.fail(name, detail, improvement)


def main(root: Path, slug: str, at: str = "handoff") -> int:
    v = Verdict()
    full = at == "handoff"
    sdlc = root / ".sdlc"
    d = sdlc / slug

    # единственное чтение: все md витка + набор
    texts = {p.name: read(p) for p in natsorted(d.glob("*.md"))}
    gates = read(sdlc / "gates.md")
    texts["gates.md"] = gates
    intent = texts.get("intent.md")
    readiness = texts.get("readiness.md")
    plan = texts.get("plan.md")
    handoff = texts.get("handoff.md")
    journals = natsorted(d.glob("chunk-*-journal.md"))
    reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
    last_report = texts.get(reports[-1].name) if reports else None
    diffs = natsorted(d.glob("chunk-*-attempt-*-diff.patch"))
    tests_out = natsorted(d.glob("chunk-*-attempt-*-tests.txt"))

    # --- 1. Артефакты на месте и непусты -----------------------------------------------------
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

    # --- 2. Плейсхолдеры — по всем артефактам витка -------------------------------------------
    for name, text in texts.items():
        if not text:
            continue
        holes = holes_in(text)
        detail = (f"{len(holes)} строк с плейсхолдером, первая: {holes[0].strip()[:60]}"
                  if holes else "")
        v.check(not holes, f"нет ‹…› в {name}", detail,
                "«заполненность артефактов» не выдерживается")

    # --- 3. Решения человека записаны ---------------------------------------------------------
    if plan:
        v.check(plan_approved(plan), "одобрение плана записано",
                f"строка «Одобрение»: {approval_line(plan).strip()[:70] or 'отсутствует'}",
                "одобрение живёт в чате или план явно не одобрен — чинить /sdlc-plan Phase 5")
    if handoff and full:
        v.check(bool(re.search(r"\*\*Приёмка:\*\*", handoff)),
                "приёмка записана в handoff", "поля «Приёмка» нет",
                "приёмка человеком не фиксируется — чинить /sdlc-handoff Phase 1")
    for p in journals:
        j = texts.get(p.name) or ""
        sig_line = next((ln for ln in j.splitlines() if "Подтвердил:" in ln), "")
        sig = sig_line.split("Подтвердил:", 1)[-1]
        v.check(bool(sig) and PLACEHOLDER not in strip_literal_spans(sig),
                f"место правки подтверждено ({p.name})", "секция «Место правки» без подписи",
                "подтверждение места правки не записывается — чинить /sdlc-chunk Phase 2")
    if readiness:
        for n in (1, 2):
            ok_val = readiness_ok(readiness, n)
            v.check(ok_val, f"readiness: прогон {n} — «готова»",
                    f"вердикт: {readiness_verdict(readiness, n)[:50] or 'отсутствует'}",
                    "виток идёт с непринятой готовностью — чинить предусловия этапов 2/4")

    # --- 4. Попытки: нумерация цела — по каждому chunk'у ---------------------------------------
    for p in journals:
        j = texts.get(p.name) or ""
        n = journal_chunk_num(p)
        if n is None:
            v.fail(f"имя журнала {p.name}", "нет номера chunk'а в имени",
                   "журналы именуются chunk-N-journal.md — иначе сверки слепнут")
            continue
        nums = attempt_nums(j)
        v.check(bool(nums), f"в журнале chunk-{n} есть строки попыток",
                "таблица «Попытки» пуста", "счёт попыток не ведётся")
        v.check(nums == list(range(1, len(nums) + 1)),
                f"нумерация попыток chunk-{n} непрерывна", f"номера: {nums}",
                "дыра/дубль в номерах: затёртая попытка — или строка с ‹…› в ячейке даты")
        for k in nums:  # по фактическим номерам из таблицы, не по range
            v.check((d / f"chunk-{n}-attempt-{k}-diff.patch").exists(),
                    f"diff chunk-{n} попытки {k} сохранён", "файла нет",
                    "попытки перезаписывают друг друга — детект прогресса слеп")
            v.check((d / f"chunk-{n}-attempt-{k}-tests.txt").exists(),
                    f"вывод тестов chunk-{n} попытки {k} сохранён", "файла нет",
                    "вывод тестов попытки потерян — сверка итогов этапа 6 слепа")
            if full:
                # на середине витка (--at verify) отчёт текущей попытки ещё пишется —
                # по-попыточная сверка отчётов возможна только на handoff'е
                v.check((d / f"verification-report-{n}-attempt-{k}.md").exists(),
                        f"отчёт приёмки chunk-{n} попытки {k} сохранён", "файла нет",
                        "история возвратов стирается — отчёт попытки затёрт или не записан")

    # --- 5. Claims сквозные + структура листа --------------------------------------------------
    if intent and plan and last_report:
        ci, cp, cr = claims_in(intent), claims_in(plan), claims_in(last_report)
        v.check(ci == cp, "claims intent == plan",
                f"intent {sorted(ci - cp)} vs plan {sorted(cp - ci)}",
                "план потерял или выдумал пункты")
        v.check(ci == cr, "claims intent == verification",
                f"разница: {sorted(ci ^ cr)}",
                "отчёт приёмки не покрывает лист 1:1")
    if intent:
        rows = [ln for ln in intent.splitlines() if re.match(r"^\|\s*claim-\d+\s*\|", ln)]
        # позиционно: | id | Пункт | Как проверить | … — пустая третья колонка не компенсируется
        # заполненной четвёртой (опциональной GWT-колонкой)
        cells = [[c.strip() for c in r.split("|")] for r in rows]
        cells_ok = all(len(c) > 3 and c[2] and c[3] for c in cells)
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

    # --- 6. Гейты: минимум включён; включённые «этап 6» имеют строку в отчёте -------------------
    if gates:
        for g in mandatory_gates(gates):
            v.check(bool(re.search(
                        rf"^\|\s*{re.escape(g)}\s*\|\s*да(?!\w)[^|]*\|", gates, flags=re.M)),
                    f"минимум: «{g}» включён", "строки нет или в ней не «да» / «да — минимум»",
                    "обязательный минимум выключен среди витка — набор не собран")
    if gates and last_report:
        enabled = [m.strip() for m in ENABLED_ROW.findall(gates)]
        header = next((ln for ln in last_report.splitlines() if "Набор гейтов:" in ln), "")
        _m = re.search(r"(\d{4}-\d{2}-\d{2})", header)
        report_gates_date = _m.group(1) if _m else None
        v.check(bool(report_gates_date), "дата набора в шапке отчёта читается",
                f"строка шапки: {header.strip()[:60] or 'отсутствует'}",
                "без даты набора нельзя отличить гейт, введённый после отчёта")
        enabled_rows = {}
        # «нет → да — минимум» (включение сразу в минимум) — тоже включение: [^|]* после «да»
        for m in re.finditer(
                r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|[^|]*→\s*да(?!\w)[^|]*\|\s*([^|]*)\|",
                gates, flags=re.M):
            g, date, reason = m.group(2).strip(), m.group(1), m.group(3)
            if date >= enabled_rows.get(g, ("", ""))[0]:
                enabled_rows[g] = (date, reason)
        for g in enabled:
            date, reason = enabled_rows.get(g, ("", ""))
            # позже даты набора из отчёта — введён после; та же дата различима только по причине:
            # запись ссылается на ТЕКУЩИЙ виток целым словом (slug-подстрока не считается)
            late = bool(report_gates_date) and (
                date > report_gates_date
                or (date == report_gates_date
                    and re.search(rf"(?<![\w-]){re.escape(slug)}(?![\w-])", reason)))
            if late and not gate_row_in(last_report, g):
                v.ok(f"гейт «{g}» включён после отчёта ({date}) — строка не требуется")
                continue
            v.check(gate_row_in(last_report, g), f"строка гейта «{g}» в отчёте", "строки нет",
                    "включённый гейт без строки — сверка отчёта с набором должна быть механической")
        v.check(bool(re.search(r"\*\*passed:\*\*", last_report)), "вердикт в отчёте",
                "поля **passed:** нет", "вердикт не посчитан")

    # --- 6b. Вердикт согласован со статусами таблиц отчёта --------------------------------------
    # Единственное правило, ради которого существует этап 6 (SDLC.md → таблица вердикта), само
    # никогда не проверялось механически: passed=true проходило и при ❌/⚠ в таблицах отчёта.
    if last_report:
        m = re.search(r"\*\*passed:\*\*\s*(true|false)", last_report)
        claimed = m.group(1) if m else None
        rows = [ln for ln in last_report.splitlines() if ln.strip().startswith("|")]
        bad_rows = [ln for ln in rows if re.search(r"\|\s*[❌⚠]\s*\|", ln)]
        skipped_rows = [ln for ln in rows if re.search(r"\|\s*⏭\s*\|", ln)]
        # «строк неприменимости нет» — буквальная фраза шаблона для пустой таблицы неприменимости;
        # её отсутствие означает, что хотя бы одна строка неприменимости подписана человеком
        has_inapplicability = "строк неприменимости нет" not in last_report
        unexcused_skips = skipped_rows and not has_inapplicability
        v.check(not (claimed == "true" and (bad_rows or unexcused_skips)),
                "passed согласован со статусами таблиц",
                f"passed=true при {len(bad_rows)} строк(е) ❌/⚠"
                + (f" и {len(skipped_rows)} строк(е) ⏭ без подписанной неприменимости"
                   if unexcused_skips else ""),
                "главное правило вердикта (SDLC.md → этап 6, таблица) не проверялось "
                "механически — passed=true могло стоять рядом с непройденным гейтом или пунктом")

    # --- 7. Scope: .sdlc не в files_to_touch ----------------------------------------------------
    if plan:
        section = next((s for s in md_sections(plan) if s.startswith("files_to_touch")), "")
        table_rows = [ln for ln in section.splitlines() if ln.strip().startswith("|")]
        v.check(not any(".sdlc/" in ln for ln in table_rows),
                "files_to_touch без .sdlc/**", ".sdlc в таблице путей",
                "артефакты процесса попали в scope кода")

    # --- Отчёт -----------------------------------------------------------------------------------
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
