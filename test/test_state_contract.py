import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


TOOL = (Path(__file__).resolve().parent.parent / "implementations" / "runner-contract"
        / "tools" / "state_contract.py")
SPEC = importlib.util.spec_from_file_location("state_contract", TOOL)
state_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state_contract)


def valid_state():
    return {
        "schema_version": "agent-sdlc/state/v1",
        "slug": "SCHED-101",
        "stage": "verify",
        "claims": [{"id": "claim-1", "text": "Каскад отменяется", "verification": "test_cancel"}],
        "gates": [], "attempts": [], "decisions": [],
        "verification": {
            "passed": True, "action": "continue",
            "review": {
                "schema_version": "agent-sdlc/verify-review/v1",
                "claims": [{"id": "claim-1", "status": "passed", "evidence": [
                    {"path": "scheduler/core.py", "anchor": "cancel:42"}], "remediation": ""}],
                "findings": [], "scope": [], "invariants": [], "regressions": [],
                "retry_instruction": ""
            }
        }
    }


class StateContractTests(unittest.TestCase):
    def test_valid_state_renders_human_projection(self):
        state = valid_state()
        self.assertEqual([], state_contract.validate_state(state))
        report = state_contract.render_verification(state)
        self.assertIn("| claim-1 | Каскад отменяется | ✅ |", report)
        self.assertIn("**passed:** true", report)

    def test_review_must_cover_exact_canonical_claims(self):
        state = valid_state()
        state["verification"]["review"]["claims"][0]["id"] = "claim-2"
        errors = state_contract.validate_state(state)
        self.assertTrue(any("набор id не совпадает" in error for error in errors))

    def test_finding_requires_addressable_evidence(self):
        state = valid_state()
        state["verification"]["review"]["findings"] = [{
            "kind": "mismatch", "summary": "неверная ветка", "evidence": []
        }]
        errors = state_contract.validate_state(state)
        self.assertTrue(any("нужна хотя бы одна ссылка" in error for error in errors))

    def test_legacy_import_is_safe_and_marks_verdict_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / ".sdlc" / "SCHED-101"
            run.mkdir(parents=True)
            (run / "intent.md").write_text(
                "| id | Пункт | Как проверить |\n|---|---|---|\n| claim-1 | Каскад | test_cancel |\n",
                encoding="utf-8")
            (run / "verification-report-1-attempt-1.md").write_text("# old\n", encoding="utf-8")
            state = state_contract.import_legacy(root, "SCHED-101")
        self.assertEqual([], state_contract.validate_state(state))
        self.assertFalse(state["verification"]["passed"])
        self.assertEqual("escalate", state["verification"]["action"])
        self.assertIn("legacy_verdict_not_trusted", state["migration"]["unresolved"])


if __name__ == "__main__":
    unittest.main()
