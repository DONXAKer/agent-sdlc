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
  python3 flow-verdict.py --metrics <корень проекта>
    наблюдения по всем виткам проекта разом — вход /sdlc-retro, не гейт; код возврата
    всегда 0, ничего не роняет (см. do_metrics).
Код возврата: 0 — контракт цел / значение напечатано; 1 — есть провалы; 2 — неверный вызов.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import (  # noqa: E402  — константы и разбор: один источник на все инструменты
    ACTIONS, AXES, CHUNK_FILES, CHUNK_LINES, CONSTANTS, DEFAULT_BUDGET as _DEFAULT_BUDGET,
    MANDATORY_GATES as MANDATORY_FALLBACK, PLACEHOLDER, attempt_nums as _attempt_nums, claims_in as _claims_in,
    gate_configuration_errors, patch_files, DIFF_GIT, unquote_git_path, preparation_checks,
    readiness_verdict, readiness_ok, diffstat, defect_class_catalog, defect_class_name,
)
import snapshot_base
import chunk_preconditions

DEFAULT_BUDGET = str(_DEFAULT_BUDGET)  # норма SDLC.md → этап 6; шаблон журнала сверяет tools/check-consistency.py

ATTEMPT_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.M)
# [^0-9\n‹] — не перескакивать на цифры следующих строк и не принимать ‹плейсхолдер› за значение
BUDGET_RE = re.compile(r"Бюджет попыток\*{0,2}:\*{0,2}[^0-9\n‹]*(\d+)")
# принять «retry — причина…» и «retry / см. отчёт», отвергнуть заготовку-триплет
# «continue / retry / escalate» (слэш + другое action-слово). `blocked_env` —
# четвёртое законное значение (SDLC.md → этап 6, «Красный из-за окружения»;
# шаблон отчёта его перечисляет наравне с остальными) — отсутствовало здесь,
# отчёт с ним не распознавался как заполненный.
_ACT = "|".join(re.escape(a) for a in ACTIONS)
ACTION_RE = re.compile(r"\*\*action:\*\*\s*(" + _ACT + r")\b(?!\s*/\s*(?:" + _ACT + r"))", re.M)
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


HOLE_RE = re.compile(r"‹[^›\n]*›")


def holes_in(text: str):
    """Строки с настоящими ‹…›: вне кода, вне цитат-упоминаний, вне fenced-блоков.

    Осознанные слепые зоны механики: ‹…› внутри «ёлочек» и внутри fenced-блоков ```…```.
    Дыры там остаются ручной части гейта (норма в SDLC.md): верификатор этапа 6 пробегает
    все артефакты витка, рецензент — свои четыре входных.

    Дыра засчитывается только парой ‹…›: голый ‹ без закрывающего › — упоминание самого
    символа в прозе («„grep -c '‹'" находит незаполненные места»), а не незаполненное
    место. Ячейка с одиночным символом без обратных кавычек не должна давать ложный провал."""
    holes, fenced = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if HOLE_RE.search(strip_literal_spans(ln)):
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
    # Ненужность повторного решения не отменяет ранее выданные полномочия.
    value = re.sub(r"(?:отдельное\s+)?повторное\s+согласование\s+не\s+требуется", "", value, flags=re.I)
    # решение ещё не принято: отрицание или слово ожидания в значении — не одобрение,
    # даже если рядом стоит дата; сомнение трактуется как «не одобрен» (ложный провал громкий,
    # ложное «одобрен» пропускало бы неодобренный план молча)
    if re.search(r"(?<!\w)не\s|ожида|отложен|позже|вернуться|tbd|\?", value, re.I):
        return False
    authority = re.search(r"пользовател\w*\s+(?:явно\s+)?(?:поручил|разрешил|делегировал)([^;\n]*)", value, re.I)
    execution = (r"вести\s+(?:цель|задачи)\b|"
                 r"(?:выполнить|выполнение|выполнять)\s+(?:текущ\w*\s+)?(?:план\w*|реализаци\w*|задач[уи])\b|"
                 r"реализовать\s+(?:текущ\w*\s+)?(?:план|задачу|изменения)\b")
    delegated = (bool(authority and re.search(execution, authority.group(1), re.I))
                 and not re.search(r"только\s+чтени|изучить|прочитать|исследова|\bаудит|\bанализ", authority.group(1), re.I)
                 and bool(re.search(r"\$sdlc-run|AGENTS\.md|источник:\s*сообщение", value, re.I)))
    return bool(re.search(r"20\d\d", value)) or "источник: файл ответов" in value or delegated


def attempt_nums(journal: str):
    return _attempt_nums(journal)


def budget_of(journal: str) -> str:
    m = BUDGET_RE.search(journal or "")
    return m.group(1) if m else DEFAULT_BUDGET


def action_of(report: str) -> str:
    """Последнее записанное значение — то же правило, что у readiness_verdict:
    артефакт может содержать историю, действует свежая запись."""
    vals = ACTION_RE.findall(report or "")
    return vals[-1] if vals else "none"


def journal_chunk_num(p: Path):
    m = re.search(r"chunk-(\d+)-journal", p.name)
    return m.group(1) if m else None


