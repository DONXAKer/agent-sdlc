#!/usr/bin/env python3
"""attempt-evidence: свидетельства попытки chunk'а производит программа, а не агент.

SDLC.md → «Свидетельства производит программа, а не агент»: патч попытки и запись о прогоне
тестов — улики, по которым этап 6 судит этап 5; пока их пишет тот, чью работу они удостоверяют,
вопрос лишь в том, когда их подделают (измерено: «PASS ✓» без единого запуска тестов).

Проверка совместимости (до Chunk):
  python attempt-evidence.py --version
    JSON: tool, version и capabilities. Для snapshot-плана требуются возможности snapshot и prepare.
    Старая копия без этого вызова либо без нужных возможностей несовместима; Git-режим не является fallback.

Режим записи (этап 5, Phase 5 /sdlc-chunk):
  python attempt-evidence.py write <корень> <slug> <n> <k> [--tests-cmd "<команда>"]
                                   [--executor-model <имя>] [--force]
    - `chunk-<n>-attempt-<k>-diff.patch` — `git diff <base_sha>` от базы из плана, без `.sdlc/**`,
      плюс новые нетракованные файлы; байты патча не трогаются (CR/LF как в дереве), пути не
      экранируются (`core.quotepath=false`) — патч применим `git apply` и на CRLF-репозиториях;
    - `chunk-<n>-attempt-<k>-tests.txt` — ПОЛНЫЙ вывод команды «Тесты» из набора (или --tests-cmd);
      на Windows команда идёт через Git Bash, если он есть;
    - `chunk-<n>-attempt-<k>-evidence.json` — base_sha, HEAD, sha256 патча и вывода, команда, код
      возврата, улика отсутствующего инструмента, diffstat (в т.ч. удалённые файлы и счёт по
      файлам — вход гейта «Перезапись файла»), модель исполнителя: `--executor-model` (в сессии —
      самоотчёт исполнителя, под раннером — факт рантайма; источник записывается рядом).
    Файлы попытки не перезаписываются: попытка K уже есть — отказ без --force.

Режим готовности (до реализации каждого chunk):
  python attempt-evidence.py prepare <корень> <slug> <n> --locator <отчёт внутри витка>
    До prepare создай журнал chunk. Создаёт .chunk-N-preconditions.json и архив начальной редакции журнала.
    Хэши связывают план, базу, locator, baseline и начальную редакцию журнала.
    Новые scoped-правки до prepare отклоняются; для следующего chunk требуется прежний evidence.
    write сохраняет также отсутствие квитанции, чтобы честное отклонение не потерялось.
    flow-verdict требует квитанцию и её закреплённый хэш; текстовая подпись не доказывает порядок.

Режим именованного гейта (Verify):
  python attempt-evidence.py run-gate <корень> <slug> <n> <k> --gate <имя из набора> [--layer <слой>]
    Запускает указанную команду этапа 6, сохраняет вывод и квитанцию, связанную с патчем и evidence попытки.
    `preflight ... --gate <имя>` проверяет конфигурацию без запуска и записи квитанции.

Режим базы (этап 5, Phase 2 /sdlc-chunk — перед первой правкой):
  python attempt-evidence.py baseline <корень> <slug> <n>
    Пишет `.chunk-<n>-baseline.json`: {путь → sha256 байтов} для всех изменённых и нетракованных
    файлов дерева (`git status --porcelain`) — единственный формат, который читает gates/scope.py.
    Это хэши, а не снимок исходных байтов. Прежние и текущие правки одного файла не разделяются.
    Для такого файла до правок согласуй отдельную Git-базу либо изолированное рабочее дерево.

Режим снимка рабочего дерева (явный **Вид базы:** snapshot в плане):
  python attempt-evidence.py snapshot <корень> <slug>
    Сохраняет исходные байты до первой правки в base-snapshot, закрепляет SHA-256 manifest в плане.
    Все chunk и попытки используют один снимок. Существующий снимок не заменяется.
    write/verify, scope и base-check проверяют его целость и связь с evidence.
    В snapshot-режиме патч содержит только изменения относительно снимка, включая untracked и binary.
    Игнорируемые untracked, .sdlc и cache/junk исключаются. Symlink и submodule отклоняются.

Режим сравнения попыток (этап 5, Phase 0 — детект отсутствия прогресса):
  python attempt-evidence.py compare <корень> <slug> <n> <k1> <k2>
    JSON: identical, similarity 0..1, changed_files.

Режим сверки (этап 6, Phase 0 /sdlc-verify):
  python attempt-evidence.py verify <корень> <slug> <n> <k>
    Перегенерирует diff тем же способом и сравнивает с патчем попытки и evidence.json;
    JSON: diff_matches_tree, patch_matches_evidence, tests_matches_evidence, missing_tool, diffstat.
    Код возврата 1, если перегенерированный diff не совпал с патчем (условие `passed=false`).

Старые формы вызова (`<корень> <slug> <n> <k>`, `--verify`, `--compare`) принимаются.
"""

