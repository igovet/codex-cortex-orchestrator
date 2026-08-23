"""Focused regressions for facade-to-runtime composition bindings."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
import cortex_hook
from cortex_runtime import gate_transitions


class RuntimeBindingRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.host_store = Path(self.temp.name) / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)
        self._previous_host_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self) -> None:
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
        self.temp.cleanup()

    def _started_task(self) -> tuple[Path, dict]:
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Preserve runtime binding behavior.",
                "complexity": "C1",
                "acceptance_criteria": ["The runtime binding behavior is preserved."],
                "verification": ["Run the focused runtime binding regression tests."],
            },
            "waves": [{"workers": [{"phase": "plan"}]}],
        })
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        return task_dir, control.load_task_state_for_artifact(task_dir)

    def test_sqlite_question_blocks_without_a_question_projection_directory(self) -> None:
        task_dir, state = self._started_task()
        attempt = state["attempts"][0]
        asked = control.worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "action": "ask",
            "question": "Which externally visible behavior is authoritative?",
            "recommendation": "Preserve the currently documented public behavior unless repository evidence proves it is incorrect.",
            "recommended_answer": "Preserve the currently documented public behavior.",
        })
        self.assertTrue(asked["ok"], asked)
        self.assertFalse((task_dir / "questions").exists())
        self.assertEqual(
            [item["question_id"] for item in control._open_blocking_questions(task_dir, state)],
            [asked["question_ref"]],
        )

    def test_auto_handoff_uses_task_identity_and_live_facade_seam(self) -> None:
        task_dir, state = self._started_task()
        with mock.patch.object(control, "handoff", return_value={"recorded": True}) as handoff:
            result = control._auto_handoff(
                {"project_root": str(self.project), "principal": "stale-session"},
                task_dir,
                state,
                "Close the task.",
            )
        self.assertEqual(result, {"recorded": True})
        handoff.assert_called_once()
        payload = handoff.call_args.args[0]
        self.assertEqual(payload["principal"], state["principal"])
        self.assertEqual(payload["thread_id"], state["thread_id"])

    def _handoff_params(self, state: dict) -> dict:
        return {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "expected_revision": state["revision"],
            "completed": ["coordinator checkpoint"],
            "files": [],
            "next_action": "continue only through the canonical worker lifecycle",
        }

    def test_handoff_refuses_pending_facade_attempt_without_mutating_state(self) -> None:
        """A generic handoff cannot replace a pending native worker result."""
        task_dir, state = self._started_task()
        attempt = state["attempts"][0]

        response = control.handoff(self._handoff_params(state))

        self.assertFalse(response["recorded"])
        self.assertTrue(response["recoverable"])
        self.assertEqual(response["reason"], "active_attempt_result_pending")
        self.assertEqual(response["candidate_attempt_ids"], [attempt["attempt_id"]])
        self.assertEqual(response["active_host_checkpoint_attempt_ids"], [])
        persisted = control.load_task_state_for_artifact(task_dir)
        self.assertFalse(persisted.get("handoff_created"))
        self.assertEqual(persisted["revision"], state["revision"])

    def test_bound_host_child_is_recovery_checkpoint_not_handoff_completion(self) -> None:
        """A known child id still needs its finalized AttemptResult."""
        task_dir, state = self._started_task()
        attempt = state["attempts"][0]
        attempt.update({
            "status": "running",
            "lifecycle_status": "running",
            "host_spawn": {"agent_id": "native.RuntimeGuard:01"},
        })
        control.db_update_task_state(self.ledger, state)

        response = control.handoff(self._handoff_params(state))

        self.assertFalse(response["recorded"])
        self.assertEqual(response["reason"], "active_attempt_result_pending")
        self.assertEqual(response["candidate_attempt_ids"], [attempt["attempt_id"]])
        self.assertEqual(response["active_host_checkpoint_attempt_ids"], [attempt["attempt_id"]])
        self.assertFalse(control.load_task_state_for_artifact(task_dir).get("handoff_created"))

    def test_gate_and_terminal_backstops_refuse_unfinalized_facade_attempt(self) -> None:
        """Evidence cannot coerce a live facade row to passed or terminal."""
        task_dir, state = self._started_task()
        attempt = state["attempts"][0]
        gate = str(attempt["gate"])
        evidence = [{"evidence_id": "evidence-runtime-guard", "attempt_id": attempt["attempt_id"]}]

        current, recovery = gate_transitions._validate_pass_evidence(
            task_dir,
            state,
            {},
            requested_gate=gate,
            gate=gate,
            outcome="passed",
            revision_correction=None,
            gate_evidence=evidence,
            gate_attempts=[attempt],
            non_terminal_attempts=[attempt],
            terminal_non_success_attempts=[],
            passed_attempts=[],
        )

        self.assertEqual(current, [])
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertEqual(recovery["reason"], "active_attempt_result_pending")
        self.assertEqual(recovery["candidate_attempt_ids"], [attempt["attempt_id"]])
        with self.assertRaisesRegex(ValueError, "active_attempt_result_pending"):
            control.validate_completion_invariants(state, artifact_root=self.ledger)

    def test_facade_finalizer_refuses_resultless_success_before_projection(self) -> None:
        """The host finalizer cannot create the terminal-status guard bypass."""
        task_dir, state = self._started_task()
        attempt = state["attempts"][0]
        attempt.update({"status": "running", "lifecycle_status": "running"})
        control.db_update_task_state(self.ledger, state)

        response = control.finalize_attempt({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "expected_revision": state["revision"],
            "attempt_id": attempt["attempt_id"],
            "status": "passed",
        })

        self.assertFalse(response["recorded"])
        self.assertTrue(response["recoverable"])
        self.assertEqual(response["reason"], "passed_attempt_result_required")
        self.assertEqual(response["next_action"], "complete_attempt")
        persisted = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(persisted["attempts"][0]["status"], "running")

    def test_stop_hook_never_blocks_pending_unbound_facade_dispatch(self) -> None:
        """The command hook is telemetry-only; server lifecycle owns recovery."""
        _task_dir, state = self._started_task()

        block = cortex_hook.active_worker_stop_block(
            {"hook_event_name": "Stop", "stop_hook_active": False}, state
        )

        self.assertIsNone(block)

    def test_stop_hook_does_not_block_stale_terminal_host_binding(self) -> None:
        """A stale host binding must not make the command hook block."""
        _task_dir, state = self._started_task()
        attempt = state["attempts"][0]
        attempt.update({
            "status": "running",
            "lifecycle_status": "running",
            "host_spawn": {"agent_id": "native.Stale:01"},
        })
        block = cortex_hook.active_worker_stop_block(
            {"hook_event_name": "Stop", "stop_hook_active": False, "cwd": str(self.project)},
            state,
        )
        self.assertIsNone(block)

    def test_stop_hook_allows_parent_turn_for_paused_question(self) -> None:
        """An open durable question takes precedence over a bound child."""
        state = {
            "status": "active",
            "attempts": [{
                "facade_managed": True,
                "status": "running",
                "lifecycle_status": "paused_awaiting_user",
                "host_stop_outcome": "awaiting_user",
                "host_question_refs": ["question-0001"],
                "host_spawn": {"agent_id": "native-question"},
            }],
        }
        block = cortex_hook.active_worker_stop_block(
            {"hook_event_name": "Stop", "stop_hook_active": False}, state
        )
        self.assertIsNone(block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
