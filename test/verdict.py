#!/usr/bin/env python3
"""Обёртка совместимости: логика живёт в инструменте скилла sdlc-verify.

Канонический путь: implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py
(устанавливается вместе со скиллом и исполняет гейт «Сверка отчёта с набором»).
Старые команды `python3 test/verdict.py <run-dir> <slug>` продолжают работать.
"""

import runpy
import sys
from pathlib import Path

TOOL = (Path(__file__).resolve().parent.parent
        / "implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py")

sys.argv[0] = str(TOOL)
runpy.run_path(str(TOOL), run_name="__main__")
