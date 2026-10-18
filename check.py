#!/usr/bin/env python3
"""check: единая точка всех проверок репозитория методологии — локально и в CI.

CLAUDE.md перечисляет пять проверок по зонам; до этого файла ни одна не запускалась
сама, и зелёный «по памяти» не отличался от зелёного по факту. Порядок фиксирован,
падение любой — красный итог (код возврата 1), остальные всё равно прогоняются.

  python check.py                 # всё, кроме сверки глобальной установки
  python check.py --with-install  # плюс ./install.sh --check (нужен ~/.claude)
  python check.py --list          # только перечислить шаги

Кроссплатформенно: bash-скрипты идут через `bash` (на Windows — Git Bash), python — через
текущий интерпретатор. Нет bash — соответствующие шаги помечаются «пропущено» и роняют
итог: пропущенная проверка не зелёная (та же норма, что у гейта `⏭`).
"""

import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
PY = sys.executable
BASH = shutil.which("bash")

TEST_FILES = sorted((ROOT / "test").glob("test_*.py"))  # discovery: новый test_*.py попадает сюда сам
STEPS = [
    ("шаблоны: копии в скиллах = корень", ["bash", "./sync-templates.sh", "--check"], ROOT),
    ("согласованность SDLC.md ↔ шаблоны ↔ скиллы ↔ агенты ↔ константы",
     [PY, "tools/check-consistency.py"], ROOT),
    ("регрессионный корпус flow-verdict.py", [PY, "test/run-corpus.py"], ROOT),
    ("самопосев flow-verdict.py (непойманные мутанты)", [PY, "test/seed-flow-verdict.py"], ROOT),
] + [(f"тесты: {t.name}", [PY, str(t.relative_to(ROOT))], ROOT) for t in TEST_FILES] + [
    ("fixture-планировщик: сборка", [PY, "-m", "compileall", "-q", "scheduler", "tests"], ROOT / "test" / "fixture"),
    ("fixture-планировщик: тесты", [PY, "-m", "unittest", "discover", "-s", "tests"], ROOT / "test" / "fixture"),
]
INSTALL_STEP = ("глобальная установка sdlc-* = репозиторий", ["bash", "./install.sh", "--check"], ROOT)


def main() -> int:
    args = sys.argv[1:]
    steps = list(STEPS) + ([INSTALL_STEP] if "--with-install" in args else [])
    if "--list" in args:
        for name, cmd, _ in steps:
            print(f"- {name}: {' '.join(cmd)}")
        return 0
    failed = []
    for name, cmd, cwd in steps:
        if cmd[0] == "bash":
            if not BASH:
                print(f"⏭ {name} — пропущено: bash не найден (на Windows нужен Git Bash)")
                failed.append(name)
                continue
            cmd = [BASH] + cmd[1:]
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
        print(f"{'✅' if ok else '❌'} {name}")
        if not ok:
            failed.append(name)
            out = (proc.stdout + proc.stderr).strip()
            print("   " + "\n   ".join(out.splitlines()[-25:]))
    print()
    if failed:
        print(f"Провалено {len(failed)} из {len(steps)}: " + "; ".join(failed))
        return 1
    print(f"Все {len(steps)} проверок зелёные.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
