#!/usr/bin/env python3
"""scaffold: бланк кладётся до этапа, механические поля заполняет программа.

Две измеренные нормы SDLC.md → «Что обязана делать исполняющая программа»: «бланк кладётся до
этапа, а не сочиняется на нём» и «механические поля бланков заполняет программа, содержательные
— модель».

  python scaffold.py <корень> <этап> <slug> [--chunk N] [--attempt K] [--run 1|2] [--force]
  этап: gates | intent | readiness | exploration-report | clarification-report | plan |
        chunk-journal | verification-report | handoff

Что заполняется (и только это — содержательные поля и ВСЕ поля решений человека остаются
`‹…›`, их словарь — sdlc-constants.json → human_decision_labels):
  - ‹название витка› → slug; даты «сегодня» там, где это дата прогона, а не подпись;
  - plan / chunk-journal / verification-report: base_sha, head_sha, бюджет попыток (набор или
    умолчание констант), дата набора, дата одобрения плана; в журнале подставляется только
    номер chunk'а — ‹K› там обобщённый номер попытки и остаётся как есть;
  - verification-report: строка на каждый включённый гейт «этап 6», таблица ранних гейтов,
    строка на каждый claim-N задачи с первыми словами пункта;
  - plan: строка на каждый claim-N в «Чем закрывается»; шесть строк осей, если «Разбор
    последствий» включён, иначе секция сведена к `⏭ — гейт в долге`;
  - exploration-report: «Опоры осей» — шесть строк либо `н/п — гейт … в долге`;
  - handoff: yaml-блок (slug, branch, base_sha, gates_date, chunk, attempts — тем же счётом
    попыток, что у flow-verdict.py); повторный виток — новая секция `## Виток K` дописывается в
    конец, номер K выводится из уже записанных секций (--run переопределяет).
Существующий файл не перезаписывается; --force — только осознанно. После раскладки скрипт
печатает, какие механические поля ему не удалось заполнить (правка формулировки шаблона
ловится здесь, а не молчит).
Шаблоны ищутся в `../../sdlc-*/templates/` рядом (установка) или в `templates/` корня репозитория.
"""

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import (  # noqa: E402
    ARTIFACTS_DIR, AXES, DEFAULT_BUDGET, PLACEHOLDER, attempt_nums, base_sha_of, enabled_gates,
    git_out, read,
)

HERE = Path(__file__).resolve().parent
STAGES = {
    "gates": lambda d, n, k: d.parent / "gates.md", "intent": lambda d, n, k: d / "intent.md",
    "readiness": lambda d, n, k: d / "readiness.md",
    "exploration-report": lambda d, n, k: d / "exploration-report.md",
    "clarification-report": lambda d, n, k: d / "clarification-report.md",
    "plan": lambda d, n, k: d / "plan.md", "chunk-journal": lambda d, n, k: d / f"chunk-{n}-journal.md",
    "verification-report": lambda d, n, k: d / f"verification-report-{n}-attempt-{k}.md",
    "handoff": lambda d, n, k: d / "handoff.md",
}
MECHANICAL = {  # что ОБЯЗАНО исчезнуть из бланка после раскладки — иначе шаблон разошёлся с инструментом
    "plan": ["‹base_sha", "отчёт разведки ‹", "отчёт по вопросам ‹", "‹имя оси из канона›"],
    "chunk-journal": ["‹N›", "‹base_sha", "‹число из строки набора"],
    "verification-report": ["‹N›", "‹K›", "‹base_sha›", "‹head_sha›", "‹имя из набора›", "‹бюджет›",
                            "‹дата последнего изменения"],
    "handoff": ["‹slug›", "base_sha: ‹", "‹дата последнего изменения набора›", "chunk: ‹", "attempts: ‹", "## Виток ‹"],
    "exploration-report": ["‹имя оси из канона›"],
}


def template(name: str) -> str:
    fname = f"{name}.template.md"
    cands = list(HERE.parents[1].glob(f"sdlc-*/templates/{fname}")) + [HERE.parents[4] / "templates" / fname] \
        if len(HERE.parents) > 4 else list(HERE.parents[1].glob(f"sdlc-*/templates/{fname}"))
    for c in cands:
        if c.exists():
            return c.read_text(encoding="utf-8")
    raise SystemExit(f"шаблон {fname} не найден ни в скиллах, ни в корне репозитория")


def gates_date(gates: str) -> str:
    m = re.search(r"Последнее изменение набора:\*\*\s*(\d{4}-\d{2}-\d{2})", gates)
    return m.group(1) if m else "‹дата›"


def approval_date(plan: str) -> str:
    m = re.search(r"\*\*Одобрение:\*\*[^\n]*?(\d{4}-\d{2}-\d{2})", plan)
    return m.group(1) if m else "‹дата›"


def budget(gates: str) -> str:
    for name, _, how in enabled_gates(gates):
        if name == "Бюджет итераций chunk'а":
            m = re.search(r"(\d+)", how)
            if m:
                return m.group(1)
    return str(DEFAULT_BUDGET)


