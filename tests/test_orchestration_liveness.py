"""Regression tests for evidence-based corrective-work liveness."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control


class OrchestrationLivenessTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
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
        task_dir = next((self.ledger / "tasks").iterdir())
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

    @staticmethod
    def _planner_recovery_waves(
        *,
        planner_objective: str | None = None,
        discovery_strategy: str | None = None,
    ) -> list[dict]:
        planner: dict[str, str] = {"phase": "plan"}
        if planner_objective:
            planner["objective"] = planner_objective
        discovery: dict[str, str] = {"phase": "discover"}
        if discovery_strategy:
            discovery["strategy"] = discovery_strategy
        return [{"workers": [planner]}, {"workers": [discovery]}]

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

    def test_paraphrased_equivalent_failure_reasons_still_trip_the_circuit_breaker(self) -> None:
        current = self._start()
        reasons = (
            "network transport unavailable before any project change",
            "The remote connection failed because the network path is unavailable; source remains unchanged.",
            "No source changes occurred because the transport service cannot be reached over the network.",
        )
        for expected, reason in enumerate(reasons, 1):
            current = self._failed_continue(current, reason=reason)
            self.assertTrue(current["ok"], current)
            if expected < 3:
                self.assertEqual(current["outcome"], "ready_to_spawn")
            else:
                self.assertEqual(current["outcome"], "blocked")

        progress = self._state()["rework_progress"]["discover"]
        self.assertEqual(progress["consecutive_identical_iterations"], 3)
        self.assertEqual(progress["failure_classes"], ["infrastructure"])

    def test_paused_resume_rejects_planner_wrapper_around_the_same_failed_route(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        self.assertEqual(current["outcome"], "blocked")

        rejected = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Ask Planner to restate the same discovery route.",
                "payload": {"future_waves": self._planner_recovery_waves()},
            }
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("materially change the failed strategy, pipeline, or verification", rejected["diagnostics"][0]["message"])
        self.assertEqual(self._state()["status"], "blocked")
        self.assertEqual(self._state()["rework_pause"]["status"], "needs_user_decision")

    def test_paused_resume_accepts_a_material_strategy_change(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        resumed = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Use a new offline discovery strategy before any remote transport verification.",
                "payload": {
                    "future_waves": self._planner_recovery_waves(
                        discovery_strategy=(
                            "Use the cached local source for discovery and defer remote transport verification "
                            "until the local analysis is complete."
                        ),
                    ),
                },
            }
        )
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        self.assertEqual(self._state()["status"], "active")
        self.assertNotIn("rework_pause", self._state())

    def test_paused_resume_accepts_matching_infrastructure_remediation(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        resumed = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Repair the unavailable transport before returning to discovery.",
                "payload": {
                    "future_waves": self._planner_recovery_waves(
                        planner_objective=(
                            "Repair the network transport configuration and verify the connection before "
                            "retrying the discovery route."
                        ),
                    ),
                },
            }
        )
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        self.assertNotIn("rework_pause", self._state())


if __name__ == "__main__":
    unittest.main()
