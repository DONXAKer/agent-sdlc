#!/usr/bin/env python3
"""run-corpus: регрессионный корпус flow-verdict.py — секунды, без сети и без токенов.

Между дешёвой фикстурой (test/fixture/, код под задачу планировщика) и дорогим
test/run-e2e.sh (реальные сессии claude) не было ничего, что проверяло бы САМ парсер
артефактов flow-verdict.py. Он написан по живой markdown-разметке: докстринги
row_glyphs()/plan_approved() ссылаются на конкретные строки реальных отчётов CV, а не
на что-то в этом репозитории. Три правки §6b подряд 2026-08-27..28 (db9210b → 95b79ab →
e6e963e) проверялись ручным прогоном по пяти виткам CV — след не сохранён, поэтому
следующая правка начинала бы с нуля (P7, retros/2026-08-28-CV.md).

Состав: test/flow-verdict-corpus/<case>/ — самодостаточный корень проекта (свой
.sdlc/gates.md + .sdlc/<case>/*), plus expected.txt — дословный вывод
`flow-verdict.py --at verify <case> <case>` на момент фиксации кейса.

Правило: правка flow-verdict.py без прогона этого корпуса не коммитится; правка,
меняющая ожидаемый вывод, обновляет expected.txt ТЕМ ЖЕ коммитом и объясняет
изменение в теле коммита. Новый кейс заводится не «когда захотелось», а по
конкретному поводу — см. правило регрессионного кейса в SDLC.md → «Когда дефект
проскочил» («„Проверка" без кейса — это „принятый риск", записанный чужим словом»).

Запуск:
  python3 test/run-corpus.py           # прогнать все кейсы, показать diff несовпавших
  python3 test/run-corpus.py --update  # переписать expected.txt под текущий вывод
                                        # (только после того, как расхождение объяснено
                                        # в теле коммита — не способ молча погасить diff)
"""

import difflib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# diff несёт глифы ✅/❌ — на Windows-консоли (cp1251) writelines падал до сравнения кейсов
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "flow-verdict-corpus"
FLOW_VERDICT = (HERE.parent / "implementations" / "claude-code" / "skills"
                / "sdlc-verify" / "tools" / "flow-verdict.py")


def run_case(case_dir: Path) -> str:
    """Кейс — самодостаточный корень `.sdlc/…`. Необязательный `case.json` рядом:
    {"mode": "verify"|"handoff", "source": "<каталог с плоскими артефактами>", "slug": "<slug>"}.
    `source` — для канонических артефактов, которые лежат не в раскладке `.sdlc/` (example/):
    они материализуются во временный корень, копия в корпусе не заводится — иначе пример и
    его копия разъезжались бы молча."""
    meta = {}
    if (case_dir / "case.json").exists():
        meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    mode = meta.get("mode", "verify")
    slug = meta.get("slug", case_dir.name)
    root, tmp = case_dir, None
    if "source" in meta:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        src = (case_dir / meta["source"]).resolve()
        (root / ".sdlc" / slug).mkdir(parents=True)
        for f in src.iterdir():
            if f.is_file():
                dst = root / ".sdlc" / ("gates.md" if f.name == "gates.md" else f"{slug}/{f.name}")
                shutil.copy(f, dst)
    try:
        proc = subprocess.run(
            [sys.executable, str(FLOW_VERDICT), "--at", mode, str(root), slug],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = proc.stdout + proc.stderr
    finally:
        if tmp:
            tmp.cleanup()
    return out


def main() -> int:
    update = "--update" in sys.argv[1:]
    if not FLOW_VERDICT.exists():
        print(f"нет flow-verdict.py по пути {FLOW_VERDICT}")
        return 2
    cases = sorted(p for p in CORPUS.iterdir() if p.is_dir()) if CORPUS.is_dir() else []
    if not cases:
        print(f"кейсов в {CORPUS} не найдено")
        return 2
    failed = []
    for case_dir in cases:
        expected_path = case_dir / "expected.txt"
        actual = run_case(case_dir)
        if update:
            expected_path.write_text(actual, encoding="utf-8")
            print(f"обновлено: {case_dir.name}")
            continue
        expected = expected_path.read_text(encoding="utf-8") if expected_path.exists() else None
        if expected is None:
            print(f"нет expected.txt: {case_dir.name}")
            failed.append(case_dir.name)
            continue
        if actual != expected:
            print(f"РАСХОЖДЕНИЕ: {case_dir.name}")
            diff = difflib.unified_diff(
                expected.splitlines(keepends=True), actual.splitlines(keepends=True),
                fromfile="expected.txt", tofile="фактический вывод",
            )
            sys.stdout.writelines(diff)
            failed.append(case_dir.name)
    if update:
        return 0
    if failed:
        print(f"\n{len(failed)} из {len(cases)} кейсов разошлись: {', '.join(failed)}")
        return 1
    print(f"все {len(cases)} кейсов совпали с expected.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
