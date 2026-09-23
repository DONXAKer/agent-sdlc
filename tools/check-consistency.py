#!/usr/bin/env python3
"""check-consistency: механическая сверка потребителей констант методологии.

CLAUDE.md перечисляет сквозные инварианты («одни и те же имена и таблицы повторяются
в нескольких файлах — при изменении сверяй согласованность»); здесь каждый — проверка с адресом:

  1. таблица перечня гейтов SDLC.md ↔ sdlc-constants.json (имена, этап, переключаемость);
  2. строки gates.template.md ↔ константы (имена, порядок, пометка «да — минимум»); подсказки в
     кавычках, которые preflight принял бы за команды; набор примера содержит каждую строку канона;
  3. оси прод-готовности дословно и в каноническом порядке во всех потребителях;
  4. числа: умолчание бюджета попыток (и нет устаревшего числа рядом с «бюджет»/«умолчани»),
     ориентир размера chunk'а, порог перезаписи файла — те же в прозе и шаблонах;
  5. таблица вердикта в sdlc-verify/SKILL.md — та же, что в SDLC.md;
  6. каждый скилл и агент реализации есть в таблицах README; счёт субагентов в прозе; у агентов
     объявлены tools; у скиллов — allowed-tools, совпадающие с таблицей прав README, и шапка
     «Контракт этапа» с шестью полями;
  7. у каждого ключа sdlc-constants.json есть потребитель (код или текст);
  8. метки решений человека из констант присутствуют в шаблонах;
  9. имена файлов реализации (*.py), упомянутые в SDLC.md и шаблонах, существуют в дереве;
 10. test/README.md перечисляет все кейсы корпуса flow-verdict;
 11. каждый шаблон начинается строкой версии формы `<!-- sdlc-template: <имя> vN -->`.

Запуск: python tools/check-consistency.py   (код возврата 1 при расхождении)
"""

import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
IMPL = ROOT / "implementations" / "claude-code"
C = json.loads((ROOT / "sdlc-constants.json").read_text(encoding="utf-8"))
failures = []


def fail(where, what):
    failures.append(f"{where}: {what}")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def table_rows(text, header_start):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(header_start):
            rows = []
            for ln2 in lines[i + 2:]:
                if not ln2.startswith("|"):
                    break
                rows.append([c.strip() for c in ln2.strip().strip("|").split("|")])
            return rows
    return None


def norm(s):
    return re.sub(r"[*]", "", s).strip()


def section_after(text, heading):
    """Текст от строки heading до следующего заголовка того же или более высокого уровня."""
    m = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    if not m:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