def small_scope_errors(d, intent):
    """Проверь область всего витка. Смена подписи не отменяет историческое отклонение."""
    paths, problems = set(), []
    documentation_config = {'.md', '.rst', '.txt', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
                            '.properties', '.conf', '.xml', '.env'}
    config_names = {'.gitignore', '.gitattributes', '.editorconfig'}
    code_types = {'.py', '.js', '.ts', '.jsx', '.tsx', '.c', '.cpp', '.h', '.hpp', '.java', '.go', '.rs',
                  '.cs', '.php', '.rb', '.lua', '.luau', '.sh', '.bash', '.ps1', '.bat', '.cmd', '.sql',
                  '.swift', '.kt', '.kts', '.scala', '.r', '.m', '.mm', '.v', '.vb', '.ipynb', '.vue',
                  '.svelte', '.css', '.scss', '.sass', '.wasm'}
    for patch in natsorted(d.glob('chunk-*-attempt-*-diff.patch')):
        match = re.fullmatch(r'chunk-(\d+)-attempt-\d+-diff.patch', patch.name)
        selected = chunk_preconditions.contour(intent)
        try:
            rec = json.loads(chunk_preconditions.receipt_path(d, match.group(1)).read_text(encoding='utf-8'))
            selected = rec.get('contour', selected)
        except (OSError, ValueError, AttributeError):
            pass  # Повреждение квитанции проверяет отдельный гейт.
        raw = patch.read_bytes()
        text = raw.decode('utf-8', errors='surrogateescape')
        paths.update(record['path'] for record in patch_files(text))
        for line in text.splitlines():
            header = DIFF_GIT.match(line)
            if header:
                paths.add(unquote_git_path(header.group(1) if header.group(1) is not None else header.group(2)))
        if selected != 'мелкий' or len(paths) <= 1:
            continue
        code = sorted(name for name in paths if Path(name).suffix.casefold() in code_types)
        unknown = {name for name in paths if Path(name).suffix.casefold() not in documentation_config
                   and Path(name).name not in config_names and not Path(name).name.startswith('.env.')}
        unknown.difference_update(code)
        # Неизвестное расширение само по себе не является программным кодом.
        # Рецензент связывает пофайловую классификацию с конкретным патчем, а не подписью плана.
        review = read(patch.with_name(patch.name.replace('-diff.patch', '-review.md'))) or ''
        pin = re.search(r'\*\*SHA-256 патча классификации:\*\*\s*`?([0-9a-f]{64})', review)
        signer = re.search(r'\*\*Классификацию подтвердил:\*\*[^\S\n]*([^\n]*)$', review, re.M)
        source = re.search(r'\*\*Источник классификации:\*\*[^\S\n]*(человек|независимый рецензент)[^\S\n]*$', review, re.M)
        signed = bool(signer and signer.group(1).strip()
                      and signer.group(1).strip().casefold() not in {'нет', 'н/п', '-', 'none', 'n/a'}
                      and not re.search(r'(?<!\w)не\s|ожида|tbd|\?', signer.group(1), re.I)
                      and not holes_in(signer.group(1)))
        if pin and pin.group(1) == snapshot_base.digest(raw) and signed and source:
            classified = {name.strip() for name, _, reason in re.findall(
                r'^\|[^\S\n]*([^|\n]+?)[^\S\n]*\|[^\S\n]*(документация|конфигурация)[^\S\n]*\|[^\S\n]*([^|\n]+?)[^\S\n]*\|[^\S\n]*$', review, re.M)
                          if reason.strip() and not holes_in(reason)}
            unknown.difference_update(classified)
        if code or unknown:
            detail = ('программный код: ' + ', '.join(code) if code else '')
            if unknown:
                detail += ('; ' if detail else '') + 'назначение не подтверждено: ' + ', '.join(sorted(unknown))
            problems.append(f'{patch.name}: {len(paths)} файлов; {detail}. '
                            'Сохрани отклонение; переход на полный контур не меняет историю этой попытки')
    return problems


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


def row_glyphs(ln: str) -> set:
    """Статусные глифы, которые несёт эта строка таблицы — первые непробельные
    символы её `|`-ячеек после снятия обрамления `*`/`` ` ``/`_` и variation
    selector U+FE0F (например `{"❌"}` или `{"⏭"}`); единственный источник этой
    нормализации в файле — вызывай отсюда, не копируй тело.

    Отчёты оборачивают статус в bold (`**❌**`), в обратные кавычки (`` `⚠` ``),
    в курсив (`_❌_`) и иногда несут аннотацию рядом (`⚠ частично`,
    `⚠️` с variation selector U+FE0F) — точное совпадение «только пробелы и символ»
    ловит форму из шаблона, но не все допустимые варианты. Сверяем по первому непробельному символу
    ячейки после снятия обрамления — тот же компромисс (ложноотрицательный экзотический
    формат вместо ложноположительного совпадения в прозе), что `holes_in()` уже
    принимает для сканирования плейсхолдеров."""
    glyphs = set()
    for cell in ln.split("|"):
        c = cell.strip().strip("*`_").strip().replace("️", "")
        if c:
            glyphs.add(c[0])
    return glyphs


