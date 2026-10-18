"""sdlc_common: общие хелперы инструментов Agent-SDLC — один источник вместо копий.

Здесь живёт всё, что раньше было продублировано по инструментам: вывод в UTF-8, чтение
sdlc-constants.json, разбор плана и набора, обёртка над git (с `core.quotepath=false` — иначе
не-ASCII пути приходят октальными эскейпами и молча выпадают из патчей), разбор unified diff,
запуск команды тестов через оболочку с декодированием консоли и распознаванием отсутствующего
инструмента, признак тестового пути. Импортируется из tools/, tools/gates/ и hooks/ (последний —
через sys.path на соседний скилл).
"""

import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- консоль
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- константы
def load_constants() -> dict:
    """sdlc-constants.json рядом с инструментом (установка) или выше по дереву (репозиторий).
    Нет нигде — жёсткие фолбэки в коде и предупреждение в stderr: молчать нельзя."""
    for base in [HERE] + list(HERE.parents)[:6]:
        cand = base / "sdlc-constants.json"
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
    print("sdlc_common: sdlc-constants.json не найден — действуют встроенные умолчания", file=sys.stderr)
    return {}


CONSTANTS = load_constants()
PLACEHOLDER = (CONSTANTS.get("placeholder") or "‹…›")[0]
ARTIFACTS_DIR = CONSTANTS.get("artifacts_dir", ".sdlc")
NA = CONSTANTS.get("not_applicable", "н/п")
CLAIM_RE = re.compile(r"\b" + (CONSTANTS.get("claim_id_pattern") or r"claim-[0-9]+") + r"\b")
ACTIONS = list(CONSTANTS.get("actions") or ["continue", "retry", "blocked_env", "escalate"])
AXES = list(CONSTANTS.get("axes") or ["Безопасность", "Ресурсы и скорость", "Отказы зависимостей",
                                       "Настройки", "Совместимость и данные", "Наблюдаемость"])
MANDATORY_GATES = list(CONSTANTS.get("mandatory_gates") or [
    "Сборка", "Тесты", "Scope: файлы вне плана", "Анти-обход тест-гейта", "Ревью независимым агентом"])
DEFAULT_BUDGET = int(CONSTANTS.get("attempt_default_budget", 3))
CHUNK_LINES = int((CONSTANTS.get("chunk_size_guideline") or {}).get("changed_lines", 400))
CHUNK_FILES = int((CONSTANTS.get("chunk_size_guideline") or {}).get("files", 8))
OVERWRITE_PERCENT = int(CONSTANTS.get("overwrite_threshold_percent", 50))


def claims_in(text) -> set:
    return set(CLAIM_RE.findall(text or ""))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogateescape")).hexdigest()


def read(path: Path, default=None):
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else default


# ---------------------------------------------------------------- план и набор
def base_sha_of(plan_text: str):
    m = re.search(r"\*\*База:\*\*\s*`?([0-9a-fA-F]{7,40})", plan_text or "")
    return m.group(1) if m else None


def require_base_sha(plan_text: str) -> str:
    sha = base_sha_of(plan_text)
    if not sha:
        raise SystemExit("в plan.md нет поля «База» с sha — базу diff'а взять неоткуда")
    return sha


GATE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*)\|", re.M)


def gate_rows(gates_text: str):
    """[(имя, вкл, этап, чем)] по всем строкам таблицы набора (кроме заголовка и разделителя)."""
    rows = []
    for m in GATE_ROW.finditer(gates_text or ""):
        name, on, stage, how = (g.strip() for g in m.groups())
        if name in ("Гейт", "") or set(name) <= {"-"}:
            continue
        rows.append((name, on, stage, how))
    return rows


def enabled(on_cell: str) -> bool:
    return bool(re.match(r"да(?!\w)", on_cell.strip()))


def enabled_gates(gates_text: str, stage=None):
    """[(имя, этап, чем)] включённых строк; stage — фильтр по колонке «Где отчитывается»."""
    return [(n, st, how) for n, on, st, how in gate_rows(gates_text)
            if enabled(on) and (stage is None or st == stage)]


def gate_enabled(gates_text: str, name: str) -> bool:
    return any(n == name for n, _, _ in enabled_gates(gates_text))


def command_of(how_cell: str):
    """Команда — только целиком в обратных кавычках (SDLC.md: проза командой не считается)."""
    m = re.search(r"`([^`]+)`", how_cell or "")
    return m.group(1).strip() if m else None


def tests_cmd_of(gates_text: str):
    for n, _, how in enabled_gates(gates_text):
        if n == "Тесты":
            return command_of(how)
    return None


def require_tests_cmd(gates_text: str) -> str:
    cmd = tests_cmd_of(gates_text)
    if not cmd:
        raise SystemExit("в наборе нет команды «Тесты» в обратных кавычках — укажи --tests-cmd")
    return cmd


