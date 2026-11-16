#!/usr/bin/env python3
"""Гейт «Анти-обход тест-гейта» — эталонная реализация.

  python anti_bypass.py <патч попытки> [--allow-net-loss] [--test-path "tools/studio/*.luau"]

Объяви проектные QA-пути через повторяемый --test-path. Шаблоны сопоставляются с путями от корня проекта.
Для Lua/Luau в объявленных тестовых путях учитываются вызовы assert(...).
Если проверки в дополнительном QA-пути не распознаны, результат — skip, а не доказательство защиты.
Разбор Lua учитывает обе стороны hunk и контекст. Если hunk начинается после первой строки,
начальное лексическое состояние неизвестно: результат — skip, если другие проверки не дали fail.

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
import argparse
import fnmatch
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


def lua_code(text, state=None):
    """Скрой литералы и комментарии Lua; перенеси состояние между соседними строками diff."""
    output, i = [], 0
    while i < len(text):
        if state and state.startswith(']'):
            end = text.find(state, i)
            if end < 0:
                return ''.join(output), state
            i, state = end + len(state), None
            continue
        if state:
            if text[i] == '\\':
                i += 2
                continue
            if text[i] == state:
                state = None
            i += 1
            continue
        comment = text.startswith('--', i)
        start = i + 2 if comment else i
        bracket = re.match(r'\[(=*)\[', text[start:])
        if bracket:
            state = ']' + bracket.group(1) + ']'
            output.append(' ')
            i = start + len(bracket.group(0))
        elif comment:
            break
        elif text[i] in ('"', "'", '`'):
            state = text[i]
            output.append(' ')
            i += 1
        else:
            output.append(text[i])
            i += 1
    return ''.join(output), state


def lua_patch_declarations(patch):
    """Разбери старую и новую стороны Lua-hunk вместе с неизменённым контекстом."""
    declarations, uncertain = {}, set()
    for section in re.split(r'(?=^diff --git )', patch, flags=re.M):
        files = patch_files(section)
        if not files or Path(files[0]['path']).suffix not in ('.lua', '.luau'):
            continue
        path = files[0]['path']
        old_line = new_line = None
        old_state = new_state = None
        for line in section.splitlines():
            header = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if header:
                old_line, new_line = map(int, header.groups())
                old_state = new_state = None
                if old_line > 1 or new_line > 1:
                    uncertain.add(path)
                continue
            if old_line is None or not line or line.startswith('\\'):
                continue
            sign, text = line[0], line[1:]
            if sign in (' ', '-'):
                code, old_state = lua_code(text, old_state)
                if sign == '-':
                    declarations[path, old_line, sign] = len(re.findall(r'(?<![\w.:])assert\s*\(', code))
                old_line += 1
            if sign in (' ', '+'):
                code, new_state = lua_code(text, new_state)
                if sign == '+':
                    declarations[path, new_line, sign] = len(re.findall(r'(?<![\w.:])assert\s*\(', code))
                new_line += 1
    return declarations, uncertain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('patch')
    parser.add_argument('--allow-net-loss', action='store_true')
    parser.add_argument('--test-path', action='append', default=[],
                        help='Дополнительный glob тестовых путей проекта; можно повторять')
    args = parser.parse_args()
    allow_loss = args.allow_net_loss
    extra_test = lambda path: any(fnmatch.fnmatchcase(path.replace('\\', '/'), pattern) for pattern in args.test_path)
    test_path = lambda path: is_test_path(path) or extra_test(path)
    patch = read(Path(args.patch), "")
    evidence = []
    hunks = patch_hunks(patch)
    lua_declarations, uncertain = lua_patch_declarations(patch)
    for path, ln, sign, text in hunks:
        if path in uncertain or sign != "+" or not (is_test_or_config_path(path) or extra_test(path)):
            continue
        for rx, label in MARKERS:
            # RSpec-маркеры относятся к Ruby. Литералы других языков не отключают RSpec.
            if label.startswith('rspec:') and Path(path).suffix != '.rb':
                continue
            if re.search(rx, text):
                evidence.append(f"{path}:{ln} — маркер отключения/фокуса ({label}): {text.strip()[:80]}")
                break
    removed = [(p, t.strip()) for p, _, s, t in hunks
               if p not in uncertain and s == "-" and ASSERT_RE.search(t) and not COMMENT_RE.match(t)]
    for path, ln, sign, text in hunks:
        if path not in uncertain and sign == "+" and COMMENT_RE.match(text) and ASSERT_RE.search(text):
            body = re.sub(r"^\s*(#|//|/\*|\*|--|;)\s*", "", text).strip()
            if any(p == path and body and body in r for p, r in removed):
                evidence.append(f"{path}:{ln} — ассерт превращён в комментарий: {text.strip()[:80]}")
    for f in patch_files(patch):
        if f["deleted"] and test_path(f["path"]):
            evidence.append(f"{f['path']} — тестовый файл удалён целиком")
    declarations = {}
    for path, line, sign, text in hunks:
        if Path(path).suffix in ('.lua', '.luau'):
            recognized = lua_declarations.get((path, line, sign), 0) if path not in uncertain and test_path(path) else 0
        else:
            recognized = any(re.search(rx, text) for rx in DECLARATIONS)
        declarations[path, line, sign] = recognized
    plus = sum(declarations[p, ln, s] for p, ln, s, _ in hunks if s == "+")
    minus = sum(declarations[p, ln, s] for p, ln, s, _ in hunks if s == "-")
    net = plus - minus
    if net < 0 and not allow_loss:
        evidence.append(f"нетто-убыль тестовых деклараций: +{plus} / −{minus} = {net} — без объяснения в плане")
    if evidence:
        return emit(GATE, "fail", evidence, f"{len(evidence)} признак(ов) обхода тест-гейта")
    unknown_context = sorted(path for path in uncertain if test_path(path))
    if unknown_context:
        return emit(GATE, "skip", unknown_context, "Начальное состояние Lua-hunk неизвестно; требуется проверка полного файла")
    unsupported = sorted({p for p, _, _, _ in hunks if extra_test(p)
                          and not any(path == p and declarations[path, line, sign] for path, line, sign, _ in hunks)})
    if unsupported:
        return emit(GATE, "skip", unsupported, "В объявленных QA-путях не распознаны проверки; требуется независимая проверка")
    return emit(GATE, "pass", [], f"маркеров нет, тестовых файлов не удалено, нетто деклараций {net:+d}")


if __name__ == "__main__":
    sys.exit(main())
