from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control  # noqa: E402
from cortex_runtime import delegation_service, ledger_db  # noqa: E402


class RequiredBriefingProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.root = self.project / ".codex" / "cortex"
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
            "objective": "verify required briefing projection",
            "complexity": "C1",
            "classification_id": classification["classification_id"],
            "principal": "projection-test",
            "project_root": str(self.project),
        })
        self.state = initialized["state"]

    def tearDown(self) -> None:
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
        job = ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)[0]
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["materialized_digest"], delegated["briefing_digest"])

    def test_required_materialization_failure_is_recoverable_without_dispatch(self) -> None:
        with mock.patch.object(delegation_service, "materialize_job", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                control.record_delegation(self._params())

        jobs = ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertIn("disk unavailable", str(jobs[0]["last_error"]))
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
        self.assertEqual(len(observed_calls), 2)
        self.assertEqual(
            [job["status"] for job in ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10)],
            ["ready", "ready"],
        )

    def test_batch_precommit_validation_failure_leaves_no_attempt_or_projection(self) -> None:
        valid = self._params()
        invalid = {**self._params(), "context_report_ids": ["not-a-task-report"]}
        with mock.patch.object(delegation_service, "materialize_job") as materialize:
            result = control.prepare_delegations({
                "task_id": "projection-boundary",
                "principal": "projection-test",
                "project_root": str(self.project),
                "delegations": [valid, invalid],
            })

        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "partial_failure")
        self.assertEqual(result["index"], 1)
        self.assertEqual(result["prepared"], [])
        materialize.assert_not_called()
        _, state, _, _ = ledger_db.load_task(self.root, "projection-boundary")
        self.assertEqual(state["attempts"], [])
        self.assertEqual(ledger_db.list_projection_jobs(self.root, task_id="projection-boundary", limit=10), [])


if __name__ == "__main__":
    unittest.main()
