from __future__ import annotations

import unittest

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import _validate_schema


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
        for field in ("assignment_ref", "continuation_ref", "report_ref", "item_ref", "digest", "cursor", "handles"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                _validate_schema(schema, {**valid, field: "forbidden"})

    def test_model_and_effort_are_explicit_llm_owned_assignment_fields(self) -> None:
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        self.assertIn("model", schema["required"])
        self.assertIn("reasoning_effort", schema["required"])
        self.assertEqual(schema["properties"]["model"]["enum"], ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])


if __name__ == "__main__":
    unittest.main()
