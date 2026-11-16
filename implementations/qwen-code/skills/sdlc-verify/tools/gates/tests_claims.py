#!/usr/bin/env python3
"""Гейт «Сверка тестов с claims» — эталонная реализация.

  python tests_claims.py <корень> <slug> <патч попытки>

Конвенция, которая делает гейт механическим: **тест несёт id пункта приёмки** — в имени или
теге: `test_claim_3_empty_input`, `@Tag("claim-3")` (до или после `@Test`), `it("claim-3: …")`,
`// claim-3` строкой над декларацией. Каждая добавленная тестовая декларация обязана нести хотя
бы один `claim-N`, и этот id обязан существовать в задаче. Побочный выход — карта
`claim → тесты`, по ней handoff считает «пункты, закрытые не тестом, а diff'ом».
Контракт вывода — см. _common.py.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ARTIFACTS_DIR, claims_in, emit, patch_hunks, read  # noqa: E402

GATE = "Сверка тестов с claims"
DECL = re.compile(r"^\s*(?:async\s+)?def (test_\w+)|@Test\b|\b(?:it|test)\s*\(\s*['\"`]([^'\"`]+)|"
                  r"^\s*func (Test\w+)|#\[test\]|\[(?:Fact|Theory|Test|TestMethod)\]|^\s*it\s+['\"]([^'\"]+)")
ANNOTATION_ONLY = re.compile(r"^\s*(@Test\b|#\[test\]|\[(?:Fact|Theory|Test|TestMethod)\])")
CLAIM = re.compile(r"claim[-_](\d+)", re.I)
SIGNATURE = re.compile(r"\b(?:void|fn|def|func|public|private|static|async)\b.*?\b(\w+)\s*\(")


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    root, slug, patch_path = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3])
    intent_claims = claims_in(read(root / ARTIFACTS_DIR / slug / "intent.md", ""))
    added = [(p, ln, t) for p, ln, s, t in patch_hunks(read(patch_path, "")) if s == "+"]
    without, unknown, mapping = [], [], {}
    for i, (path, ln, text) in enumerate(added):
        m = DECL.search(text)
        if not m:
            continue
        name = next((g for g in m.groups() if g), None)
        # окно: до двух добавленных строк выше (не дальше предыдущей декларации) …
        window = [text]
        for p, _, t in reversed(added[max(0, i - 2):i]):
            if p != path or DECL.search(t):
                break
            window.append(t)
        # … и, для аннотации без имени (@Test, #[test], [Fact]), до трёх строк ниже — до сигнатуры:
        # там живут @Tag("claim-N")/@DisplayName и само имя метода
        if ANNOTATION_ONLY.match(text):
            for p, _, t in added[i + 1:i + 4]:
                if p != path or ANNOTATION_ONLY.match(t):
                    break
                window.append(t)
                sig = SIGNATURE.search(t)
                if sig:
                    name = name or sig.group(1)
                    break
        name = name or text.strip()[:60]
        ids = {f"claim-{n}" for n in CLAIM.findall(" ".join(window))}
        if not ids:
            without.append(f"{path}:{ln} — {name}")
            continue
        for cid in sorted(ids):
            if cid not in intent_claims:
                unknown.append(f"{path}:{ln} — {name} → {cid} нет в задаче")
            mapping.setdefault(cid, []).append(name)
    if mapping:
        print("карта claim → тесты: " + json.dumps(mapping, ensure_ascii=False))
    evidence = [f"без claim-id: {w}" for w in without] + [f"несуществующий id: {u}" for u in unknown]
    if evidence:
        return emit(GATE, "fail", evidence,
                    f"{len(without)} тест(ов) без claim-id, {len(unknown)} с несуществующим — лист дополняет человек, id ставит исполнитель")
    return emit(GATE, "pass", [f"{c}: {', '.join(t)}" for c, t in sorted(mapping.items())],
                f"каждый новый тест несёт существующий claim-id ({len(mapping)} пунктов покрыто)")


if __name__ == "__main__":
    sys.exit(main())