WORDS = {1: "одна", 2: "две", 3: "три", 4: "четыре", 5: "пять", 6: "шесть"}
COUNT = {1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять", 6: "шесть"}

# --- 1. SDLC.md: перечень гейтов ------------------------------------------------------------
sdlc = read("SDLC.md")
rows = table_rows(sdlc, "| Гейт | Где отчитывается | Можно выключить |")
if rows is None:
    fail("SDLC.md", "таблица перечня гейтов не найдена")
else:
    expected = [(g["name"], g["stage"], g["switchable"]) for g in C["gates"]]
    actual = [(r[0], r[1], not r[2].startswith("нет")) for r in rows]
    if [a[0] for a in actual] != [e[0] for e in expected]:
        fail("SDLC.md перечень гейтов", f"имена/порядок: {[a[0] for a in actual]} ↔ константы {[e[0] for e in expected]}")
    for a, e in zip(actual, expected):
        if a[0] == e[0] and (a[1], a[2]) != (e[1], e[2]):
            fail("SDLC.md перечень гейтов", f"«{a[0]}»: этап/переключатель {a[1:]} ↔ константы {e[1:]}")
sdlc_flat = re.sub(r"\s+", " ", sdlc)
for g in C["mandatory_gates"]:
    if f"«{g}»" not in sdlc_flat:
        fail("SDLC.md", f"имя обязательного гейта «{g}» не встречается дословно")

# --- 2. gates.template.md и пример --------------------------------------------------------------
tpl = read("templates/gates.template.md")
rows = table_rows(tpl, "| Гейт | Вкл | Где отчитывается | Чем реализован |")
if rows is None:
    fail("templates/gates.template.md", "таблица набора не найдена")
else:
    names = [r[0] for r in rows]
    if names != [g["name"] for g in C["gates"]]:
        fail("templates/gates.template.md", f"имена/порядок строк ↔ константы: {names}")
    marked = [r[0] for r in rows if r[1].startswith("да — минимум")]
    if marked != C["mandatory_gates"]:
        fail("templates/gates.template.md", f"«да — минимум» стоит у {marked}, ожидалось {C['mandatory_gates']}")
    for r in rows:
        stage = next((g["stage"] for g in C["gates"] if g["name"] == r[0]), None)
        if stage and r[2] != stage:
            fail("templates/gates.template.md", f"«{r[0]}»: «{r[2]}» ↔ константы «{stage}»")
        for hint in re.findall(r"‹([^›]*)›", r[3]) if r[2] == "этап 6" else []:
            if "`" in hint:
                fail("templates/gates.template.md", f"«{r[0]}»: обратные кавычки внутри подсказки ‹…› — preflight примет их за команду")
ex_text = read("example/gates.md")
ex_names = {r[0] for r in (table_rows(ex_text, "| Гейт | Вкл | Где отчитывается | Чем реализован |") or [])}
for g in C["gates"]:
    if g["name"] not in ex_names:
        fail("example/gates.md", f"нет канонической строки «{g['name']}» (строки не удаляются — только переключаются)")
if "## Классы дефектов проекта" not in ex_text:
    fail("example/gates.md", "нет секции «Классы дефектов проекта» из шаблона")

# --- 3. Оси ----------------------------------------------------------------------------------
AXES_CONSUMERS = ["SDLC.md", "templates/plan.template.md", "templates/exploration-report.template.md",
                  "implementations/claude-code/agents/sdlc-reviewer.md",
                  "implementations/claude-code/skills/sdlc-plan/SKILL.md",
                  "implementations/claude-code/skills/sdlc-explore/SKILL.md"]
for rel in AXES_CONSUMERS:
    text = read(rel)
    for ax in C["axes"]:
        if ax not in text:
            fail(rel, f"ось «{ax}» не упомянута дословно")
    idx = [text.find(ax) for ax in C["axes"]]
    if all(i >= 0 for i in idx) and idx != sorted(idx):
        fail(rel, "порядок первых упоминаний осей отличается от канона")

# --- 4. Числа ---------------------------------------------------------------------------------
budget = C["attempt_default_budget"]
if f"{WORDS[budget]} попытки" not in sdlc and f"{budget} попытки" not in sdlc:
    fail("SDLC.md", f"умолчание бюджета «{WORDS[budget]} попытки» не найдено")
for ln in sdlc.splitlines():
    if re.search(r"умолчани|Бюджет", ln):
        for m in re.finditer(r"\b(\w+) попыт(?:ки|ок)\b", ln):
            word = m.group(1).lower()
            if word in WORDS.values() and word != WORDS[budget]:
                fail("SDLC.md", f"рядом с бюджетом стоит «{m.group(0)}», умолчание — {WORDS[budget]}: {ln.strip()[:80]}")
if not re.search(rf"/ {budget} — умолчание", read("templates/chunk-journal.template.md")):
    fail("templates/chunk-journal.template.md", f"умолчание бюджета «{budget} — умолчание» не найдено")
if f"По умолчанию {budget} на chunk" not in read("implementations/claude-code/skills/sdlc-chunk/SKILL.md"):
    fail("sdlc-chunk/SKILL.md", f"«По умолчанию {budget} на chunk» не найдено")
if f"умолчание — {budget}" not in tpl:
    fail("templates/gates.template.md", f"«Бюджет итераций chunk'а»: «умолчание — {budget}» не найдено")
lines_, files_ = C["chunk_size_guideline"]["changed_lines"], C["chunk_size_guideline"]["files"]
for rel in ("SDLC.md", "implementations/claude-code/skills/sdlc-plan/SKILL.md"):
    if f"~{lines_} изменённых строк и до ~{files_}" not in read(rel):
        fail(rel, f"ориентир chunk'а «~{lines_} изменённых строк и до ~{files_}» не найден")
pct = C["overwrite_threshold_percent"]
for rel in ("SDLC.md", "templates/gates.template.md", "implementations/claude-code/skills/sdlc-verify/SKILL.md",
            "templates/chunk-journal.template.md"):
    if not re.search(rf"{pct} %|{pct}%|половин", read(rel)):
        fail(rel, f"порог перезаписи файла ({pct} %) не упомянут")


# --- 5. Таблица вердикта -----------------------------------------------------------------------
def verdict_rows(text):
    rows_ = table_rows(text, "| Что случилось | Статус | Вердикт |")
    return [(norm(r[0]), norm(r[1])) for r in rows_] if rows_ else None


canon = verdict_rows(sdlc)
copy = verdict_rows(read("implementations/claude-code/skills/sdlc-verify/SKILL.md"))
expected_rows = [(a, f"`{b}`" if len(b) <= 6 else b) for a, b in C["verdict_table"]]
if canon != expected_rows:
    fail("SDLC.md таблица вердикта", f"строки ↔ константы: {canon} ↔ {expected_rows}")
if copy != canon:
    fail("sdlc-verify/SKILL.md таблица вердикта", f"копия разошлась с SDLC.md: {copy} ↔ {canon}")

# --- 6. Скиллы и агенты ------------------------------------------------------------------------
readme = read("implementations/claude-code/README.md")
skills = sorted(p.parent.name for p in (IMPL / "skills").glob("sdlc-*/SKILL.md"))
agents = sorted(p.stem for p in (IMPL / "agents").glob("sdlc-*.md"))
for name in skills:
    if not re.search(rf"^\| `{re.escape(name)}` \|", readme, re.M):
        fail("implementations/claude-code/README.md", f"скилл «{name}» отсутствует в таблице «Состав»")
for name in agents:
    if not re.search(rf"^\| `{re.escape(name)}` \|", readme, re.M):
        fail("implementations/claude-code/README.md", f"агент «{name}» отсутствует в таблице агентов")
for rel in ("README.md", "CLAUDE.md"):
    for m in re.finditer(r"(\w+) субагент", read(rel)):
        word = m.group(1).lower()
        if word in COUNT.values() and word != COUNT[len(agents)]:
            fail(rel, f"«{m.group(0)}» ↔ в agents/ {len(agents)} файлов ({', '.join(agents)})")
for p in (IMPL / "agents").glob("sdlc-*.md"):
    text = p.read_text(encoding="utf-8")
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    if not re.search(r"^tools:\s*\S", head, re.M):
        fail(str(p.relative_to(ROOT)), "frontmatter без строки tools: — права агента не объявлены")
rights = {r[0].strip("`"): r[1] for r in (table_rows(readme, "| Скилл | allowed-tools |") or [])}
if not rights:
    fail("implementations/claude-code/README.md", "нет таблицы прав «| Скилл | allowed-tools |»")
CONTRACT_LABELS = ["**Вход:**", "**Выход:**", "**Паузы человека:**", "**Инструменты:**", "**Останов:**", "**Дальше:**"]
for p in (IMPL / "skills").glob("sdlc-*/SKILL.md"):
    text = p.read_text(encoding="utf-8")
    rel = str(p.relative_to(ROOT))
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    m = re.search(r"^allowed-tools:\s*(.+)$", head, re.M)
    if not m:
        fail(rel, "frontmatter без allowed-tools: — состав предодобренных инструментов не объявлен")
    elif rights and rights.get(p.parent.name) != m.group(1).strip():
        fail(rel, f"allowed-tools «{m.group(1).strip()}» ≠ таблица прав README «{rights.get(p.parent.name)}»")
    block = section_after(text, "## Контракт этапа")
    if not block:
        fail(rel, "нет шапки «## Контракт этапа»")
    for label in CONTRACT_LABELS:
        if block and label not in block:
            fail(rel, f"в «Контракте этапа» нет поля {label}")

# --- 7. Каждый ключ констант кому-то нужен ------------------------------------------------------
py_text = "\n".join(p.read_text(encoding="utf-8") for p in ROOT.rglob("*.py")
                    if "flow-verdict-corpus" not in str(p) and "__pycache__" not in str(p))
templates_text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "templates").glob("*.md"))
skills_text = "\n".join(p.read_text(encoding="utf-8") for p in (IMPL / "skills").glob("sdlc-*/SKILL.md"))
CONSUMERS = {
    "placeholder": lambda: '"placeholder"' in py_text,
    "not_applicable": lambda: '"not_applicable"' in py_text,
    "claim_id_pattern": lambda: '"claim_id_pattern"' in py_text,
    "artifacts_dir": lambda: '"artifacts_dir"' in py_text,
    "attempt_default_budget": lambda: '"attempt_default_budget"' in py_text,
    "chunk_size_guideline": lambda: '"chunk_size_guideline"' in py_text,
    "mandatory_gates": lambda: '"mandatory_gates"' in py_text,
    "gates": lambda: True,
    "axes": lambda: '"axes"' in py_text,
    "axis_outcomes": lambda: all(tok.split(" ")[0] in templates_text for tok in C["axis_outcomes"]),
    "gate_statuses": lambda: "/".join(C["gate_statuses"]) in templates_text,
    "claim_statuses": lambda: "/".join(C["claim_statuses"]) in templates_text,
    "actions": lambda: '"actions"' in py_text and " / ".join(C["actions"]) in templates_text,
    "human_decision_labels": lambda: True,
    "defect_actions": lambda: all(a in templates_text for a in C["defect_actions"]),
    "verdict_table": lambda: True,
    "overwrite_threshold_percent": lambda: '"overwrite_threshold_percent"' in py_text,
    "model_order": lambda: " < ".join(C["model_order"]) in skills_text,
    "defect_classes": lambda: all(cls in sdlc for cls in C["defect_classes"]),
}
for key in C:
    if key.startswith("_") or key == "version":
        continue
    check = CONSUMERS.get(key)
    if check is None:
        fail("sdlc-constants.json", f"ключ «{key}» никем не потребляется и не описан в check-consistency")
    elif not check():
        fail("sdlc-constants.json", f"ключ «{key}»: потребитель не подтверждён (код или текст разошлись)")

