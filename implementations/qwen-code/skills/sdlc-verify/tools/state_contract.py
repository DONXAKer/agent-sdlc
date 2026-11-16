#!/usr/bin/env python3
"""Reference validation and rendering for the Agent-SDLC state-first protocol.

The reference intentionally uses only the standard library so an external runner can
exercise the contract before choosing a JSON Schema runtime or constrained decoder.
"""

import argparse
import json
import re
import sys
import hashlib
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_DIR = (Path(__file__).resolve().parent.parent / "schemas")
if not SCHEMA_DIR.is_dir():
    SCHEMA_DIR = Path(__file__).resolve().parent / "state_contract" / "schemas"

STATE_VERSION = "agent-sdlc/state/v1"
REVIEW_VERSION = "agent-sdlc/verify-review/v1"
CLAIM_RE = re.compile(r"claim-[1-9][0-9]*$")
SHA_RE = re.compile(r"[a-f0-9]{64}$")


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def expect_object(value, where, errors):
    if not isinstance(value, dict):
        errors.append(f"{where}: ожидается object")
        return {}
    return value


def expect_keys(value, where, required, allowed, errors):
    obj = expect_object(value, where, errors)
    for key in required:
        if key not in obj:
            errors.append(f"{where}: нет обязательного поля {key}")
    for key in obj:
        if key not in allowed:
            errors.append(f"{where}: неизвестное поле {key}")
    return obj


def reference_errors(ref, where):
    errors = []
    obj = expect_keys(ref, where, {"path", "anchor"}, {"path", "anchor"}, errors)
    if not isinstance(obj.get("path"), str) or not obj.get("path"):
        errors.append(f"{where}.path: нужен непустой путь")
    if not isinstance(obj.get("anchor"), str) or not obj.get("anchor"):
        errors.append(f"{where}.anchor: нужен адрес файла, символа, hunk или теста")
    return errors


def _validate_review_structure(review, claim_ids=None):
    errors = []
    obj = expect_keys(
        review, "review",
        {"schema_version", "claims", "findings", "scope", "invariants", "regressions", "retry_instruction"},
        {"schema_version", "claims", "findings", "scope", "invariants", "regressions", "retry_instruction"},
        errors,
    )
    if obj.get("schema_version") != REVIEW_VERSION:
        errors.append("review.schema_version: ожидается agent-sdlc/verify-review/v1")
    seen = set()
    claims = obj.get("claims")
    if not isinstance(claims, list):
        errors.append("review.claims: ожидается массив")
    else:
        for index, claim in enumerate(claims):
            where = f"review.claims[{index}]"
            item = expect_keys(claim, where, {"id", "status", "evidence", "remediation"},
                               {"id", "status", "evidence", "remediation"}, errors)
            claim_id = item.get("id")
            if not isinstance(claim_id, str) or not CLAIM_RE.fullmatch(claim_id):
                errors.append(f"{where}.id: ожидается claim-N")
            elif claim_id in seen:
                errors.append(f"{where}.id: дублируется {claim_id}")
            else:
                seen.add(claim_id)
            if item.get("status") not in {"passed", "failed", "uncertain", "manual"}:
                errors.append(f"{where}.status: недопустимый статус")
            evidence = item.get("evidence")
            if not isinstance(evidence, list):
                errors.append(f"{where}.evidence: ожидается массив")
            else:
                for ref_index, ref in enumerate(evidence):
                    errors.extend(reference_errors(ref, f"{where}.evidence[{ref_index}]"))
            if not isinstance(item.get("remediation"), str):
                errors.append(f"{where}.remediation: ожидается строка")
        if claim_ids is not None and seen != set(claim_ids):
            errors.append("review.claims: набор id не совпадает с каноническими claims")
    allowed_kinds = {
        "findings": {"mismatch", "uncovered_behavior"},
        "scope": {"scope"}, "invariants": {"invariant"}, "regressions": {"regression"},
    }
    for field, kinds in allowed_kinds.items():
        findings = obj.get(field)
        if not isinstance(findings, list):
            errors.append(f"review.{field}: ожидается массив")
            continue
        for index, finding in enumerate(findings):
            where = f"review.{field}[{index}]"
            item = expect_keys(finding, where, {"kind", "summary", "evidence"},
                               {"kind", "summary", "evidence"}, errors)
            if item.get("kind") not in kinds:
                errors.append(f"{where}.kind: не соответствует секции {field}")
            if not isinstance(item.get("summary"), str) or not item.get("summary"):
                errors.append(f"{where}.summary: нужна непустая формулировка")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{where}.evidence: нужна хотя бы одна ссылка")
            elif isinstance(evidence, list):
                for ref_index, ref in enumerate(evidence):
                    errors.extend(reference_errors(ref, f"{where}.evidence[{ref_index}]"))
    if not isinstance(obj.get("retry_instruction"), str):
        errors.append("review.retry_instruction: ожидается строка")
    return errors