import argparse
import difflib
import json
import os
import re
import sys
from contextlib import contextmanager
from tempfile import NamedTemporaryFile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdlc_common import (  # noqa: E402
    ARTIFACTS_DIR, diffstat, git_out, git_run, is_junk_path, missing_tool, read, require_base_sha,
    require_tests_cmd, run_shell, sha256, artifact_targets, artifact_observed, gate_context,
)
import snapshot_base
import chunk_preconditions
import gate_evidence
import profile_guard

TOOL = "attempt-evidence.py"
VERSION = 2
CAPABILITIES = ("baseline", "compare", "git", "preflight", "prepare", "run-gate", "snapshot", "verify", "write")


def write_bytes_text(path: Path, text: str):
    """Записать текст как есть: без перевода \\n → \\r\\n и без потери \\r."""
    with NamedTemporaryFile(dir=path.parent, prefix='.write-', delete=False) as tmp:
        staging = Path(tmp.name)
        tmp.write(text.encode("utf-8", errors="surrogateescape"))
    try:
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


@contextmanager
def attempt_lock(directory, n, k):
    """Блокировка ядра освобождается также при аварийном завершении процесса."""
    if not all(re.fullmatch(r'[1-9][0-9]*', str(value)) for value in (n, k)):
        raise ValueError('Недопустимый номер попытки')
    with (directory / f'.chunk-{n}-attempt-{k}.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Запись этой попытки уже выполняется') from error
        yield


def read_bytes_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def regenerate_diff(root: Path, base_sha: str, canonical=True, d=None) -> str:
    """git diff от базы без .sdlc/** плюс новые нетракованные файлы (как добавленные)."""
    formatting = (['--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--src-prefix=a/', '--dst-prefix=b/',
                   '--output-indicator-new=+', '--output-indicator-old=-', '--output-indicator-context= ']
                  if canonical else [])
    rc, diff, err = git_run(["diff", *formatting, base_sha, "--", ".", f":(exclude){ARTIFACTS_DIR}"], root)
    if rc != 0:
        raise SystemExit(f"git diff {base_sha}: код {rc}\n{err}")
    targets = artifact_targets(root, d) if d else []
    observed = set(targets)
    if targets:
        tracked = git_out(['ls-files', '--', ARTIFACTS_DIR], root) or ''
        observed.update(name for name in tracked.splitlines() if artifact_observed(name, targets, d))
        for name in targets:
            parent = (root / name).parent
            if parent.is_dir():
                observed.update(str(path.relative_to(root)).replace('\\', '/') for path in parent.iterdir() if path.is_file())
        observed.update(str(path.relative_to(root)).replace('\\', '/')
                        for path in (root / ARTIFACTS_DIR).rglob('*')
                        if path.is_file() and artifact_observed(str(path.relative_to(root)).replace('\\', '/'), targets, d))
        observed = {name for name in observed if artifact_observed(name, targets, d)}
        for name in sorted(observed):
            snapshot_base.safe_path(root, name)
            rc, extra, err = git_run(['diff', *formatting, base_sha, '--', ':(literal)' + name], root)
            if rc:
                raise ValueError(f'Не удалось построить патч цели: {err}')
            diff += extra
    untracked = git_out(["ls-files", "--others", "--exclude-standard", "--", ".",
                         f":(exclude){ARTIFACTS_DIR}"], root) or ""
    extra_untracked = {name for name in observed if (root / name).is_file()
                       and not (git_out(['ls-files', '--', ':(literal)' + name], root) or '').strip()}
    for path in sorted(p for p in set(untracked.splitlines()) | extra_untracked if p.strip() and not is_junk_path(p)):
        rc, out, err = git_run(["diff", "--no-index", *formatting, "--", "/dev/null", path], root)
        if rc not in (0, 1):
            raise SystemExit(f"git diff --no-index {path}: код {rc}\n{err}")
        diff += out
    return diff


