#!/usr/bin/env python3
"""seed-flow-verdict: самопосев в инструмент — какие проверки flow-verdict.py корпус не ловит.

Методология требует от каждого гейта проекта доказательства чувствительности посевом
(«не бывает так, что гейт есть, но чувствительность его никто не проверял»). Это то же
требование к её собственному инструменту: каждая проверка `v.check(...)` в flow-verdict.py
обязана иметь в регрессионном корпусе кейс, на котором она красная. Иначе проверка —
утверждение о самой себе.

Механика (мутационное тестирование одной операцией): для каждого вызова `v.check(cond, …)`
собирается мутант, где `cond` заменён на `not (cond)`; корпус прогоняется на мутанте;
если ни один кейс не изменил вывод — мутант выжил, проверка корпусом не покрыта.

Выжившие сравниваются со списком `test/seed-flow-verdict.known.txt` — это «долг» инструмента:
проверки, для которых кейса пока нет, записанные явно, по одной на строку. Новый выживший,
которого нет в списке, — красный итог: либо заведи кейс, либо впиши долг. Убитая проверка
из списка — тоже красный: долг закрыт, строку надо удалить (иначе список врёт).

  python test/seed-flow-verdict.py            # сверка с known.txt, код возврата 1 при расхождении
  python test/seed-flow-verdict.py --report   # только отчёт, код возврата 0
"""

import ast
import shutil
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "flow-verdict-corpus"
TOOL_DIR = HERE.parent / "implementations" / "claude-code" / "skills" / "sdlc-verify" / "tools"
FLOW_VERDICT = TOOL_DIR / "flow-verdict.py"
KNOWN = HERE / "seed-flow-verdict.known.txt"


class Negate(ast.NodeTransformer):
    """Инвертирует первый аргумент ровно одного вызова v.check по порядковому номеру."""

    def __init__(self, target_index):
        self.target_index = target_index
        self.index = -1
        self.label = None

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "check"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "v"):
            self.index += 1
            if self.index == self.target_index and node.args:
                self.label = ast.unparse(node.args[1]) if len(node.args) > 1 else "?"
                node.args[0] = ast.UnaryOp(op=ast.Not(), operand=node.args[0])
        return node


def count_checks(tree):
    n = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "check" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "v"):
            n += 1
    return n


def load_run_corpus():
    """Тот же прогон кейса, что у run-corpus.py (режим и источник из case.json)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_corpus", HERE / "run-corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RUN_CORPUS = load_run_corpus()


def run(tool: Path, case_dir: Path) -> str:
    RUN_CORPUS.FLOW_VERDICT = tool
    return RUN_CORPUS.run_case(case_dir)


def main() -> int:
    report_only = "--report" in sys.argv[1:]
    source = FLOW_VERDICT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    total = count_checks(tree)
    cases = sorted(p for p in CORPUS.iterdir() if p.is_dir())
    expected = {c.name: (c / "expected.txt").read_text(encoding="utf-8") for c in cases}
    survivors, killed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(TOOL_DIR / "sdlc-constants.json", tmp / "sdlc-constants.json")
        for i in range(total):
            mut = Negate(i)
            mtree = mut.visit(ast.parse(source))
            ast.fix_missing_locations(mtree)
            mfile = tmp / "flow-verdict.py"
            mfile.write_text(ast.unparse(mtree), encoding="utf-8")
            changed = any(run(mfile, c) != expected[c.name] for c in cases)
            (killed if changed else survivors).append(mut.label)
    print(f"Проверок v.check: {total} · убито корпусом: {len(killed)} · выжило: {len(survivors)}")
    for label in survivors:
        print(f"- выжил: {label}")
    if report_only:
        return 0
    known = set()
    if KNOWN.exists():
        known = {ln.strip() for ln in KNOWN.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")}
    new = [s for s in survivors if s not in known]
    closed = [k for k in known if k not in survivors]
    rc = 0
    if new:
        rc = 1
        print(f"\nНОВЫЕ выжившие (нет кейса и нет строки долга в {KNOWN.name}):")
        for s in new:
            print(f"- {s}")
    if closed:
        rc = 1
        print(f"\nДолг закрыт, но строка осталась в {KNOWN.name} — удали:")
        for s in closed:
            print(f"- {s}")
    if rc == 0:
        print("Самопосев: каждая проверка либо убита корпусом, либо записана долгом.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
