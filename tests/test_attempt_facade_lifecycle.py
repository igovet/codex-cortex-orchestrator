"""Focused public-facade and native-stop regression tests.

These tests deliberately exercise the seams where a worker's semantic work is
already durable but server finalization is not. A failed materialization is
retryable finalization work, never evidence that the worker must be failed or replaced.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
import cortex_hook
from cortex_runtime import attempt_facade, attempt_protocol, context_handoff, ledger_db


class AttemptFacadeLifecycleTests(unittest.TestCase):
    """Protect the public lifecycle from finalization failure paths."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="cortex-attempt-facade-")
        self.project = Path(self._temporary.name) / "project"
        self.project.mkdir()
        # Do not inherit a process-level CORTEX_ROOT from another test run:
        # this contract owns its isolated SQLite ledger explicitly.
        self.root = self.project / ".attempt-facade-ledger"
        ledger_db.ensure_database(self.root)
        self.task_id = "facade-lifecycle-task"
        self.attempt_id = "implementation-01"
        self.attempt = {
            "attempt_id": self.attempt_id,
            "status": "running",
            "gate": "implementation",
            "profile": "backend_dev",
            "agent": "backend_dev",
            "dispatch_ref": "dispatch-implementation-01",
            "briefing_digest": "a" * 64,
            "briefing_artifact_ref": "artifact-briefing",
            "result_baseline_ref": "manifest-implementation-01",
            "result_baseline_digest": "b" * 64,
            "allowed_paths": ["."],
            "context_result_refs": [],
        }
        self.state = {
            "task_id": self.task_id,
            "task_number": 1,
            "principal": "coordinator",
            "thread_id": "coordinator",
            "revision": 3,
            "attempts": [self.attempt],
        }
        ledger_db.create_task(
            self.root,
            {
                "task_id": self.task_id,
                "task_number": 1,
                "project_root": str(self.project),
            },
            self.state,
            "tasks/0001-facade-lifecycle-task",
        )
        attempt_protocol.acknowledge_briefing(
            self.root,
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            dispatch_ref="dispatch-implementation-01",
            digest="a" * 64,
        )
        self.params = {
            "project_root": str(self.project),
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "profile": "backend_dev",
            "status": "completed",
            "summary": "Implemented the bounded server-owned completion transition.",
            "findings": [{"summary": "Semantic result was persisted before projection."}],
            "decisions_needed": [],
            "unresolved": [],
            "claims": [],
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _facade_patches(self, generated_view):
        """Supply host facts while keeping AttemptResult persistence real."""
        observation = {
            "baseline_ref": "manifest-implementation-01",
            "baseline_digest_sha256": "b" * 64,
            "current_digest_sha256": "c" * 64,
            "complete": True,
            "safe_to_attribute": True,
            "changed_files": ["src/owned.py"],
        }
        patches = ExitStack()
        patches.enter_context(mock.patch.object(
            attempt_facade._runtime, "ledger_root", return_value=self.root,
        ))
        patches.enter_context(mock.patch.multiple(
            attempt_facade,
            _worker_context=mock.Mock(return_value=(
                self.project, self.project, self.state, self.attempt, "backend_dev",
            )),
            _receipt_guard=mock.Mock(return_value={}),
            _workspace_observation=mock.Mock(return_value=observation),
            _mark_attempt=mock.Mock(),
        ))
        patches.enter_context(mock.patch.object(
            attempt_protocol, "build_attempt_result_view", generated_view,
        ))
        return patches

    def test_public_complete_attempt_keeps_semantic_result_through_projection_retry(self) -> None:
        """The second call finalizes the original result rather than a new worker."""
        failed_projection = mock.Mock(side_effect=RuntimeError("injected result-view failure"))
        with self._facade_patches(failed_projection):
            pending = attempt_facade.complete_attempt(dict(self.params))

        self.assertFalse(pending["ok"])
        self.assertEqual(pending["outcome"], "finalization_pending")
        self.assertTrue(pending["retryable"])
        self.assertFalse(pending["worker_replacement_authorized"])
        stored = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["summary"], self.params["summary"])
        self.assertEqual(stored["findings"], self.params["findings"])
        self.assertEqual(stored["changed_files"], ["src/owned.py"])
        self.assertEqual(stored["lifecycle_status"], attempt_protocol.LIFECYCLE_FINALIZING)
        self.assertEqual(
            [event["event_type"] for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )],
            ["briefing_acknowledged", "work_completed", "finalizing", "finalization_failed"],
        )

        recorded_projection = mock.Mock(return_value={"projection_ref": "attempt-result-view-implementation-01"})
        with self._facade_patches(recorded_projection):
            completed = attempt_facade.complete_attempt(dict(self.params))

        self.assertTrue(completed["ok"])
        self.assertEqual(completed["outcome"], "attempt_completed")
        self.assertEqual(completed["attempt_result_ref"], stored["result_ref"])
        self.assertEqual(completed["projection_ref"], "attempt-result-view-implementation-01")
        self.assertFalse(completed["worker_replacement_authorized"])
        final = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertEqual(final["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)
        self.assertEqual(final["result_ref"], stored["result_ref"])
        self.assertEqual(
            [event["event_type"] for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=self.attempt_id,
            )],
            ["briefing_acknowledged", "work_completed", "finalizing", "finalization_failed", "completed"],
        )
        self.assertEqual(recorded_projection.call_args.kwargs, {
            "task_id": self.task_id, "attempt_id": self.attempt_id,
        })

    def test_public_completed_attempt_preserves_successor_unresolved_handoff(self) -> None:
        """The facade stores scoped unresolved work without rewriting its status."""
        params = dict(self.params)
        params["unresolved"] = [{"summary": "Governance close must resolve the inherited risk."}]
        with self._facade_patches(mock.Mock(return_value={"projection_ref": "attempt-result-view-implementation-01"})):
            response = attempt_facade.complete_attempt(params)

        self.assertTrue(response["ok"])
        stored = attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id=self.attempt_id,
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["unresolved"], params["unresolved"])
        self.assertEqual(stored["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)

    def test_finalizing_or_completed_work_stop_does_not_become_worker_failure(self) -> None:
        """The native-stop hook preserves both intermediate success lifecycles."""
        for lifecycle in (
            attempt_protocol.LIFECYCLE_WORK_COMPLETED,
            attempt_protocol.LIFECYCLE_FINALIZING,
        ):
            with self.subTest(lifecycle=lifecycle):
                attempt = {
                    "attempt_id": "implementation-02",
                    "status": "running",
                    "gate": "implementation",
                    "profile": "backend_dev",
                    "dispatch_ref": "dispatch-implementation-02",
                    "host_spawn": {
                        "agent_id": "native.Finalization:02",
                        "task_name": "implementation-finalization",
                        "tool": "spawn_agent",
                        "confirmed_at": "2026-08-22T00:00:00+00:00",
                    },
                }
                state = {
                    "task_id": "native-stop-finalization",
                    "thread_id": "host-parent-session",
                    "attempts": [attempt],
                }
                package: dict[str, object] = {}
                session_write = mock.Mock()
                saved = mock.Mock()
                canonical_result = {
                    "status": "completed",
                    "lifecycle_status": lifecycle,
                    "result_ref": "attempt-result-finalization-02",
                }
                with mock.patch.object(cortex, "select_project_root", return_value=self.project), \
                     mock.patch.object(cortex, "ledger_root", return_value=self.root), \
                     mock.patch.object(cortex, "state_lock", return_value=nullcontext()), \
                     mock.patch.object(cortex, "_host_session_bindings", return_value={
                         "tasks": {state["task_id"]: "host-parent-session"},
                     }), \
                     mock.patch.object(cortex, "_v3_task_state", return_value=(self.project, state, {})), \
                     mock.patch.object(cortex, "_open_blocking_questions", return_value=[]), \
                     mock.patch.object(cortex, "_delegation_package", return_value=package), \
                     mock.patch.object(cortex, "_write_delegation_package"), \
                     mock.patch.object(cortex, "db_put_worker_session", session_write), \
                     mock.patch.object(cortex, "save_state", saved), \
                     mock.patch.object(cortex.attempt_protocol, "get_attempt_result", return_value=canonical_result):
                    result = cortex.finalize_host_worker_stop_from_hook(
                        str(self.project), state["task_id"], "host-parent-session", "native.Finalization:02",
                    )

                self.assertTrue(result["updated"])
                self.assertEqual(result["outcome"], "work_completed_finalization_pending")
                self.assertEqual(attempt["status"], "running")
                self.assertEqual(attempt["host_stop_outcome"], "work_completed_finalization_pending")
                self.assertFalse(attempt["host_resumable"])
                self.assertEqual(attempt["attempt_result_ref"], canonical_result["result_ref"])
                self.assertEqual(package["spawn_status"], "stopped_finalization_pending")
                self.assertFalse(package["resumable"])
                self.assertEqual(session_write.call_args.args[1]["status"], "stopped_recoverable")
                self.assertFalse(session_write.call_args.args[1]["resumable"])
                self.assertEqual(saved.call_args.args[3], "host_stop_finalization_pending")

    def test_terminal_attempt_result_reconciles_exact_worker_session(self) -> None:
        """Terminal canonical results cannot leave their native session live."""
        ledger_db.put_worker_session(self.root, {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "host_agent_id": "native.Implementation:01",
            "host_task_name": "implementation-worker",
            "host_tool": "spawn_agent",
            "status": "running",
            "resumable": True,
        })
        result = {
            "result_ref": "attempt-result-terminal-session-01",
            "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
            "work_completed_at": "2026-08-22T00:00:00+00:00",
            "completed_at": "2026-08-22T00:01:00+00:00",
        }
        with mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade._runtime, "state_lock", return_value=nullcontext()), \
             mock.patch.object(attempt_facade._runtime, "load_state", return_value=(self.project, self.project, self.state)), \
             mock.patch.object(attempt_facade._runtime, "save_state"):
            attempt_facade._mark_attempt(
                self.project,
                self.task_id,
                self.attempt_id,
                lifecycle_status="result_finalized",
                result=result,
            )

        sessions = ledger_db.list_worker_sessions(self.root, self.task_id)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "completed")
        self.assertEqual(sessions[0]["resumable"], 0)
        self.assertEqual(sessions[0]["terminated_at"], result["completed_at"])
        self.assertEqual(self.attempt["worker_session_terminal_status"], "completed")
        self.assertEqual(self.attempt["worker_session_reconciled_at"], result["completed_at"])

    def test_terminal_attempt_result_without_worker_session_fails_closed(self) -> None:
        """A result cannot fabricate a missing native identity during repair."""
        result = {
            "result_ref": "attempt-result-missing-session-01",
            "lifecycle_status": attempt_protocol.LIFECYCLE_BLOCKED,
            "work_completed_at": "2026-08-22T00:00:00+00:00",
        }
        with mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade._runtime, "state_lock", return_value=nullcontext()), \
             mock.patch.object(attempt_facade._runtime, "load_state", return_value=(self.project, self.project, self.state)), \
             mock.patch.object(attempt_facade._runtime, "save_state"):
            with self.assertRaisesRegex(ValueError, "no persisted worker session"):
                attempt_facade._mark_attempt(
                    self.project,
                    self.task_id,
                    self.attempt_id,
                    lifecycle_status="blocked",
                    result=result,
                    terminal_status="blocked",
                )

        self.assertNotIn("worker_session_reconciled_at", self.attempt)

    def test_terminal_result_live_session_is_an_invariant_violation(self) -> None:
        """Completion checks reject snapshots that still advertise live work."""
        attempt = {
            "attempt_id": self.attempt_id,
            "facade_managed": True,
            "status": "running",
            "attempt_result_ref": "attempt-result-live-session-01",
        }
        session = {
            "attempt_id": self.attempt_id,
            "status": "running",
            "resumable": 1,
            "terminated_at": None,
        }
        terminal = {
            "result_ref": attempt["attempt_result_ref"],
            "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
        }
        with mock.patch.object(cortex, "load_task_definition", return_value={"task_id": self.task_id}), \
             mock.patch.object(cortex, "_task_document_root", return_value=self.root), \
             mock.patch.object(cortex, "db_list_worker_sessions", return_value=[session]), \
             mock.patch.object(cortex.attempt_protocol, "get_attempt_result", return_value=terminal):
            violations = cortex._terminal_facade_attempts_with_live_sessions(
                self.project, [attempt],
            )
        self.assertEqual(violations, [self.attempt_id])

    def test_wait_hook_for_finalization_explicitly_forbids_failed_continuation_and_replacement(self) -> None:
        """Coordinator recovery wording cannot accidentally take the failure path."""
        context = cortex_hook.stopped_worker_after_wait_context(
            {"hook_event_name": "PostToolUse", "tool_name": "wait"},
            {
                "current_gates": ["implementation"],
                "attempts": [{
                    "attempt_id": "implementation-03",
                    "gate": "implementation",
                    "host_stop_outcome": "work_completed_finalization_pending",
                    "attempt_result_ref": "attempt-result-finalization-03",
                }],
            },
            "task-ref-finalization",
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("retry complete_attempt only for this same persisted attempt", context)
        self.assertIn("Do not submit status='failed'", context)
        self.assertIn("or spawn a replacement", context)

    def test_context_handoff_keeps_finalization_pending_out_of_failure_recovery(self) -> None:
        """Compaction must not turn the hook's exact pending tag into a failed receipt."""
        state = {
            "task_id": "finalization-context-task",
            "status": "active",
            "current_gates": ["implementation"],
            "current_pipeline": ["implementation"],
            "parallel_groups": [["implementation"]],
            "completed_gates": [],
            "skipped_gates": [],
            "attempts": [{
                "attempt_id": "implementation-04",
                "status": "running",
                "gate": "implementation",
                "profile": "backend_dev",
                "dispatch_ref": "dispatch-implementation-04",
                "lifecycle_status": "finalizing",
                "attempt_result_ref": "attempt-result-finalization-04",
                "host_stopped_at": "2026-08-22T00:00:00+00:00",
                "host_stop_outcome": "work_completed_finalization_pending",
                "host_spawn": {"agent_id": "native.Finalization:04", "task_name": "implementation-finalization"},
            }],
        }
        task = {"user_request": "finalize one persisted attempt", "acceptance_criteria": [], "verification": []}
        plan = {"waves": [{
            "wave_id": "wave-01",
            "gates": ["implementation"],
            "attempt_ids": ["implementation-04"],
        }]}
        handoff = context_handoff._context_handoff(self.project, state, task, plan)

        stopped = handoff["stopped_workers"]
        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0]["finalization_pending"])
        self.assertIsNone(stopped[0]["failure_status"])
        self.assertIsNone(stopped[0]["failure_reason"])
        self.assertFalse(stopped[0]["resumable"])
        self.assertIn("retry complete_attempt on that exact persisted attempt only", handoff["next_action"])
        self.assertNotIn("submit exactly one failed continuation", handoff["next_action"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
