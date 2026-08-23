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

    def test_recovery_is_host_bound_and_worker_is_denied(self) -> None:
        started, authorization, durable_start = self._start_coordinator_task()
        task_ref = str(started["task_ref"])
        initial_generation = int(durable_start["coordinator_capability_claims"]["generation"])
        original_capability_digest = str(durable_start["coordinator_capability_digest"])

        recovery_arguments = {
            "action": "recover_coordinator_capability",
            "task_ref": task_ref,
            "capability_generation": initial_generation,
        }
        recovery_response = self._rpc(
            None,
            {
                "jsonrpc": "2.0",
                "id": "default-recovery-with-identifiers-only",
                "method": "tools/call",
                "params": {"name": "manage_governance", "arguments": recovery_arguments},
            },
        )["result"]["structuredContent"]
        self.assertTrue(recovery_response["ok"], recovery_response)
        serialized_recovery_response = json.dumps(recovery_response, sort_keys=True)
        self.assertNotIn('"coordinator_capability":', serialized_recovery_response)
        self.assertNotIn('"coordinator_recovery_proof":', serialized_recovery_response)

        worker_response = self._rpc(
            "worker",
            {
                "jsonrpc": "2.0",
                "id": "worker-recovery",
                "method": "tools/call",
                "params": {"name": "manage_governance", "arguments": recovery_arguments},
            },
        )
        self.assertIn("result", worker_response)
        worker_receipt = worker_response["result"]["structuredContent"]
        self.assertTrue(worker_response["result"]["isError"])
        self.assertEqual(worker_receipt["schema"], "cortex/tool-availability/v1")
        self.assertEqual(
            worker_receipt["code"],
            "tool_not_available_for_worker_mcp_audience",
        )
        self.assertEqual(worker_receipt["outcome"], "recovery_advice")
        self.assertFalse(worker_receipt["worker_replacement_authorized"])
        self.assertIn("host coordinator", worker_receipt["next_action"])
        serialized_worker_response = json.dumps(worker_response, sort_keys=True)
        self.assertNotIn('"coordinator_capability":', serialized_worker_response)
        self.assertNotIn('"coordinator_recovery_proof":', serialized_worker_response)
        self.assertNotIn(task_ref, serialized_worker_response)

        registry = cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)}))
        unchanged = next(iter(registry["tasks"].values()))["start"]
        self.assertEqual(
            unchanged["coordinator_capability_claims"]["generation"], initial_generation
        )
        self.assertEqual(unchanged["coordinator_capability_digest"], original_capability_digest)

    def test_explicit_coordinator_transport_can_recover_with_rotating_proof(self) -> None:
        started, authorization, durable_start = self._start_coordinator_task()
        initial_generation = int(durable_start["coordinator_capability_claims"]["generation"])
        recovered = self._rpc(
            "coordinator",
            {
                "jsonrpc": "2.0",
                "id": "coordinator-recovery",
                "method": "tools/call",
                "params": {
                    "name": "manage_governance",
                    "arguments": {
                        "action": "recover_coordinator_capability",
                        "task_ref": str(started["task_ref"]),
                        "capability_generation": initial_generation,
                    },
                },
            },
        )["result"]["structuredContent"]
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["outcome"], "coordinator_capability_recovered")
        self.assertNotIn("authorization_update", json.dumps(recovered))
        acknowledged = self._rpc(
            "coordinator",
            {
                "jsonrpc": "2.0",
                "id": "coordinator-recovery-acknowledgement",
                "method": "tools/call",
                "params": {
                    "name": "manage_governance",
                    "arguments": {
                        "action": "acknowledge_coordinator_recovery",
                        "task_ref": str(started["task_ref"]),
                        "capability_generation": initial_generation,
                    },
                },
            },
        )["result"]["structuredContent"]
        self.assertTrue(acknowledged["ok"], acknowledged)
        stale_proof = self._rpc(
            "coordinator",
            {
                "jsonrpc": "2.0",
                "id": "stale-recovery-proof",
                "method": "tools/call",
                "params": {
                    "name": "manage_governance",
                    "arguments": {
                        "action": "recover_coordinator_capability",
                        "task_ref": str(started["task_ref"]),
                        "capability_generation": initial_generation + 1,
                    },
                },
            },
        )["result"]["structuredContent"]
        self.assertFalse(stale_proof["ok"])
        self.assertEqual(stale_proof["code"], "coordinator_capability_stale")
        registry_text = json.dumps(
            cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)})),
            sort_keys=True,
        )
        current_start = next(iter(json.loads(registry_text)["tasks"].values()))["start"]
        self.assertEqual(
            current_start["coordinator_capability_claims"]["generation"], initial_generation
        )
        self.assertNotIn('"coordinator_capability":', registry_text)
        self.assertNotIn('"coordinator_recovery_proof":', registry_text)


if __name__ == "__main__":
    unittest.main()
