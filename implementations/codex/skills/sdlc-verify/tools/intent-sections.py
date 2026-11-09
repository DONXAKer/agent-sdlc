#!/usr/bin/env python3
"""intent-sections: три законные правки задачи внутри витка — проверяемо, а не по памяти.

SDLC.md → этап 1: внутри витка intent.md не переписывается; законных правок ровно три —
  1. секция «Что придётся тронуть» (разведка);
  2. закрытие вопросов в «Открытых вопросах» (этап 3);
  3. уточнение формулировки с одобрения человека — включая **дополнение приёмочного листа**
     по находке гейта этапа 4 (новый пункт дописывает человек).

  python intent-sections.py --snapshot <корень> <slug>   # этап 1 (прогон 1) и этап 4 после одобрения плана
  python intent-sections.py --check    <корень> <slug>   # этап 4 (прогон 2) и этап 6

Снимок — `.sdlc/<slug>/.intent-sections.json`: по секциям хэш, множество claim-id листа и число
записей об одобрении. Проверка печатает JSON: changed, allowed (правки 1–2), additive_claims
(лист только дополнен новыми claim-N, старые строки не тронуты — правка 3 без записи), approved
(секции с НОВОЙ записью «уточнено с одобрения ‹имя›» относительно снимка — одна старая запись не
легализует последующие переписывания), illegal, legal. Код возврата 1, если illegal не пуст.
Этап 4 после одобрения плана снимает снимок заново: с этого момента лист снова неизменяем.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import ARTIFACTS_DIR, CLAIM_RE  # noqa: E402

ALWAYS_ALLOWED = {"Что придётся тронуть", "Открытые вопросы"}
CLAIMS_SECTION = "Приёмочный лист"
APPROVAL_RE = re.compile(r"уточнен[оа]\s+с\s+одобрения\s+\S+", re.I)
# строка листа и в обратных кавычках (`claim-3 [edge]`) — так её пишут шаблон и раннер
CLAIM_ROW = re.compile(r"^\s*\|\s*`?(claim-\d+)\b[^|]*\|(.*)$", re.M)


def sections(text: str):
    out, name, buf = {}, "(шапка)", []
    for ln in text.splitlines():
        if ln.startswith("## "):
            out[name] = "\n".join(buf)
            name, buf = ln[3:].strip(), []
        else:
            buf.append(ln)
    out[name] = "\n".join(buf)
    return out


def snapshot_of(bodies: dict):
    snap = {}
    for k, v in bodies.items():
        entry = {"hash": hashlib.sha256(v.encode("utf-8")).hexdigest(),
                 "approvals": len(APPROVAL_RE.findall(v))}
        if k == CLAIMS_SECTION:
            entry["claim_rows"] = {m.group(1): hashlib.sha256(m.group(0).encode("utf-8")).hexdigest()
                                   for m in CLAIM_ROW.finditer(v)}
            rest = "\n".join(ln for ln in v.splitlines() if not CLAIM_ROW.match(ln))
            entry["other"] = hashlib.sha256(rest.encode("utf-8")).hexdigest()
        snap[k] = entry
    return snap


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 3 or args[0] not in ("--snapshot", "--check"):
        print(__doc__)
        return 2
    mode, root, slug = args[0], Path(args[1]).resolve(), args[2]
    d = root / ARTIFACTS_DIR / slug
    intent = (d / "intent.md").read_text(encoding="utf-8")
    snap_path = d / ".intent-sections.json"
    now = snapshot_of(sections(intent))
    if mode == "--snapshot":
        snap_path.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"снимок {len(now)} секций: {snap_path}")
        return 0
    if not snap_path.exists():
        print(json.dumps({"legal": None, "error": "снимка нет — прогон 1 этапа 1 его не снял"}, ensure_ascii=False))
        return 1
    old = json.loads(snap_path.read_text(encoding="utf-8"))
    changed = sorted(k for k in set(old) | set(now)
                     if (old.get(k) or {}).get("hash") != (now.get(k) or {}).get("hash"))
    allowed, additive, approved, illegal = [], [], [], []
    for k in changed:
        if k in ALWAYS_ALLOWED:
            allowed.append(k)
            continue
        o, n = old.get(k) or {}, now.get(k) or {}
        if k == CLAIMS_SECTION and o.get("claim_rows") is not None:
            old_rows, new_rows = o["claim_rows"], n.get("claim_rows", {})
            only_added = (all(new_rows.get(cid) == h for cid, h in old_rows.items())
                          and set(new_rows) >= set(old_rows) and n.get("other") == o.get("other"))
            if only_added:
                additive.append(k)
                continue
        if n.get("approvals", 0) > o.get("approvals", 0):
            approved.append(k)
            continue
        illegal.append(k)
    result = {"legal": not illegal, "changed": changed, "allowed": allowed, "additive_claims": additive,
              "approved_rewording": approved, "illegal": illegal}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not illegal else 1


if __name__ == "__main__":
    sys.exit(main())