def paths_for(d: Path, n: str, k: str):
    return (d / f"chunk-{n}-attempt-{k}-diff.patch", d / f"chunk-{n}-attempt-{k}-tests.txt",
            d / f"chunk-{n}-attempt-{k}-evidence.json")


def current_diff(root, d, base, snapshot):
    if snapshot is None:
        return regenerate_diff(root, base['id'], d=d)
    return snapshot_base.snapshot_diff(root, snapshot, d=d)


def current_revision(root, d):
    base, snapshot = snapshot_base.load(root, d)
    return base, sha256(current_diff(root, d, base, snapshot))


def write_mode(root: Path, slug: str, n: str, k: str, tests_cmd, executor_model, force: bool, layer=None, resume=False) -> int:
    d = root / ARTIFACTS_DIR / slug
    base, snapshot = snapshot_base.load(root, d)
    base_sha = base['id'] if base['kind'] == 'git' else None
    tests_cmd = tests_cmd or require_tests_cmd(read(root / ARTIFACTS_DIR / "gates.md", ""))
    context = gate_context(root, d, tests_cmd, n, k, layer, allow_pending_patch=True)
    patch, tests_out, evidence = paths_for(d, n, k)
    if not force and (evidence.exists() or (not resume and any(p.exists() for p in (patch, tests_out)))):
        print(f"попытка {k} chunk'а {n} уже имеет свидетельства — они не перезаписываются "
              f"(следующая попытка = следующий номер; --force только осознанно)")
        return 1
    tracking = None
    if (d / 'verification-policy.json').exists():
        import verification_runs
        if force:
            raise ValueError('Политика повторной верификации запрещает --force и замену истории')
        tracking = verification_runs.track_source(root, d, n, tests_cmd, layer, context)
    # История всех попыток должна ссылаться на один и тот же вид и идентификатор базы.
    for previous in d.glob('chunk-*-attempt-*-evidence.json'):
        if not re.fullmatch(r'chunk-\d+-attempt-\d+-evidence\.json', previous.name):
            continue
        record = json.loads(read(previous, '{}'))
        previous_base = record.get('base', {'kind': 'git', 'id': record.get('base_sha')})
        if previous_base != base:
            raise ValueError('База отличается от истории попыток; создай новый виток')
    diff = current_diff(root, d, base, snapshot)
    chunk_diff = None
    if chunk_preconditions.receipt_path(d, n).is_file():
        receipt = json.loads(chunk_preconditions.receipt_path(d, n).read_bytes())
        if 'tree' in receipt:
            chunk_diff = snapshot_base.snapshot_diff(root, chunk_preconditions.load_tree(root, d, n),
                                                     git_special=base['kind'] == 'git', d=d)
    chunk_patch = d / f'chunk-{n}-attempt-{k}-chunk.patch'
    if resume and any(p.exists() for p in (patch, tests_out, chunk_patch)):
        if not patch.is_file() or read_bytes_text(patch) != diff:
            raise ValueError('Незавершённая попытка относится к другой редакции патча')
        if chunk_patch.exists() and read_bytes_text(chunk_patch) != chunk_diff:
            raise ValueError('Патч chunk отличается от незавершённой попытки')
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_bytes_text(patch, diff)
    rc, output = run_shell(tests_cmd, root, env={**os.environ, **context})
    missing = missing_tool(output, tests_cmd, rc)
    write_bytes_text(patch, diff)
    write_bytes_text(tests_out, output)
    last_line = next((ln for ln in reversed(output.splitlines()) if ln.strip()), "")
    record = {
        "tool": TOOL, "version": VERSION, "generated_at": started,
        "slug": slug, "chunk": int(n), "attempt": int(k),
        "base_sha": base_sha, "head_sha": (git_out(["rev-parse", "HEAD"], root) or "").strip(),
        "base": base,
        'preconditions': {'errors': chunk_preconditions.errors(root, d, n),
                          'receipt_sha256': snapshot_base.digest(chunk_preconditions.receipt_path(d, n).read_bytes())
                          if chunk_preconditions.receipt_path(d, n).is_file() else None},
        "diff_sha256": sha256(diff), "diff_empty": not diff.strip(), "diffstat": diffstat(diff),
        "tests_cmd": tests_cmd, "tests_exit": rc, "tests_sha256": sha256(output), 'gate_context': context, 'layer': layer,
        "tests_last_line": last_line[:200], "missing_tool": missing,
        "executor_model": executor_model or "unknown",
        "executor_model_source": "самоотчёт исполнителя (--executor-model)" if executor_model else "не записана",
    }
    if tracking is not None:
        record['candidate_tracking'] = tracking
    if chunk_diff is not None:
        if chunk_patch.exists() and not (force or resume):
            raise ValueError('Патч chunk уже существует')
        write_bytes_text(chunk_patch, chunk_diff)
        record['chunk_diff'] = {'path': chunk_patch.name, 'sha256': sha256(chunk_diff),
                                'diffstat': diffstat(chunk_diff)}
    write_bytes_text(evidence, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("diff_empty", "diffstat", "tests_exit", "missing_tool")},
                     ensure_ascii=False))
    print(f"записано: {patch.name}, {tests_out.name}, {evidence.name}")
    if record["diff_empty"]:
        print("ВНИМАНИЕ: патч пуст — этап не изменил дерево; молчать об этом нельзя (SDLC.md)")
    if missing:
        print(f"ВНИМАНИЕ: оболочка не нашла инструмент команды тестов — кандидат в blocked_env: {missing}")
    return 0


