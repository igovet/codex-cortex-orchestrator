"""Black-box coverage for coordinator/worker MCP capability separation."""
from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import mcp_api


class McpCapabilityBoundaryTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.server = SCRIPTS / "cortex.py"

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def test_start_wave_preflight_aggregates_exact_paths_before_state_reservation(self) -> None:
        response = cortex.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Reject every independent unsafe worker scope.",
                "acceptance_criteria": ["No state is reserved."],
                "verification": ["Inspect exact correction pointers."],
            },
            "waves": [{
                "unexpected_wave_field": True,
                "workers": [
                    {"phase": "discover", "allowed_paths": "src", "strategy": "model-authored"},
                    {"phase": "qa", "allowed_paths": []},
                    {"phase": "review", "allowed_paths": ["/absolute", "../traversal", "safe/path", 7]},
                ],
            }],
        })
        self.assertFalse(response["ok"])
        self.assertFalse(response["recovery"]["state_mutated"])
        diagnostics = response["error"]["diagnostics"]
        pointers = [item["json_pointer"] for item in diagnostics]
        expected = {
            "/waves/0/unexpected_wave_field",
            "/waves/0/workers/0/strategy",
            "/waves/0/workers/0/allowed_paths",
            "/waves/0/workers/1/allowed_paths",
            "/waves/0/workers/2/allowed_paths/0",
            "/waves/0/workers/2/allowed_paths/1",
            "/waves/0/workers/2/allowed_paths/3",
        }
        self.assertEqual(set(pointers), expected)
        self.assertEqual(len(pointers), len(set(pointers)))
        self.assertNotIn("/request", pointers)
        by_pointer = {item["json_pointer"]: item for item in diagnostics}
        for pointer in (
            "/waves/0/workers/0/allowed_paths",
            "/waves/0/workers/1/allowed_paths",
        ):
            self.assertEqual(by_pointer[pointer]["field_schema"], {"type": "array", "minItems": 1})
        for pointer in (
            "/waves/0/workers/2/allowed_paths/0",
            "/waves/0/workers/2/allowed_paths/1",
            "/waves/0/workers/2/allowed_paths/3",
        ):
            self.assertEqual(by_pointer[pointer]["field_schema"]["format"], "project-relative-path")
        self.assertEqual(list(self.host_state_dir.iterdir()), [])
        self.assertEqual(list(self.project.iterdir()), [])

    def test_public_response_projection_removes_host_credentials_recursively(self) -> None:
        value = {
            "authorization": {"actor": "coordinator", "coordinator_capability": "a" * 64},
            "nested": [{"coordinator_recovery_proof": "b" * 64, "ok": True}],
            "authorization_update": {"coordinator_capability": "c" * 64},
        }
        projected = mcp_api._scrub_public_response(value)
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn('"coordinator_capability":', serialized)
        self.assertNotIn('"coordinator_recovery_proof":', serialized)
        self.assertNotIn('"authorization_update":', serialized)
        self.assertEqual(projected["authorization"]["actor"], "coordinator")
        self.assertTrue(projected["nested"][0]["ok"])

    def test_jsonrpc_request_id_encoding_preserves_json_types(self) -> None:
        encode = mcp_api._canonical_jsonrpc_request_id
        self.assertNotEqual(encode(1), encode("1"))
        self.assertNotEqual(encode(None), encode("null"))
        self.assertNotEqual(encode(False), encode(0))
        self.assertEqual(encode(1), encode(1))

    def test_null_jsonrpc_id_is_rejected_for_mutating_start(self) -> None:
        handler = mock.Mock(return_value={"ok": True})
        request = {
            "jsonrpc": "2.0",
            "id": None,
            "method": "tools/call",
            "params": {"name": "start_orchestration", "arguments": {}},
        }
        output = io.StringIO()
        with mock.patch.object(mcp_api.sys, "stdin", io.StringIO(json.dumps(request) + "\n")), \
            mock.patch.object(mcp_api.sys, "stdout", output), \
            mock.patch("cortex_runtime.mcp_api.log_tool_error", create=True):
            mcp_api.serve_stdio(
                public_tools={"start_orchestration": (handler, {})},
                internal_handlers={},
                server_version="9.2.22",
                instructions="test",
                log_tool_error=mock.Mock(),
                audience="coordinator",
            )
        self.assertEqual(output.getvalue(), "")
        handler.assert_not_called()

    def test_missing_jsonrpc_id_is_rejected_for_mutating_start(self) -> None:
        handler = mock.Mock(return_value={"ok": True})
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "start_orchestration", "arguments": {}},
        }
        output = io.StringIO()
        with mock.patch.object(mcp_api.sys, "stdin", io.StringIO(json.dumps(request) + "\n")), \
            mock.patch.object(mcp_api.sys, "stdout", output), \
            mock.patch("cortex_runtime.mcp_api.log_tool_error", create=True):
            mcp_api.serve_stdio(
                public_tools={"start_orchestration": (handler, {})},
                internal_handlers={},
                server_version="9.2.22",
                instructions="test",
                log_tool_error=mock.Mock(),
                audience="coordinator",
            )
        self.assertEqual(output.getvalue(), "")
        handler.assert_not_called()

    def test_stdio_ledger_busy_is_structured_and_skips_tool_error_log(self) -> None:
        def busy_handler(_arguments: dict[str, object]) -> dict[str, object]:
            raise cortex.LedgerBusyError(
                "attempt_result_commit",
                37,
                holder={"pid": 123, "operation": "attempt_result_commit", "task_id": "task-1"},
            )

        request = {
            "jsonrpc": "2.0",
            "id": "busy-1",
            "method": "tools/call",
            "params": {"name": "start_orchestration", "arguments": {}},
        }
        output = io.StringIO()
        with mock.patch.object(mcp_api.sys, "stdin", io.StringIO(json.dumps(request) + "\n")), \
            mock.patch.object(mcp_api.sys, "stdout", output), \
            mock.patch("cortex_runtime.mcp_api.log_tool_error", create=True) as log_error:
            mcp_api.serve_stdio(
                public_tools={"start_orchestration": (busy_handler, {})},
                internal_handlers={},
                server_version="9.2.22",
                instructions="test",
                log_tool_error=log_error,
                audience="coordinator",
            )

        response = json.loads(output.getvalue())
        self.assertEqual(response["error"]["code"], -32009)
        data = response["error"]["data"]
        self.assertEqual(data["schema"], "cortex/ledger-busy/v1")
        self.assertTrue(data["retryable"])
        self.assertEqual(data["operation"], "attempt_result_commit")
        self.assertEqual(data["held_duration_ms"], 37)
        self.assertNotIn("token", data["holder"])
        log_error.assert_not_called()

    def _rpc(self, audience: str | None, request: dict[str, object]) -> dict[str, object]:
        command = [sys.executable, str(self.server)]
        if audience is not None:
            command.append(f"--mcp-audience={audience}")
        environment = os.environ.copy()
        # ``None`` models the documented ordinary Desktop launch, whose
        # audience must not be influenced by the test runner's environment.
        if audience is None:
            environment.pop("CORTEX_MCP_AUDIENCE", None)
        completed = subprocess.run(
            command,
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        return json.loads(completed.stdout)

    def _start_coordinator_task(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        response = self._rpc(
            "coordinator",
            {
                "jsonrpc": "2.0",
                "id": "coordinator-start",
                "method": "tools/call",
                "params": {
                    "name": "start_orchestration",
                    "arguments": {
                        "project_root": str(self.project),
                        "task": {
                            "user_request": "Exercise the capability transport boundary.",
                            "complexity": "C1",
                            "acceptance_criteria": ["The boundary remains scoped."],
                            "verification": ["Run the capability boundary regression test."],
                        },
                        "waves": [{"workers": [{"phase": "discover"}]}],
                    },
                },
            },
        )
        result = response["result"]["structuredContent"]
        self.assertTrue(result["ok"])
        authorization = result.get("authorization") or {}
        self.assertNotIn("coordinator_capability", json.dumps(result))
        self.assertNotIn("coordinator_recovery_proof", json.dumps(result))
        self.assertNotIn("authorization", result)
        registry = cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)}))
        start = next(iter(registry["tasks"].values()))["start"]
        return result, authorization, start

    def test_default_registry_is_fresh_only_while_explicit_audiences_remain_strict(self) -> None:
        expected_default = {
            "start_orchestration",
            "continue_orchestration",
            "manage_orchestration",
            "manage_governance",
            "worker_question",
            "record_attempt_event",
            "complete_attempt",
            "read_dispatch_briefing",
            "read_worker_result",
        }
        expected_worker = {
            "worker_question",
            "record_attempt_event",
            "complete_attempt",
            "read_dispatch_briefing",
            "read_worker_result",
        }
        for audience in (None, "default"):
            response = self._rpc(
                audience,
                {"jsonrpc": "2.0", "id": f"{audience or 'default'}-list", "method": "tools/list", "params": {}},
            )
            names = {item["name"] for item in response["result"]["tools"]}
            self.assertEqual(names, expected_default)

        worker = self._rpc(
            "worker",
            {"jsonrpc": "2.0", "id": "worker-list", "method": "tools/list", "params": {}},
        )
        worker_names = {item["name"] for item in worker["result"]["tools"]}
        self.assertEqual(worker_names, expected_worker)
        self.assertNotIn("manage_governance", worker_names)
        self.assertNotIn("start_orchestration", worker_names)
        listed_worker_question = next(
            item for item in worker["result"]["tools"]
            if item["name"] == "worker_question"
        )
        self.assertEqual(
            listed_worker_question["inputSchema"],
            cortex.PUBLIC_SCHEMA_REGISTRY["worker_question"],
        )
        self.assertEqual(
            listed_worker_question["description"],
            mcp_api.PUBLIC_TOOL_DESCRIPTIONS["worker_question"],
        )

        coordinator = self._rpc(
            "coordinator",
            {"jsonrpc": "2.0", "id": "coordinator-list", "method": "tools/list", "params": {}},
        )
        coordinator_names = {item["name"] for item in coordinator["result"]["tools"]}
        self.assertEqual(
            coordinator_names,
            {
                "start_orchestration",
                "continue_orchestration",
                "manage_orchestration",
                "manage_governance",
                "read_worker_result",
            },
        )

    def test_worker_audience_cannot_invoke_coordinator_governance_or_mutate_state(self) -> None:
        started, _authorization, _durable_start = self._start_coordinator_task()
        task_ref = str(started["task_ref"])
        coordinator_ref = str(started["coordinator_ref"])
        root = cortex.ledger_root({"project_root": str(self.project)})
        task_id = next(iter(cortex.db_task_index(root)))
        before = cortex.db_load_task(root, task_id)[1]

        worker_response = self._rpc(
            "worker",
            {
                "jsonrpc": "2.0",
                "id": "worker-governance-denied",
                "method": "tools/call",
                "params": {
                    "name": "manage_governance",
                    "arguments": {
                        "action": "snapshot",
                        "task_ref": task_ref,
                        "coordinator_ref": coordinator_ref,
                    },
                },
            },
        )

        receipt = worker_response["result"]["structuredContent"]
        self.assertTrue(worker_response["result"]["isError"])
        self.assertEqual(receipt["schema"], "cortex/governance-response/v11")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["outcome"], "failed")
        self.assertEqual(receipt["error"]["code"], "public_response_projection_failed")
        self.assertFalse(receipt["recovery"]["state_mutated"])
        serialized = json.dumps(worker_response, sort_keys=True)
        self.assertNotIn(coordinator_ref, serialized)

        after = cortex.db_load_task(root, task_id)[1]
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
