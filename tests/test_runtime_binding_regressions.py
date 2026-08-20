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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