def workspace_status(root):
    status = git_out(["status", "--porcelain", "--untracked-files=all", "--", ".", f":(exclude){ARTIFACTS_DIR}"], root)
    if status is None:
        raise SystemExit("git status не выполнился — база chunk'а не снята")
    return status


def baseline_mode(root: Path, slug: str, n: str, snapshot_status=None) -> int:
    import hashlib
    d = root / ARTIFACTS_DIR / slug
    # После prepare квитанция пинит sha256 baseline. Повторная съёмка (модель зовёт
    # `baseline` «на всякий случай») перезаписывает файл и ломает pin — be-foundation-r3/r4.
    receipt = d / f".chunk-{n}-preconditions.json"
    if receipt.is_file():
        raise SystemExit(
            f"квитанция {receipt.name} уже есть — baseline после prepare заморожен; "
            "пересъёмка ломает pin («Baseline изменён после подготовки»)"
        )
    status = workspace_status(root) if snapshot_status is None else snapshot_status
    base = {}
    for ln in status.splitlines():
        path = ln[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        p = root / path
        if p.is_file():
            base[path] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = d / f".chunk-{n}-baseline.json"
    target.write_text(json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": target.name, "dirty_files": sorted(base)}, ensure_ascii=False))
    if snapshot_status is None and base:
        print(f"дерево грязное: {len(base)} чужих незакоммиченных файлов — назови их человеку до правок")
    return 0


def normalize_patch(text: str) -> str:
    return "\n".join(ln.rstrip() for ln in text.splitlines()
                     if not ln.startswith(("index ", "similarity index", "@@")))


def compare_mode(root: Path, slug: str, n: str, k1: str, k2: str) -> int:
    d = root / ARTIFACTS_DIR / slug
    p1, p2 = d / f"chunk-{n}-attempt-{k1}-diff.patch", d / f"chunk-{n}-attempt-{k2}-diff.patch"
    for p in (p1, p2):
        if not p.exists():
            print(json.dumps({"error": f"нет {p.name}"}, ensure_ascii=False))
            return 2
    a, b = normalize_patch(read_bytes_text(p1)), normalize_patch(read_bytes_text(p2))
    ratio = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines()).ratio()
    fa, fb = set(diffstat(a)["paths"]), set(diffstat(b)["paths"])
    print(json.dumps({"attempts": [int(k1), int(k2)], "identical": sha256(a) == sha256(b),
                      "similarity": round(ratio, 3), "changed_files": sorted(fa ^ fb),
                      "files_k1": sorted(fa), "files_k2": sorted(fb)}, ensure_ascii=False, indent=2))
    return 0


def verify_mode(root: Path, slug: str, n: str, k: str) -> int:
    d = root / ARTIFACTS_DIR / slug
    base, snapshot = snapshot_base.load(root, d)
    patch, tests_out, evidence = paths_for(d, n, k)
    if not patch.exists():
        print(json.dumps({"error": f"нет {patch.name} — верификация не начинается"}, ensure_ascii=False))
        return 2
    current = current_diff(root, d, base, snapshot)
    patch_text = read_bytes_text(patch)
    patch_hash = sha256(patch_text)
    rec = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else None
    result = {
        "base_matches_evidence": rec is not None and rec.get('base', {'kind': 'git', 'id': rec.get('base_sha')}) == base,
        "base": base,
        "diff_matches_tree": sha256(current) == patch_hash,
        "evidence_present": rec is not None,
        "patch_matches_evidence": rec is not None and rec.get("diff_sha256") == patch_hash,
        "tests_matches_evidence": (rec is not None and tests_out.exists()
                                   and rec.get("tests_sha256") == sha256(read_bytes_text(tests_out))),
        "missing_tool": rec.get("missing_tool") if rec else None,
        "tests_exit": rec.get("tests_exit") if rec else None,
        "executor_model": rec.get("executor_model") if rec else None,
        "diffstat": diffstat(current),
        "produced_by": (rec or {}).get("tool", "не программой — файлы написал агент, свидетельством не считаются"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    intact = ('diff_matches_tree', 'base_matches_evidence', 'patch_matches_evidence', 'tests_matches_evidence')
    return 0 if all(result[key] for key in intact) else 1


def main() -> int:
    argv = sys.argv[1:]
    if argv == ['--version']:
        print(json.dumps({'tool': TOOL, 'version': VERSION, 'capabilities': list(CAPABILITIES)}, ensure_ascii=False))
        return 0
    # старые формы: `--verify …`, `--compare …`, `<root> <slug> <n> <k>` без подкоманды
    if argv and argv[0] == "--verify":
        argv = ["verify"] + argv[1:]
    elif argv and argv[0] == "--compare":
        argv = ["compare"] + argv[1:]
    elif argv and argv[0] not in ("write", "baseline", "snapshot", "prepare", "compare", "verify", "run-gate", "preflight", "-h", "--help"):
        argv = ["write"] + argv
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    w = sub.add_parser("write")
    w.add_argument("root"); w.add_argument("slug"); w.add_argument("n"); w.add_argument("k")
    w.add_argument("--tests-cmd"); w.add_argument("--executor-model"); w.add_argument("--force", action="store_true")
    w.add_argument('--layer')
    w.add_argument('--resume', action='store_true', help='Продолжить прерванную запись для того же патча')
    for mode in ('preflight', 'run-gate'):
        runner = sub.add_parser(mode)
        runner.add_argument('root'); runner.add_argument('slug'); runner.add_argument('n'); runner.add_argument('k')
        selector = runner.add_mutually_exclusive_group(required=True)
        selector.add_argument('--command')
        selector.add_argument('--gate', help='Имя включённого гейта этапа 6 из gates.md')
        runner.add_argument('--layer')
        runner.add_argument('--phase', choices=('chunk', 'verify'), default='verify')
    b = sub.add_parser("baseline")
    b.add_argument("root"); b.add_argument("slug"); b.add_argument("n")
    s = sub.add_parser('snapshot')
    s.add_argument('root'); s.add_argument('slug')
    p = sub.add_parser('prepare')
    p.add_argument('root'); p.add_argument('slug'); p.add_argument('n'); p.add_argument('--locator', required=True)
    c = sub.add_parser("compare")
    c.add_argument("root"); c.add_argument("slug"); c.add_argument("n"); c.add_argument("k1"); c.add_argument("k2")
    v = sub.add_parser("verify")
    v.add_argument("root"); v.add_argument("slug"); v.add_argument("n"); v.add_argument("k")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    root = Path(a.root).resolve()
    if not re.fullmatch(r'[\w.-]+', a.slug) or a.slug in ('.', '..'):
        raise ValueError('Недопустимый slug')
    if a.mode in ('preflight', 'run-gate'):
        d = root / ARTIFACTS_DIR / a.slug
        pending = a.mode == 'preflight' and a.phase == 'chunk'
        command = gate_evidence.gate_command(root, a.gate) if a.gate else a.command
        if a.gate:
            context = gate_evidence.configured_context(root, d, a.gate, command, a.n, a.k, a.layer, pending)
        else:
            context = gate_context(root, d, command, a.n, a.k, a.layer, allow_pending_patch=pending)
        if a.mode == 'preflight':
            print(json.dumps({'status': 'configured', 'inputs': context}, ensure_ascii=False))
            return 0
        if a.gate:
            if a.phase != 'verify':
                raise ValueError('Именованный запуск с квитанцией выполняется на Verify')
            rc, output, receipt = gate_evidence.run(root, d, a.n, a.k, a.gate, command, context, a.layer,
                                                   lambda: current_revision(root, d))
            print(f'Квитанция гейта: {receipt.relative_to(d)}', file=sys.stderr)
        else:
            rc, output = run_shell(command, root, env={**os.environ, **context})
        print(output, end='')
        return rc
    if a.mode == 'prepare':
        profile = profile_guard.before_prepare(Path(__file__).parent, root)
        d = root / ARTIFACTS_DIR / a.slug
        base, snapshot = snapshot_base.load(root, d)
        current = regenerate_diff(root, base['id'], canonical=True, d=d) if snapshot is None else snapshot_base.snapshot_diff(root, snapshot, d=d)
        snapshot_status = None
        if snapshot is not None:
            snapshot_status = workspace_status(root)
            print(f'Отличия рабочего дерева от Git HEAD: {len(snapshot_status.splitlines())} путей')
            paths = diffstat(current)['paths']
            print(f'Изменения относительно снимка: {len(paths)} путей' + (': ' + ', '.join(paths) if paths else ''))
        receipt = chunk_preconditions.create(root, d, a.n, a.locator, current,
                                              lambda: baseline_mode(root, a.slug, a.n, snapshot_status),
                                              profile=profile)
        print(json.dumps(receipt, ensure_ascii=False))
        return 0
    if a.mode == 'snapshot':
        print(json.dumps({'kind': 'snapshot', 'id': snapshot_base.create(root, root / ARTIFACTS_DIR / a.slug)}))
        return 0
    if a.mode == "write":
        if a.force and a.resume:
            raise ValueError('Выбери один режим записи')
        with attempt_lock(root / ARTIFACTS_DIR / a.slug, a.n, a.k):
            return write_mode(root, a.slug, a.n, a.k, a.tests_cmd, a.executor_model, a.force, a.layer, a.resume)
    if a.mode == "baseline":
        return baseline_mode(root, a.slug, a.n)
    if a.mode == "compare":
        return compare_mode(root, a.slug, a.n, a.k1, a.k2)
    return verify_mode(root, a.slug, a.n, a.k)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