def _validate_state_structure(state):
    errors = []
    obj = expect_keys(
        state, "state",
        {"schema_version", "slug", "stage", "claims", "gates", "attempts", "decisions", "verification"},
        {"schema_version", "slug", "stage", "claims", "gates", "attempts", "decisions", "verification", "migration"},
        errors,
    )
    if obj.get("schema_version") != STATE_VERSION:
        errors.append("state.schema_version: ожидается agent-sdlc/state/v1")
    if not isinstance(obj.get("slug"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", obj.get("slug", "")):
        errors.append("state.slug: недопустимый slug")
    if obj.get("stage") not in {"intent", "explore", "ask", "plan", "chunk", "verify", "handoff", "blocked"}:
        errors.append("state.stage: недопустимый этап")
    claims = obj.get("claims")
    claim_ids = []
    if not isinstance(claims, list):
        errors.append("state.claims: ожидается массив")
    else:
        for index, claim in enumerate(claims):
            item = expect_keys(claim, f"state.claims[{index}]", {"id", "text", "verification"},
                               {"id", "text", "verification", "tags"}, errors)
            claim_id = item.get("id")
            if not isinstance(claim_id, str) or not CLAIM_RE.fullmatch(claim_id) or claim_id in claim_ids:
                errors.append(f"state.claims[{index}].id: нужен уникальный claim-N")
            else:
                claim_ids.append(claim_id)
            for field in ("text", "verification"):
                if not isinstance(item.get(field), str) or not item.get(field):
                    errors.append(f"state.claims[{index}].{field}: нужна непустая строка")
    for field in ("gates", "attempts", "decisions"):
        if not isinstance(obj.get(field), list):
            errors.append(f"state.{field}: ожидается массив")
    verification = obj.get("verification")
    if isinstance(verification, dict):
        if not isinstance(verification.get("passed"), bool):
            errors.append("state.verification.passed: ожидается boolean")
        if verification.get("action") not in {"continue", "retry", "blocked_env", "escalate"}:
            errors.append("state.verification.action: недопустимый action")
        errors.extend(validate_review(verification.get("review"), claim_ids))
    else:
        errors.append("state.verification: ожидается object")
    if "migration" in obj:
        migration = expect_keys(obj["migration"], "state.migration", {"source", "artifacts", "unresolved"},
                                {"source", "artifacts", "unresolved"}, errors)
        if migration.get("source") != "legacy_markdown":
            errors.append("state.migration.source: ожидается legacy_markdown")
        for field in ("artifacts", "unresolved"):
            if not isinstance(migration.get(field), list) or not all(isinstance(x, str) and x for x in migration[field]):
                errors.append(f"state.migration.{field}: ожидается массив непустых строк")
    return errors


# Validate the exact keyword subset used by the shipped v1 schemas, without dependencies.
def schema_errors(value, schema, document, where):
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/"):
            target = document
            for part in ref[2:].split("/"):
                target = target[part]
            return schema_errors(value, target, document, where)
        target = load(SCHEMA_DIR / ref)
        return schema_errors(value, target, target, where)
    errors = []
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}
    kind = schema.get("type")
    if kind and (not isinstance(value, types[kind]) or kind == "integer" and isinstance(value, bool)):
        return [f"{where}: ожидается {kind}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: неверная константа")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: недопустимое значение")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: нет обязательного поля {key}")
        for key, item in value.items():
            if key not in props and schema.get("additionalProperties") is False:
                errors.append(f"{where}: неизвестное поле {key}")
            elif key in props:
                errors.extend(schema_errors(item, props[key], document, f"{where}.{key}"))
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema.get("items", {}), document, f"{where}[{index}]"))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: пустая строка")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{where}: неверный формат")
        if schema.get("format") == "date-time":
            from datetime import datetime
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError()
            except ValueError:
                errors.append(f"{where}: нужен date-time с часовым поясом")
    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            errors.append(f"{where}: вне диапазона")
    return errors


def validate_review(review, claim_ids=None):
    schema = load(SCHEMA_DIR / "verify-review-v1.schema.json")
    errors = schema_errors(review, schema, schema, "review")
    if errors:
        return errors
    return _validate_review_structure(review, claim_ids)