def files_to_touch(plan_text: str):
    """Пути из таблицы files_to_touch плана (первая колонка), без .sdlc/**."""
    sec = next((s for s in re.split(r"^## ", plan_text or "", flags=re.M) if s.startswith("files_to_touch")), "")
    paths = []
    for ln in sec.splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in ln.strip().strip("|").split("|")]
        if not cells or cells[0] in ("Путь", "") or set(cells[0]) <= {"-"}:
            continue
        if PLACEHOLDER in cells[0] or cells[0].startswith(ARTIFACTS_DIR + "/"):
            continue
        paths.append(cells[0])
    return paths


# ---------------------------------------------------------------- git
GIT_BASE = ["git", "-c", "core.quotepath=false"]


def git_run(args, cwd):
    """(rc, stdout, stderr) — байты декодируются вручную, переводы строк не трогаются."""
    try:
        proc = subprocess.run(GIT_BASE + list(args), cwd=cwd, capture_output=True)
    except FileNotFoundError:
        return 127, "", "git: not found"
    return proc.returncode, proc.stdout.decode("utf-8", errors="surrogateescape"), \
        proc.stderr.decode("utf-8", errors="replace")


def git_out(args, cwd):
    rc, out, _ = git_run(args, cwd)
    return out if rc == 0 else None


def git_ok(args, cwd) -> bool:
    return git_run(args, cwd)[0] == 0


_OCT = re.compile(r"\\([0-7]{3})")


def unquote_git_path(p: str) -> str:
    """Развернуть путь в кавычках с октальными эскейпами (на случай quotepath у чужого git)."""
    p = p.strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        inner = p[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        raw = _OCT.sub(lambda m: chr(int(m.group(1), 8)), inner)
        try:
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw
    return p


# ---------------------------------------------------------------- оболочка и консоль
MISSING_TOOL_RE = re.compile(
    r"command not found|not recognized as an internal or external command|"
    r"No such file or directory|не является внутренней или внешней|"
    r"is not recognized|CreateProcess error=2|No Java runtime|JAVA_HOME is not set", re.I)


def decode_console(raw: bytes) -> str:
    """utf-8, затем кодировки консоли Windows (cmd.exe пишет в OEM cp866), последняя — с заменой."""
    candidates = ["utf-8"]
    if sys.platform.startswith("win"):
        candidates += ["cp866", "cp1251"]
    candidates.append(locale.getpreferredencoding(False) or "utf-8")
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def first_token(cmd: str) -> str:
    for part in (cmd or "").split():
        if "=" in part and not part.startswith(("./", ".\\", "/")) and "/" not in part and "\\" not in part:
            continue  # префикс окружения VAR=x
        return part
    return ""


def git_bash():
    """Git Bash на Windows (не WSL-заглушка из System32): рядом с git.exe или в стандартных путях."""
    if not sys.platform.startswith("win"):
        return None
    cands = []
    git_exe = shutil.which("git")
    if git_exe:
        g = Path(git_exe).resolve()
        cands += [g.parents[1] / "bin" / "bash.exe", g.parents[1] / "usr" / "bin" / "bash.exe",
                  g.parents[2] / "bin" / "bash.exe", g.parents[2] / "usr" / "bin" / "bash.exe"]
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if pf:
            cands += [Path(pf) / "Git" / "bin" / "bash.exe", Path(pf) / "Git" / "usr" / "bin" / "bash.exe"]
    for c in cands:
        if c.exists():
            return str(c)
    b = shutil.which("bash")
    return None if not b or "system32" in b.lower() else b


def run_shell(cmd: str, cwd):
    """(rc, вывод). На Windows — через Git Bash, если он есть: команды наборов пишутся в POSIX-стиле
    (`./gradlew`, `make -C`), и cmd.exe их не исполняет."""
    bash = git_bash()
    if bash:
        proc = subprocess.run([bash, "-c", cmd], cwd=cwd, capture_output=True)
    else:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True)
    return proc.returncode, decode_console(proc.stdout + proc.stderr)


def missing_tool(output: str, cmd: str, rc: int = 0):
    """Отсутствующий инструмент: строка-диагностика оболочки называет САМ инструмент команды
    (не любое «No such file» из лога тестов). Возвращает строку-улику или None."""
    if rc in (127, 9009):
        return next((ln for ln in output.splitlines() if ln.strip()), f"код возврата {rc}")
    tok = first_token(cmd)
    base = tok.replace("\\", "/").split("/")[-1]
    for ln in output.splitlines():
        if MISSING_TOOL_RE.search(ln) and (tok in ln or (base and base in ln)):
            return ln.strip()[:200]
    return None


# ---------------------------------------------------------------- unified diff
DIFF_GIT = re.compile(r'^diff --git (?:"a/(.+?)"|a/(.+?)) (?:"b/(.+)"|b/(.+))$')