# --- 8. Метки решений человека -----------------------------------------------------------------
for label in C["human_decision_labels"]:
    if label not in templates_text:
        fail("templates/", f"метка решения человека «{label}» не встречается ни в одном шаблоне")

# --- 9. Имена файлов реализации в первоисточнике и шаблонах существуют ---------------------------
tree_files = {p.name for p in ROOT.rglob("*.py") if "__pycache__" not in str(p)}
for rel in ["SDLC.md"] + [str(p.relative_to(ROOT)) for p in (ROOT / "templates").glob("*.md")]:
    for name in set(re.findall(r"\b([\w-]+\.py)\b", read(rel))):
        if name not in tree_files:
            fail(rel, f"упомянут файл «{name}», которого нет в дереве реализации")

# --- 10. README корпуса перечисляет все кейсы ---------------------------------------------------
cases = sorted(p.name for p in (ROOT / "test" / "flow-verdict-corpus").iterdir() if p.is_dir())
test_readme = read("test/README.md")
for c in cases:
    if f"`{c}`" not in test_readme:
        fail("test/README.md", f"кейс корпуса «{c}» не перечислен")

# --- 11. Версия формы в каждом шаблоне ----------------------------------------------------------
for p in (ROOT / "templates").glob("*.template.md"):
    first = p.read_text(encoding="utf-8").splitlines()[0]
    name = p.name.replace(".template.md", "")
    if not re.fullmatch(rf"<!-- sdlc-template: {re.escape(name)} v\d+ -->", first):
        fail(str(p.relative_to(ROOT)), f"первая строка не «<!-- sdlc-template: {name} vN -->»")

# --- Итог --------------------------------------------------------------------------------------
if failures:
    print(f"РАЗОШЛОСЬ: {len(failures)}")
    for f in failures:
        print(f"- ❌ {f}")
    sys.exit(1)
print("Согласованность: SDLC.md, шаблоны, скиллы, агенты, README, пример и константы сходятся.")