def claims(intent: str):
    out = []
    for ln in intent.splitlines():
        m = re.match(r"^\|\s*(claim-\d+)\b[^|]*\|\s*([^|]*)\|", ln)
        if m:
            words = m.group(2).strip().split()
            out.append((m.group(1), " ".join(words[:5]) + ("…" if len(words) > 5 else "")))
    return out


def replace_row(text: str, row_prefix: str, rows: list) -> str:
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith(row_prefix):
            lines[i:i + 1] = [r + "\n" for r in rows]
            return "".join(lines)
    return text


def sub1(text: str, pattern: str, repl: str) -> str:
    return re.sub(pattern, lambda m: repl, text, count=1)


def collapse_section(t: str, title: str, next_titles, body: str) -> str:
    start = t.find(f"## {title}")
    if start < 0:
        return t
    ends = [t.find(f"## {nt}", start + 1) for nt in next_titles]
    ends = [e for e in ends if e > 0]
    end = min(ends) if ends else len(t)
    return t[:start] + f"## {title}\n\n{body}\n\n" + t[end:]


def fill(stage: str, root: Path, slug: str, n: str, k: str, run: str) -> str:
    d = root / ARTIFACTS_DIR / slug
    gates, intent, plan = read(root / ARTIFACTS_DIR / "gates.md", ""), read(d / "intent.md", ""), read(d / "plan.md", "")
    today = dt.date.today().isoformat()
    t = template(stage).replace("‹название витка›", slug)
    on = enabled_gates(gates)
    axes_on = any(name == "Разбор последствий" for name, _, _ in on)
    base = base_sha_of(plan) or "‹base_sha›"

    if stage == "readiness":
        marker = "## Прогон 1" if run == "1" else "## Прогон 2"
        head, sep, tail = t.partition(marker)
        t = head + sep + tail.replace("- **Дата:** ‹дата›", f"- **Дата:** {today}", 1)
    elif stage == "plan":
        t = sub1(t, r"‹base_sha[^›]*›", (git_out(["rev-parse", "HEAD"], root) or "").strip() or "‹base_sha›")
        t = sub1(t, r"отчёт разведки ‹да/нет›", "отчёт разведки " + ("да" if (d / "exploration-report.md").exists() else "нет"))
        t = sub1(t, r"отчёт по вопросам ‹да / шага не было›",
                 "отчёт по вопросам " + ("да" if (d / "clarification-report.md").exists() else "шага не было"))
        t = replace_row(t, "| claim-1 | ‹тест / место в коде› |",
                        [f"| {cid} | ‹тест / место в коде› |" for cid, _ in claims(intent)] or ["| claim-1 | ‹тест / место в коде› |"])
        if axes_on:
            t = replace_row(t, "| ‹имя оси из канона› |",
                            [f"| {ax} | ‹да/нет› | ‹шаг N: что именно, `путь:символ`› / ‹почему ось не затронута› | "
                             f"‹claim-N / инвариант / гейт «‹имя›» / риск / следующий виток: вопрос ‹N› / н/п — ‹почему›› |" for ax in AXES])
        else:
            t = collapse_section(t, "Последствия шагов", ["Контекст-пакет", "Необратимые шаги"], "- **Статус:** ⏭ — гейт в долге")
    elif stage == "exploration-report":
        if axes_on:
            t = replace_row(t, "| ‹имя оси из канона› |",
                            [f"| {ax} | ‹path/to/file:Symbol› / нет механизма | ‹как применяется / почему не применим› |" for ax in AXES])
        else:
            t = collapse_section(t, "Опоры осей", ["Приёмочный лист, выведенный независимо"], "н/п — гейт «Разбор последствий» в долге")
    elif stage == "chunk-journal":
        t = t.replace("‹N›", n)  # ‹K› — обобщённый номер попытки в шапке и заголовке колонки, не трогаем
        t = sub1(t, r"одобрение от ‹дата›", f"одобрение от {approval_date(plan)}")
        t = sub1(t, r"‹base_sha[^›]*›", base)
        t = sub1(t, r"‹число из строки набора[^›]*›", budget(gates))
    elif stage == "verification-report":
        head = (git_out(["rev-parse", "HEAD"], root) or "").strip() or "‹head_sha›"
        t = t.replace("chunk ‹N›, попытка ‹K›", f"chunk {n}, попытка {k}")
        t = t.replace("chunk-‹N›-attempt-‹K›", f"chunk-{n}-attempt-{k}")
        t = t.replace("‹base_sha›", base).replace("‹head_sha›", head)
        t = sub1(t, r"одобрение от ‹дата›", f"одобрение от {approval_date(plan)}")
        t = sub1(t, r"от ‹дата последнего изменения[^›]*›", f"от {gates_date(gates)} — совпадает с журналом набора: ‹да/нет›")
        t = sub1(t, r"- \*\*Попытка:\*\* ‹K› из ‹бюджет›", f"- **Попытка:** {k} из {budget(gates)}")
        stage6 = [name for name, st, _ in on if st == "этап 6"]
        t = replace_row(t, "| ‹имя из набора› |", [f"| {g} | ‹✅/❌/⏭› | ‹фактический результат› |" for g in stage6])
        early = [(name, st) for name, st, _ in on if st in ("этап 1", "этап 2", "этап 4", "этап 5")]
        where = {"этап 1": "`readiness.md`", "этап 2": "`exploration-report.md`", "этап 4": "`plan.md`",
                 "этап 5": f"`chunk-{n}-journal.md`"}
        if early:
            t = replace_row(t, "| ‹гейт› | ‹этап› |", [f"| {g} | {st.split()[-1]} | ‹✅/❌/⏭› | {where[st]} |" for g, st in early])
            t = t.replace("Включённых гейтов ранних этапов нет / таблица ниже заполнена:", "Таблица ранних гейтов — по набору:")
        else:
            t = replace_row(t, "| ‹гейт› | ‹этап› |", [])
            t = t.replace("Включённых гейтов ранних этапов нет / таблица ниже заполнена:", "Включённых гейтов ранних этапов нет.")
        t = replace_row(t, "| claim-1 | ‹начало пункта…› |",
                        [f"| {cid} | {words} | ‹✅/❌/⚠/manual› | ‹доказательство› | ‹что чинить› / н/п |" for cid, words in claims(intent)]
                        or ["| claim-1 | ‹начало пункта…› | ‹✅/❌/⚠/manual› | ‹доказательство› | ‹что чинить› / н/п |"])
    elif stage == "handoff":
        branch = re.search(r"\*\*Ветка витка:\*\*\s*([^\n]+)", intent)
        journals = sorted(d.glob("chunk-*-journal.md"), key=lambda p: int(re.search(r"chunk-(\d+)", p.name).group(1)))
        attempts = len(attempt_nums(read(journals[-1], ""))) if journals else 0
        cur_chunk = re.search(r"chunk-(\d+)", journals[-1].name).group(1) if journals else n
        t = (sub1(t, r"slug: ‹slug›", f"slug: {slug}")
             .replace("branch: ‹sdlc/слаг›", f"branch: {branch.group(1).strip() if branch else 'н/п'}")
             .replace("base_sha: ‹sha›", f"base_sha: {base}")
             .replace("gates_date: ‹дата последнего изменения набора›", f"gates_date: {gates_date(gates)}")
             .replace("chunk: ‹N›", f"chunk: {cur_chunk}")
             .replace("attempts: ‹K›", f"attempts: {attempts}"))
        t = t.replace("## Виток ‹K› — ‹дата›", f"## Виток {run} — {today}")
    return t


