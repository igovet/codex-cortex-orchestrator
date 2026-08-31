from __future__ import annotations

import json
import unittest

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import _validate_schema


EXPECTED_TOOLS = (
    "open_task", "read_task", "open_clarification", "record_clarification",
    "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "open_assignment", "publish_plan", "publish_result", "publish_documentation",
    "assess_governance", "close_task",
)
FORBIDDEN = {
    "assignment_ref", "continuation_ref", "binding_ref", "report_ref", "report_refs",
    "plan_ref", "decision_ref", "item_ref", "cursor", "handles", "digest",
    "idempotency_key", "after_sequence",
}


def property_names(value):
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            yield from properties
        for item in value.values():
            yield from property_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from property_names(item)


class PublicMcpFirstCallConformanceTests(unittest.TestCase):
    def test_catalogue_is_flat_task_ref_only(self) -> None:
        self.assertEqual(tuple(PUBLIC_TOOLS), EXPECTED_TOOLS)
        for name, contract in PUBLIC_TOOLS.items():
            for surface in ("inputSchema", "outputSchema", "runtimeOutputSchema"):
                names = set(property_names(contract[surface]))
                self.assertFalse(names & FORBIDDEN, (name, surface, names & FORBIDDEN))
                identifier_like = {item for item in names if item.endswith(("_ref", "_refs", "_id", "_ids"))}
                self.assertLessEqual(identifier_like, {"task_ref"})

    def test_old_handle_shapes_fail_at_schema_boundary(self) -> None:
        worker = "t_0123456789ab_" + "a" * 32
        base = {
            "task_ref": worker, "summary": "Done.", "outcome": "Done.", "changes": [],
            "verification_facts": [{"state": "executed", "summary": "Focused check passed."}],
            "outcome_coverage": [{"outcome": "Build.", "status": "complete", "verification": ["Passed."]}],
            "documentation_impact": "No documentation impact.", "risks": [], "unresolved": [], "status": "completed",
        }
        _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], base)
        for field in FORBIDDEN - {"digest"}:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], {**base, field: "old"})

    def test_publications_are_separate_flat_closed_contracts(self) -> None:
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            schema = PUBLIC_TOOLS[name]["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("evidence", schema["properties"])
            self.assertIn("outcome_coverage", schema["properties"])
        self.assertIn("stages", PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"])
        self.assertIn("changes", PUBLIC_TOOLS["publish_result"]["inputSchema"]["properties"])
        self.assertIn("findings", PUBLIC_TOOLS["publish_documentation"]["inputSchema"]["properties"])

    def test_publish_plan_advertises_required_empty_evidence_arrays(self) -> None:
        contract = PUBLIC_TOOLS["publish_plan"]
        schema = contract["inputSchema"]
        self.assertIn("unresolved", schema["required"])
        self.assertIn("risks", schema["required"])
        self.assertIn("must be present", schema["properties"]["unresolved"]["description"])
        self.assertIn("empty array", schema["properties"]["unresolved"]["description"])
        self.assertIn("must be present", schema["properties"]["risks"]["description"])
        self.assertIn("empty array", schema["properties"]["risks"]["description"])
        self.assertIn("explicit empty", contract["description"])

    def test_assignment_and_task_opening_are_flat(self) -> None:
        self.assertNotIn("task", PUBLIC_TOOLS["open_task"]["inputSchema"]["properties"])
        self.assertNotIn("mission", PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"])
        self.assertIn("outcomes", PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"])
        self.assertEqual(
            PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"]["outcomes"]["items"]["type"],
            "string",
        )
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            coverage = PUBLIC_TOOLS[name]["inputSchema"]["properties"]["outcome_coverage"]["items"]
            self.assertEqual(coverage["properties"]["outcome"]["type"], "string")

    def test_serialized_catalogue_contains_no_removed_callable_names(self) -> None:
        serialized = json.dumps({name: value["inputSchema"] for name, value in PUBLIC_TOOLS.items()}, sort_keys=True)
        for name in FORBIDDEN:
            self.assertNotIn(f'"{name}"', serialized)


if __name__ == "__main__":
    unittest.main()
