"""First-call conformance for the ten-tool semantic Cortex MCP boundary."""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_INSTRUCTIONS, SERVER_VERSION  # noqa: E402
from cortex_runtime.mcp_api import MAX_PHYSICAL_JSONL_FRAME_BYTES, _WIRE_OUTPUT_SCHEMA, _validate_schema, serve_stdio  # noqa: E402

EXPECTED_TOOLS = (
    "open_task", "read_task", "open_decision", "open_assignment", "consume_assignment_evidence",
    "publish_plan", "publish_result", "publish_documentation", "record_decision",
    "assess_governance", "close_task",
)


class PublicMcpFirstCallConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cortex-public-mcp-")
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(Path(self.tmp.name) / "home"), "CODEX_HOME": str(Path(self.tmp.name) / "codex")}, clear=False)
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
        self.assertLessEqual(len(response_line.encode()), MAX_PHYSICAL_JSONL_FRAME_BYTES)
        for name in EXPECTED_TOOLS:
            tool = PUBLIC_TOOLS[name]
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("allOf", schema)
            self.assertNotIn("anyOf", schema)
            self.assertNotIn("oneOf", schema)
            self.assertIn("outputSchema", tool)
            self.assertEqual(tool["outputSchema"]["type"], "object")
            self.assertNotIn("max_bytes", schema["properties"])
            self.assertNotIn("maxBytes", schema["properties"])
            self.assertNotIn("budget", schema["properties"])

    def test_open_task_first_call_returns_exact_task_handle(self) -> None:
        arguments = {
            "project_root": str(self.project), "objective": "Create one bounded task.",
            "user_request_original": "Create one bounded task.", "user_language": "en",
            "requirements": ["Preserve the task contract."], "constraints": ["Use one task."],
            "acceptance_criteria": ["The task opens successfully."],
        }
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
        _validate_schema(_WIRE_OUTPUT_SCHEMA, structured)
        self.assertRegex(structured["handles"]["task_ref"], r"^t_[0-9a-f]{12}$")

    def test_semantic_producer_consumer_handle_matrix_is_named_and_scalar(self) -> None:
        """Every advertised next-call handle has one matching consumer field."""
        producers = {
            "open_task": ("task_ref", ("read_task", "open_decision", "open_assignment", "assess_governance", "close_task")),
            "open_assignment": ("assignment_ref", ("consume_assignment_evidence", "publish_plan", "publish_result", "publish_documentation")),
            "open_decision": ("binding_ref", ("record_decision",)),
            "consume_assignment_evidence": ("cursor", ("consume_assignment_evidence",)),
        }
        for producer, (handle, consumers) in producers.items():
            with self.subTest(producer=producer, handle=handle):
                self.assertIn(handle, _WIRE_OUTPUT_SCHEMA["properties"]["handles"]["properties"])
                emitted = _WIRE_OUTPUT_SCHEMA["properties"]["handles"]["properties"][handle]
                self.assertIn(emitted["type"], ("string", "integer"))
                for consumer in consumers:
                    self.assertIn(handle, PUBLIC_TOOLS[consumer]["inputSchema"]["properties"])
        decision = PUBLIC_TOOLS["record_decision"]["inputSchema"]
        self.assertIn("binding_ref", decision["required"])
        self.assertNotIn("binding", decision["properties"])
        self.assertEqual(decision["properties"]["binding_ref"]["type"], "string")

    def test_stdio_decision_binding_consumes_replays_and_conflicts(self) -> None:
        base = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "binding", "version": "1"}}}
        task = {"project_root": str(self.project), "objective": "Bind decision.", "user_request_original": "Bind decision.", "user_language": "en", "requirements": ["Bind."], "constraints": ["Exact."], "acceptance_criteria": ["Decision persists."]}
        # A real incremental stdio session keeps all public calls on one MCP
        # process; later request bodies are built only from prior receipts.
        env = dict(os.environ) | {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SCRIPTS)}
        process = subprocess.Popen([sys.executable, str(SCRIPTS / "cortex.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        def call(request: dict) -> dict:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request) + "\n"); process.stdin.flush()
            return json.loads(process.stdout.readline())
        try:
            call(base)
            assert process.stdin is not None
            # Notifications never have a response; reading here would consume
            # the first response to the next public call.
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"); process.stdin.flush()
            opened = call({"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "open_task", "arguments": task}})["result"]["structuredContent"]
            ref = opened["handles"]["task_ref"]
            reread = call({"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": ref}}})["result"]["structuredContent"]
            self.assertEqual(reread["handles"]["task_ref"], ref)
            assignment = call({"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "open_assignment", "arguments": {"task_ref": ref, "objective": "Read no predecessor reports.", "role": "planner", "profile_name": "planner", "scope": "One bounded test.", "instructions": "Inspect only assigned context.", "model": "gpt-5.6-luna", "reasoning_effort": "high"}}})["result"]["structuredContent"]
            assignment_ref = assignment["handles"]["assignment_ref"]
            consumed = call({"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"name": "consume_assignment_evidence", "arguments": {"assignment_ref": assignment_ref}}})["result"]["structuredContent"]
            self.assertEqual(consumed["assignment_ref"], assignment_ref)
            self.assertEqual(consumed["evidence"]["state"], "none")
            issued = call({"jsonrpc": "2.0", "id": 24, "method": "tools/call", "params": {"name": "open_decision", "arguments": {"task_ref": ref, "prompt": "Confirm.", "prompt_language": "en"}}})["result"]["structuredContent"]
            binding_ref = issued["binding_ref"]
            self.assertEqual(issued["handles"]["binding_ref"], binding_ref)
            self.assertIn("decision_context", issued)
            self.assertNotIn("binding", issued)
            old_shape = call({"jsonrpc": "2.0", "id": 25, "method": "tools/call", "params": {"name": "record_decision", "arguments": {"task_ref": ref, "binding": {"clarification_binding": binding_ref}, "response_original": "yes", "user_language": "en"}}})["result"]
            self.assertTrue(old_shape["isError"])
            self.assertIn("validation_error", old_shape["content"][0]["text"])
            request = {"jsonrpc": "2.0", "id": 26, "method": "tools/call", "params": {"name": "record_decision", "arguments": {"task_ref": ref, "binding_ref": binding_ref, "response_original": "yes", "user_language": "en"}}}
            first = call(request)["result"]["structuredContent"]
            request["id"] = 27
            replay = call(request)["result"]["structuredContent"]
            self.assertFalse(first["replayed"]); self.assertTrue(replay["replayed"])
            request["id"] = 28; request["params"]["arguments"]["response_original"] = "no"
            conflict = call(request)["result"]
            self.assertTrue(conflict["isError"])
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


if __name__ == "__main__":
    unittest.main()
