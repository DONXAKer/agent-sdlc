#!/usr/bin/env python3
"""Гейт «Секреты в diff» — эталонная реализация.

  python secrets.py <патч попытки>

Смотрит только ДОБАВЛЕННЫЕ строки патча: ключи облаков, приватные ключи, токены платформ,
присваивания password/secret/token/api_key литералом. Строка с пометкой `sdlc:not-a-secret`
(в комментарии рядом) исключается — явное решение, а не молчаливый пропуск.
Контракт вывода — см. _common.py.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit, patch_hunks, read  # noqa: E402

GATE = "Секреты в diff"
PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"-----BEGIN (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY", "приватный ключ"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}", "API key вида sk-…"),
    (r"\bAIza[0-9A-Za-z_-]{35}", "Google API key"),
    (r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
     "литеральное значение секрета"),
    (r"(?i)(?:postgres|mysql|mongodb|redis|amqp)://[^:\s/]+:[^@\s]{4,}@", "URL с паролем"),
]
ALLOW = "sdlc:not-a-secret"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    patch = read(Path(sys.argv[1]), "")
    evidence = []
    for path, ln, sign, text in patch_hunks(patch):
        if sign != "+" or ALLOW in text:
            continue
        for rx, label in PATTERNS:
            if re.search(rx, text):
                shown = re.sub(r"[A-Za-z0-9_\-]{6,}", lambda m: m.group(0)[:3] + "…", text.strip())[:80]
                evidence.append(f"{path}:{ln} — {label}: {shown}")
                break
    if evidence:
        return emit(GATE, "fail", evidence, f"{len(evidence)} строк(и) с признаком секрета")
    return emit(GATE, "pass", [], "в добавленных строках секретов не найдено")


if __name__ == "__main__":
    sys.exit(main())
