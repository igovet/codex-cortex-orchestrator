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
        # A worker may emit progress and completion only after the server has
        # recorded the exact immutable briefing read receipt.
        attempt_protocol.acknowledge_briefing(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            dispatch_ref="dispatch-attempt-0001",
            digest="a" * 64,
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
        self.assertEqual(verification["event"]["sequence"], 2)

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
        self.assertEqual(projection["events"][0]["event_type"], "briefing_acknowledged")
        self.assertIn("verification_claimed", [event["event_type"] for event in projection["events"]])
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
            ["briefing_acknowledged", "verification_claimed", "work_completed", "finalization_failed", "finalizing", "completed"],
        )

    def test_completion_materializes_findings_atomically_and_replays_evidence_once(self) -> None:
        findings = [{
            "fingerprint": "missing-artifact",
            "severity": "P1",
            "summary": "Required artifact is missing.",
            "details": {"path": "tests/generated.py"},
            "evidence_ref": "attempt-event-42",
        }]
        completion = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="blocked",
            summary="The required artifact was not created.",
            findings=findings,
            submission_id="blocked-completion-1",
        )
        self.assertEqual(completion["result"]["lifecycle_status"], "BLOCKED")
        stored = ledger_db.list_task_findings(self.root, self.task_id)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["fingerprint"], "missing-artifact")
        self.assertEqual(stored[0]["severity"], "P1")
        self.assertTrue(stored[0]["blocking"])
        self.assertEqual(stored[0]["details"], '{"path":"tests/generated.py"}')
        self.assertEqual(len(stored[0]["source_evidence"]), 1)
        evidence = stored[0]["source_evidence"][0]
        self.assertEqual(evidence["attempt_result_ref"], completion["result"]["result_ref"])
        self.assertEqual(evidence["origin_result_ref"], completion["result"]["result_ref"])
        self.assertEqual(evidence["evidence_ref"], "attempt-event-42")

        replay = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="blocked",
            summary="The required artifact was not created.",
            findings=findings,
            submission_id="blocked-completion-1",
        )
        self.assertTrue(replay["idempotent"])
        replayed = ledger_db.list_task_findings(self.root, self.task_id)
        self.assertEqual(len(replayed), 1)
        self.assertEqual(len(replayed[0]["source_evidence"]), 1)

    def test_finalization_replays_completed_result_after_lifecycle_invalidation(self) -> None:
        """Recovery retirement must not reject an already completed receipt."""
        completed = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="Completed before lifecycle recovery.",
            submission_id="completion-before-recovery",
        )["result"]
        attempt_protocol.finalize_attempt(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        _definition, state, _task, _task_dir = ledger_db.load_task(self.root, self.task_id)
        state["attempts"][0]["invalidated"] = True
        state["attempts"][0]["invalidation_reason"] = "lifecycle_recovery"
        ledger_db.update_task_state(self.root, state)
        replay = attempt_protocol.finalize_attempt(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["idempotent"])
        self.assertTrue(replay["recovered_invalidated_attempt"])
        self.assertEqual(replay["result"]["result_ref"], completed["result_ref"])
        self.assertEqual(replay["result"]["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)

        # The immutable event stream remains readable while the mutable
        # attempt projection is retired. Recovery needs this evidence to
        # reconcile and continue; treating the read as a mutation would turn
        # a technical race into a repeated coordinator blocker.
        events = attempt_protocol.list_attempt_events(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["briefing_acknowledged", "work_completed", "finalizing", "completed"],
        )

    def test_worker_progress_and_completion_are_rejected_before_briefing_receipt(self) -> None:
        """Implementation and documentation cannot mutate before briefing read."""
        for number, gate in enumerate(("implementation", "documentation"), 2):
            with self.subTest(gate=gate):
                unacknowledged_root = Path(self._temporary.name) / f"unacknowledged-{gate}"
                ledger_db.ensure_database(unacknowledged_root)
                unacknowledged_task = f"attempt-protocol-unacknowledged-{gate}"
                unacknowledged_attempt = f"attempt-unacknowledged-{gate}"
                fixture = self._attempt_fixture(unacknowledged_attempt)
                fixture["gate"] = gate
                ledger_db.create_task(
                    unacknowledged_root,
                    {"task_id": unacknowledged_task, "project_root": "/workspace/project"},
                    {
                        "task_id": unacknowledged_task,
                        "task_number": number,
                        "status": "active",
                        "revision": 1,
                        "attempts": [fixture],
                    },
                    f"tasks/000{number}-attempt-protocol-unacknowledged-{gate}",
                )
                with self.assertRaisesRegex(ValueError, "briefing read receipt is required"):
                    attempt_protocol.record_attempt_event(
                        unacknowledged_root,
                        task_id=unacknowledged_task,
                        attempt_id=unacknowledged_attempt,
                        event_type="progress",
                        payload={"summary": "must wait for the briefing receipt"},
                    )
                with self.assertRaisesRegex(ValueError, "briefing read receipt is required"):
                    attempt_protocol.complete_attempt(
                        unacknowledged_root,
                        task_id=unacknowledged_task,
                        attempt_id=unacknowledged_attempt,
                        status="completed",
                        summary="must not be accepted before briefing read",
                    )
                self.assertEqual(
                    attempt_protocol.list_attempt_events(
                        unacknowledged_root,
                        task_id=unacknowledged_task,
                        attempt_id=unacknowledged_attempt,
                    ),
                    [],
                )
                self.assertIsNone(
                    attempt_protocol.get_attempt_result(
                        unacknowledged_root,
                        task_id=unacknowledged_task,
                        attempt_id=unacknowledged_attempt,
                    )
                )

    def _attempt_fixture(self, attempt_id: str) -> dict[str, object]:
        return {
            "attempt_id": attempt_id,
            "gate": "implementation",
            "profile": "backend_dev",
            "agent": "backend_dev",
            "dispatch_ref": "dispatch-" + attempt_id,
            "briefing_digest": "a" * 64,
            "briefing_artifact_ref": "artifact-briefing",
            "result_baseline_ref": "manifest-" + attempt_id,
            "result_baseline_digest": "b" * 64,
            "context_result_refs": [],
        }

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

    def test_changed_retry_is_typed_canonical_conflict_without_mutation(self) -> None:
        first = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            status="completed",
            summary="The immutable canonical result.",
        )
        before_events = attempt_protocol.list_attempt_events(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        with self.assertRaises(attempt_protocol.CanonicalResultConflict) as raised:
            attempt_protocol.complete_attempt(
                self.root,
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                status="completed",
                summary="A changed retry must not replace it.",
            )
        self.assertEqual(raised.exception.result_ref, first["result"]["result_ref"])
        self.assertEqual(raised.exception.diagnostics[0]["code"], "attempt_canonical_result_conflict")
        self.assertEqual(
            attempt_protocol.get_attempt_result(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )["result_ref"],
            first["result"]["result_ref"],
        )
        self.assertEqual(
            attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            ),
            before_events,
        )

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

    def test_oversized_unicode_result_and_event_round_trip_losslessly(self) -> None:
        """Result/event volume is not a backend admission constraint."""
        large_event = {"evidence": "🙂" * 40_000}
        first = attempt_protocol.record_verification_observation(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            event_key="verification_observed:unicode-limit",
            payload=large_event,
        )
        replay = attempt_protocol.record_verification_observation(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            event_key="verification_observed:unicode-limit",
            payload=large_event,
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(first["event"]["event_ref"], replay["event"]["event_ref"])
        self.assertEqual(first["event"]["payload"], large_event)
        oversized_result = "🙂" * 140_000
        attempt_protocol.complete_attempt(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            status="completed", summary="Completed after inspecting the protocol.",
            findings=[oversized_result],
        )
        stored = attempt_protocol.get_attempt_result(self.root, task_id=self.task_id, attempt_id=self.attempt_id)
        self.assertEqual(stored["findings"], [oversized_result])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