def validate_state(state):
    schema = load(SCHEMA_DIR / "agent-sdlc-state-v1.schema.json")
    errors = schema_errors(state, schema, schema, "state")
    if errors:
        return errors
    errors.extend(_validate_state_structure(state))
    verification = state["verification"]
    review = verification["review"]
    claims = {claim["id"]: claim for claim in state["claims"]}
    for result in review["claims"]:
        if result["status"] == "passed" and not result["evidence"]:
            errors.append(f"{result['id']}: passed требует evidence")
        if result["status"] == "manual" and "manual" not in claims.get(result["id"], {}).get("tags", []):
            errors.append(f"{result['id']}: manual требует канонический тег manual")
    if verification["passed"] != (verification["action"] == "continue"):
        errors.append("state.verification: passed/action противоречат друг другу")
    if verification["passed"]:
        if not claims:
            errors.append("passed требует непустой приёмки")
        if state["stage"] not in {"verify", "handoff"}:
            errors.append("passed допустим только на verify/handoff")
        if any(item["status"] in {"failed", "uncertain"} for item in review["claims"]):
            errors.append("passed противоречит failed/uncertain claims")
        if any(review[field] for field in ("findings", "scope", "invariants", "regressions")):
            errors.append("passed противоречит открытым findings")
        limit = 7 if state["stage"] == "handoff" else 6
        required = {"Сборка", "Тесты", "Scope: файлы вне плана", "Анти-обход тест-гейта", "Ревью независимым агентом"}
        if any(g["name"] in required and g["stage"] != 6 for g in state["gates"]):
            errors.append("минимальные гейты должны относиться к этапу 6")
        covered = {g["name"] for g in state["gates"] if g["enabled"] and g["stage"] <= limit}
        if not required <= covered:
            errors.append("passed требует минимальный набор гейтов текущего этапа")
        if any(g["enabled"] and g["stage"] <= limit and g["status"] == "passed" and not g.get("evidence")
               for g in state["gates"]):
            errors.append("passed гейт требует хотя бы одно хэшированное evidence")
        if any(g["enabled"] and g["stage"] <= limit and g["status"] != "passed" for g in state["gates"]):
            errors.append("passed противоречит непройденному обязательному гейту этапа")
        if state.get("migration", {}).get("unresolved"):
            errors.append("passed противоречит unresolved migration")
    gate_ids = [(g["name"], g["stage"]) for g in state["gates"]]
    attempt_ids = [(a["chunk"], a["number"]) for a in state["attempts"]]
    if len(set(gate_ids)) != len(gate_ids) or len(set(attempt_ids)) != len(attempt_ids):
        errors.append("дублируются гейты или попытки")
    return errors


def evidence_errors(state, root, directory):
    """Paths are project-relative; explicit artifact: prefix selects run-relative files."""
    errors = []
    root, directory = root.resolve(), directory.resolve()
    def check(ref):
        path = ref["path"]
        base = directory if path.startswith("artifact:") else root
        raw = path[len("artifact:"):] if path.startswith("artifact:") else path
        candidate = (base / raw).resolve()
        if not raw or Path(raw).is_absolute() or not candidate.is_relative_to(base):
            errors.append(f"evidence: путь вне разрешённого корня: {path}")
        elif not candidate.is_file():
            errors.append(f"evidence: файл отсутствует: {path}")
        elif "sha256" in ref and sha256(candidate) != ref["sha256"]:
            errors.append(f"evidence: sha256 не совпадает: {path}")
    for gate in state["gates"]:
        for ref in gate.get("evidence", []):
            check(ref)
    for attempt in state["attempts"]:
        for field in ("diff", "tests"):
            if field in attempt:
                check(attempt[field])
    for field in ("claims", "findings", "scope", "invariants", "regressions"):
        for item in state["verification"]["review"][field]:
            for ref in item["evidence"]:
                check(ref)
    for artifact in state.get("migration", {}).get("artifacts", []):
        path, separator, digest = artifact.rpartition(":")
        if not separator or not SHA_RE.fullmatch(digest):
            errors.append("migration.artifacts: ожидается artifact-relative-path:sha256")
        else:
            check({"path": "artifact:" + path, "sha256": digest})
    return errors


def status_glyph(status):
    return {"passed": "✅", "failed": "❌", "uncertain": "⚠", "manual": "manual"}[status]


def refs(items):
    return ", ".join(f"{item['path']}:{item['anchor']}" for item in items) or "н/п"


