from __future__ import annotations

from plan_fixtures import ordinary_candidates
import unittest

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import (
    _validate_public_call_shape,
    _validation_failure,
    _validate_schema,
)
from test_execution_graph_integrity import graph
from test_graph_ledger import observation


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

    def test_reference_error_does_not_direct_caller_to_removed_handles_envelope(self) -> None:
        from cortex_runtime.mcp_api import _service_failure, _tool_error_result
        from cortex_runtime.v12_service import V12ServiceError
        for code in ("invalid_identifier", "task_not_found", "delegation_not_found", "report_not_found"):
            with self.subTest(code=code):
                result = _tool_error_result(_service_failure(V12ServiceError("private failure", code=code)), mutation="read_task")
                rendered = repr(result)
                self.assertNotIn("structuredContent.handles", rendered)
                self.assertNotIn("private failure", rendered)
                self.assertIn("exact server-issued reference", rendered)

    def test_publications_reject_old_handle_fields_before_runtime(self) -> None:
        schema = PUBLIC_TOOLS["publish_plan"]["inputSchema"]
        valid = {
            "task_ref": "t_0123456789ab_" + "a" * 32,
            "summary": "Plan.", "scope": "Bounded.",
            "candidates": ordinary_candidates(graph()), "artifact": observation(),
            "risks": [], "unresolved": [], "status": "completed",
        }
        _validate_schema(schema, valid)
        self.assertNotIn("review_policy", schema["properties"])
        for field in ("review_policy", "nonmateriality", "assignment_ref", "continuation_ref", "report_ref", "item_ref", "digest", "cursor", "handles"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                _validate_schema(schema, {**valid, field: "forbidden"})

    def test_model_and_effort_are_explicit_llm_owned_assignment_fields(self) -> None:
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        self.assertIn("model", schema["required"])
        self.assertIn("reasoning_effort", schema["required"])
        self.assertEqual(schema["properties"]["model"]["enum"], ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])

    def test_runtime_assignment_shape_requires_one_typed_intent(self) -> None:
        for intent in ({"nodes": ["baseline"]}, {"bootstrap": {"kind": "planning"}},
                       {"bootstrap": {"kind": "discovery", "question": "Which interface exists?"}}):
            _validate_public_call_shape("open_assignment", intent)
        for invalid in ({}, {"nodes": ["baseline"], "bootstrap": {"kind": "planning"}}):
            with self.subTest(intent=invalid), self.assertRaisesRegex(ValueError, "exactly one"):
                _validate_public_call_shape("open_assignment", invalid)
        for invalid in ({"bootstrap": {"kind": "discovery"}},
                        {"bootstrap": {"kind": "planning", "question": "Invent scope"}}):
            with self.subTest(intent=invalid), self.assertRaises(ValueError):
                _validate_public_call_shape("open_assignment", invalid)

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
