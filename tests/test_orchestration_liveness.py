"""Regression tests for evidence-based corrective-work liveness."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control


class OrchestrationLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _start(self) -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Investigate the repeated failure without claiming a false result.",
                "complexity": "C1",
                "acceptance_criteria": ["A verified result is produced or a user decision is requested."],
                "verification": ["Exercise the corrective retry lifecycle."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })

    def _state(self) -> dict:
        task_dir = next((self.project / ".codex" / "cortex" / "tasks").iterdir())
        return control.load_task_state_for_artifact(task_dir)

    def _failed_continue(self, current: dict, *, reason: str, next_strategy: str | None = None) -> dict:
        result = {
            "status": "failed",
            "reason": reason,
            "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
        }
        if next_strategy:
            result["next_strategy"] = next_strategy
        return control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "step": current["step"],
            "results": [result],
        })

    def test_three_identical_infrastructure_failures_pause_without_false_terminal_result(self) -> None:
        current = self._start()
        reason = "network transport unavailable before any project change"
        for expected in (1, 2):
            current = self._failed_continue(current, reason=reason)
            self.assertTrue(current["ok"], current)
            self.assertEqual(current["outcome"], "ready_to_spawn")
            self.assertEqual(len(current["dispatches"]), 1)
            progress = self._state()["rework_progress"]["discover"]
            self.assertEqual(progress["consecutive_identical_iterations"], expected)

        paused = self._failed_continue(current, reason=reason)
        self.assertTrue(paused["ok"], paused)
        self.assertEqual(paused["outcome"], "blocked")
        self.assertEqual(paused["dispatches"], [])
        state = self._state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["rework_pause"]["status"], "needs_user_decision")
        self.assertEqual(state["rework_pause"]["failure_class"], "infrastructure")
        self.assertEqual(state["rework_pause"]["consecutive_identical_iterations"], 3)
        self.assertNotIn("discover", state["completed_gates"])
        self.assertNotEqual(state["status"], "completed")

        rejected_resume = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": paused["task_ref"],
            "intent": "resume",
            "reason": "Retry the identical failed path.",
        })
        self.assertFalse(rejected_resume["ok"])
        self.assertIn("Planner-first recovery plan", rejected_resume["diagnostics"][0]["message"])

    def test_material_strategy_change_resets_the_no_progress_streak(self) -> None:
        current = self._start()
        reason = "network transport unavailable before any project change"
        current = self._failed_continue(current, reason=reason)
        current = self._failed_continue(current, reason=reason)
        changed = self._failed_continue(
            current,
            reason=reason,
            next_strategy="Use the cached offline source and verify transport only after the local analysis completes.",
        )
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["outcome"], "ready_to_spawn")
        state = self._state()
        self.assertEqual(state["status"], "active")
        self.assertNotIn("rework_pause", state)
        self.assertEqual(state["rework_progress"]["discover"]["consecutive_identical_iterations"], 1)


if __name__ == "__main__":
    unittest.main()