def unfilled_mechanics(stage: str, text: str):
    return [m for m in MECHANICAL.get(stage, []) if m in text]


def main() -> int:
    ap = argparse.ArgumentParser(prog="scaffold.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root"); ap.add_argument("stage", choices=sorted(STAGES)); ap.add_argument("slug")
    ap.add_argument("--chunk", default="1"); ap.add_argument("--attempt", default="1")
    ap.add_argument("--run", default=None, help="readiness: номер прогона (1|2); handoff: номер витка")
    ap.add_argument("--force", action="store_true")
    try:
        a = ap.parse_args()
    except SystemExit:
        return 2
    root, stage, slug, n, k = Path(a.root).resolve(), a.stage, a.slug, a.chunk, a.attempt
    d = root / ARTIFACTS_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    target = STAGES[stage](d, n, k)
    existing = read(target, "")
    run = a.run
    if stage == "handoff" and run is None:
        run = str(len(re.findall(r"^## Виток ", existing, re.M)) + 1)
    run = run or "1"
    text = fill(stage, root, slug, n, k, run)
    if target.exists() and not a.force:
        if stage == "handoff":
            body = text.split("## Виток", 1)[1]
            target.write_text(existing.rstrip("\n") + "\n\n## Виток" + body, encoding="utf-8")
            print(f"дописана секция «Виток {run}»: {target}")
            return 0
        if stage == "readiness" and run == "2" and "## Прогон 2" in existing:
            head, sep, tail = existing.partition("## Прогон 2")
            if "- **Дата:** ‹дата›" in tail:
                target.write_text(head + sep + tail.replace("- **Дата:** ‹дата›", f"- **Дата:** {dt.date.today().isoformat()}", 1),
                                  encoding="utf-8")
                print(f"дата прогона 2 проставлена: {target}")
                return 0
        print(f"{target} уже есть — существующий артефакт не трогается (это работа человека или прошлого "
              f"этапа); --force только осознанно")
        return 1
    target.write_text(text, encoding="utf-8")
    holes = len(re.findall(r"‹[^›\n]*›", text))
    print(f"разложен бланк: {target} · осталось заполнить: {holes} полей ‹…› (решения человека — не агенту)")
    left = unfilled_mechanics(stage, text)
    if left:
        print(f"ВНИМАНИЕ: механические поля не заполнены — шаблон разошёлся с scaffold.py: {left}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
