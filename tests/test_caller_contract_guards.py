from __future__ import annotations

import unittest

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import (
    _validate_public_call_shape,
    _validation_failure,
    _validate_schema,
)


class CallerContractGuardTests(unittest.TestCase):
    def test_all_public_input_objects_are_closed(self) -> None:
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        for contract in PUBLIC_TOOLS.values():
            visit(contract["inputSchema"])

    def test_publications_reject_old_handle_fields_before_runtime(self) -> None:
        schema = PUBLIC_TOOLS["publish_plan"]["inputSchema"]
        valid = {
            "task_ref": "t_0123456789ab_" + "a" * 32,
            "summary": "Plan.", "scope": "Bounded.",
            "stages": [{"owner": "implementation", "work": ["Build."], "verification": ["Test."]}],
            "verification_facts": [{"state": "not_run", "summary": "Execution belongs to implementation."}],
            "outcome_coverage": [{"outcome": "Build.", "status": "planned", "verification": ["Mapped."]}],
            "risks": [], "unresolved": [], "status": "completed",
        }
        _validate_schema(schema, valid)
        self.assertNotIn("review_policy", schema["properties"])
        for field in ("review_policy", "assignment_ref", "continuation_ref", "report_ref", "item_ref", "digest", "cursor", "handles"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                _validate_schema(schema, {**valid, field: "forbidden"})

    def test_model_and_effort_are_explicit_llm_owned_assignment_fields(self) -> None:
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        self.assertIn("model", schema["required"])
        self.assertIn("reasoning_effort", schema["required"])
        self.assertEqual(schema["properties"]["model"]["enum"], ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])

    def test_runtime_assignment_shape_matches_optional_complete_scope_contract(self) -> None:
        for responsibility in ("delivery", "evidence", "planning"):
            with self.subTest(responsibility=responsibility):
                _validate_public_call_shape(
                    "open_assignment", {"responsibility": responsibility},
                )
        with self.assertRaisesRegex(ValueError, "unsupported property"):
            _validate_public_call_shape(
                "open_assignment", {"responsibility": "planning", "outcomes": ["A"]},
            )
        _validate_public_call_shape(
            "open_assignment", {
                "responsibility": "delivery",
                "loss_recovery": {"state": "blocked", "reason": "Lost", "evidence": ["Confirmed"]},
            },
        )

    def test_missing_required_publication_fields_are_reported_together(self) -> None:
        schema = PUBLIC_TOOLS["publish_result"]["inputSchema"]
        with self.assertRaises(ValueError) as raised:
            _validate_schema(schema, {})
        error = raised.exception
        self.assertEqual(
            error.missing_fields,
            tuple(schema["required"]),
        )
        failure = _validation_failure(
            error, tool_name="publish_result", arguments={}, input_schema=schema,
        )
        self.assertEqual(failure["details"]["missing_fields"], list(schema["required"]))


if __name__ == "__main__":
    unittest.main()