def mandatory_gates(gates: str):
    """Минимум = канонический фолбэк ∪ пометки набора: пометка расширяет минимум,
    но снятая с канонической строки пометка НЕ выводит её из-под проверки —
    иначе проверка самореферентна (список из проверяемого же файла)."""
    marked = [g.strip() for g in MANDATORY_ROW.findall(gates or "")]
    return sorted(set(marked) | set(MANDATORY_FALLBACK))


def md_sections(text: str):
    return re.split(r"^## ", text or "", flags=re.M)


def claims_in(text):
    return _claims_in(text)


# ---------------------------------------------------------------- --print для оркестраторов

def do_print(what: str, root: Path, slug: str) -> int:
    try:
        state = canonical_state(root, slug)
        if state is not None:
            values = {"action": state["verification"]["action"],
                      "attempts": len(state["attempts"]),
                      "chunk": max((a["chunk"] for a in state["attempts"]), default=1)}
            if what not in values:
                raise ValueError(f"--print {what}: v1 state не содержит надёжной проекции; Markdown не используется")
            print(values[what])
            return 0
    except (ValueError, OSError) as exc:
        print(f"❌ state-first: {exc}", file=sys.stderr)
        return 1
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


# ---------------------------------------------------------------- --metrics для /sdlc-retro

def do_metrics(root: Path) -> int:
    """Наблюдения по всем виткам проекта. НЕ гейт: код возврата всегда 0, ничего не роняет —
    это вход `/sdlc-retro`, а не проверка витка. Норматив из этих чисел не делается
    (SDLC.md → «Долой лозунги и квоты» + Гудхарт): числа существуют, чтобы повтор одного
    класса отказа был виден по сохранённым артефактам между витками."""
    sdlc = root / ".sdlc"
    if not sdlc.is_dir():
        print("нет .sdlc/ — метрик считать не из чего")
        return 0
    rows = []
    catalog = defect_class_catalog(read(sdlc / 'gates.md') or '')
    diagnostics = list(catalog['errors'])
    for d in sorted(p for p in sdlc.iterdir() if p.is_dir()):
        if not (d / "intent.md").exists():
            continue
        journals = natsorted(d.glob("chunk-*-journal.md"))
        reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
        j = read(journals[-1]) if journals else None
        nums = attempt_nums(j) if j else []
        first_pass = next((index for index, report in enumerate(reports, 1)
                           if confirmed_verify(d, report)), None)
        textual = any(re.search(r'\*\*passed:\*\*\s*true', read(p) or '') for p in reports)
        ho = read(d / "handoff.md")
        classes = set()
        for value in re.findall(r'^-\s*\*\*Класс:\*\*[^\S\n]*([^\n]*)', ho or '', re.M):
            name, diagnostic = defect_class_name(value, catalog)
            if name is not None:
                classes.add(name)
            if diagnostic:
                diagnostics.append(f'{d.name}/handoff.md: {diagnostic}')
        rows.append(dict(
            slug=d.name, attempts=len(nums), reports=len(reports),
            budget=budget_of(j) if j else "-", passed_at=first_pass,
            handoff=ho is not None, classes=classes, unconfirmed=textual and first_pass is None,
            first=bool(reports) and reports[0].name == 'verification-report-1-attempt-1.md'
                  and first_pass == 1 and first_verify_succeeded(d, reports[0]),
        ))
    if not rows:
        print("витков в .sdlc/ не найдено")
        return 0
    done = [r for r in rows if r["handoff"]]
    first = [r for r in rows if r['first']]
    reached = [r for r in rows if r["passed_at"] is not None]
    print(f"Витков: {len(rows)} · завершённых handoff'ом: {len(done)}")
    print(f"Подтверждённый verify с первой попытки: {len(first)}/{len(rows)}")
    print(f"Подтверждённый успешный verify: {len(reached)}/{len(rows)}")
    print(f"Текстовый passed=true без подтверждения: {sum(r['unconfirmed'] for r in rows)}/{len(rows)}")
    over = [r for r in rows if str(r["budget"]).isdigit() and r["attempts"] > int(r["budget"])]
    print(f"Превысили бюджет попыток: {len(over)}/{len(rows)}"
          f" ({', '.join(r['slug'] for r in over) or 'нет'})")
    seen = {}
    for r in rows:
        for c in r["classes"]:
            seen.setdefault(c, []).append(r["slug"])
    repeated = {c: v for c, v in seen.items() if len(v) > 1}
    print(f"Классов дефектов с полем «Класс»: {len(seen)} · "
          f"повторившихся на ≥2 витках: {len(repeated)}")
    for c, v in sorted(repeated.items()):
        print(f"  ПОВТОР «{c}» — {', '.join(v)} → SDLC.md → «Когда дефект проскочил»: "
              f"править систему, не случай")
    for diagnostic in diagnostics:
        print('Диагностика классов: ' + diagnostic)
    return 0


