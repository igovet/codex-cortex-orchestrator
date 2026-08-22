"""Atomic dispatch-context preflight regressions for orchestration advance."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control
from cortex_runtime import orchestration_engine


class DispatchContextPreflightTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = control.ledger_root({"project_root": str(self.project)})

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def test_advance_rejects_uncompilable_context_before_any_gate_or_attempt_mutation(self) -> None:
        """A compiler failure must retain the exact active source wave for retry.

        This is deliberately an adversarial legacy-shaped record: one
        requirement has the wrong type.  The test exercises the same canonical
        ContextCompiler boundary that dispatch uses, while proving that
        ``advance`` does not first complete the worker or record its gate.
        """
        task_id = "atomic-context"
        task_dir = self.ledger / "tasks" / task_id
        state = {
            "task_id": task_id,
            "status": "active",
            "revision": 7,
            "attempts": [{
                "attempt_id": "attempt-discover-01",
                "gate": "discover",
                "status": "running",
                "invalidated": False,
            }],
            "completed_gates": [],
            "skipped_gates": [],
            "current_pipeline": ["discover", "review"],
        }
        plan = {
            "waves": [{
                "wave_id": "wave-discover",
                "gates": ["discover"],
                "delegations": [{"gate": "discover", "agent": "explorer"}],
            }],
        }
        task = {
            "user_request": "Continue the active discovery attempt safely.",
            # A historical row can be malformed even though fresh task input
            # rejects it.  Compiler validation must not discover it after the
            # active worker result has been consumed.
            "requirements": ["Retain the valid requirement.", 42],
        }
        request = {
            "operation": "advance",
            "submission_id": "atomic-context-advance",
            "project_root": str(self.project),
            "principal": "thread-a",
            "task_id": task_id,
            "wave_id": "wave-discover",
            "completions": [{
                "attempt_id": "attempt-discover-01",
                "status": "passed",
                "attempt_result_ref": "attempt-result-discover-01",
            }],
        }

        with (
            mock.patch.object(orchestration_engine, "load_state", return_value=(self.ledger, task_dir, state)),
            mock.patch.object(orchestration_engine, "authorize"),
            mock.patch.object(orchestration_engine, "_load_orchestrate_plan", return_value=plan),
            mock.patch.object(orchestration_engine, "load_task_definition", return_value=task),
            mock.patch.object(orchestration_engine, "_complete_orchestrate_attempt") as completed,
            mock.patch.object(orchestration_engine, "record_gate") as recorded_gate,
        ):
            rejected = orchestration_engine.orchestrate(request)

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "orchestrate_validation_failed")
        self.assertEqual(rejected["phase"], "started")
        self.assertIn("canonical requirements must be an array of strings", rejected["diagnostics"][0]["message"])
        completed.assert_not_called()
        recorded_gate.assert_not_called()
        self.assertEqual(state["attempts"][0]["status"], "running")
        self.assertEqual(state["completed_gates"], [])
        self.assertEqual(state["revision"], 7)

        receipt = control.db_get_operation(self.ledger, request["submission_id"])
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["phase"], "started")

        # The exact same request can be reserved again; no gates_recorded
        # partial state forces a synthetic completion, replacement worker, or
        # altered continuation identity after the task data is repaired.
        _path, retried, replay = orchestration_engine._begin_orchestrate_transaction(self.ledger, request)
        self.assertIsNone(replay)
        self.assertEqual(retried["status"], "running")
        self.assertEqual(retried["phase"], "started")
