from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control  # noqa: E402
from cortex_runtime import delegation_service, ledger_db  # noqa: E402


class RequiredBriefingProjectionTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.root = control.ledger_root_path({"project_root": str(self.project)})
        control.activate_orchestration({
            "user_command": "/cortex",
            "principal": "projection-test",
            "thread_id": "projection-test",
            "project_root": str(self.project),
        })
        classification = control.classify_task({
            "complexity": "C1",
            "requirements": [],
            "principal": "projection-test",
            "project_root": str(self.project),
        })
        initialized = control.init_task({
            "task_id": "projection-boundary",
            "user_request": "verify required briefing projection",
            "complexity": "C1",
            "classification_id": classification["classification_id"],
            "principal": "projection-test",
            "project_root": str(self.project),
        })
        self.state = initialized["state"]

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def _params(self) -> dict[str, object]:
        observed = control.status({
            "task_id": "projection-boundary",
            "principal": "projection-test",
            "project_root": str(self.project),
        })
        return {
            "task_id": "projection-boundary",
            "principal": "projection-test",
            "project_root": str(self.project),
            "expected_revision": observed["state"]["revision"],
            "status_receipt": observed["status_receipt"],
            "gate": "discover",
            "agent": "explorer",
            "objective": "check transaction boundary",
            "task_kind": "reading",
            "risk": "low",
            "requested_model": "gpt-5.6-luna",
            "requested_reasoning_effort": "medium",
            "ownership": "Own briefing boundary verification",
            "allowed_paths": ["."],
            "acceptance_criteria": ["Required briefing is materialized after commit"],
            "verification": ["Focused unit test"],
        }

    def test_required_materializer_runs_after_state_transaction_commits(self) -> None:
        original = delegation_service.materialize_job

        def materialize_after_commit(root: Path, job: dict[str, object], **kwargs: object) -> dict[str, object]:
            self.assertNotIn(ledger_db._root_key(root), ledger_db._active_connections())
            return original(root, job, **kwargs)

        with mock.patch.object(delegation_service, "materialize_job", side_effect=materialize_after_commit):
            delegated = control.record_delegation(self._params())

        self.assertEqual(delegated["spawn_request"]["briefing_digest"], delegated["briefing_digest"])
        job = next(
            item for item in ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)
            if item["projection_type"] == "dispatch_briefing"
        )
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["materialized_digest"], delegated["briefing_digest"])

    def test_durable_dispatch_contract_has_no_absolute_host_payload_and_rehydrates(self) -> None:
        delegated = control.record_delegation(self._params())
        _, task_dir, state = control.load_state("projection-boundary", {
            "project_root": str(self.project),
        })
        attempt = state["attempts"][-1]
        durable = attempt["spawn_request"]
        self.assertNotIn("briefing_path", durable)
        self.assertNotIn("message", durable)
        self.assertNotIn("prompt", durable)
        self.assertNotIn(str(task_dir), str(durable))

        package = control._delegation_package(task_dir, state["task_id"], attempt["attempt_id"])
        self.assertNotIn("briefing_path", package["spawn_request"])
        self.assertNotIn("message", package["spawn_request"])
        restored = delegation_service.rehydrate_dispatch_spawn_request(
            task_dir, control.load_task_definition(task_dir, state), attempt,
        )
        self.assertEqual(restored["briefing_path"], delegated["spawn_request"]["briefing_path"])
        self.assertEqual(restored["message"], delegated["spawn_request"]["message"])

    def test_required_materialization_failure_is_recoverable_without_dispatch(self) -> None:
        with mock.patch.object(delegation_service, "materialize_job", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                control.record_delegation(self._params())

        jobs = ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)
        # Every dispatch now carries a full immutable task-contract artifact
        # in addition to user intent and its rendered briefing.  A bounded
        # prompt can therefore omit a whole oversized task field without
        # losing its canonical source or stranding continuation.
        self.assertEqual(len(jobs), 3)
        self.assertEqual(
            sorted(item["projection_type"] for item in jobs),
            ["dispatch_briefing", "task_contract", "user_intent"],
        )
        failed = next(item for item in jobs if item["status"] == "failed")
        self.assertEqual(failed["projection_type"], "user_intent")
        self.assertIn("disk unavailable", str(failed["last_error"]))
        _, state, _, _ = ledger_db.load_task(self.root, "projection-boundary")
        self.assertEqual(state["attempts"][-1]["status"], "failed")
        self.assertEqual(
            state["attempts"][-1]["failure_reason"],
            "required_dispatch_briefing_projection_failed",
        )

    def test_single_facade_materializes_after_its_transaction_commits(self) -> None:
        original = delegation_service.materialize_job

        def materialize_after_commit(root: Path, job: dict[str, object], **kwargs: object) -> dict[str, object]:
            self.assertNotIn(ledger_db._root_key(root), ledger_db._active_connections())
            return original(root, job, **kwargs)

        with mock.patch.object(delegation_service, "materialize_job", side_effect=materialize_after_commit):
            prepared = control.prepare_delegation({
                "task_id": "projection-boundary",
                "principal": "projection-test",
                "project_root": str(self.project),
                "delegation": self._params(),
            })

        self.assertTrue(prepared["atomic"])
        self.assertEqual(
            prepared["delegation"]["spawn_request"]["briefing_digest"],
            prepared["delegation"]["briefing_digest"],
        )

    def test_batch_facade_materializes_only_after_batch_admission_commits(self) -> None:
        original = delegation_service.materialize_job
        observed_calls: list[str] = []

        def materialize_after_commit(root: Path, job: dict[str, object], **kwargs: object) -> dict[str, object]:
            self.assertNotIn(ledger_db._root_key(root), ledger_db._active_connections())
            observed_calls.append(str(job["projection_key"]))
            return original(root, job, **kwargs)

        first = self._params()
        second = {**self._params(), "objective": "check second transaction boundary"}
        with mock.patch.object(delegation_service, "materialize_job", side_effect=materialize_after_commit):
            prepared = control.prepare_delegations({
                "task_id": "projection-boundary",
                "principal": "projection-test",
                "project_root": str(self.project),
                "delegations": [first, second],
            })

        self.assertTrue(prepared["recorded"])
        self.assertEqual(len(prepared["spawn_requests"]), 2)
        # One shared intent projection plus each worker's task contract and
        # briefing projection: the complete immutable artifact must be ready
        # before either native child is visible.
        self.assertEqual(len(observed_calls), 5)
        self.assertEqual(
            [job["status"] for job in ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)],
            ["ready", "ready", "ready", "ready", "ready"],
        )



if __name__ == "__main__":
    unittest.main()
