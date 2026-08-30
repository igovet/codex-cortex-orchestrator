"""First-call conformance for the registry-derived Cortex MCP boundary."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_INSTRUCTIONS, SERVER_VERSION  # noqa: E402
from cortex_runtime.mcp_api import (  # noqa: E402
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
    _success_tool_result,
    _validation_failure,
    _validate_schema,
    serve_stdio,
)
from cortex_runtime.delegation import validate_native_dispatch_projection  # noqa: E402
from cortex_runtime.worker_message import packaged_profile_names  # noqa: E402

EXPECTED_TOOLS = (
    "open_task", "read_task", "open_clarification", "record_clarification", "open_plan_review", "record_plan_review",
    "open_steering", "record_steering", "open_assignment", "consume_assignment_evidence",
    "publish_plan", "publish_result", "publish_documentation", "assess_governance", "close_task",
)


class PublicMcpFirstCallConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cortex-public-mcp-")
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(Path(self.tmp.name) / "home"), "CODEX_HOME": str(Path(self.tmp.name) / "codex"), "CORTEX_SOURCE_MODE": "1"}, clear=False)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_catalog_is_complete_single_page_and_concrete(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        result = response["result"]
        self.assertNotIn("nextCursor", result)
        self.assertEqual(tuple(tool["name"] for tool in result["tools"]), EXPECTED_TOOLS)
        response_line = next(line for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        self.assertLessEqual(len(response_line.encode()), 65536)
        advertised = {tool["name"]: tool for tool in result["tools"]}
        for name in EXPECTED_TOOLS:
            tool = PUBLIC_TOOLS[name]
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("allOf", schema)
            self.assertIn("outputSchema", tool)
            self.assertEqual(tool["outputSchema"]["type"], "object")
            self.assertEqual(advertised[name]["outputSchema"], tool["outputSchema"])
            self.assertNotIn("max_bytes", schema["properties"])
            self.assertNotIn("maxBytes", schema["properties"])
            self.assertNotIn("budget", schema["properties"])
            serialized_output = json.dumps(tool.get("outputSchema", {}), sort_keys=True)
            self.assertNotIn("idempotency_key", serialized_output)
            self.assertNotIn("retry_handle", serialized_output)

    def test_every_public_tool_has_one_closed_schema_complete_first_call(self) -> None:
        """The first semantic request is canonical; generic envelopes are rejected.

        This is deliberately table-driven across the complete catalogue.  It
        exercises the model-facing boundary, including the live failure mode
        where a planner appended an undeclared top-level ``evidence`` field to
        an otherwise valid publish_plan request.
        """
        refs = {
            "task_ref": "t_0123456789ab", "assignment_ref": "d_0123456789ab",
            "binding_ref": "cb_" + "a" * 32, "plan_ref": "r_0123456789ab",
        }
        plan_evidence = {
            "schema": "cortex/report/plan/v3", "summary": "Plan.",
            "verification": [], "risks": [], "deviations": [], "unresolved": [],
            "source_text": "", "scope": "Bounded.",
            "stages": [{"owner": "planner", "work": ["Plan."], "verification": ["Review."]}],
            "verification_facts": [{"state": "not_run", "summary": "No command."}],
            "documentation_impact": "No documentation impact.",
        }
        # Run3n regression: planner's first publication must not require a
        # post-implementation documentation verdict.
        plan_first_call = {key: value for key, value in plan_evidence.items() if key != "documentation_impact"}
        calls = {
            "open_task": {"task": {"project_root": str(self.project), "request_original": "Task.", "user_language": "en", "outcomes": [{"requirement": "Do task.", "acceptance": ["Task opens."]}], "constraints": ["Bounded."]}},
            "read_task": {"task_ref": refs["task_ref"]},
            "open_clarification": {"task_ref": refs["task_ref"], "prompt": "Question?", "prompt_language": "en"},
            "record_clarification": {"task_ref": refs["task_ref"], "binding_ref": refs["binding_ref"], "response_original": "Answered.", "user_language": "en"},
            "open_plan_review": {"task_ref": refs["task_ref"], "plan_ref": refs["plan_ref"], "prompt": "Approve?", "prompt_language": "en"},
            "record_plan_review": {"task_ref": refs["task_ref"], "binding_ref": refs["binding_ref"], "response_original": "Approved.", "user_language": "en", "outcome": "approve"},
            "open_steering": {"task_ref": refs["task_ref"], "prompt": "Change?", "prompt_language": "en"},
            "record_steering": {"task_ref": refs["task_ref"], "binding_ref": refs["binding_ref"], "response_original": "Changed.", "user_language": "en", "add": [], "retire_item_refs": ["o_0123456789ab"]},
            "open_assignment": {"task_ref": refs["task_ref"], "mission": {"role": "Planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Bounded.", "instructions": "Plan."}},
            "consume_assignment_evidence": {"assignment_ref": refs["assignment_ref"]},
            "publish_plan": {"continuation_ref": "c_" + "a" * 32, "assignment_ref": refs["assignment_ref"], "evidence": plan_first_call},
            "publish_result": {"continuation_ref": "c_" + "a" * 32, "assignment_ref": refs["assignment_ref"], "evidence": {"schema": "cortex/report/result/v3", "summary": "Done.", "verification": [], "risks": [], "deviations": [], "unresolved": [], "source_text": "", "outcome": "Done.", "changes": [], "verification_facts": [{"state": "not_run", "summary": "No command."}], "documentation_impact": "No documentation impact."}},
            "publish_documentation": {"continuation_ref": "c_" + "a" * 32, "assignment_ref": refs["assignment_ref"], "evidence": {"schema": "cortex/report/synthesis/v3", "summary": "No impact.", "verification": [], "risks": [], "deviations": [], "unresolved": [], "source_text": "", "findings": [], "recommendations": [], "documentation_impact": "No documentation impact."}},
            "assess_governance": {"task_ref": refs["task_ref"], "mode": "minimal"},
            "close_task": {"task_ref": refs["task_ref"], "verdict": "ready"},
        }
        self.assertEqual(tuple(calls), EXPECTED_TOOLS)
        generic_extras = ("evidence", "metadata", "idempotency", "token", "budget")
        for name in EXPECTED_TOOLS:
            with self.subTest(tool=name):
                schema = PUBLIC_TOOLS[name]["inputSchema"]
                _validate_schema(schema, calls[name])
                description = schema["description"].lower()
                self.assertIn("canonical closed request", description)
                self.assertIn("exactly the properties advertised", description)
                for forbidden_name in ("idempotency_key", "metadata", "budget"):
                    self.assertNotIn(forbidden_name, description)
                def schema_descriptions(node):
                    if isinstance(node, dict):
                        if isinstance(node.get("description"), str):
                            yield node["description"].lower()
                        for value in node.values():
                            yield from schema_descriptions(value)
                    elif isinstance(node, list):
                        for value in node:
                            yield from schema_descriptions(value)
                all_descriptions = " ".join(schema_descriptions(schema))
                for forbidden_name in ("idempotency_key", "metadata", "budget"):
                    self.assertNotIn(forbidden_name, all_descriptions)
                for extra in generic_extras:
                    if extra in schema["properties"]:
                        continue
                    with self.assertRaisesRegex(ValueError, "unsupported property"):
                        _validate_schema(schema, {**calls[name], extra: "invented"})
        # Regression for the live planner failure: ``evidence`` is the one
        # canonical publication property, but a generic sibling envelope is
        # still rejected by the closed publication-evidence object.
        with self.assertRaisesRegex(ValueError, "unsupported property"):
            _validate_schema(
                PUBLIC_TOOLS["publish_plan"]["inputSchema"],
                {**calls["publish_plan"], "evidence": {**plan_evidence, "metadata": {"retry": True}}},
            )

    def test_plan_publication_schema_places_sequence_and_acceptance_unambiguously(self) -> None:
        evidence = PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"]["evidence"]
        stage = evidence["properties"]["stages"]
        self.assertIn("dependency order", stage["items"]["description"])
        self.assertIn("acceptance checks", stage["items"]["properties"]["verification"]["description"].lower())
        self.assertIn("cross-stage acceptance checks", evidence["properties"]["verification"]["description"].lower())
        self.assertNotIn("evidence", evidence["properties"])
        self.assertIn("observable facts", evidence["properties"]["verification_facts"]["description"].lower())
        self.assertNotIn("closure evidence", evidence["properties"]["verification_facts"]["description"].lower())

        invalid = {
            "continuation_ref": "c_" + "a" * 32,
            "assignment_ref": "d_0123456789ab",
            "evidence": {
                "schema": "cortex/report/plan/v3", "summary": "Plan.",
                "verification": [], "risks": [], "deviations": [], "unresolved": [], "scope": "Bounded.",
                "stages": [{"owner": "planner", "work": ["Plan."], "verification": ["Review."]}],
                "verification_facts": [{"state": "not_run", "summary": "No command."}],
                "acceptance_checks": ["Invented sibling."],
            },
        }
        with self.assertRaises(ValueError) as raised:
            _validate_schema(PUBLIC_TOOLS["publish_plan"]["inputSchema"], invalid)
        failure = _validation_failure(raised.exception, tool_name="publish_plan", arguments=invalid)
        self.assertEqual(failure["details"]["path"], "$.evidence")
        self.assertEqual(failure["details"]["field"], "acceptance_checks")

    def test_task_read_advertises_server_formatted_plan_link_as_the_user_surface(self) -> None:
        contract = PUBLIC_TOOLS["read_task"]
        handles = contract["outputSchema"]["properties"]["handles"]
        runtime = contract["runtimeOutputSchema"]
        self.assertFalse(handles["additionalProperties"])
        self.assertNotIn("human_view", handles["properties"])
        self.assertIn("markdown_link", runtime["properties"]["human_view"]["properties"])
        self.assertNotIn("approval_view", handles["properties"])
        self.assertNotIn("path", runtime["properties"]["human_view"]["properties"])
        self.assertNotIn("path", runtime["properties"]["approval_view"]["properties"])
        self.assertIn(
            "copy its server-formatted markdown_link byte-for-byte",
            runtime["properties"]["handles"]["description"],
        )
        self.assertIn("never render or reconstruct its host-private path", contract["description"].lower())

    def test_every_semantic_operation_advertises_only_its_own_callable_handles(self) -> None:
        expected = {
            "open_task": {"task_ref"},
            "read_task": {"task_ref", "report_refs", "after_sequence"},
            "open_clarification": {"task_ref", "binding_ref"},
            "open_plan_review": {"task_ref", "binding_ref"},
            "open_steering": {"task_ref", "binding_ref"},
            "record_clarification": {"task_ref", "binding_ref", "decision_ref"},
            "record_plan_review": {"task_ref", "binding_ref", "decision_ref"},
            "record_steering": {"task_ref", "binding_ref", "decision_ref"},
            "open_assignment": {"assignment_ref"},
            "consume_assignment_evidence": {"assignment_ref", "continuation_ref"},
            "publish_plan": {"report_ref"},
            "publish_result": {"report_ref"},
            "publish_documentation": {"report_ref"},
            "assess_governance": {"task_ref"},
            "close_task": {"task_ref"},
        }
        self.assertEqual(set(PUBLIC_TOOLS), set(expected))
        for name, fields in expected.items():
            with self.subTest(operation=name):
                handles = PUBLIC_TOOLS[name]["outputSchema"]["properties"]["handles"]
                self.assertFalse(handles["additionalProperties"])
                self.assertEqual(set(handles["properties"]), fields)

    def test_assignment_profile_schema_advertises_first_attempt_admission_classes(self) -> None:
        contract = PUBLIC_TOOLS["open_assignment"]
        profile = contract["inputSchema"]["properties"]["mission"]["properties"]["profile_name"]
        classes = profile["oneOf"]
        self.assertEqual(len(classes), 3)
        names = [name for item in classes for name in item["enum"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(packaged_profile_names()))
        descriptions = " ".join(item["description"] for item in classes)
        self.assertIn("light/full governance", descriptions)
        self.assertIn("approved planner evidence", descriptions)
        self.assertIn("Non-owning review", descriptions)
        self.assertIn("Planning profile", descriptions)
        self.assertIn("Before the first attempt", contract["description"])
        self.assertIn("governance must remain minimal from the outset", contract["description"])

    def test_initialize_negotiates_current_and_legacy_core_versions(self) -> None:
        self.assertEqual(MCP_SUPPORTED_PROTOCOL_VERSIONS, ("2025-11-25", "2025-06-18"))
        for requested in MCP_SUPPORTED_PROTOCOL_VERSIONS:
            requests = [{
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": requested, "capabilities": {}, "clientInfo": {"name": "negotiation", "version": "1"}},
            }]
            output = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO(json.dumps(requests[0]) + "\n")), mock.patch("sys.stdout", output):
                serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
            response = json.loads(output.getvalue().splitlines()[0])
            self.assertEqual(response["result"]["protocolVersion"], requested)
            self.assertEqual(response["result"]["capabilities"], {"tools": {}})

    def test_initialize_counteroffers_newest_core_version_for_unknown_version(self) -> None:
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2099-01-01", "capabilities": {}, "clientInfo": {"name": "future", "version": "1"}},
        }
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(response["result"]["protocolVersion"], MCP_SUPPORTED_PROTOCOL_VERSIONS[0])
        self.assertEqual(response["result"]["capabilities"], {"tools": {}})

    def test_advertised_inputs_never_borrow_legacy_or_sibling_tool_semantics(self) -> None:
        serialized = json.dumps(
            {name: contract["inputSchema"] for name, contract in PUBLIC_TOOLS.items()},
            sort_keys=True,
        )
        for stale_term in (
            "create_task",
            "submit_report",
            "inspect_task",
            "read_reports",
            "user_request_original",
        ):
            self.assertNotIn(stale_term, serialized)

        open_task = PUBLIC_TOOLS["open_task"]
        self.assertIn("never inside a second nested task object", open_task["description"])
        task_schema = open_task["inputSchema"]["properties"]["task"]
        self.assertIn("do not add another task wrapper", task_schema["description"])
        self.assertEqual(
            task_schema["required"],
            ["project_root", "request_original", "user_language", "outcomes", "constraints"],
        )

    def test_success_structured_content_is_serialized_in_text_from_same_projection(self) -> None:
        value = {"handles": {"task_ref": "t_0123456789ab"}, "replayed": False, "nested": [1, "x"]}
        result = _success_tool_result(value)
        self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        self.assertTrue(result["content"][1]["text"].startswith('{"handles":'))

    def test_ready_view_is_the_leading_user_facing_content_block(self) -> None:
        link = "[Open plan revision](/verified/plan.md)"
        result = _success_tool_result({
            "handles": {"task_ref": "t_0123456789ab"},
            "approval_view": {"status": "ready", "markdown_link": link},
        })
        self.assertEqual(result["content"][0], {"type": "text", "text": link})
        self.assertEqual(result["structuredContent"]["approval_view"]["markdown_link"], link)
        self.assertNotIn("path", result["structuredContent"]["approval_view"])
        self.assertEqual(json.loads(result["content"][1]["text"]), result["structuredContent"])

    def test_stdio_success_never_exposes_legacy_caller_receipt_identity(self) -> None:
        """The semantic transport strips legacy storage receipts on real calls."""
        arguments = {"task": {
            "project_root": str(self.project),
            "request_original": "Create one bounded task.", "user_language": "en",
            "outcomes": [{"requirement": "Preserve the semantic boundary.", "acceptance": ["The task opens once."]}],
            "constraints": ["Keep receipt identity server-owned."],
        }}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_task", "arguments": arguments}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        structured = response["result"]["structuredContent"]
        serialized = json.dumps(response["result"], sort_keys=True)
        self.assertNotIn("idempotency_key", serialized)
        self.assertNotIn("retry_handle", serialized)
        self.assertRegex(structured["handles"]["task_ref"], r"^t_[0-9a-f]{12}$")

    def test_open_task_first_call_returns_exact_task_handle(self) -> None:
        task_schema = PUBLIC_TOOLS["open_task"]["inputSchema"]["properties"]["task"]
        self.assertNotIn("objective", task_schema["properties"])
        self.assertNotIn("objective", task_schema["required"])
        arguments = {"task": {
            "project_root": str(self.project),
            "request_original": "Create one bounded task.", "user_language": "en",
            "outcomes": [{"requirement": "Preserve the task contract.", "acceptance": ["The task opens successfully."]}],
            "constraints": ["Use one task."],
        }}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_task", "arguments": arguments}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        self.assertNotIn("error", response)
        structured = response["result"]["structuredContent"]
        _validate_schema(PUBLIC_TOOLS["open_task"]["outputSchema"], structured)
        self.assertRegex(structured["handles"]["task_ref"], r"^t_[0-9a-f]{12}$")

    def test_task_identity_is_connection_bound_without_ledger_recency_guessing(self) -> None:
        task_arguments = {"task": {
            "project_root": str(self.project),
            "request_original": "Bind one exact transport task.", "user_language": "en",
            "outcomes": [{"requirement": "Keep task identity server-owned after opening.", "acceptance": ["A same-connection read succeeds without retranscribing the locator."]}],
            "constraints": ["Never infer the newest ledger task."],
        }}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_task", "arguments": task_arguments}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_task", "arguments": {}}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        responses = {value["id"]: value for value in map(json.loads, output.getvalue().splitlines()) if "id" in value}
        opened_ref = responses[2]["result"]["structuredContent"]["handles"]["task_ref"]
        self.assertFalse(responses[3]["result"]["isError"])
        self.assertEqual(responses[3]["result"]["structuredContent"]["handles"]["task_ref"], opened_ref)

        # A new stdio process has no connection context. It must not select
        # the only/newest task merely because that task exists in the ledger.
        fresh_requests = [
            {"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "read_task", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": opened_ref}}},
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "read_task", "arguments": {}}},
        ]
        fresh_output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in fresh_requests) + "\n")), mock.patch("sys.stdout", fresh_output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        fresh = {value["id"]: value for value in map(json.loads, fresh_output.getvalue().splitlines()) if "id" in value}
        self.assertTrue(fresh[11]["result"]["isError"])
        self.assertEqual(fresh[11]["result"]["structuredContent"]["error"]["details"]["field"], "task_ref")
        self.assertFalse(fresh[12]["result"]["isError"])
        self.assertFalse(fresh[13]["result"]["isError"])

        task_scoped = [
            contract for name, contract in PUBLIC_TOOLS.items()
            if name != "open_task" and "task_ref" in contract["inputSchema"]["properties"]
        ]
        self.assertTrue(task_scoped)
        for contract in task_scoped:
            self.assertNotIn("task_ref", contract["inputSchema"]["required"])
            self.assertIn("server-owned context", contract["description"])

    def test_open_task_rejects_an_outcome_without_its_acceptance_before_mutation(self) -> None:
        arguments = {"task": {
            "project_root": str(self.project),
            "request_original": "Reject incomplete task.", "user_language": "en",
            "outcomes": [{"requirement": "Keep acceptance paired."}],
            "constraints": ["Do not weaken completeness."],
        }}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "conformance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_task", "arguments": arguments}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["error"]["code"], "validation_error")
        self.assertEqual(response["result"]["structuredContent"]["error"]["details"]["field"], "acceptance")

    def test_semantic_producer_consumer_handle_matrix_is_named_and_scalar(self) -> None:
        """Every advertised next-call handle has one matching consumer field."""
        producers = {
            "open_task": ("task_ref", ("read_task", "open_clarification", "open_plan_review", "open_steering", "open_assignment", "assess_governance", "close_task")),
            "open_assignment": ("assignment_ref", ("consume_assignment_evidence", "publish_plan", "publish_result", "publish_documentation")),
            "open_clarification": ("binding_ref", ("record_clarification",)),
            "open_plan_review": ("binding_ref", ("record_plan_review",)),
            "open_steering": ("binding_ref", ("record_steering",)),
            "consume_assignment_evidence": ("cursor", ("consume_assignment_evidence",)),
        }
        for producer, (handle, consumers) in producers.items():
            with self.subTest(producer=producer, handle=handle):
                emitted = PUBLIC_TOOLS[producer]["outputSchema"]["properties"].get("handles", {}).get("properties", {}).get(handle)
                if emitted is not None:
                    self.assertIn(emitted["type"], ("string", "integer"))
                for consumer in consumers:
                    consumer_schema = PUBLIC_TOOLS[consumer].get("_runtimeInputSchema", PUBLIC_TOOLS[consumer]["inputSchema"])
                    self.assertIn(handle, consumer_schema["properties"])
        decision = PUBLIC_TOOLS["record_clarification"]["inputSchema"]
        def assert_host_callable_schema(node: object) -> None:
            if isinstance(node, dict):
                self.assertFalse({"anyOf", "oneOf", "allOf"}.intersection(node))
                if "required" in node:
                    self.assertTrue(node["required"])
                for value in node.values():
                    assert_host_callable_schema(value)
            elif isinstance(node, list):
                for value in node:
                    assert_host_callable_schema(value)
        assert_host_callable_schema(decision)
        self.assertIn("binding_ref", decision["required"])
        self.assertNotIn("binding", decision["properties"])
        self.assertEqual(decision["properties"]["binding_ref"]["type"], "string")
        for name in ("record_clarification", "record_plan_review", "record_steering"):
            self.assertIn(name, PUBLIC_TOOLS)
        clarification = PUBLIC_TOOLS["open_clarification"]["inputSchema"]
        self.assertNotIn("subject_ref", clarification["properties"])
        self.assertNotIn("subject_ref", clarification["required"])
        self.assertIn("server derives the task subject", PUBLIC_TOOLS["open_clarification"]["description"])

    def test_stdio_decision_binding_consumes_replays_and_conflicts(self) -> None:
        base = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "binding", "version": "1"}}}
        task = {"task": {"project_root": str(self.project), "request_original": "Bind decision.", "user_language": "en", "outcomes": [{"requirement": "Bind.", "acceptance": ["Decision persists."]}], "constraints": ["Exact."]}}
        # A real incremental stdio session keeps all public calls on one MCP
        # process; later request bodies are built only from prior receipts.
        env = dict(os.environ) | {"PYTHONDONTWRITEBYTECODE": "1", "CORTEX_SOURCE_MODE": "1"}
        env.pop("PYTHONPATH", None)
        process = subprocess.Popen([sys.executable, str(SCRIPTS / "cortex.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        def call(request: dict) -> dict:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request) + "\n"); process.stdin.flush()
            return json.loads(process.stdout.readline())
        try:
            initialized = call(base)
            self.assertFalse(initialized["result"]["serverInfo"]["parityVerified"])
            assert process.stdin is not None
            # Notifications never have a response; reading here would consume
            # the first response to the next public call.
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"); process.stdin.flush()
            listed = call({"jsonrpc": "2.0", "id": 19, "method": "tools/list", "params": {}})["result"]["tools"]
            listed_clarification = next(tool for tool in listed if tool["name"] == "open_clarification")
            self.assertNotIn("subject_ref", listed_clarification["inputSchema"]["properties"])
            self.assertIn("server derives the task subject", listed_clarification["description"])
            listed_record_decision = next(tool for tool in listed if tool["name"] == "record_clarification")
            self.assertEqual(
                set(listed_record_decision["outputSchema"]["properties"]["handles"]["properties"]),
                {"task_ref", "binding_ref", "decision_ref"},
            )
            self.assertEqual(
                set(listed_clarification["outputSchema"]["properties"]["handles"]["properties"]),
                {"task_ref", "binding_ref"},
            )
            self.assertNotIn("steering_delta", json.dumps(listed_record_decision["inputSchema"]))
            for narrow_name, forbidden in {
                "record_clarification": {"outcome", "add", "retire_item_refs"},
                "record_plan_review": {"add", "retire_item_refs"},
                "record_steering": {"outcome"},
            }.items():
                narrow = next(tool for tool in listed if tool["name"] == narrow_name)
                props = narrow["inputSchema"]["properties"]
                self.assertTrue(narrow["inputSchema"]["additionalProperties"] is False)
                self.assertTrue(forbidden.isdisjoint(props))
            opened = call({"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "open_task", "arguments": task}})["result"]["structuredContent"]
            ref = opened["handles"]["task_ref"]
            reread = call({"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": ref}}})["result"]["structuredContent"]
            self.assertEqual(reread["handles"]["task_ref"], ref)
            assignment_response = call({"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "open_assignment", "arguments": {"task_ref": ref, "mission": {"role": "planner", "profile_name": "planner", "goal": "Read no predecessor reports.", "constraints": "One bounded test.", "instructions": "Inspect only assigned context."}}}})
            self.assertNotIn("error", assignment_response, assignment_response)
            self.assertFalse(assignment_response["result"].get("isError"), assignment_response)
            assignment = assignment_response["result"]["structuredContent"]
            self.assertEqual(set(assignment), {"assignment_ref", "handles", "native_dispatch", "replayed", "relations"})
            self.assertFalse(assignment["replayed"])
            self.assertEqual(assignment["relations"], {})
            replay_response = call({"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"name": "open_assignment", "arguments": {"task_ref": ref, "mission": {"role": "planner", "profile_name": "planner", "goal": "Prepare the same bounded plan.", "constraints": "One bounded test.", "instructions": "Inspect the same assigned context."}}}})
            self.assertNotIn("error", replay_response, replay_response)
            replay_assignment = replay_response["result"]["structuredContent"]
            self.assertTrue(replay_assignment["replayed"])
            self.assertEqual(replay_assignment["assignment_ref"], assignment["assignment_ref"])
            self.assertEqual(set(assignment["handles"]), {"assignment_ref"})
            self.assertFalse(PUBLIC_TOOLS["open_assignment"]["runtimeOutputSchema"]["additionalProperties"])
            self.assertLess(len(json.dumps(assignment, ensure_ascii=False).encode()), 24 * 1024)
            assignment_ref = assignment["handles"]["assignment_ref"]
            # The native worker receives one compact server-issued host
            # projection. There is no parallel semantic brief from which the
            # coordinator could reconstruct a different message.
            native = assignment["native_dispatch"]
            self.assertEqual(set(native), {"fork_turns", "message", "task_name"})
            self.assertNotIn("dispatch_brief", assignment)
            self.assertNotIn("native_arguments", assignment)
            self.assertIn("Mandatory bootstrap gate", native["message"])
            self.assertEqual(native["fork_turns"], "none")
            self.assertRegex(native["task_name"], r"^planner_d_[0-9a-f]{12}$")
            # Exercise the real stdio structured result through the same
            # closed seam a native host uses. The fake host captures kwargs
            # exactly; it is not allowed to rebuild them from the semantic
            # brief or inject server-selected routing.
            native_args = validate_native_dispatch_projection(native, assignment_ref=assignment_ref)
            host_spawn_calls = []
            def fake_host_spawn(**kwargs):
                host_spawn_calls.append(dict(kwargs))
            fake_host_spawn(**native_args)
            self.assertEqual(host_spawn_calls, [native_args])
            native_message = native["message"]
            self.assertEqual(json.dumps(assignment, ensure_ascii=False).count(json.dumps(native_message, ensure_ascii=False)), 1)
            self.assertLessEqual(len(native_message.encode("utf-8")), 16 * 1024)
            self.assertNotIn("wb_", native_message)
            authority_free = json.loads(json.dumps(assignment))
            authority_free["native_dispatch"]["message"] = ""
            self.assertNotIn("wb_", json.dumps(authority_free))
            wrong_relation = call({"jsonrpc": "2.0", "id": 221, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": assignment_ref}}})["result"]
            self.assertTrue(wrong_relation["isError"])
            self.assertEqual(wrong_relation["structuredContent"]["error"]["code"], "validation_error")
            self.assertEqual(wrong_relation["structuredContent"]["error"]["details"]["field"], "task_ref")
            correct_relation = call({"jsonrpc": "2.0", "id": 222, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": ref}}})["result"]
            self.assertFalse(correct_relation["isError"])
            host_sequence = ["native_spawn"]
            consumed = call({"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"name": "consume_assignment_evidence", "arguments": {"assignment_ref": assignment_ref}}})["result"]["structuredContent"]
            host_sequence.append("child_first_consume")
            self.assertEqual(host_sequence, ["native_spawn", "child_first_consume"])
            self.assertEqual(consumed["assignment_ref"], assignment_ref)
            self.assertEqual(set(consumed["handles"]), {"assignment_ref", "continuation_ref"})
            self.assertEqual(consumed["evidence"]["state"], "none")
            self.assertGreaterEqual(consumed["effective_contract"]["revision"], 1)
            consumed_refs = [item["item_ref"] for item in consumed["effective_contract"]["planning_items"]]
            self.assertTrue(consumed_refs)
            self.assertEqual(consumed["assignment_context"]["profile_name"], "planner")
            continuation = consumed["continuation_ref"]
            self.assertEqual(consumed["predecessor_evidence"], [])
            self.assertEqual(consumed["decision_evidence"], [])
            self.assertNotIn('"content"', json.dumps(consumed, ensure_ascii=False))
            replayed_consumed = call({"jsonrpc": "2.0", "id": 230, "method": "tools/call", "params": {"name": "consume_assignment_evidence", "arguments": {"assignment_ref": assignment_ref}}})["result"]["structuredContent"]
            self.assertEqual(replayed_consumed, consumed)
            documentation_impact_schema = PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"]["evidence"]["properties"]["documentation_impact"]
            self.assertEqual(documentation_impact_schema["type"], "string")
            self.assertGreaterEqual(documentation_impact_schema["minLength"], 1)
            self.assertNotIn("properties", documentation_impact_schema)
            for publication_name in ("publish_plan", "publish_result", "publish_documentation"):
                self.assertNotIn("contract_coverage", PUBLIC_TOOLS[publication_name]["inputSchema"]["properties"]["evidence"]["properties"])
            stage_schema = PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"]["evidence"]["properties"]["stages"]["items"]
            _validate_schema(stage_schema, {"order": 1, "dependencies": [], "owner": "planner", "work": ["Plan."], "verification": ["Review."]})
            _validate_schema(stage_schema, {"order": 0, "dependencies": [0], "owner": "planner", "work": ["Plan."], "verification": ["Review."]})
            self.assertNotIn("order", stage_schema["required"])
            self.assertNotIn("dependencies", stage_schema["required"])
            incomplete = call({"jsonrpc": "2.0", "id": 231, "method": "tools/call", "params": {"name": "publish_plan", "arguments": {"continuation_ref": continuation, "assignment_ref": assignment_ref, "evidence": {"schema": "cortex/report/plan/v3"}}}})["result"]
            self.assertTrue(incomplete["isError"])
            self.assertEqual(incomplete["structuredContent"]["error"]["code"], "validation_error")
            self.assertIn(incomplete["structuredContent"]["error"]["details"]["field"], {"summary", "evidence"})
            def invoke(identifier: int, name: str, arguments: dict) -> dict:
                response = call({"jsonrpc": "2.0", "id": identifier, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                self.assertNotIn("error", response)
                result = response["result"]
                self.assertFalse(result.get("isError"), result)
                return result["structuredContent"]

            clarification = invoke(24, "open_clarification", {"task_ref": ref, "prompt": "Confirm.", "prompt_language": "en"})
            self.assertEqual(clarification["next_action"], "record_clarification")
            clarification_binding = clarification["handles"]["binding_ref"]
            self.assertEqual(clarification_binding, clarification["binding_ref"])
            wrong_record = call({"jsonrpc": "2.0", "id": 241, "method": "tools/call", "params": {"name": "record_clarification", "arguments": {"task_ref": ref, "binding_ref": clarification_binding, "response_original": "yes", "user_language": "en", "outcome": "approve"}}})["result"]
            self.assertTrue(wrong_record["isError"])
            self.assertEqual(wrong_record["structuredContent"]["error"]["details"]["field"], "outcome")
            clarification_record = invoke(25, "record_clarification", {"task_ref": ref, "binding_ref": clarification_binding, "response_original": "yes", "user_language": "en"})
            self.assertEqual(clarification_record["handles"]["binding_ref"], clarification_binding)
            self.assertRegex(clarification_record["handles"]["decision_ref"], r"^u_[0-9a-f]{12}$")

            plan = invoke(26, "publish_plan", {"continuation_ref": continuation, "assignment_ref": assignment_ref, "evidence": {
                "schema": "cortex/report/plan/v3", "summary": "Complete plan.", "scope": "Complete contract.",
                "stages": [{"owner": "planner", "work": ["Map every requirement."], "verification": ["Check every item."]}],
                "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}],
                "documentation_impact": "No documentation changed; no affected paths.",
            }})
            published_relation = plan["approval_view"]
            self.assertEqual(published_relation["status"], "ready")
            self.assertEqual(published_relation["report_content_digest"], plan["report"]["content_digest"])
            self.assertIsInstance(published_relation["approval_handle"], str)
            self.assertEqual(
                set(plan["report"]),
                {"report_ref", "report_type", "status", "semantic_status", "content_digest"},
            )
            self.assertEqual(
                set(published_relation),
                {"report_content_digest", "status", "source_sequence", "content_digest", "approval_handle", "report_ref", "delegation_ref"},
            )
            self.assertEqual(plan["report"]["report_ref"], published_relation["report_ref"])
            self.assertEqual(
                set(plan["handles"]),
                {"report_ref"},
            )
            def keys(value):
                if isinstance(value, dict):
                    return set(value).union(*(keys(item) for item in value.values()))
                if isinstance(value, list):
                    return set().union(*(keys(item) for item in value)) if value else set()
                return set()
            self.assertFalse({"decision_id", "task_id", "subject_id", "report_id", "delegation_id", "operation_id"} & keys(plan))
            self.assertIsInstance(published_relation["content_digest"], str)
            self.assertIsInstance(published_relation["source_sequence"], int)
            # Materialize and verify the server-owned plan view before opening
            # a review relation. The review binding captures this exact view.
            task_view = invoke(27, "read_task", {"task_ref": ref})
            self.assertIn("markdown_link", task_view["human_view"])
            self.assertNotIn("path", task_view["human_view"])
            plan_review_response = call({"jsonrpc": "2.0", "id": 28, "method": "tools/call", "params": {"name": "open_plan_review", "arguments": {"task_ref": ref, "plan_ref": plan["handles"]["report_ref"], "prompt": "Review plan.", "prompt_language": "en"}}})["result"]
            self.assertFalse(plan_review_response.get("isError"), plan_review_response)
            plan_review = plan_review_response["structuredContent"]
            self.assertEqual(plan_review_response["content"][0]["text"], plan_review["approval_view"]["markdown_link"])
            plan_binding = plan_review["handles"]["binding_ref"]
            self.assertEqual(plan_review["approval_view"]["status"], "ready")
            self.assertRegex(plan_review["approval_view"]["markdown_link"], r"^\[[^\]]+\]\([^\n]+\)$")
            self.assertNotIn("path", plan_review["approval_view"])
            plan_record = invoke(29, "record_plan_review", {"task_ref": ref, "binding_ref": plan_binding, "outcome": "cancel", "response_original": "Cancel", "user_language": "en"})
            self.assertEqual(plan_record["handles"]["binding_ref"], plan_binding)

            # The compact plan locator must be resolved at the storage boundary
            # before the aggregate sees its canonical subject ID.
            subject_binding = invoke(30, "open_clarification", {"task_ref": ref, "prompt": "Clarify task scope.", "prompt_language": "en"})["handles"]["binding_ref"]
            subject_record = invoke(31, "record_clarification", {"task_ref": ref, "binding_ref": subject_binding, "response_original": "Scope confirmed.", "user_language": "en"})
            self.assertEqual(subject_record["handles"]["binding_ref"], subject_binding)

            steering_opened = invoke(32, "open_steering", {"task_ref": ref, "assignment_ref": assignment_ref, "prompt": "Add verification?", "prompt_language": "en"})
            self.assertEqual(steering_opened["next_action"], "record_steering")
            steering_binding = steering_opened["handles"]["binding_ref"]
            steering_record = invoke(33, "record_steering", {"task_ref": ref, "binding_ref": steering_binding, "response_original": "Yes", "user_language": "en", "add": [{"category": "verification", "text": "Run public handle test."}], "retire_item_refs": []})
            self.assertEqual(steering_record["handles"]["binding_ref"], steering_binding)
            replay = invoke(34, "record_steering", {"task_ref": ref, "binding_ref": steering_binding, "response_original": "Yes", "user_language": "en", "add": [{"category": "verification", "text": "Run public handle test."}], "retire_item_refs": []})
            self.assertTrue(replay["replayed"])

            documentation_assignment = invoke(35, "open_assignment", {
                "task_ref": ref,
                "input_decision_refs": [subject_record["handles"]["decision_ref"]],
                "mission": {
                    "role": "Documentation reviewer", "profile_name": "technical_writer",
                    "goal": "Assess documentation impact.", "constraints": "Read-only assessment.",
                    "instructions": "Publish one complete documentation synthesis.",
                },
            })
            documentation_consumed = invoke(36, "consume_assignment_evidence", {
                "assignment_ref": documentation_assignment["handles"]["assignment_ref"],
            })
            documentation_message = documentation_assignment["native_dispatch"]["message"]
            self.assertNotIn("Scope confirmed.", documentation_message)
            self.assertEqual(documentation_consumed["decision_evidence"], [{
                "decision_ref": subject_record["handles"]["decision_ref"],
                "decision_type": "clarification", "subject_type": "task",
                "subject_digest": documentation_consumed["decision_evidence"][0]["subject_digest"],
                "prompt": "Clarify task scope.", "response_original": "Scope confirmed.",
                "user_language": "en",
            }])
            documentation = invoke(37, "publish_documentation", {
                "continuation_ref": documentation_consumed["continuation_ref"],
                "assignment_ref": documentation_assignment["handles"]["assignment_ref"],
                "evidence": {
                    "schema": "cortex/report/synthesis/v3", "summary": "No documentation change is required.",
                    "findings": ["The existing instructions cover the verified behavior."],
                    "recommendations": [], "verification": ["Reviewed the declared documentation scope."],
                    "risks": [], "deviations": [], "unresolved": [],
                    "documentation_impact": "No documentation change is required.",
                },
            })
            self.assertFalse(documentation["replayed"])
            self.assertEqual(documentation["report"]["report_type"], "synthesis")
            self.assertEqual(set(documentation["handles"]), {"report_ref"})
            self.assertRegex(documentation["handles"]["report_ref"], r"^r_[0-9a-f]{12}$")
        finally:
            process.terminate()
            process.wait(timeout=5)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.close()
            process.stdout.close()

    def test_stdio_internal_dispatch_error_is_safe_non_retryable_tool_error(self) -> None:
        def explode(*, task_ref: str, after_sequence: int = 0) -> dict:
            raise RuntimeError("private controlled exception")

        tools = {name: dict(contract) for name, contract in PUBLIC_TOOLS.items()}
        tools["read_task"]["handler"] = explode
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "failure", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": "t_000000000000"}}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=tools, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["code"], "ledger_error")
        self.assertFalse(error["retryable"])
        self.assertIn("Do not blindly repeat", error["action"])
        self.assertNotIn("private controlled exception", json.dumps(response))

    def test_open_assignment_rejects_server_owned_routing_input_before_mutation(self) -> None:
        """Routing recommendations are output-only, never mission input."""
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "routing", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_task", "arguments": {"task": {"project_root": str(self.project), "request_original": "Routing boundary.", "user_language": "en", "outcomes": [{"requirement": "Reject routing input.", "acceptance": ["No assignment mutation."]}], "constraints": ["Exact."]}}}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        task = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)["result"]["structuredContent"]
        task_ref = task["handles"]["task_ref"]
        request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_assignment", "arguments": {"task_ref": task_ref, "mission": {"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Bounded.", "instructions": "Inspect."}, "recommended_model": "gpt-5.6-luna"}}}
        wire = [requests[0], requests[1], request]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in wire) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        result = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 3)["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "validation_error")
        self.assertEqual(result["structuredContent"]["error"]["details"]["field"], "recommended_model")

    def test_open_assignment_first_call_contract_does_not_inherit_replay_metadata(self) -> None:
        """A model-facing assignment opening is exactly the advertised schema.

        This guards the live failure where generic recovery prose caused the
        model to invent a transport field on an otherwise valid first call.
        Recovery metadata belongs only to tools that advertise it; it must not
        be inferred from another operation's receipt or description.
        """
        schema = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        valid = {
            "task_ref": "t_0123456789ab",
            "mission": {
                "role": "planner",
                "profile_name": "planner",
                "goal": "Prepare a bounded plan.",
                "constraints": "Stay within the assigned project scope.",
                "instructions": "Return one complete plan report.",
            },
        }
        _validate_schema(schema, valid)
        with self.assertRaisesRegex(ValueError, "unsupported property"):
            _validate_schema(schema, {**valid, "idempotency_key": "invented-transport-field"})

        skill = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md"
        skill_text = skill.read_text(encoding="utf-8")
        dispatch_start = skill_text.index("Form every delegation request")
        dispatch_end = skill_text.index("Healthy writes are preferred", dispatch_start)
        dispatch_guidance = skill_text[dispatch_start:dispatch_end]
        self.assertRegex(dispatch_guidance, r"solely\s+from that operation's live advertised\s+input schema")
        self.assertNotRegex(dispatch_guidance, r"(?i)idempot|retry|replay|metadata")


if __name__ == "__main__":
    unittest.main()
