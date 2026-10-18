#!/usr/bin/env python3
"""review-validate: ответ рецензента принимается только структурированным и адресным.

Один контракт на сессию и раннер — `verify-review-v1` (runner-contract/schemas/
verify-review-v1.schema.json): статусы `passed|failed|uncertain|manual`; kind находок —
`mismatch|uncovered_behavior` в `findings`, `scope`/`invariant`/`regression` — в одноимённых
массивах; `schema_version` = `agent-sdlc/verify-review/v1` (нет — подставляется, это
единственное послабление). Рецензент возвращает рядом с markdown fenced-блок ```json.

  python review-validate.py <корень> <slug> <n> <k> <файл с ответом рецензента>
  (путь ответа — от корня проекта или абсолютный; относительный ищется и от cwd)

Проверки: JSON-блок разбирается; `claims` — ровно те id, что в задаче; статус из словаря; у
passed/failed — хотя бы одна ссылка evidence (path + anchor); у каждой находки во всех четырёх
массивах — kind своего массива и evidence с path из патча попытки (или `intent.md`/`plan.md`/
`gates.md` для находок про артефакты); хотя бы одна ссылка ответа называет путь из патча;
`retry_instruction` — строка. Любой мусор в структуре — ошибка в списке, не traceback.
Итог — JSON последней строкой: {"valid", "errors", "claims", "review"}.
Код возврата 0 — валиден, 1 — нет, 2 — неверный вызов.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import ARTIFACTS_DIR, claims_in, diffstat, read  # noqa: E402

SCHEMA_VERSION = "agent-sdlc/verify-review/v1"
STATUSES = {"passed", "failed", "uncertain", "manual"}
KINDS = {"findings": {"mismatch", "uncovered_behavior"}, "scope": {"scope"},
         "invariants": {"invariant"}, "regressions": {"regression"}}
ARTIFACT_PATHS = {"intent.md", "plan.md", "gates.md"}
GLYPH_STATUS = {"✅": "passed", "❌": "failed", "⚠": "uncertain"}


def extract_json(text: str):
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.S)
    raw = m.group(1) if m else text
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, f"JSON не разбирается: {e}"


def refs_of(item, where, paths, errors):
    """Ссылки evidence элемента: список валидных (path, anchor); ошибки — в errors."""
    ok, any_patch = [], False
    ev = item.get("evidence") if isinstance(item, dict) else None
    if ev is None:
        return ok, any_patch
    if not isinstance(ev, list):
        errors.append(f"{where}: evidence не массив")
        return ok, any_patch
    for ref in ev:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref.get("path") \
                or not isinstance(ref.get("anchor"), str) or not ref.get("anchor"):
            errors.append(f"{where}: ссылка без path/anchor")
            continue
        ok.append((ref["path"], ref["anchor"]))
        if ref["path"] in paths:
            any_patch = True
    return ok, any_patch


def validate(review, intent_claims: set, paths: set):
    errors, statuses = [], {}
    if not isinstance(review, dict):
        return ["ответ не объект"], {}
    if review.get("schema_version") not in (None, SCHEMA_VERSION):
        errors.append(f"schema_version «{review.get('schema_version')}» ≠ {SCHEMA_VERSION}")
    review.setdefault("schema_version", SCHEMA_VERSION)
    claims = review.get("claims")
    if not isinstance(claims, list):
        errors.append("нет массива claims")
        claims = []
    ids = []
    any_patch_ref = False
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            errors.append(f"claims[{i}]: не объект")
            continue
        cid, st = c.get("id"), c.get("status")
        if isinstance(st, str) and st in GLYPH_STATUS:
            st = c["status"] = GLYPH_STATUS[st]
        ids.append(cid if isinstance(cid, str) else None)
        if st not in STATUSES:
            errors.append(f"{cid}: статус «{st}» вне {sorted(STATUSES)}")
        statuses[cid] = st
        refs, hit = refs_of(c, f"{cid}", paths, errors)
        any_patch_ref = any_patch_ref or hit
        if st in ("passed", "failed") and not refs:
            errors.append(f"{cid}: статус {st} без единой ссылки evidence")
        if "remediation" in c and not isinstance(c["remediation"], str):
            errors.append(f"{cid}: remediation не строка")
        c.setdefault("remediation", "")
    valid_ids = [i for i in ids if i]
    if set(valid_ids) != intent_claims or len(valid_ids) != len(set(valid_ids)) or len(valid_ids) != len(ids):
        errors.append(f"набор id не совпадает с задачей: ответ {sorted(valid_ids)} ↔ задача {sorted(intent_claims)}")
    for field, kinds in KINDS.items():
        items = review.get(field)
        if items is None:
            review[field] = []
            continue
        if not isinstance(items, list):
            errors.append(f"{field}: не массив")
            continue
        for i, f in enumerate(items):
            where = f"{field}[{i}]"
            if not isinstance(f, dict):
                errors.append(f"{where}: находка не объект")
                continue
            summary = f.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                errors.append(f"{where}: нет summary")
            kind = f.get("kind")
            if kind is None and len(kinds) == 1:
                f["kind"] = kind = next(iter(kinds))
            if kind not in kinds:
                errors.append(f"{where}: kind «{kind}» вне {sorted(kinds)}")
            refs, hit = refs_of(f, where, paths, errors)
            if not refs:
                errors.append(f"{where} «{(summary or '')[:40]}»: без evidence")
            for p, _ in refs:
                if p not in paths and Path(p).name not in ARTIFACT_PATHS:
                    errors.append(f"{where}: ссылается на «{p}», которого нет ни в патче, ни среди артефактов")
            any_patch_ref = any_patch_ref or hit
    if not any_patch_ref and paths:
        errors.append("ни одна ссылка ответа не называет путь из патча — разбор неотличим от пересказа")
    if not isinstance(review.get("retry_instruction", ""), str):
        errors.append("retry_instruction не строка")
    review.setdefault("retry_instruction", "")
    return errors, statuses


def main() -> int:
    if len(sys.argv) != 6:
        print(__doc__)
        return 2
    root, slug, n, k = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3], sys.argv[4]
    answer = Path(sys.argv[5])
    if not answer.is_absolute() and not answer.exists() and (root / answer).exists():
        answer = root / answer
    d = root / ARTIFACTS_DIR / slug
    if not answer.exists():
        print(json.dumps({"valid": False, "errors": [f"файл ответа не найден: {answer}"], "claims": {}}, ensure_ascii=False))
        return 1
    intent_claims = claims_in(read(d / "intent.md", ""))
    patch = d / f"chunk-{n}-attempt-{k}-diff.patch"
    paths = set(diffstat(patch.read_bytes().decode("utf-8", errors="surrogateescape"))["paths"]) if patch.exists() else set()
    review, err = extract_json(answer.read_text(encoding="utf-8", errors="replace"))
    if err:
        print(json.dumps({"valid": False, "errors": [err], "claims": {}}, ensure_ascii=False))
        return 1
    try:
        errors, statuses = validate(review, intent_claims, paths)
    except Exception as e:  # структура вне ожиданий — ошибка в списке, не traceback
        errors, statuses = [f"структура ответа не разбирается: {type(e).__name__}: {e}"], {}
    print(json.dumps({"valid": not errors, "errors": errors, "claims": statuses,
                      "review": review if not errors else None}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
