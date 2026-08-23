"""Integration regression for stale coordinator snapshots during recovery."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex  # noqa: E402
from cortex_runtime import attempt_protocol, ledger_db, orchestration_engine  # noqa: E402


class InvalidatedOrchestrationReconcileTests(unittest.TestCase):
    def test_stale_completion_reconciles_canonical_result_without_blocking(self) -> None:
        """A DB-retired attempt must be repaired by Cortex, not surfaced as a blocker."""
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-state"
            previous_host_store = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            params = {
                "project_root": str(project),
                "principal": "orchestration-task-race",
                "thread_id": "thread-race",
            }
            try:
                cortex.activate_orchestration({**params, "user_command": "/cortex"})
                root = cortex.ledger_root(params)
                task_id = "task-race-invalidated"
                attempt_id = "attempt-race-01"
                artifact_dir = f"tasks/0001-{task_id}"
                state = {
                "schema": cortex.SCHEMA,
                "task_id": task_id,
                "task_number": 1,
                "status": "active",
                "revision": 4,
                "principal": params["principal"],
                "thread_id": params["thread_id"],
                "current_pipeline": ["implementation"],
                "completed_gates": [],
                "skipped_gates": [],
                "attempts": [{
                    "attempt_id": attempt_id,
                    "gate": "implementation",
                    "profile": "backend_dev",
                    "agent": "backend_dev",
                    "status": "running",
                    "dispatch_ref": "dispatch-race-01",
                    "briefing_digest": "a" * 64,
                    "briefing_artifact_ref": "artifact-briefing",
                    "result_baseline_ref": "manifest-race-01",
                    "result_baseline_digest": "b" * 64,
                    "context_result_refs": [],
                    "facade_managed": True,
                }],
                "evidence": [],
                }
                definition = {
                "schema": cortex.SCHEMA,
                "task_id": task_id,
                "project_root": str(project),
                "user_request": "race recovery",
                "principal": params["principal"],
                "thread_id": params["thread_id"],
                }
                ledger_db.create_task(root, definition, state, artifact_dir)
                task_dir = root / artifact_dir
                task_dir.mkdir(parents=True)
                ledger_db.put_task_document(
                root,
                task_id,
                f"dispatch:{attempt_id}",
                {
                    "schema": cortex.SCHEMA,
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "dispatch_ref": "dispatch-race-01",
                    "spawn_status": "worker_running",
                },
                )
                attempt_protocol.acknowledge_briefing(
                root,
                task_id=task_id,
                attempt_id=attempt_id,
                dispatch_ref="dispatch-race-01",
                digest="a" * 64,
                )
                result = attempt_protocol.complete_attempt(
                root,
                task_id=task_id,
                attempt_id=attempt_id,
                status="completed",
                summary="canonical result retained",
                submission_id="race-submit",
                )["result"]
                attempt_protocol.finalize_attempt(
                root, task_id=task_id, attempt_id=attempt_id,
                )

            # The coordinator has a stale snapshot, but the worker result
            # reference was already recorded before lifecycle recovery.
                current = ledger_db.load_task(root, task_id)[1]
                current["attempts"][0]["attempt_result_ref"] = result["result_ref"]
                current["attempts"][0]["lifecycle_status"] = "COMPLETED"
                ledger_db.update_task_state(root, current, event="worker_result", detail="result receipt")
                stale_state = ledger_db.load_task(root, task_id)[1]

                current = ledger_db.load_task(root, task_id)[1]
                current["attempts"][0]["invalidated"] = True
                current["attempts"][0]["invalidation_reason"] = "lifecycle_recovery"
                ledger_db.update_task_state(root, current, event="lifecycle_recovery", detail="retired stale projection")

                reconciled, _ = orchestration_engine._complete_orchestrate_attempt(
                {**params, "task_id": task_id},
                task_dir,
                stale_state,
                {
                    "attempt_id": attempt_id,
                    "status": "passed",
                    "attempt_result_ref": result["result_ref"],
                },
                )
                persisted = ledger_db.load_task(root, task_id)[1]["attempts"][0]
                canonical = attempt_protocol.get_attempt_result(
                root, task_id=task_id, attempt_id=attempt_id,
                )

                self.assertEqual(persisted["status"], "passed")
                self.assertTrue(persisted["reconciled_from_canonical_result"])
                self.assertTrue(persisted["invalidated"])
                self.assertEqual(canonical["lifecycle_status"], "COMPLETED")
                self.assertEqual(canonical["result_ref"], result["result_ref"])
                self.assertFalse(reconciled.get("requires_user_decision", False))
            finally:
                if previous_host_store is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous_host_store


if __name__ == "__main__":
    unittest.main()
