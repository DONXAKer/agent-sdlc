#!/usr/bin/env python3
"""Гейт «Анти-обход тест-гейта» — эталонная реализация.

  python anti_bypass.py <патч попытки> [--allow-net-loss]

Зелёный прогон тестов сам по себе не означает, что тесты не отключали (SDLC.md → «Набор
гейтов»). Скрипт смотрит на патч, а не на прогон, и роняет гейт при любом из четырёх:
  1. в ТЕСТОВОМ или конфигурационном файле добавлен маркер отключения/фокуса теста (MARKERS);
     продакшн-код (`Model.objects.exclude(...)`, `stream.skip(1)`, `pending = []`) не проверяется —
     там эти слова законны;
  2. ассерт превратился в комментарий (строка с assert удалена, рядом добавлена та же
     закомментированной);
  3. удалён тестовый файл целиком;
  4. нетто-убыль тестовых деклараций (счёт по DECLARATIONS в «+» против «−» строк) —
     если не передан --allow-net-loss (план объяснил убыль строкой).
Контракт вывода — см. _common.py.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit, is_test_or_config_path, is_test_path, patch_files, patch_hunks, read  # noqa: E402

GATE = "Анти-обход тест-гейта"

MARKERS = [
    (r"\.skip\s*\(", "js/ts: .skip("), (r"\.only\s*\(", "js/ts: .only("),
    (r"\b(xit|xdescribe|xtest|fit|fdescribe)\s*\(", "js/ts: xit/xdescribe/fit"),
    (r"@Disabled\b", "junit5: @Disabled"), (r"@Ignore\b", "junit4/nunit: @Ignore"),
    (r"@pytest\.mark\.skip", "pytest: @pytest.mark.skip"), (r"@unittest\.skip", "unittest: @unittest.skip"),
    (r"pytest\.skip\s*\(", "pytest: pytest.skip("), (r"\bt\.Skip\w*\s*\(", "go: t.Skip"),
    (r"#\[ignore\]", "rust: #[ignore]"), (r"\[Ignore\b", "nunit/mstest: [Ignore"),
    (r"\bmarkTestSkipped\s*\(", "phpunit: markTestSkipped"), (r"\bxcontext\b|\bxit\b|\bpending\b", "rspec: xit/pending"),
    (r"\bskip\s*[=:]\s*true\b", "skip = true"), (r"^\s*@?skip\b", "generic: skip"),
    (r"--skip\b", "cli: --skip"), (r"\bexclude(?:Tags|Groups|TestsMatching)?\s*(?:[\(=]|['\"])", "gradle/junit: exclude"),
]
DECLARATIONS = [
    r"^\s*def test_\w+", r"^\s*async def test_\w+", r"@Test\b", r"\b(?:it|test)\s*\(\s*['\"`]",
    r"^\s*func Test\w+", r"#\[test\]", r"\[Fact\]|\[Theory\]|\[Test\]|\[TestMethod\]",
    r"^\s*(?:public\s+)?function test\w+", r"^\s*it\s+['\"]",
]
ASSERT_RE = re.compile(r"\b(assert\w*|expect|should|verify)\b")
COMMENT_RE = re.compile(r"^\s*(#|//|/\*|\*|--|;)")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_loss = "--allow-net-loss" in sys.argv
    if len(args) != 1:
        print(__doc__)
        return 2
    patch = read(Path(args[0]), "")
    evidence = []
    hunks = patch_hunks(patch)
    for path, ln, sign, text in hunks:
        if sign != "+" or not is_test_or_config_path(path):
            continue
        for rx, label in MARKERS:
            if re.search(rx, text):
                evidence.append(f"{path}:{ln} — маркер отключения/фокуса ({label}): {text.strip()[:80]}")
                break
    removed = [(p, t.strip()) for p, _, s, t in hunks
               if s == "-" and ASSERT_RE.search(t) and not COMMENT_RE.match(t)]
    for path, ln, sign, text in hunks:
        if sign == "+" and COMMENT_RE.match(text) and ASSERT_RE.search(text):
            body = re.sub(r"^\s*(#|//|/\*|\*|--|;)\s*", "", text).strip()
            if any(p == path and body and body in r for p, r in removed):
                evidence.append(f"{path}:{ln} — ассерт превращён в комментарий: {text.strip()[:80]}")
    for f in patch_files(patch):
        if f["deleted"] and is_test_path(f["path"]):
            evidence.append(f"{f['path']} — тестовый файл удалён целиком")
    plus = sum(1 for _, _, s, t in hunks if s == "+" and any(re.search(rx, t) for rx in DECLARATIONS))
    minus = sum(1 for _, _, s, t in hunks if s == "-" and any(re.search(rx, t) for rx in DECLARATIONS))
    net = plus - minus
    if net < 0 and not allow_loss:
        evidence.append(f"нетто-убыль тестовых деклараций: +{plus} / −{minus} = {net} — без объяснения в плане")
    if evidence:
        return emit(GATE, "fail", evidence, f"{len(evidence)} признак(ов) обхода тест-гейта")
    return emit(GATE, "pass", [], f"маркеров нет, тестовых файлов не удалено, нетто деклараций {net:+d}")


if __name__ == "__main__":
    sys.exit(main())
