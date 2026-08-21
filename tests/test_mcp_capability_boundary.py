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

    def test_stdio_ledger_busy_is_structured_and_skips_tool_error_log(self) -> None:
        def busy_handler(_arguments: dict[str, object]) -> dict[str, object]:
            raise cortex.LedgerBusyError(
                "report_publication",
                37,
                holder={"pid": 123, "operation": "report_publication", "task_id": "task-1"},
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
        self.assertEqual(data["operation"], "report_publication")
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
        capability = str(authorization.get("coordinator_capability") or "")
        recovery_proof = str(authorization.get("coordinator_recovery_proof") or "")
        self.assertRegex(capability, r"^[0-9a-f]{64}$")
        self.assertRegex(recovery_proof, r"^[0-9a-f]{64}$")
        registry = cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)}))
        start = next(iter(registry["tasks"].values()))["start"]
        return result, authorization, start

    def test_default_registry_is_compatible_while_explicit_audiences_remain_strict(self) -> None:
        expected_compatibility = {
            "start_orchestration",
            "continue_orchestration",
            "manage_orchestration",
            "manage_governance",
            "worker_question",
            "get_report_template",
            "record_report",
            "read_dispatch_briefing",
            "read_worker_report",
        }
        expected_worker = {
            "worker_question",
            "get_report_template",
            "record_report",
            "read_dispatch_briefing",
            "read_worker_report",
        }
        for audience in (None, "compat"):
            response = self._rpc(
                audience,
                {"jsonrpc": "2.0", "id": f"{audience or 'default'}-list", "method": "tools/list", "params": {}},
            )
            names = {item["name"] for item in response["result"]["tools"]}
            self.assertEqual(names, expected_compatibility)

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
                "read_worker_report",
            },
        )

    def test_compatibility_recovery_requires_non_durable_proof_and_worker_is_denied(self) -> None:
        started, authorization, durable_start = self._start_coordinator_task()
        task_ref = str(started["task_ref"])
        principal = str(durable_start["principal"])
        thread_id = str(durable_start["thread_id"])
        initial_generation = int(durable_start["coordinator_capability_claims"]["generation"])
        original_capability_digest = str(durable_start["coordinator_capability_digest"])

        recovery_arguments = {
            "project_root": str(self.project),
            "action": "recover_coordinator_capability",
            "task_ref": task_ref,
            "principal": principal,
            "thread_id": thread_id,
            "capability_generation": initial_generation,
        }
        compatibility_response = self._rpc(
            None,
            {
                "jsonrpc": "2.0",
                "id": "default-recovery-with-identifiers-only",
                "method": "tools/call",
                "params": {"name": "manage_governance", "arguments": recovery_arguments},
            },
        )["result"]["structuredContent"]
        self.assertFalse(compatibility_response["ok"])
        self.assertEqual(compatibility_response["code"], "coordinator_recovery_proof_required")
        serialized_compatibility_response = json.dumps(compatibility_response, sort_keys=True)
        self.assertNotIn(str(authorization["coordinator_capability"]), serialized_compatibility_response)
        self.assertNotIn(str(authorization["coordinator_recovery_proof"]), serialized_compatibility_response)

        worker_response = self._rpc(
            "worker",
            {
                "jsonrpc": "2.0",
                "id": "worker-recovery",
                "method": "tools/call",
                "params": {"name": "manage_governance", "arguments": recovery_arguments},
            },
        )
        self.assertEqual(worker_response["error"]["code"], -32602)
        self.assertEqual(
            worker_response["error"]["message"],
            "tool_not_available_for_worker_mcp_audience",
        )
        serialized_worker_response = json.dumps(worker_response, sort_keys=True)
        self.assertNotIn(str(authorization["coordinator_capability"]), serialized_worker_response)
        self.assertNotIn(str(authorization["coordinator_recovery_proof"]), serialized_worker_response)

        # Handler-level proof validation is also authoritative for an
        # in-process call. Even if a caller has learned every durable
        # identifier, absence of the raw recovery proof must fail before any
        # generation mutation.
        direct = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "recover_coordinator_capability",
                "task_ref": task_ref,
                "principal": principal,
                "thread_id": thread_id,
                "capability_generation": initial_generation,
            }
        )
        self.assertFalse(direct["ok"])
        self.assertEqual(direct["code"], "coordinator_recovery_proof_required")

        registry = cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)}))
        unchanged = next(iter(registry["tasks"].values()))["start"]
        self.assertEqual(
            unchanged["coordinator_capability_claims"]["generation"], initial_generation
        )
        self.assertEqual(unchanged["coordinator_capability_digest"], original_capability_digest)

    def test_explicit_coordinator_transport_can_recover_with_rotating_proof(self) -> None:
        started, authorization, durable_start = self._start_coordinator_task()
        original_capability = str(authorization["coordinator_capability"])
        original_proof = str(authorization["coordinator_recovery_proof"])
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
                        "project_root": str(self.project),
                        "action": "recover_coordinator_capability",
                        "task_ref": str(started["task_ref"]),
                        "principal": str(durable_start["principal"]),
                        "thread_id": str(durable_start["thread_id"]),
                        "capability_generation": initial_generation,
                        "coordinator_recovery_proof": original_proof,
                    },
                },
            },
        )["result"]["structuredContent"]
        self.assertTrue(recovered["ok"])
        update = recovered["authorization_update"]
        renewed_capability = str(update["coordinator_capability"])
        renewed_proof = str(update["coordinator_recovery_proof"])
        self.assertRegex(renewed_capability, r"^[0-9a-f]{64}$")
        self.assertRegex(renewed_proof, r"^[0-9a-f]{64}$")
        self.assertNotEqual(renewed_capability, original_capability)
        self.assertNotEqual(renewed_proof, original_proof)
        acknowledged = self._rpc(
            "coordinator",
            {
                "jsonrpc": "2.0",
                "id": "coordinator-recovery-acknowledgement",
                "method": "tools/call",
                "params": {
                    "name": "manage_governance",
                    "arguments": {
                        "project_root": str(self.project),
                        "action": "acknowledge_coordinator_recovery",
                        "task_ref": str(started["task_ref"]),
                        "principal": str(durable_start["principal"]),
                        "thread_id": str(durable_start["thread_id"]),
                        "capability_generation": initial_generation + 1,
                        "coordinator_capability": renewed_capability,
                        "coordinator_recovery_proof": renewed_proof,
                        "previous_coordinator_recovery_proof": original_proof,
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
                        "project_root": str(self.project),
                        "action": "recover_coordinator_capability",
                        "task_ref": str(started["task_ref"]),
                        "principal": str(durable_start["principal"]),
                        "thread_id": str(durable_start["thread_id"]),
                        "capability_generation": initial_generation + 1,
                        "coordinator_recovery_proof": original_proof,
                    },
                },
            },
        )["result"]["structuredContent"]
        self.assertFalse(stale_proof["ok"])
        self.assertEqual(stale_proof["code"], "coordinator_recovery_proof_required")
        registry_text = json.dumps(
            cortex._operation_registry(cortex.ledger_root({"project_root": str(self.project)})),
            sort_keys=True,
        )
        current_start = next(iter(json.loads(registry_text)["tasks"].values()))["start"]
        self.assertEqual(
            current_start["coordinator_capability_claims"]["generation"], initial_generation + 1
        )
        self.assertNotIn(original_capability, registry_text)
        self.assertNotIn(original_proof, registry_text)
        self.assertNotIn(renewed_capability, registry_text)
        self.assertNotIn(renewed_proof, registry_text)


if __name__ == "__main__":
    unittest.main()