def verify_receipt(d, report, result):
    """Сохрани каждый машинный результат. Не исправляй прежний отказ новой записью."""
    match = re.fullmatch(r'verification-report-(\d+)-attempt-(\d+)\.md', report.name)
    n, k = map(int, match.groups())
    prefix = f'verify-result-{n}-attempt-{k}-run-'
    number = max((int(p.stem[len(prefix):]) for p in d.glob(prefix + '*.json')
                  if p.stem[len(prefix):].isdigit()), default=0) + 1
    record = {'version': 1, 'tool': 'flow-verdict.py', 'at': 'verify', 'slug': d.name,
              'chunk': n, 'attempt': k, 'report': report.name,
              'report_sha256': snapshot_base.digest(report.read_bytes()), 'exit_code': result,
              'passed': result == 0 and bool(re.search(r'\*\*passed:\*\*\s*true\b', read(report) or ''))}
    with (d / f'{prefix}{number}.json').open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False, indent=2)


def confirmed_verify(d, report):
    match = re.fullmatch(r'verification-report-(\d+)-attempt-(\d+)\.md', report.name)
    n, k = map(int, match.groups())
    receipts = natsorted(d.glob(f'verify-result-{n}-attempt-{k}-run-*.json'))
    if not receipts:
        return False
    try:
        rec = json.loads(receipts[-1].read_bytes())
        return verify_receipt_matches(rec, d, report)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def verify_receipt_matches(rec, d, report):
    n, k = map(int, re.findall(r'\d+', report.name))
    return (rec.get('version') == 1 and rec.get('tool') == 'flow-verdict.py'
            and rec.get('at') == 'verify' and rec.get('slug') == d.name
            and rec.get('chunk') == n and rec.get('attempt') == k and rec.get('report') == report.name
            and rec.get('report_sha256') == snapshot_base.digest(report.read_bytes())
            and rec.get('exit_code') == 0 and rec.get('passed') is True
            and bool(re.search(r'\*\*passed:\*\*\s*true\b', read(report) or '')))


