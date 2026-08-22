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
from cortex_runtime import communication, orchestration_engine


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

    def test_orchestration_output_has_segregated_human_and_internal_views(self) -> None:
        response = orchestration_engine._segregate_orchestration_output({
            "schema": "cortex/orchestration/v3",
            "ok": True,
            "task_id": "private-task-ref",
            "wave_id": "wave-1",
            "state": "awaiting_plan_approval",
            "spawn_requests": [{"attempt_id": "attempt-1"}],
            "diagnostics": [],
            "next_action": "call plan_approval with request_id=private-request",
        })
        self.assertEqual(response["internal"]["task_ref"], "private-task-ref")
        self.assertEqual(response["internal"]["dispatches"], [{"attempt_id": "attempt-1"}])
        self.assertIn("request_id=private-request", response["internal"]["next_action"])
        self.assertTrue(response["user_view"]["requires_user_decision"])
        self.assertEqual(response["user_view"]["next_step"], "Choose approve, revise, or cancel.")
        self.assertNotIn("private-task-ref", str(response["user_view"]))
        self.assertNotIn("attempt-1", str(response["user_view"]))
        self.assertNotIn("request_id", str(response["user_view"]))

    def test_natural_progress_views_keep_all_visible_fields_plain(self) -> None:
        for state in ("ready_to_spawn", "completed"):
            with self.subTest(state=state):
                response = orchestration_engine._segregate_orchestration_output({
                    "schema": "cortex/orchestration/v3",
                    "ok": True,
                    "state": state,
                    "communication_profile": "natural",
                    "user_language": "en",
                    "spawn_requests": [],
                    "diagnostics": [],
                })
                view = response["user_view"]
                visible = " ".join(str(view.get(field) or "") for field in (
                    "message", "why_it_matters", "next_step", "recommendation", "risks"
                ))
                self.assertNotRegex(visible, communication._TECHNICAL_RE)
                self.assertTrue(view["quality"]["ok"], view)

    def test_remediation_heuristic_ignores_negated_action_but_accepts_later_clause(self) -> None:
        self.assertFalse(
            orchestration_engine._has_non_negated_term(
                "Do not restart the service; investigate the timeout.", "restart"
            )
        )
        self.assertTrue(
            orchestration_engine._has_non_negated_term(
                "Do not restart the service; repair the network configuration.", "repair"
            )
        )

    def test_failure_classifier_does_not_treat_negated_network_as_infrastructure(self) -> None:
        self.assertEqual(
            orchestration_engine._failure_class_from_completion(
                {"reason": "This was not a network timeout; the product assertion failed."}
            ),
            "product",
        )
        self.assertEqual(
            orchestration_engine._failure_class_from_completion(
                {"reason": "Network timeout prevented the dependency download."}
            ),
            "infrastructure",
        )

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

    def _start_parallel(self) -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Exercise independent parallel corrective routes.",
                "complexity": "C1",
                "acceptance_criteria": ["Independent work is not stranded by a sibling retry."],
                "verification": ["Exercise a partial parallel wave."],
            },
            "waves": [
                {"workers": [{"phase": "qa"}, {"phase": "security"}]},
                {"workers": [{"phase": "review"}]},
            ],
        })



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
        pause = state["rework_pauses"]["discover"]
        self.assertEqual(pause["status"], "needs_user_decision")
        self.assertEqual(pause["failure_class"], "infrastructure")
        self.assertEqual(pause["consecutive_identical_iterations"], 3)
        self.assertEqual(set(state["rework_pauses"]), {"discover"})
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
        self.assertNotIn("discover", state.get("rework_pauses", {}))
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
        state = self._state()
        self.assertEqual(state["rework_pauses"]["discover"]["status"], "needs_user_decision")

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
        self.assertNotIn("discover", self._state().get("rework_pauses", {}))

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
        self.assertNotIn("discover", self._state().get("rework_pauses", {}))



    def test_multi_gate_recovery_requires_and_unpauses_only_named_gate(self) -> None:
        current = self._start_parallel()
        for _ in range(3):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"], "step": current["step"],
                "results": [
                    {
                        "worker": 1, "status": "failed", "reason": "network transport unavailable",
                        "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
                    },
                    {
                        "worker": 2, "status": "failed", "reason": "network transport unavailable",
                        "dispatch_ref": current["dispatches"][1]["dispatch_ref"],
                    },
                ],
            })
        self.assertEqual(current["outcome"], "blocked")
        recovery_waves = [
            {"workers": [{"phase": "plan", "objective": "Repair network transport configuration before retry."}]},
            {"workers": [{"phase": "qa"}, {"phase": "security"}]},
        ]
        ambiguous = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": current["task_ref"], "intent": "resume",
            "reason": "Recover the failed route.", "payload": {"future_waves": recovery_waves},
        })
        self.assertFalse(ambiguous["ok"])
        self.assertIn("name the intended rework gate", ambiguous["diagnostics"][0]["message"])
        resumed = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": current["task_ref"], "intent": "resume",
            "reason": "Repair network transport before the QA retry.",
            "payload": {"rework": "qa", "future_waves": recovery_waves},
        })
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        state = self._state()
        self.assertEqual(state["status"], "active")
        self.assertEqual(set(state["rework_pauses"]), {"security"})


if __name__ == "__main__":
    unittest.main()
