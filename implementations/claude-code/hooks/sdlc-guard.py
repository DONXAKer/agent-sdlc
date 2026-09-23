#!/usr/bin/env python3
"""sdlc-guard: PreToolUse-hook Claude Code — границы этапов 5 и 6 конструкцией, не просьбой.

Три режима, все включаются файлом-маркером в `.sdlc/<slug>/`, который ставит и снимает скилл:

1. **Граница scope** (гейт «Граница scope hook'ом», этап 5; `sdlc-chunk/SKILL.md` Phase 2a):
   пока существует `.scope.json` — {"slug", "chunk", "allow": [пути]}, — Write/Edit/MultiEdit/
   NotebookEdit в путь вне `allow` (и вне `.sdlc/**`) отклоняются.
2. **Ревью без записи** (этап 6; `sdlc-verify/SKILL.md` Phase 2): пока существует `.review-lock`,
   отклоняются Write/Edit/MultiEdit/NotebookEdit вне `.sdlc/**` (артефакты витка — ответ
   рецензента, отчёт — скилл писать обязан) и Bash-команды, меняющие дерево или git. Проверка
   команды — по тексту без кавычечных литералов, поэтому `grep "->"`, `2>/dev/null`,
   `python -c "print(1 > 0)"` проходят; `git -C/-c … commit`, `xargs rm`, `find -delete`,
   `python -c "open('f','w')"`, `perl -i`, `Set-Content` — нет. Это deny-list по тексту: он
   отбирает штатные способы записи, а не доказывает её невозможность.

3. **Слепота claims-агента** (этап 2; `sdlc-explore/SKILL.md` Phase 1): пока существует
   `.claims-lock`, отклоняются Read/Grep/Glob с путём внутри `.sdlc/` и Bash-команды, упоминающие
   `.sdlc/`. Второй агент разведки обязан вывести приёмочный лист, не видя авторского, — при
   подключённом hook'е это конструкция, а не просьба в промпте. Оркестратор на это время передаёт
   разведчику текст задачи инлайн, а не путь к `intent.md`.

Какой виток активен, hook узнаёт из `.sdlc/.active` (пишут `--lock`/`--scope`); нет файла —
действуют маркеры всех витков. Нечитаемый ввод hook'а при наличии маркеров — deny (fail-closed).

CLI для скиллов (эти вызовы hook не блокирует — `python` не в списке мутирующих):
  python sdlc-guard.py --lock   <корень> <slug>      # поставить .review-lock и .active
  python sdlc-guard.py --unlock <корень> <slug>      # снять .review-lock (и .active)
  python sdlc-guard.py --scope  <корень> <slug> <chunk> <путь>…   # записать .scope.json и .active
  python sdlc-guard.py --unscope <корень> <slug>     # снять .scope.json (и .active)
  python sdlc-guard.py --claims-lock <корень> <slug> # закрыть чтение .sdlc/ на время этапа 2
  python sdlc-guard.py --claims-unlock <корень> <slug>
  python sdlc-guard.py --status <корень>             # какие маркеры стоят

Подключение — `.claude/settings.json` ПРОЕКТА (не глобально: hook — отдельный процесс на каждый
вызов инструмента, платить за него всем проектам незачем):
  {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
     "hooks": [{"type": "command", "command": "python ~/.claude/hooks/sdlc-guard.py"}]}]}}
"""