def render_verification(state):
    review = state["verification"]["review"]
    claims = {claim["id"]: claim for claim in state["claims"]}
    lines = [
        f"# Отчёт приёмки: {state['slug']}", "",
        "## 1. Пункты приёмки", "",
        "| id | Пункт | passed | Чем подтверждён | Что чинить |",
        "|---|---|---|---|---|",
    ]
    for result in review["claims"]:
        lines.append("| {id} | {text} | {status} | {evidence} | {fix} |".format(
            id=result["id"], text=claims[result["id"]]["text"], status=status_glyph(result["status"]),
            evidence=refs(result["evidence"]), fix=result["remediation"] or "н/п"))
    sections = [("2. Ревью: что искали опровергнуть", "findings"), ("3. Scope", "scope"),
                ("4. Инварианты", "invariants"), ("5. Регрессии", "regressions")]
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        entries = review[key]
        lines.extend([f"- {entry['summary']} — {refs(entry['evidence'])}" for entry in entries] or ["- н/п"])
    verdict = state["verification"]
    lines.extend(["", "## Вердикт", "", f"- **passed:** {str(verdict['passed']).lower()}",
                  f"- **action:** {verdict['action']}",
                  f"- **retry_instruction:** {verdict.get('retry_instruction') or review['retry_instruction'] or 'н/п'}"])
    return "\n".join(lines) + "\n"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_claims(intent):
    claims = []
    for line in intent.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 4 or not CLAIM_RE.fullmatch(cells[1].split()[0] if cells[1] else ""):
            continue
        claims.append({"id": cells[1].split()[0], "text": cells[2], "verification": cells[3]})
    return claims


def import_legacy(root, slug):
    directory = root / ".sdlc" / slug
    intent_path = directory / "intent.md"
    report_paths = sorted(directory.glob("verification-report-*-attempt-*.md"))
    if not intent_path.exists() or not report_paths:
        raise ValueError("legacy import требует intent.md и хотя бы один verification-report")
    claims = legacy_claims(intent_path.read_text(encoding="utf-8"))
    if not claims:
        raise ValueError("legacy import не нашёл claim-N в intent.md")
    report_path = report_paths[-1]
    review_claims = [{
        "id": claim["id"], "status": "uncertain",
        "evidence": [{"path": "artifact:" + report_path.name, "anchor": claim["id"]}],
        "remediation": "Требуется повторная верификация из канонического state."
    } for claim in claims]
    artifacts = [intent_path, report_path]
    return {
        "schema_version": STATE_VERSION, "slug": slug, "stage": "blocked", "claims": claims,
        "gates": [], "attempts": [], "decisions": [],
        "verification": {"passed": False, "action": "escalate", "retry_instruction": "Повторить verify: импорт не доверяет legacy-вердикту.",
                         "review": {"schema_version": REVIEW_VERSION, "claims": review_claims,
                                    "findings": [], "scope": [], "invariants": [], "regressions": [],
                                    "retry_instruction": "Повторить verify: импорт не доверяет legacy-вердикту."}},
        "migration": {"source": "legacy_markdown",
                      "artifacts": [f"{path.name}:{sha256(path)}" for path in artifacts],
                      "unresolved": ["legacy_gate_results", "legacy_attempt_history", "legacy_verdict_not_trusted"]}
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-state", "render-verification"):
        sub.add_parser(name).add_argument("state", type=Path)
    validate_review_parser = sub.add_parser("validate-review")
    validate_review_parser.add_argument("review", type=Path)
    validate_review_parser.add_argument("--claims", type=Path, help="state.json для сверки набора claim-id")
    import_parser = sub.add_parser("import-legacy")
    import_parser.add_argument("root", type=Path)
    import_parser.add_argument("slug")
    import_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "import-legacy":
            state = import_legacy(args.root, args.slug)
            errors = validate_state(state)
            if not errors:
                if args.output.exists():
                    errors = [f"{args.output}: уже существует; импорт не перезаписывает состояние"]
                else:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif args.command == "validate-review":
            claim_ids = None
            if args.claims:
                claim_ids = [claim["id"] for claim in load(args.claims).get("claims", [])]
            errors = validate_review(load(args.review), claim_ids)
        else:
            state = load(args.state)
            errors = validate_state(state)
            if not errors and args.command == "render-verification":
                sys.stdout.write(render_verification(state))
    except ValueError as exc:
        errors = [str(exc)]
    if errors:
        print("\n".join(f"❌ {error}" for error in errors), file=sys.stderr)
        return 1
    if args.command.startswith("validate"):
        print("✅ контракт валиден")
    elif args.command == "import-legacy" and not errors:
        print(f"✅ legacy Markdown импортирован в {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
