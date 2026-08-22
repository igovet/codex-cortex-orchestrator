"""Focused contract tests for the database-centric worker completion protocol."""
from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import attempt_protocol, ledger_db


class AttemptProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name) / "ledger"
        ledger_db.ensure_database(self.root)
        self.task_id = "attempt-protocol-task"
        self.attempt_id = "attempt-0001"
        ledger_db.create_task(
            self.root,
            {"task_id": self.task_id, "project_root": "/workspace/project"},
            {
                "task_id": self.task_id,
                "task_number": 1,
                "status": "active",
                "revision": 4,
                "attempts": [{
                    "attempt_id": self.attempt_id,
                    "gate": "implementation",
                    "profile": "backend_dev",
                    "agent": "backend_dev",
                    "dispatch_ref": "dispatch-attempt-0001",
                    "briefing_digest": "a" * 64,
                    "briefing_artifact_ref": "artifact-briefing",
                    "result_baseline_ref": "manifest-attempt-0001",
                    "result_baseline_digest": "b" * 64,
                    "context_result_refs": ["attempt-result-predecessor-0001"],
                }],
            },
            "tasks/0001-attempt-protocol-task",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_events_result_projection_and_finalization_are_durable_and_idempotent(self) -> None:
        verification = attempt_protocol.record_attempt_event(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            # Worker claims and server observations are different event
            # authorities.  A worker may claim a check; it cannot assert that
            # Cortex itself observed the command/exit code.
            event_type="verification_claimed",
            event_key="verification:focused",
            payload={
                "command": "python3 -m unittest tests.test_attempt_protocol",
                "cwd": ".",
                "exit_code": 0,
                "evidence": "focused protocol test passed",
            },
        )
        self.assertFalse(verification["idempotent"])
        self.assertEqual(verification["event"]["sequence"], 1)

        completion = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="Implemented the durable completion protocol.",
            findings=[{"severity": "info", "summary": "completion is durable"}],
            unresolved=["No unresolved work."],
            submission_id="worker-completion-1",
            workspace_observation={
                "baseline_ref": "manifest-attempt-0001",
                "baseline_digest_sha256": "b" * 64,
                "current_digest_sha256": "c" * 64,
                "complete": True,
                "safe_to_attribute": True,
                "changed_files": ["src/protocol.py", "tests/test_attempt_protocol.py"],
            },
        )
        self.assertEqual(completion["result"]["lifecycle_status"], "WORK_COMPLETED")
        self.assertEqual(
            completion["result"]["changed_files"],
            ["src/protocol.py", "tests/test_attempt_protocol.py"],
        )
        self.assertTrue(completion["finalization_required"])

        replay = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="Implemented the durable completion protocol.",
            findings=[{"severity": "info", "summary": "completion is durable"}],
            unresolved=["No unresolved work."],
            submission_id="worker-completion-1",
            workspace_observation={
                "baseline_ref": "manifest-attempt-0001",
                "baseline_digest_sha256": "b" * 64,
                "current_digest_sha256": "c" * 64,
                "complete": True,
                "safe_to_attribute": True,
                "changed_files": ["src/protocol.py", "tests/test_attempt_protocol.py"],
            },
        )
        self.assertTrue(replay["idempotent"])

        with self.assertRaisesRegex(ValueError, "event stream is closed"):
            attempt_protocol.record_attempt_event(
                self.root,
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                event_type="progress",
                payload={"summary": "too late"},
            )

        projection = attempt_protocol.build_attempt_result_view(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(projection["result"]["summary"], "Implemented the durable completion protocol.")
        self.assertEqual(projection["result"]["changed_files"], completion["result"]["changed_files"])
        self.assertEqual(projection["attempt_result_ref"], completion["result"]["result_ref"])
        self.assertEqual(projection["events"][0]["event_type"], "verification_claimed")
        self.assertNotIn("verification_observed", [event["event_type"] for event in projection["events"]])

        attempt_protocol.record_finalization_failure(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            reason_code="projection_unavailable",
        )
        self.assertEqual(
            attempt_protocol.get_attempt_result(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )["lifecycle_status"],
            "WORK_COMPLETED",
        )
        finalized = attempt_protocol.finalize_attempt(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(finalized["result"]["lifecycle_status"], "COMPLETED")
        self.assertEqual(
            [event["event_type"] for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )],
            ["verification_claimed", "work_completed", "finalization_failed", "finalizing", "completed"],
        )

    def test_workspace_delta_is_omitted_without_safe_server_attribution(self) -> None:
        completion = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="Work completed in a shared workspace.",
            workspace_observation={
                "baseline_ref": "manifest-attempt-0001",
                "baseline_digest_sha256": "b" * 64,
                "current_digest_sha256": "c" * 64,
                "complete": True,
                "safe_to_attribute": False,
                "changed_files": ["someone-else/file.py"],
            },
        )
        self.assertEqual(completion["result"]["changed_files"], [])
        self.assertEqual(completion["result"]["changed_files_status"], "not_attributable")
        view = attempt_protocol.build_attempt_result_view(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(view["result"]["changed_files"], [])
        self.assertEqual(view["result"]["changed_files_status"], "not_attributable")

    def test_worker_cannot_emit_server_verification_observation(self) -> None:
        """Worker claims are distinct from Cortex-observed verification events."""
        with self.assertRaisesRegex(ValueError, "workers may record"):
            attempt_protocol.record_attempt_event(
                self.root,
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                event_type="verification_observed",
                payload={"command": "pytest -q", "exit_code": 0},
            )

    def test_server_verification_observation_is_idempotent_only_while_stream_is_open(self) -> None:
        payload = {
            "schema": "cortex/verification-observation/v1",
            "tests": [{"command": "/usr/bin/true", "cwd": ".", "exit_code": 0}],
        }
        first = attempt_protocol.record_verification_observation(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            event_key="verification_observed:benign_success",
            payload=payload,
        )
        replay = attempt_protocol.record_verification_observation(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            event_key="verification_observed:benign_success",
            payload=payload,
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(first["event"]["event_ref"], replay["event"]["event_ref"])
        attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="Close the event stream after the trusted check.",
        )
        with self.assertRaisesRegex(ValueError, "stream is closed"):
            attempt_protocol.record_verification_observation(
                self.root,
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                event_key="verification_observed:other",
                payload=payload,
            )

    def test_workspace_observation_must_bind_to_this_attempt_baseline(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match the attempt baseline"):
            attempt_protocol.complete_attempt(
                self.root,
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                status="completed",
                summary="This must not be stored.",
                workspace_observation={
                    "baseline_ref": "different-manifest",
                    "complete": True,
                    "safe_to_attribute": True,
                    "changed_files": ["src/protocol.py"],
                },
            )
        self.assertIsNone(
            attempt_protocol.get_attempt_result(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