import fnmatch
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ARTIFACTS_DIR = ".sdlc"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "Write", "Edit", "MultiEdit", "NotebookEdit"}
MARKERS = (".scope.json", ".review-lock", ".claims-lock")
GIT_MUT = r"git(?:\s+-[cC]\s+\S+)*\s+(?:add|commit|checkout|switch|reset|push|rm|mv|stash|rebase|merge|cherry-pick|apply|clean|restore|tag|branch\s+-[dDm]|worktree\s+(?:add|remove))\b"
# по тексту команды БЕЗ кавычечных литералов: команды оболочки и перенаправления
SHELL_MUT = re.compile(
    r"(?:^|[;&|]\s*|\bxargs\s+(?:-\S+\s+)*)"
    r"(?:" + GIT_MUT + r"|rm\b|rmdir\b|mv\b|cp\b|touch\b|mkdir\b|tee\b|sed\s+(?:-\S*)?-i|truncate\b|dd\b|chmod\b|chown\b|ln\b|"
    r"pip\s+install|npm\s+(?:install|i|ci|uninstall)\b|perl\s+(?:-\S*)?-i)"
    r"|\bfind\b.*\s-(?:delete|exec\s+rm)\b"
    r"|(?<![<>\d&\-=|])>{1,2}(?!&\d)\s*(?!/dev/null)", re.M)
# по сырому тексту: запись изнутри интерпретаторов, где полезная нагрузка стоит в кавычках
PAYLOAD_MUT = re.compile(
    r"\bopen\([^)]*['\"](?:w|a|r\+|w\+|a\+)b?['\"]"
    r"|\b(?:Set-Content|Out-File|Add-Content|Remove-Item|New-Item)\b"
    r"|\bos\.(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir)\b|\bshutil\.(?:rmtree|move|copy)"
    r"|\bwrite_(?:text|bytes)\(|\bfs\.(?:write|unlink|rm|rename)\w*\(", re.M)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def strip_quotes(cmd: str) -> str:
    return QUOTED.sub("''", cmd or "")


def is_mutating(cmd: str) -> bool:
    cmd = cmd or ""
    return bool(SHELL_MUT.search(strip_quotes(cmd)) or PAYLOAD_MUT.search(cmd))


def project_root(start: Path):
    cur = start.resolve()
    for _ in range(12):
        if (cur / ARTIFACTS_DIR).is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def find_markers(root: Path):
    """[(каталог витка, имя маркера)] — по активному витку (.sdlc/.active), иначе по всем."""
    sdlc = root / ARTIFACTS_DIR
    active = (sdlc / ".active").read_text(encoding="utf-8").strip() if (sdlc / ".active").exists() else ""
    dirs = [sdlc / active] if active and (sdlc / active).is_dir() else [p for p in sdlc.iterdir() if p.is_dir()]
    found = []
    for d in dirs:
        for name in MARKERS:
            if (d / name).exists():
                found.append((d, name))
    return found


def deny(reason: str):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


