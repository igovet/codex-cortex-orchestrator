from __future__ import annotations

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
from cortex_runtime.mcp_api import _validation_failure, _validate_schema


EXPECTED_TOOLS = (
    "open_task", "read_task", "open_clarification", "record_clarification",
    "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "open_assignment", "publish_plan", "publish_result", "publish_documentation",
    "assess_governance", "close_task",
)

# Keep this table deliberately about the public contract, rather than handler
# implementation details.  It catches omissions in any one of the fourteen
# advertised operations while allowing genuinely optional fields to evolve.
EXPECTED_REQUIRED = {
    "open_task": {"project_root", "request_original", "user_language", "outcomes", "constraints"},
    "read_task": {"task_ref", "view"},
    "open_clarification": {"task_ref", "prompt", "prompt_language", "purpose", "options"},
    "record_clarification": {"task_ref", "response_original", "user_language", "outcome"},
    "open_plan_review": {"task_ref", "prompt", "prompt_language"},
    "record_plan_review": {"task_ref", "response_original", "user_language", "outcome"},
    "open_steering": {"task_ref", "prompt", "prompt_language"},
    "record_steering": {"task_ref", "response_original", "user_language", "add", "retire"},
    "open_assignment": {
        "task_ref", "role", "profile_name", "model", "reasoning_effort", "responsibility",
        "goal", "scope", "instructions", "outcomes", "report_policy",
    },
    "publish_plan": {
        "task_ref", "summary", "scope", "review_policy", "stages", "verification_facts",
        "outcome_coverage", "risks", "unresolved", "status",
    },
    "publish_result": {
        "task_ref", "summary", "outcome", "changes", "verification_facts", "outcome_coverage",
        "documentation_impact", "risks", "unresolved", "status",
    },
    "publish_documentation": {
        "task_ref", "summary", "findings", "recommendations", "verification_facts",
        "outcome_coverage", "documentation_impact", "risks", "unresolved", "status",
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
    def test_every_public_input_contract_is_required_and_closed(self) -> None:
        """The advertised boundary, not a handler convenience, is the API."""
        self.assertEqual(set(PUBLIC_TOOLS), set(EXPECTED_REQUIRED))
        for name, expected in EXPECTED_REQUIRED.items():
            with self.subTest(tool=name):
                schema = PUBLIC_TOOLS[name]["inputSchema"]
                self.assertEqual(set(schema["required"]), expected)
                self.assertFalse(schema["additionalProperties"])
                for field in expected:
                    self.assertTrue(
                        schema["properties"][field]["description"].startswith("Required property."),
                        (name, field, schema["properties"][field].get("description")),
                    )

                def assert_closed_objects(value: object, path: str = "schema") -> None:
                    if isinstance(value, dict):
                        if value.get("type") == "object" and "properties" in value:
                            self.assertIs(value.get("additionalProperties"), False, (name, path))
                        for key, child in value.items():
                            assert_closed_objects(child, f"{path}.{key}")
                    elif isinstance(value, list):
                        for index, child in enumerate(value):
                            assert_closed_objects(child, f"{path}[{index}]")

                assert_closed_objects(schema)

    def test_public_descriptions_advertise_ownership_and_timing(self) -> None:
        """Descriptions prevent a worker from guessing lifecycle ownership."""
        semantic_tokens = {
            "open_task": ("coordinator-only", "first project execution"),
            "read_task": ("fresh worker", "first cortex operation", "assignment"),
            "open_clarification": ("coordinator-only", "decision opening"),
            "record_clarification": ("coordinator-only", "direct user answer"),
            "open_plan_review": ("coordinator-only", "current finalized active plan"),
            "record_plan_review": ("coordinator-only", "direct user decision"),
            "open_steering": ("coordinator-only", "decision opening"),
            "record_steering": ("coordinator-only", "atomic", "direct user steering"),
            "open_assignment": ("coordinator-only", "exactly one", "private worker assignment"),
            "publish_plan": ("worker-only", "atomic", "complete"),
            "publish_result": ("worker-only", "atomic", "complete"),
            "publish_documentation": ("worker-only", "atomic", "complete"),
            "assess_governance": ("coordinator-only", "before the first worker", "explicit"),
            "close_task": (
                "coordinator-only", "ledger", "unresolved evidence",
                "post-result review", "readiness probe",
            ),
        }
        for name, tokens in semantic_tokens.items():
            with self.subTest(tool=name):
                description = PUBLIC_TOOLS[name]["description"].lower()
                for token in tokens:
                    self.assertIn(token, description)

    def test_public_descriptions_publish_the_exact_required_set(self) -> None:
        """The callable description must expose the same required set as its schema.

        This is deliberately derived from ``inputSchema`` rather than keeping a
        second hand-written field list in the test.  A model should be able to
        validate a publication call before sending it, and the description must
        not silently drift when a contract gains or removes a required field.
        """
        for name, contract in PUBLIC_TOOLS.items():
            with self.subTest(tool=name):
                schema = contract["inputSchema"]
                required = ", ".join(schema.get("required", []))
                description = contract["description"]
                self.assertIn(
                    f"Required properties for this call: {required}.",
                    description,
                )
                self.assertIn(
                    "Before invoking, verify every required property is present",
                    description,
                )

    def test_publish_result_validation_reports_all_missing_required_fields(self) -> None:
        """One malformed publication must not force serial field-by-field retries."""
        schema = PUBLIC_TOOLS["publish_result"]["inputSchema"]
        try:
            _validate_schema(schema, {"task_ref": "t_0123456789ab_" + "a" * 32})
        except ValueError as error:
            missing = tuple(getattr(error, "missing_fields", ()))
            self.assertEqual(
                missing,
                tuple(field for field in schema["required"] if field != "task_ref"),
            )
            failure = _validation_failure(
                error,
                tool_name="publish_result",
                arguments={"task_ref": "t_0123456789ab_" + "a" * 32},
                input_schema=schema,
            )
            details = failure["details"]
            self.assertEqual(details["missing_fields"], list(missing))
        else:
            self.fail("publish_result accepted a payload missing its required properties")

    def test_representative_first_calls_cross_real_stdio_boundary(self) -> None:
        """Exercise catalogue discovery plus task-opening and governance calls in stdio."""
        script = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts" / "cortex.py"
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            env = dict(os.environ, CODEX_HOME=home, CORTEX_SOURCE_MODE="1", PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)
            process = subprocess.Popen(
                [sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env,
            )
            try:
                assert process.stdin is not None and process.stdout is not None

                def call(payload: dict) -> dict:
                    process.stdin.write(json.dumps(payload) + "\n")
                    process.stdin.flush()
                    line = process.stdout.readline()
                    self.assertTrue(line.strip(), "stdio server closed before a response")
                    return json.loads(line)

                call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}})
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
                process.stdin.flush()
                catalogue = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                self.assertEqual(len(catalogue["result"]["tools"]), 14)
                opened = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_task", "arguments": {"project_root": project, "request_original": "Conformance", "user_language": "en", "outcomes": [{"outcome": "Check the contract.", "acceptance": ["The contract is durable."], "constraints": [], "verification": ["Read the created task."]}], "constraints": ["No additional constraints."]}}})
                self.assertNotIn("error", opened)
                self.assertFalse(opened["result"].get("isError"), opened)
                task_ref = opened["result"]["structuredContent"]["task_ref"]
                read = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": task_ref, "view": "state"}}})
                self.assertNotIn("error", read)
                unreviewed_close = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "close_task", "arguments": {"task_ref": task_ref, "verdict": "ready"}}})
                self.assertTrue(unreviewed_close["result"]["isError"])
                self.assertEqual(
                    unreviewed_close["result"]["structuredContent"]["error"]["code"],
                    "closure_review_required",
                )
                incomplete_result = call({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "publish_result", "arguments": {"task_ref": task_ref}}})
                self.assertTrue(incomplete_result["result"]["isError"])
                publication_error = incomplete_result["result"]["structuredContent"]["error"]
                self.assertEqual(publication_error["code"], "validation_error")
                expected_missing = [
                    field for field in PUBLIC_TOOLS["publish_result"]["inputSchema"]["required"]
                    if field != "task_ref"
                ]
                self.assertEqual(publication_error["details"]["missing_fields"], expected_missing)
                self.assertIn("summary", publication_error["action"])
                self.assertIn("status", publication_error["action"])
                missing_mode = call({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "assess_governance", "arguments": {"task_ref": task_ref}}})
                self.assertTrue(missing_mode["result"]["isError"])
                self.assertEqual(missing_mode["result"]["structuredContent"]["error"]["code"], "validation_error")
                assessed = call({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "assess_governance", "arguments": {"task_ref": task_ref, "mode": "light"}}})
                self.assertNotIn("error", assessed)
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=5)

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
        self.assertIn("empty", contract["description"])

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

    def test_governance_assessment_advertises_coordinator_owned_explicit_depth(self) -> None:
        contract = PUBLIC_TOOLS["assess_governance"]
        description = contract["description"].lower()
        mode_description = contract["inputSchema"]["properties"]["mode"]["description"].lower()

        # The catalogue description must tell a worker that this is not a
        # worker-owned lifecycle operation, while telling the coordinator
        # what semantic choice is required.  The actual call shape remains
        # solely in the advertised schema.
        self.assertIn("coordinator-only", description)
        self.assertIn("semantic ownership", description)
        self.assertIn("explicit", description)
        self.assertIn("depth", description)
        self.assertIn("explicit coordinator depth selection", mode_description)

    def test_assignment_advertises_one_complete_instruction_field(self) -> None:
        contract = PUBLIC_TOOLS["open_assignment"]
        description = contract["description"].lower()
        instruction_description = contract["inputSchema"]["properties"]["instructions"]["description"].lower()

        self.assertIn("sole task-specific instruction field", description)
        self.assertIn("never invent supplementary fields", description)
        self.assertIn("complete task-specific worker instructions", instruction_description)
        self.assertIn("sole instruction channel", instruction_description)
        self.assertFalse(contract["inputSchema"]["additionalProperties"])

    def test_serialized_catalogue_contains_no_removed_callable_names(self) -> None:
        serialized = json.dumps({name: value["inputSchema"] for name, value in PUBLIC_TOOLS.items()}, sort_keys=True)
        for name in FORBIDDEN:
            self.assertNotIn(f'"{name}"', serialized)


if __name__ == "__main__":
    unittest.main()