def first_verify_succeeded(d, report):
    receipts = natsorted(d.glob('verify-result-1-attempt-1-run-*.json'))
    if not receipts or not confirmed_verify(d, report):
        return False
    try:
        rec = json.loads(receipts[0].read_bytes())
        return verify_receipt_matches(rec, d, report)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def defect_record_errors(d, handoff):
    """Не превращай любой failed-отчёт в дефект. Требуй подтверждённую находку или явную запись."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('defect_review', Path(__file__).with_name('review-validate.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required, history_errors = set(), []
    catalog = defect_class_catalog(read(d.parent / 'gates.md') or '')
    for validation in d.glob('.review-validation-*-attempt-*.json'):
        try:
            n, k = map(int, re.findall(r'\d+', validation.name))
            rec = json.loads(validation.read_bytes())
            review_path = module.confirmed_answer(d, rec, n, k)
            patch_path = d / f'chunk-{n}-attempt-{k}-diff.patch'
            if (rec.get('tool') != 'review-validate.py' or rec.get('slug') != d.name
                    or rec.get('chunk') != n or rec.get('attempt') != k
                    or rec.get('review_sha256') != snapshot_base.digest(review_path.read_bytes())
                    or rec.get('patch_sha256') != snapshot_base.digest(patch_path.read_bytes())):
                raise ValueError('Хэши или область подтверждённого ревью изменены')
        except (OSError, ValueError, TypeError, AttributeError) as error:
            history_errors.append(f'История подтверждённого ревью повреждена: {validation.name}: {error}')
    for path in d.glob('chunk-*-attempt-*-review.md'):
        text = read(path) or ''
        match = re.search(r'```json\s*\n(.*?)\n```', text, re.S)
        try:
            review = json.loads(match.group(1) if match else text)
        except ValueError:
            continue
        if not isinstance(review, dict):
            continue
        findings = review.get('findings')
        if not isinstance(findings, list) or not isinstance(review.get('claims'), list):
            continue
        patch = path.with_name(path.name.replace('-review.md', '-diff.patch'))
        if not patch.is_file():
            continue
        artifact_paths = module.artifact_aliases(d.name)
        try:
            n, k = map(int, re.findall(r'\d+', path.name))
            validation = d / f'.review-validation-{n}-attempt-{k}.json'
            primary_paths = set()
            archived_intent = d / f'.chunk-{n}-intent.md'
            claim_ids = claims_in(read(archived_intent if archived_intent.exists() else d / 'intent.md') or '')
            if validation.exists():
                rec = json.loads(validation.read_bytes())
                if (module.confirmed_answer(d, rec, n, k) != path
                        or rec.get('tool') != 'review-validate.py' or rec.get('slug') != d.name
                        or rec.get('chunk') != n or rec.get('attempt') != k
                        or rec.get('review_sha256') != snapshot_base.digest(path.read_bytes())
                        or rec.get('patch_sha256') != snapshot_base.digest(patch.read_bytes())):
                    continue
                claim_ids = set(rec['claim_ids'])
                primary_paths = set(rec['primary_paths'])
            errors, _ = module.validate(review, claim_ids,
                                         set(diffstat(patch.read_bytes().decode('utf-8', errors='surrogateescape'))['paths']),
                                         primary_paths=primary_paths, artifact_paths=artifact_paths)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if errors:
            continue
        for finding in findings:
            if (isinstance(finding, dict) and finding.get('summary')
                    and (finding.get('kind') == 'uncovered_behavior'
                         or finding.get('kind') == 'mismatch' and review.get('claims')
                         and all(c.get('status') == 'passed' for c in review['claims']))):
                required.add(f'{path.name}#{snapshot_base.digest(finding["summary"].encode("utf-8"))[:16]}')
    for report in d.glob('verification-report-*-attempt-*.md'):
        required.update(re.findall(r'^- \*\*Проскочивший дефект:\*\*\s*(\S+)', read(report) or '', re.M))
    blocks = re.split(r'(?=^- \*\*Имя:\*\*)', handoff or '', flags=re.M)[1:]
    errors, covered = list(history_errors), set()
    fields = ('Имя', 'Класс', 'Повтор', 'Что проскочило',
              'Какой гейт должен был поймать и почему не поймал', 'Действие', 'Кто утвердил',
              'Где реализовано', 'Чем закреплено')
    for block in blocks:
        values = {name: (re.search(rf'^- \*\*{re.escape(name)}:\*\*[^\S\n]*([^\n]*)', block, re.M)
                         or [None, ''])[1].strip() for name in fields}
        if values['Имя'] in ('нет', 'н/п', ''):
            continue
        class_name, class_error = defect_class_name(values['Класс'], catalog)
        errors.extend(catalog['errors'])
        if class_error:
            errors.append(class_error)
        if class_name is None:
            errors.append('Для реального дефекта требуется класс из канонического или проектного словаря')
        for name, value in values.items():
            if (not value or '‹' in value or 'не утверждено' in value
                    or value == 'н/п' and name != 'Повтор'):
                errors.append(f'Запись «{values["Имя"]}»: не заполнено поле «{name}»')
        if values['Действие'] not in ('конструкция', 'проверка', 'принятие риска'):
            errors.append('Запись о дефекте требует одно допустимое действие')
        covered.update(re.findall(r'^- \*\*Источник дефекта:\*\*\s*(\S+)', block, re.M))
    missing = required - covered
    if missing:
        errors.append('Нет обязательных записей о дефектах: ' + ', '.join(sorted(missing)))
    return errors


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


def canonical_state(root, slug):
    """None means legacy; a present but invalid canonical file never falls back."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        raise ValueError("недопустимый slug витка")
    path = root / ".sdlc" / slug / "state.json"
    if not path.exists() and not path.is_symlink():
        return None
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("state.json выходит за корень проекта")
    import importlib.util
    local_tool = Path(__file__).resolve().parent / "state_contract.py"
    tool = local_tool if local_tool.is_file() else next(
        (parent / "runner-contract" / "tools" / "state_contract.py"
         for parent in Path(__file__).resolve().parents
         if (parent / "runner-contract" / "tools" / "state_contract.py").is_file()), None)
    if tool is None:
        raise ValueError("state.json существует, но reference validator не установлен рядом с реализацией")
    spec = importlib.util.spec_from_file_location("agent_sdlc_state", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.load(path)
    errors = module.validate_state(state)
    if not errors:
        if state["slug"] != slug:
            errors.append("state.slug не совпадает с каталогом витка")
        errors.extend(module.evidence_errors(state, root, path.parent))
    if errors:
        raise ValueError("; ".join(errors))
    return state


def state_verdict(root, slug, at):
    try:
        state = canonical_state(root, slug)
        if state is None:
            return None
        errors = []
        if state["stage"] != at:
            errors.append(f"ожидался stage={at}, получен {state['stage']}")
        if not state["verification"]["passed"]:
            errors.append("verification.passed=false")
        limit = 7 if at == "handoff" else 6
        enabled = {g["name"] for g in state["gates"] if g["enabled"] and g["stage"] <= limit}
        for name in MANDATORY_FALLBACK:
            if name not in enabled:
                errors.append(f"нет обязательного гейта: {name}")
        if at == 'handoff':
            d = root / '.sdlc' / slug
            errors.extend(defect_record_errors(d, read(d / 'handoff.md') or ''))
            if (d / 'git-operation.json').exists():
                import git_operation
                errors.extend(git_operation.completion_errors(root, d))
        print(f"# Вердикт state-first: {slug} (--at {at})")
        for error in errors:
            print(f"❌ {error}")
        if not errors:
            print("✅ Канонический контракт и доступные evidence проверены")
        return 1 if errors else 0
    except (ValueError, OSError) as exc:
        print(f"❌ state-first: {exc}", file=sys.stderr)
        return 1


def check_flow(root: Path, slug: str, at: str = "handoff") -> int:
    canonical = state_verdict(root, slug, at)
    if canonical is not None:
        return canonical
    v = Verdict()
    full = at == "handoff"
    sdlc = root / ".sdlc"
    d = sdlc / slug

    # единственное чтение: все md витка + набор
    texts = {p.name: read(p) for p in natsorted(d.glob("*.md"))}
    gates = read(sdlc / "gates.md")
    texts["gates.md"] = gates
    for name, detail in gate_configuration_errors(gates, root=root, slug=slug):
        v.check(False, f"конфигурация гейта {name}", detail, "заполни состояние переключателя явно")
    intent = texts.get("intent.md")
    readiness = texts.get("readiness.md")
    plan = texts.get("plan.md")
    handoff = texts.get("handoff.md")
    journals = natsorted(d.glob("chunk-*-journal.md"))
    reports = natsorted(d.glob("verification-report-*-attempt-*.md"))
    last_report = texts.get(reports[-1].name) if reports else None
    diffs = natsorted(d.glob("chunk-*-attempt-*-diff.patch"))
    tests_out = natsorted(d.glob("chunk-*-attempt-*-tests.txt"))
    current_chunk = max((int(journal_chunk_num(path) or 0) for path in journals), default=0)
    if diffs:
        scope_problems = small_scope_errors(d, intent)
        v.check(not scope_problems, 'контур допустим по фактическому diff', '; '.join(scope_problems),
                'подпись в плане не заменяет проверку области; позднее изменение контура не исправляет историю')

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

    # --- 2. Плейсхолдеры — по всем артефактам витка, кроме улик и архива ------------------------
    # chunk-N-attempt-K-review.md — сырой ответ рецензента (он вправе цитировать «‹причина›»),
    # plan-vK.md — прежняя редакция плана; оба не форма, скан их не судит.
    for name, text in texts.items():
        if (not text or name.endswith("-review.md") or re.match(r"plan-v\d+\.md$", name)
                or re.fullmatch(r"\.chunk-\d+-initial-journal\.md", name)):
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
        if (d / 'git-operation.json').exists():
            import git_operation
            operation_errors = git_operation.completion_errors(root, d)
            v.check(not operation_errors, 'результат Git-операции проверен', '; '.join(operation_errors),
                    'выполни postcheck; ошибка оставляет задачу открытой с процедурой исправления')
        defects = defect_record_errors(d, handoff)
        v.check(not defects, 'обязательные записи о дефектах завершены', '; '.join(defects),
                'запиши подтверждённую находку, действие, утверждение и закрепляющую проверку')
        # handoff дописывается новой секцией на каждый виток той же задачи (SDLC.md → «Раскладка
        # артефактов»): действует ПОСЛЕДНЯЯ запись «Приёмка», а не первая попавшаяся — иначе
        # приёмка прошлого витка засчитывалась бы новому (у которого поля может не быть вовсе)
        acc_lines = [ln for ln in handoff.splitlines() if "**Приёмка:**" in ln]
        last_acc = acc_lines[-1].split("**Приёмка:**", 1)[-1].strip() if acc_lines else ""
        loops = [s_ for s_ in md_sections(handoff) if s_.startswith("Виток ")]
        last_loop = loops[-1] if loops else handoff
        v.check(bool(acc_lines) and "**Приёмка:**" in last_loop
                and bool(last_acc) and PLACEHOLDER not in strip_literal_spans(last_acc),
                "приёмка записана в handoff (последний виток)",
                "поля «Приёмка» нет в последней секции витка или оно не заполнено",
                "приёмка человеком не фиксируется — чинить /sdlc-handoff Phase 1")
    for p in journals:
        j = texts.get(p.name) or ""
        sig_line = next((ln for ln in j.splitlines() if "Подтвердил:" in ln), "")
        sig = sig_line.split("Подтвердил:", 1)[-1]
        v.check(bool(sig) and PLACEHOLDER not in strip_literal_spans(sig),
                f"подпись места правки записана ({p.name})", "секция «Место правки» без подписи",
                "подтверждение места правки не записывается — чинить /sdlc-chunk Phase 2")
        n = journal_chunk_num(p)
        if n is not None:
            problems = chunk_preconditions.errors(root, d, n, historical=int(n) < current_chunk)
            v.check(not problems, f'предусловия chunk-{n} подтверждены квитанцией', '; '.join(problems),
                    'выполни prepare до правок; позднее отклонение сохрани явно, не объявляй порядок доказанным')
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
        # та же K подряд законна после blocked_env (SDLC.md → «blocked_env в этот счёт не входит»):
        # повтор номера — не дубль, дыра — по-прежнему затёртая попытка
        uniq = sorted(set(nums))
        v.check(uniq == list(range(1, len(uniq) + 1)) and nums == sorted(nums),
                f"нумерация попыток chunk-{n} непрерывна", f"номера: {nums}",
                "дыра в номерах или номер не по порядку: затёртая попытка — или строка с ‹…› в ячейке даты")
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
        ci, cr = claims_in(intent), claims_in(last_report)
        v.check(ci == cr, "claims intent == verification",
                f"разница: {sorted(ci ^ cr)}",
                "отчёт приёмки не покрывает лист 1:1")
    if intent and plan and readiness:
        for ok, name, detail in preparation_checks(intent, plan, readiness,
                                                  chunk_preconditions.contour(intent) == 'мелкий'):
            v.check(ok, name, detail, 'исправь форму до prepare; сохрани прежние отказы')

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
        bad_rows, skipped_rows = [], []
        for ln in rows:  # один проход по ячейкам строки на оба набора глифов сразу
            glyphs = row_glyphs(ln)
            if glyphs & set("❌⚠"):
                bad_rows.append(ln)
            if "⏭" in glyphs:
                skipped_rows.append(ln)
        # «строк неприменимости нет» — фраза шаблона для пустой таблицы неприменимости; регистр
        # и bold-обёртка могут различаться. Сверка регистронезависимая.
        has_inapplicability = not re.search(r"строк\s+неприменимости\s+нет", last_report, re.I)
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
        from sdlc_common import artifact_targets
        try:
            artifact_targets(root, d)
            targets_ok = True
        except ValueError:
            targets_ok = False
        v.check(targets_ok,
                "files_to_touch без .sdlc/**", ".sdlc в таблице путей",
                "артефакты процесса попали в scope кода")

    # --- 8. Разбор последствий: строка на каждую ось, адресат каждого исхода существует ------------
    # SDLC.md → этап 4: «Адресат исхода обязан существовать … Проверка механическая» — до этой
    # секции обещание держалось прозой: шесть осей закрывались «claim-99» и «гейт «Такого нет»».
    if gates and plan and intent and re.search(r"^\|\s*Разбор последствий\s*\|\s*да(?!\w)", gates, re.M):
        section = next((s_ for s_ in md_sections(plan) if s_.startswith("Последствия шагов")), "")
        # две таблицы секции различаются заголовком, а не порядком: «| Ось | Затронута шагами |»
        # и «| Ось | Риск словами |»
        axis_rows, risk_axes, current = {}, set(), None
        for ln in section.splitlines():
            if not ln.strip().startswith("|"):
                continue
            r = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(r) >= 2 and r[0] == "Ось":
                current = "axes" if r[1].startswith("Затронута") else ("risks" if r[1].startswith("Риск") else None)
                continue
            if not r or r[0] not in AXES or set(r[0]) <= {"-"}:
                continue
            if current == "axes" and r[0] not in axis_rows:
                axis_rows[r[0]] = r
            elif current == "risks":
                risk_axes.add(r[0])
        missing = [a for a in AXES if a not in axis_rows]
        v.check(not missing, "разбор последствий: строка на каждую ось канона",
                f"нет строк: {missing}" if missing else "",
                "сокращённая таблица делает «оси нет» неотличимым от «не подумали» (SDLC.md → этап 4)")
        open_q = bool(re.search(r"^- \[ \] \*\*\[неблокирующий\]\*\*", intent, re.M))
        inv_section = next((s_ for s_ in md_sections(intent) if s_.startswith("Инварианты")), "")
        has_invariant = any(ln.startswith("- ") and not ln.startswith("- н/п")
                            and PLACEHOLDER not in strip_literal_spans(ln)
                            for ln in inv_section.splitlines())
        ci = claims_in(intent)
        problems = []
        for axis, r in axis_rows.items():
            raw = r[3] if len(r) > 3 else ""
            outcome = strip_literal_spans(raw).strip()
            low = outcome.lower()
            if re.search(r"\bclaim-\d+\b", raw):
                for cid in sorted(claims_in(raw)):
                    if cid not in ci:
                        problems.append(f"{axis}: {cid} нет в задаче")
            elif low.startswith("гейт"):
                m = re.search(r"«([^»]+)»", raw)
                name = m.group(1) if m else ""
                if not name or not re.search(rf"^\|\s*{re.escape(name)}\s*\|\s*да(?!\w)", gates, re.M):
                    problems.append(f"{axis}: гейт «{name}» не включён в наборе")
            elif low.startswith("следующий виток"):
                if not open_q:
                    problems.append(f"{axis}: «следующий виток» без открытого неблокирующего вопроса в задаче")
            elif low.startswith("риск"):
                if axis not in risk_axes:
                    problems.append(f"{axis}: «риск» без строки в таблице принятых рисков")
            elif low.startswith("инвариант"):
                if not has_invariant:
                    problems.append(f"{axis}: «инвариант» при пустой секции «Инварианты» задачи")
            elif low.startswith("н/п"):
                # причина — по сырой ячейке: «н/п — «ось не затронута»» законна, en-dash тоже
                if not re.search(r"н/п\s*[—–-]\s*\S", raw.strip()):
                    problems.append(f"{axis}: «н/п» без причины")
            else:
                problems.append(f"{axis}: исход «{outcome[:40]}» вне словаря")
        v.check(not problems, "разбор последствий: адресаты исходов существуют",
                "; ".join(problems),
                "исход без адресата — то же ключевое слово вместо решения (SDLC.md → этап 4)")

    # --- 9. Размер chunk'а: ориентир измерим, молчаливое превышение — нет ---------------------------
    # SDLC.md → этап 4: до ~400 строк и ~8 файлов; больше — законно только со строкой плана
    # «почему этот chunk больше ориентира». Diff попытки кумулятивен от base_sha витка (SDLC.md →
    # этап 6), поэтому chunk'у приписывается ПРИРАЩЕНИЕ: его последняя попытка минус последняя
    # попытка предыдущего chunk'а. Свидетельств нет — проверять нечего, и это не провал.
    by_chunk = {}
    selected_base = None
    try:
        if snapshot_base.kind_of(plan) == 'snapshot':
            selected_base, _ = snapshot_base.load(root, d)
            v.check(True, 'снимок базы сохранён и цел', '', '')
    except ValueError as error:
        v.fail('снимок базы сохранён и цел', str(error), 'восстанови исходный снимок; не подменяй базу')
    for ev in natsorted(d.glob("chunk-*-attempt-*-evidence.json")):
        m = re.fullmatch(r"chunk-(\d+)-attempt-(\d+)-evidence\.json", ev.name)
        if not m:
            continue
        try:
            rec = json.loads(read(ev) or "{}")
        except json.JSONDecodeError:
            v.fail(f"свидетельство {ev.name} читается", "не JSON", "evidence.json пишет инструмент, не агент")
            continue
        if selected_base is not None or rec.get('base', {}).get('kind') == 'snapshot':
            v.check(selected_base is not None and rec.get('base') == selected_base,
                    f'база {ev.name} совпадает с планом', 'вид или хэш базы изменён',
                    'все попытки витка используют один снимок')
        if m:
            readiness = rec.get('preconditions') or {}
            receipt_hash = readiness.get('receipt_sha256')
            problems = chunk_preconditions.errors(root, d, m.group(1), expected_hash=receipt_hash,
                                                 historical=int(m.group(1)) < current_chunk)
            v.check(bool(receipt_hash) and not readiness.get('errors') and not problems,
                    f'квитанция {ev.name} сохранена до попытки', '; '.join(problems) or 'квитанция не закреплена в evidence',
                    'не создавай свидетельство готовности задним числом')
        if m:
            stat = rec.get('diffstat') or {}
            chunk_diff = rec.get('chunk_diff')
            if chunk_diff:
                try:
                    raw = chunk_preconditions.artifact(d, chunk_diff['path']).read_bytes()
                    if (chunk_diff['path'] != f'chunk-{m.group(1)}-attempt-{m.group(2)}-chunk.patch'
                            or snapshot_base.digest(raw) != chunk_diff['sha256']):
                        raise ValueError('Патч chunk изменён')
                    stat = diffstat(raw.decode('utf-8', errors='surrogateescape'))
                    if stat != chunk_diff['diffstat']:
                        raise ValueError('Размер chunk не совпадает с патчем')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    v.fail(f'размер {ev.name} подтверждён', str(error), 'восстанови свидетельства; не переписывай историю')
            by_chunk.setdefault(int(m.group(1)), {})[int(m.group(2))] = (stat, bool(chunk_diff))
    explained = bool(re.search(r"почему этот chunk больше ориентира", plan or "", re.I))
    prev_lines, prev_files = 0, 0
    for cn in sorted(by_chunk):
        stat, measured = by_chunk[cn][max(by_chunk[cn])]
        total_lines = int(stat.get("added", 0)) + int(stat.get("deleted", 0))
        total_files = int(stat.get("files", 0))
        lines, files = ((total_lines, total_files) if measured else
                        (max(0, total_lines - prev_lines), max(0, total_files - prev_files)))
        prev_lines, prev_files = total_lines, total_files
        over = lines > CHUNK_LINES or files > CHUNK_FILES
        v.check(not over or explained,
                f"размер chunk-{cn} в ориентире или объяснён",
                f"{lines} строк / {files} файлов при ориентире {CHUNK_LINES} / {CHUNK_FILES}, "
                "строки «почему этот chunk больше ориентира» в плане нет",
                "молчаливое превышение ориентира — рецензент вправе вернуть diff как непроверяемый целиком")

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


def main(root: Path, slug: str, at: str = 'handoff') -> int:
    result = check_flow(root, slug, at)
    d = root / '.sdlc' / slug
    reports = natsorted(d.glob('verification-report-*-attempt-*.md'))
    if at == 'verify' and reports and not (d / 'state.json').exists() and not (d / 'state.json').is_symlink():
        verify_receipt(d, reports[-1], result)
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    at, mode, metrics = "handoff", None, False
    pos = []
    i = 0
    while i < len(args):
        if args[i] == "--at" and i + 1 < len(args):
            at = args[i + 1]; i += 2
        elif args[i] == "--print" and i + 1 < len(args):
            mode = args[i + 1]; i += 2
        elif args[i] == "--metrics":
            metrics = True; i += 1
        else:
            pos.append(args[i]); i += 1
    if metrics:
        if len(pos) != 1:
            print(__doc__)
            sys.exit(2)
        sys.exit(do_metrics(Path(pos[0])))
    if at not in ("verify", "handoff") or len(pos) != 2:
        print(__doc__)
        sys.exit(2)
    if mode:
        sys.exit(do_print(mode, Path(pos[0]), pos[1]))
    sys.exit(main(Path(pos[0]), pos[1], at))
