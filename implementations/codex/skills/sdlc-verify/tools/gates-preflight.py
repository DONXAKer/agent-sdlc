#!/usr/bin/env python3
"""gates-preflight: исполним ли набор гейтов в этой среде — ДО того, как потрачена попытка.

SDLC.md → «Набор гейтов проекта»: «Runner-реализация обязана проверять это на старте каждого
этапа». И → этап 6, «Красный из-за окружения»: `blocked_env` — когда инструмента для гейта
класса «Сборка»/«Тесты» в среде нет физически.

Что делает: читает `.sdlc/gates.md`, берёт включённые строки заданного этапа, из колонки
«Чем реализован» — команду в обратных кавычках (проза командой не считается), и проверяет:
  - первое слово исполнимо: в PATH, либо путь от корня проекта (`./gradlew`, `make -C test`);
  - для интерпретаторов (`python`, `python3`, `node`, `bash`, `sh`) — что скрипт-аргумент
    существует: от корня проекта, абсолютно, либо через `~` (эталоны методологии живут в
    `~/.claude/skills/sdlc-verify/tools/`).
Ничего не запускает.

  python gates-preflight.py <корень проекта> [--stage "этап 6"] [--json]
Код возврата: 0 — всё исполнимо (или команд нет); 1 — есть неисполнимые — кандидат в
`blocked_env`, строка долга печатается готовой; 2 — набора нет.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import ARTIFACTS_DIR, command_of, enabled_gates, first_token, read  # noqa: E402

INTERPRETERS = {"python", "python3", "py", "node", "bash", "sh"}
BUILTINS = {"true", "false", "echo", "cd", "export", "set", "test"}


def path_exists(token: str, root: Path) -> bool:
    p = Path(token.replace("\\", "/")).expanduser()
    if not p.is_absolute():
        p = root / p
    return p.exists() or Path(str(p) + ".bat").exists() or Path(str(p) + ".cmd").exists()


def executable(token: str, root: Path) -> bool:
    if not token:
        return False
    if token in BUILTINS:
        return True
    if token.startswith(("./", ".\\", "/", "~")) or "/" in token or "\\" in token:
        return path_exists(token, root)
    return shutil.which(token) is not None


def script_of(cmd: str, tok: str):
    """Для интерпретатора — путь скрипта (первый аргумент не с `-`), иначе None."""
    if tok.replace("\\", "/").split("/")[-1].split(".")[0] not in INTERPRETERS:
        return None
    parts = cmd.split()
    idx = parts.index(tok) if tok in parts else 0
    for p in parts[idx + 1:]:
        if p.startswith("-"):
            if p in ("-m", "-c"):
                return None  # модуль/строка кода — файла нет
            continue
        return p
    return None


def preflight(root: Path, stage: str = "этап 6"):
    gates = root / ARTIFACTS_DIR / "gates.md"
    if not gates.exists():
        return None
    rows = []
    for name, _, how in enabled_gates(read(gates, ""), stage):
        cmd = command_of(how)
        if cmd is None:
            rows.append({"gate": name, "command": None, "status": "no-command",
                         "detail": "команды в обратных кавычках нет — при прогоне даст ⏭ «исполнить нечем», "
                                   "если нет встроенной реализации"})
            continue
        tok = first_token(cmd)
        ok = executable(tok, root)
        detail = "" if ok else f"«{tok}» не найден: ни в PATH, ни как путь от корня проекта"
        script = script_of(cmd, tok) if ok else None
        if script and not path_exists(script, root):
            ok, detail = False, f"скрипт «{script}» не найден: ни от корня проекта, ни по абсолютному пути, ни через ~"
        rows.append({"gate": name, "command": cmd, "token": tok, "status": "ok" if ok else "missing",
                     "detail": detail})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(prog="gates-preflight.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root"); ap.add_argument("--stage", default="этап 6"); ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args()
    except SystemExit:
        return 2
    root = Path(a.root).resolve()
    rows = preflight(root, a.stage)
    if rows is None:
        print(f"нет {root / ARTIFACTS_DIR / 'gates.md'} — набор не собран, виток не стартует")
        return 2
    missing = [r for r in rows if r["status"] == "missing"]
    if a.json:
        print(json.dumps({"stage": a.stage, "rows": rows, "blocked_env": bool(missing)}, ensure_ascii=False, indent=2))
        return 1 if missing else 0
    print(f"# Preflight набора гейтов ({a.stage}): {root}\n")
    for r in rows:
        glyph = {"ok": "✅", "missing": "❌", "no-command": "⚠"}[r["status"]]
        print(f"- {glyph} {r['gate']}: `{r['command']}`" if r["command"] else f"- {glyph} {r['gate']}: {r['detail']}")
        if r["status"] == "missing":
            print(f"    {r['detail']}")
    if missing:
        print("\nСреды для гейта физически нет — это `blocked_env` ДО попытки, не после (SDLC.md → этап 6).")
        print("Строка долга набора (заводится один раз на проект, не на каждый виток):")
        for r in missing:
            print(f"| {r['gate']}: нет «{r['token']}» в среде исполнения | этап 6 | "
                  f"‹кто и чем закрывает: ставит инструмент / собирает образ› | ‹дата› | ‹имя› |")
        return 1
    print("\nВсе команды включённых гейтов исполнимы в этой среде.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