def patch_files(patch: str):
    """[{path, new, deleted, added, removed}] — заголовки читаются только до первого hunk'а файла,
    поэтому строки `++…`/`--…` внутри hunk'ов заголовками не считаются."""
    files, cur, in_header = [], None, False
    for ln in (patch or "").splitlines():
        m = DIFF_GIT.match(ln)
        if m:
            b = m.group(3) if m.group(3) is not None else m.group(4)
            cur = {"path": unquote_git_path(b or ""), "new": False, "deleted": False, "added": 0, "removed": 0}
            files.append(cur)
            in_header = True
            continue
        if cur is None:
            continue
        if in_header:
            if ln.startswith("@@"):
                in_header = False
            elif ln.startswith("--- "):
                if ln[4:].strip() == "/dev/null":
                    cur["new"] = True
            elif ln.startswith("+++ "):
                tgt = ln[4:].strip()
                if tgt == "/dev/null":
                    cur["deleted"] = True
                elif tgt.startswith("b/") or tgt.startswith('"b/'):
                    cur["path"] = unquote_git_path(tgt)[2:] if not tgt.startswith('"') else unquote_git_path(tgt)[2:]
            continue
        if ln.startswith("@@"):
            continue
        if ln.startswith("+"):
            cur["added"] += 1
        elif ln.startswith("-"):
            cur["removed"] += 1
    return files


def patch_hunks(patch: str):
    """[(путь, номер строки в новой/старой версии, '+'|'-', текст)] по всем hunk'ам."""
    out, path, new_ln, old_ln, in_header = [], None, 0, 0, False
    for ln in (patch or "").splitlines():
        m = DIFF_GIT.match(ln)
        if m:
            b = m.group(3) if m.group(3) is not None else m.group(4)
            path, in_header = unquote_git_path(b or ""), True
            continue
        if path is None:
            continue
        if in_header:
            if ln.startswith("+++ ") and ln[4:].strip() != "/dev/null":
                tgt = unquote_git_path(ln[4:].strip())
                path = tgt[2:] if tgt.startswith("b/") else tgt
            if ln.startswith("@@"):
                in_header = False
            else:
                continue
        if ln.startswith("@@"):
            mm = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", ln)
            old_ln, new_ln = (int(mm.group(1)), int(mm.group(2))) if mm else (0, 0)
        elif ln.startswith("+"):
            out.append((path, new_ln, "+", ln[1:]))
            new_ln += 1
        elif ln.startswith("-"):
            out.append((path, old_ln, "-", ln[1:]))
            old_ln += 1
        elif not ln.startswith("\\"):
            new_ln += 1
            old_ln += 1
    return out


def diffstat(patch: str):
    files = patch_files(patch)
    return {
        "files": len(files),
        "added": sum(f["added"] for f in files),
        "deleted": sum(f["removed"] for f in files),
        "paths": sorted(f["path"] for f in files),
        "deleted_paths": sorted(f["path"] for f in files if f["deleted"]),
        "per_file": {f["path"]: {"added": f["added"], "deleted": f["removed"],
                                 "new": f["new"], "deleted_entirely": f["deleted"]} for f in files},
    }


# кэш-мусор — тот же список, что в junk-names.sh репозитория методологии (install.sh, run-e2e.sh):
# нетракованный .pyc в проекте без .gitignore — не работа chunk'а и не тестовый файл
JUNK_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}
JUNK_FILES = re.compile(r"(^|/)(\.DS_Store|Thumbs\.db)$|\.pyc$|\.pyo$")


def is_junk_path(path: str) -> bool:
    parts = (path or "").replace("\\", "/").split("/")
    return any(p in JUNK_DIRS for p in parts) or bool(JUNK_FILES.search(path or ""))


TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|__tests__)(/|$)"
    r"|(^|/)test_[^/]*$"
    r"|(^|/)[^/]*_test\.[^/.]+$"
    r"|(^|/)[^/]*\.(test|spec)\.[^/]+$"
    r"|(^|/)tests?\.py$"
    r"|(^|/)[A-Za-z0-9]*Tests?\.(java|kt|cs|scala)$", re.I)


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path or ""))


CONFIG_PATH = re.compile(r"(^|/)(build\.gradle(\.kts)?|pom\.xml|pytest\.ini|setup\.cfg|pyproject\.toml|tox\.ini|"
                         r"package\.json|jest\.config[^/]*|karma\.conf[^/]*|Makefile|\.github/workflows/[^/]+)$")


def is_test_or_config_path(path: str) -> bool:
    return is_test_path(path) or bool(CONFIG_PATH.search(path or ""))


# ---------------------------------------------------------------- журнал
ATTEMPT_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.M)


def attempt_nums(journal: str):
    """Номера попыток из таблицы «Попытки»: строка-образец шаблона (‹дата› во второй ячейке) — не попытка.
    Единственное определение; flow-verdict.py и scaffold.py считают одинаково."""
    nums = []
    for ln in (journal or "").splitlines():
        m = ATTEMPT_ROW.match(ln)
        if not m:
            continue
        cells = ln.split("|")
        date_cell = re.sub(r"`[^`]*`|«[^»]*»", "", cells[2] if len(cells) > 2 else "")
        if PLACEHOLDER in date_cell:
            continue
        nums.append(int(m.group(1)))
    return nums
