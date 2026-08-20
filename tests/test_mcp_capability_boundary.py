"""Black-box coverage for coordinator/worker MCP capability separation."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex


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

    def _rpc(self, audience: str | None, request: dict[str, object]) -> dict[str, object]:
        command = [sys.executable, str(self.server)]
        if audience is not None:
            command.append(f"--mcp-audience={audience}")
        completed = subprocess.run(
            command,
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            check=True,
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

    def test_default_and_worker_registries_expose_no_coordinator_operations(self) -> None:
        expected_worker = {
            "worker_question",
            "get_report_template",
            "record_report",
            "read_dispatch_briefing",
            "read_worker_report",
        }
        for audience in (None, "worker"):
            response = self._rpc(
                audience,
                {"jsonrpc": "2.0", "id": f"{audience or 'default'}-list", "method": "tools/list", "params": {}},
            )
            names = {item["name"] for item in response["result"]["tools"]}
            self.assertEqual(names, expected_worker)
            self.assertNotIn("manage_governance", names)
            self.assertNotIn("start_orchestration", names)

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

    def test_worker_recovery_call_is_denied_and_identifiers_cannot_rotate_generation(self) -> None:
        started, authorization, durable_start = self._start_coordinator_task()
        task_ref = str(started["task_ref"])
        principal = str(durable_start["principal"])
        thread_id = str(durable_start["thread_id"])
        initial_generation = int(durable_start["coordinator_capability_claims"]["generation"])
        original_capability_digest = str(durable_start["coordinator_capability_digest"])

        for audience, request_id in ((None, "default-recovery"), ("worker", "worker-recovery")):
            worker_response = self._rpc(
                audience,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "manage_governance",
                        "arguments": {
                            "project_root": str(self.project),
                            "action": "recover_coordinator_capability",
                            "task_ref": task_ref,
                            "principal": principal,
                            "thread_id": thread_id,
                            "capability_generation": initial_generation,
                        },
                    },
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

        # A direct call is the remaining shared-transport fallback.  Even if a
        # worker has learned every durable identifier, absence of the raw
        # recovery proof must fail before any generation mutation.
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