def rel_path(root: Path, raw: str) -> str:
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.resolve().relative_to(root).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def allowed(rel: str, patterns) -> bool:
    if rel.startswith(ARTIFACTS_DIR + "/"):
        return True
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def hook() -> int:
    root = project_root(Path(os.getcwd()))
    if root is None:
        return 0
    markers = find_markers(root)
    if not markers:
        return 0
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input") or {}
    except (json.JSONDecodeError, ValueError, AttributeError):
        return deny("sdlc-guard: ввод hook'а не разобран, а в .sdlc/ стоят маркеры границ — отказ (fail-closed).")
    for d, name in markers:
        unlock = f"python ~/.claude/hooks/sdlc-guard.py --unlock {root} {d.name}"
        if name == ".claims-lock":
            target = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or ""
            pattern = tool_input.get("pattern") or ""
            if tool in READ_TOOLS and target and rel_path(root, target).rstrip("/") in (ARTIFACTS_DIR,) \
                    or tool in READ_TOOLS and target and rel_path(root, target).startswith(ARTIFACTS_DIR + "/") \
                    or tool in READ_TOOLS and ARTIFACTS_DIR in pattern \
                    or tool == "Bash" and ARTIFACTS_DIR + "/" in (tool_input.get("command", "") or ""):
                return deny(f"Этап 2, слепой вывод приёмочного листа в {d.name}: `.sdlc/` закрыт для чтения, "
                            f"пока стоит маркер {d / name} — второй агент не должен видеть авторский лист. "
                            f"Снять: python ~/.claude/hooks/sdlc-guard.py --claims-unlock {root} {d.name}")
            continue
        if name == ".review-lock":
            if tool in WRITE_TOOLS:
                target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
                if target and not rel_path(root, target).startswith(ARTIFACTS_DIR + "/"):
                    return deny(f"Этап 6, ревью в {d.name}: инструменты записи вне .sdlc/ на время ревью закрыты "
                                f"(маркер {d / name}). Снять после ответа рецензента: {unlock}")
            if tool == "Bash" and is_mutating(tool_input.get("command", "") or ""):
                return deny(f"Этап 6, ревью в {d.name}: команда меняет дерево или git — на время ревью разрешены "
                            f"только чтение и запуск (маркер {d / name}). Снять: {unlock}")
        elif name == ".scope.json" and tool in WRITE_TOOLS:
            try:
                scope = json.loads((d / name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            if not target:
                continue
            rel = rel_path(root, target)
            if not allowed(rel, scope.get("allow") or []):
                return deny(f"Этап 5, chunk {scope.get('chunk')} витка {scope.get('slug')}: «{rel}» вне "
                            f"files_to_touch одобренного плана. Это точка «Когда остановиться и спросить» — "
                            f"расширение scope только новым одобрением, не правкой мимо hook'а.")
    return 0


def cli(args) -> int:
    mode = args[0]
    root = Path(args[1]).resolve() if len(args) > 1 else None
    sdlc = root / ARTIFACTS_DIR if root else None
    if mode == "--status":
        if not sdlc or not sdlc.is_dir():
            print("нет .sdlc/")
            return 0
        active = (sdlc / ".active").read_text(encoding="utf-8").strip() if (sdlc / ".active").exists() else "(нет)"
        print(f"активный виток: {active}")
        for d, name in find_markers(root):
            print(f"- {d.name}/{name}")
        return 0
    if len(args) < 3:
        print(__doc__)
        return 2
    slug = args[2]
    d = sdlc / slug
    d.mkdir(parents=True, exist_ok=True)
    if mode == "--lock":
        (d / ".review-lock").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n", encoding="utf-8")
        (sdlc / ".active").write_text(slug + "\n", encoding="utf-8")
        print(f"замок ревью поставлен: {d / '.review-lock'}")
        return 0
    if mode == "--unlock":
        (d / ".review-lock").unlink(missing_ok=True)
        if not (d / ".scope.json").exists() and not (d / ".claims-lock").exists():
            (sdlc / ".active").unlink(missing_ok=True)
        print(f"замок ревью снят: {slug}")
        return 0
    if mode == "--scope":
        if len(args) < 5:
            print(__doc__)
            return 2
        chunk, paths = args[3], args[4:]
        (d / ".scope.json").write_text(json.dumps({"slug": slug, "chunk": int(chunk),
                                                   "allow": paths + [ARTIFACTS_DIR + "/**"]},
                                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (sdlc / ".active").write_text(slug + "\n", encoding="utf-8")
        print(f"граница scope поставлена: {d / '.scope.json'} ({len(paths)} путей)")
        return 0
    if mode == "--unscope":
        (d / ".scope.json").unlink(missing_ok=True)
        if not (d / ".review-lock").exists() and not (d / ".claims-lock").exists():
            (sdlc / ".active").unlink(missing_ok=True)
        print(f"граница scope снята: {slug}")
        return 0
    if mode == "--claims-lock":
        (d / ".claims-lock").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n", encoding="utf-8")
        (sdlc / ".active").write_text(slug + "\n", encoding="utf-8")
        print(f"чтение .sdlc/ закрыто на время слепого вывода: {d / '.claims-lock'}")
        return 0
    if mode == "--claims-unlock":
        (d / ".claims-lock").unlink(missing_ok=True)
        if not (d / ".review-lock").exists() and not (d / ".scope.json").exists():
            (sdlc / ".active").unlink(missing_ok=True)
        print(f"чтение .sdlc/ открыто: {slug}")
        return 0
    print(__doc__)
    return 2


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        return cli(sys.argv[1:])
    return hook()


if __name__ == "__main__":
    sys.exit(main())
