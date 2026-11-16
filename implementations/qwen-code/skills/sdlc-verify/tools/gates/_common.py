"""Контракт вывода эталонных гейт-скриптов Agent-SDLC; разбор артефактов — в ../sdlc_common.py.

Контракт (SDLC.md → «Набор гейтов проекта», «Контракт гейт-скрипта»):
  - последняя строка stdout — JSON одной строкой:
      {"gate": "<имя дословно из набора>", "status": "pass"|"fail"|"skip",
       "evidence": ["<путь:строка / hunk / имя теста>", …], "detail": "<одна фраза>",
       "missing_tool": null | "<что отсутствует в среде>"}
  - статус считается по АРТЕФАКТУ прогона, не по коду возврата чужой команды; отказ инструмента
    среды — "skip" с "missing_tool", никогда "pass";
  - код возврата: 0 — pass, 1 — fail, 3 — skip (исполнить нечем), 2 — неверный вызов.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdlc_common import (  # noqa: E402,F401  — реэкспорт для гейт-скриптов
    ARTIFACTS_DIR, PLACEHOLDER, claims_in, diffstat, files_to_touch, git_out, git_run,
    is_test_or_config_path, is_test_path, patch_files, patch_hunks, read, sha256,
)

EXIT = {"pass": 0, "fail": 1, "skip": 3}


def emit(gate: str, status: str, evidence, detail: str, missing_tool=None) -> int:
    """Печатает человекочитаемый итог и последней строкой — JSON контракта; возвращает код."""
    glyph = {"pass": "✅", "fail": "❌", "skip": "⏭"}[status]
    print(f"{glyph} {gate}: {detail}")
    for e in evidence:
        print(f"  - {e}")
    print(json.dumps({"gate": gate, "status": status, "evidence": list(evidence),
                      "detail": detail, "missing_tool": missing_tool}, ensure_ascii=False))
    return EXIT[status]
