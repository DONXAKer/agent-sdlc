#!/usr/bin/env python3
"""Гейты «Scope: файлы вне плана» и «Scope: пути плана без правок» — эталонная реализация.

  python scope.py outside   <корень> <slug> <патч попытки>   # файлы патча, которых нет в files_to_touch
  python scope.py unchanged <корень> <slug> <патч попытки>   # пути плана, которых нет в патче

«outside»: пути `.sdlc/**` из сверки исключены (SDLC.md → «Раскладка артефактов»). Файл, чьё
изменение зафиксировано в базе chunk'а (`.chunk-<n>-baseline.json`: путь → sha256 содержимого)
и чей хэш с тех пор не менялся, — чужая незакоммиченная правка, не работа chunk'а: он
отчитывается отдельной строкой «вне плана, из базы chunk'а», гейт не роняет.
«unchanged»: каждый непокрытый путь плана обязан быть разобран в отчёте одной из двух
формулировок («не потребовалось, потому что …» / «не сделано») — скрипт лишь называет список,
разбор — за исполнителем и рецензентом. Пустой список — pass.
Контракт вывода — см. _common.py.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ARTIFACTS_DIR, emit, files_to_touch, patch_files, read  # noqa: E402


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[1] not in ("outside", "unchanged"):
        print(__doc__)
        return 2
    mode, root, slug, patch_path = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3], Path(sys.argv[4])
    plan = read(root / ARTIFACTS_DIR / slug / "plan.md", "")
    planned = files_to_touch(plan)
    touched = [f["path"] for f in patch_files(read(patch_path, "")) if not f["path"].startswith(ARTIFACTS_DIR + "/")]
    if mode == "outside":
        gate = "Scope: файлы вне плана"
        baselines = sorted((root / ARTIFACTS_DIR / slug).glob(".chunk-*-baseline.json"))
        base = {}
        for b in baselines:
            try:
                base.update(json.loads(read(b, "{}")))
            except json.JSONDecodeError:
                pass
        outside, foreign = [], []
        for p in touched:
            if p in planned:
                continue
            cur = root / p
            if p in base and cur.exists() and hashlib.sha256(cur.read_bytes()).hexdigest() == base[p]:
                foreign.append(f"{p} — вне плана, из базы chunk'а (чужая правка, не работа chunk'а)")
            else:
                outside.append(p)
        if outside:
            return emit(gate, "fail", outside + foreign,
                        f"{len(outside)} файл(ов) в патче вне files_to_touch")
        return emit(gate, "pass", foreign, "все файлы патча названы в files_to_touch"
                    + (f"; {len(foreign)} чужих из базы chunk'а" if foreign else ""))
    gate = "Scope: пути плана без правок"
    missing = [p for p in planned if p not in touched]
    if missing:
        return emit(gate, "fail", missing,
                    f"{len(missing)} путь(ей) плана без правок — каждому нужна строка «не потребовалось, потому что …» или «не сделано»")
    return emit(gate, "pass", [], "каждый путь files_to_touch есть в патче")


if __name__ == "__main__":
    sys.exit(main())
