from __future__ import annotations

from plan_fixtures import ordinary_candidates
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS
from cortex_runtime.mcp_api import (
    _SchemaError,
    _validation_failure,
    _validate_schema,
    _worker_candidate_read_schema,
)


EXPECTED_TOOLS = (
    "open_task", "read_task", "read_state", "read_scope", "read_outcome",
    "read_continuations", "read_evidence", "read_timeline",
    "open_clarification", "record_clarification",
    "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "open_assignment", "publish_plan", "publish_result", "publish_documentation",
    "assess_governance", "close_task",
)

# Keep this table deliberately about the public contract, rather than handler
# implementation details.  It catches omissions in any one of the twenty
# advertised operations while allowing genuinely optional fields to evolve.
EXPECTED_REQUIRED = {
    "open_task": {"outcomes", "project_root", "request_original", "user_language", "constraints"},
    "read_task": {"task_ref"},
    "read_state": {"task_ref"},
    "read_scope": {"task_ref", "responsibility"},
    "read_outcome": {"task_ref", "outcome"},
    "read_continuations": {"task_ref"},
    "read_evidence": {"task_ref", "report_policy"},
    "read_timeline": {"task_ref"},
    "open_clarification": {"task_ref", "prompt", "prompt_language"},
    "record_clarification": {"task_ref", "response_original", "user_language"},
    "open_plan_review": {"task_ref", "prompt", "prompt_language"},
    "record_plan_review": {"task_ref", "response_original", "user_language", "outcome"},
    "open_steering": {"task_ref", "prompt", "prompt_language"},
    "record_steering": {"task_ref", "response_original", "user_language", "add", "retire"},
    "open_assignment": {
        "task_ref", "profile_name", "model", "reasoning_effort",
    },
    "publish_plan": {
        "task_ref", "summary", "scope", "candidates", "artifact", "risks", "unresolved", "status",
    },
    "publish_result": {
        "task_ref", "summary", "outcome", "changes", "node_coverage",
        "documentation_impact", "risks", "unresolved", "status", "artifact",
    },
    "publish_documentation": {
        "task_ref", "summary", "findings", "recommendations",
        "node_coverage", "documentation_impact", "risks", "unresolved", "status", "artifact",
    },
    "assess_governance": {"task_ref", "mode"},
    "close_task": {"task_ref", "verdict"},
}
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
    def test_required_property_checklist_is_generated_from_each_live_schema(self):
        for name, contract in PUBLIC_TOOLS.items():
            with self.subTest(tool=name):
                expected = "Required properties: " + ", ".join(contract["inputSchema"]["required"]) + "."
                self.assertTrue(contract["description"].endswith(expected))
                self.assertEqual(contract["description"].count("Required properties:"), 1)

    def test_exact_operation_set_and_required_closed_objects(self):
        self.assertEqual(tuple(PUBLIC_TOOLS), EXPECTED_TOOLS)
        for name, expected in EXPECTED_REQUIRED.items():
            with self.subTest(tool=name):
                schema = PUBLIC_TOOLS[name]["inputSchema"]
                self.assertEqual(set(schema["required"]), expected)
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(schema["maxBytes"], 65536)
                def visit(value):
                    if isinstance(value, dict):
                        if value.get("type") == "object" and "properties" in value:
                            self.assertIs(value.get("additionalProperties"), False)
                            self.assertLessEqual(set(value.get("required", [])), set(value["properties"]))
                        for child in value.values():
                            visit(child)
                    elif isinstance(value, list):
                        for child in value:
                            visit(child)
                visit(schema)

    def test_open_task_preserves_complete_source_contract(self):
        schema = PUBLIC_TOOLS["open_task"]["inputSchema"]
        self.assertEqual(next(iter(schema["properties"])), "outcomes")
        outcome = schema["properties"]["outcomes"]["items"]
        self.assertEqual(list(outcome["properties"]), ["outcome", "acceptance", "constraints", "verification"])
        self.assertEqual(set(outcome["required"]), set(outcome["properties"]))
        self.assertGreaterEqual(schema["properties"]["constraints"]["minItems"], 1)

    def test_plans_and_results_have_one_verification_source(self):
        from test_execution_graph_integrity import graph
        from test_graph_ledger import observation
        from test_typed_publication_transaction import baseline_content
        worker = "t_0123456789ab_" + "a" * 32
        result = dict(task_ref=worker, **baseline_content())
        plan = dict(task_ref=worker, status="completed", summary="Plan", scope="Product",
                    candidates=ordinary_candidates(graph()), artifact=observation(), risks=[], unresolved=[])
        documentation = {k: v for k, v in result.items() if k not in {"changes", "outcome"}}
        documentation.update(findings=[], recommendations=[])
        for name, content in (("publish_plan", plan), ("publish_result", result), ("publish_documentation", documentation)):
            schema = PUBLIC_TOOLS[name]["inputSchema"]
            _validate_schema(schema, content)
            self.assertEqual(list(schema["properties"])[:3], ["task_ref", "status", "summary"])
            self.assertEqual(schema["required"][:3], ["task_ref", "status", "summary"])
            for field in ("verification_facts", "outcome_coverage", "stages", *FORBIDDEN):
                with self.subTest(tool=name, field=field), self.assertRaisesRegex(ValueError, "unsupported property"):
                    _validate_schema(schema, {**content, field: "obsolete"})
            self.assertEqual(schema["properties"]["risks"]["minItems"], 0)
            self.assertEqual(schema["properties"]["unresolved"]["minItems"], 0)
            for field in schema["required"]:
                reduced = {k: v for k, v in content.items() if k != field}
                with self.subTest(tool=name, missing=field), self.assertRaises(_SchemaError):
                    _validate_schema(schema, reduced)
        self.assertNotIn("node_coverage", PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"])
        self.assertNotIn("graph", PUBLIC_TOOLS["publish_result"]["inputSchema"]["properties"])

    def test_worker_candidate_accepts_only_assignment_paging_fields(self):
        schema = _worker_candidate_read_schema(PUBLIC_TOOLS["read_task"])
        worker = "t_0123456789ab_" + "a" * 32
        _validate_schema(schema, {"task_ref": worker})
        _validate_schema(schema, {"task_ref": worker, "continue": True})
        for field in ("view", "report_policy", "agent_id", "worker", "cursor"):
            with self.subTest(field=field), self.assertRaises(_SchemaError):
                _validate_schema(schema, {"task_ref": worker, field: "invented"})

    def test_identifiers_are_not_selectors_and_artifact_digests_are_observations(self):
        for name, contract in PUBLIC_TOOLS.items():
            for surface in ("inputSchema", "outputSchema", "runtimeOutputSchema"):
                names = set(property_names(contract[surface]))
                self.assertFalse(names & (FORBIDDEN - {"digest"}), (name, surface))
                self.assertLessEqual({key for key in names if key.endswith(("_ref", "_refs", "_id", "_ids"))}, {"task_ref"})
                self.assertNotIn("digest", contract[surface].get("properties", {}))
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            artifact = PUBLIC_TOOLS[name]["inputSchema"]["properties"]["artifact"]
            if "anyOf" in artifact:
                self.assertEqual(artifact["anyOf"][1], {"type": "null"})
                artifact = artifact["anyOf"][0]
            self.assertEqual(artifact["properties"]["changes"]["properties"]["digest"]["minLength"], 64)

    def test_assignment_scope_is_structural_not_freeform_instructions(self):
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        props = schema["properties"]
        for obsolete in ("instructions", "scope", "goal", "role", "outcomes", "responsibility", "report_policy", "loss_recovery"):
            self.assertNotIn(obsolete, props)
        self.assertEqual(props["nodes"]["items"]["type"], "string")
        self.assertEqual(props["bootstrap"]["properties"]["kind"]["enum"], ["discovery", "planning"])
        self.assertEqual(props["model"]["enum"], ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])
        self.assertEqual(props["reasoning_effort"]["enum"], ["low", "medium", "high", "xhigh", "max"])

    def test_complete_missing_fields_are_reported_together(self):
        for name in ("publish_plan", "publish_result", "publish_documentation"):
            schema = PUBLIC_TOOLS[name]["inputSchema"]
            with self.assertRaises(_SchemaError) as raised:
                _validate_schema(schema, {})
            error = _validation_failure(raised.exception, tool_name=name, arguments={}, input_schema=schema)
            self.assertEqual(error["details"]["missing_fields"], schema["required"])

    def test_decisions_describe_visible_review_and_direct_user_authority(self):
        for name in ("open_clarification", "open_plan_review", "open_steering"):
            description = PUBLIC_TOOLS[name]["description"].lower()
            self.assertIn("coordinator-only", description)
            self.assertIn("does not display its prompt to the user", description)
            self.assertIn("after success", description)
            self.assertIn("final answer", description)
        plan = PUBLIC_TOOLS["open_plan_review"]["description"].lower()
        for text in ("markdown_link", "byte-for-byte", "decision-ready", "approve", "cancel"):
            self.assertIn(text, plan)
        approval = PUBLIC_TOOLS["record_plan_review"]["description"].lower()
        self.assertIn("requires explicit current approval", approval)
        self.assertIn("silence, unrelated text, or an old-plan decision is not approval", approval)
        self.assertIn("empty/empty delta", PUBLIC_TOOLS["record_steering"]["description"].lower())
        close = PUBLIC_TOOLS["close_task"]["description"].lower()
        for text in ("mandatory closure_review", "record exactly revise or close", "ledger"):
            self.assertIn(text, close)

    def test_role_and_recovery_contracts_remain_explicit(self):
        for name in EXPECTED_TOOLS:
            expected = "worker-only" if name in {"read_task", "publish_plan", "publish_result", "publish_documentation"} else "coordinator-only"
            self.assertIn(expected, PUBLIC_TOOLS[name]["description"].lower())
        self.assertIn("call this next", PUBLIC_TOOLS["read_continuations"]["description"].lower())
        self.assertIn("not worker-liveness polling", PUBLIC_TOOLS["read_state"]["description"].lower())
        self.assertIn("explicit chronology", PUBLIC_TOOLS["read_timeline"]["description"].lower())

    def test_all_first_calls_cross_source_stdio_with_real_preconditions(self):
        from typed_stdio_scenario import run_matrix
        with tempfile.TemporaryDirectory(prefix="cortex-conformance-home-") as home:
            with tempfile.TemporaryDirectory(prefix="cortex-conformance-project-") as project:
                run_matrix(home, project)


if __name__ == "__main__":
    unittest.main()
