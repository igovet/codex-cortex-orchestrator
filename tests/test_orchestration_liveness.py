"""Regression tests for evidence-based corrective-work liveness."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_awaiting_user_keeps_short_visible_status_and_machine_guard(self) -> None:
        """Question pauses expose plain status while retaining internal receipt data."""
        response = orchestration_engine._segregate_orchestration_output({
            "schema": "cortex/orchestration/v3",
            "ok": True,
            "state": "needs_input",
            "result": {
                "outcome": "awaiting_user",
                "question_ref": "private-question-ref",
                "next_action": "answer the open question before resuming",
            },
            "task_id": "private-task-ref",
            "wave_id": "wave-question",
            "next_action": "answer the open question before resuming",
            "communication_profile": "natural",
            "user_language": "en",
        })
        visible = json.dumps(response["user_view"], sort_keys=True)
        self.assertEqual(response["user_view"]["message_type"], "decision_required")
        self.assertTrue(response["user_view"]["requires_user_decision"])
        self.assertNotIn("private-question-ref", visible)
        self.assertNotIn("private-task-ref", visible)
        self.assertNotIn("next_action", visible)
        self.assertEqual(response["internal"]["result"]["outcome"], "awaiting_user")
        self.assertEqual(response["internal"]["result"]["question_ref"], "private-question-ref")

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

    def _passed_plan_rework_fixture(self, *, changed: bool = False) -> tuple[dict, dict, list]:
        """Build one passed-plan future-wave contract without worker I/O."""
        implementation = {
            "gate": "implementation", "agent": "general",
            "objective": "Implement the changed behavior." if changed else "Implement the behavior.",
            "strategy": "new strategy" if changed else "default",
            "allowed_paths": ["."], "acceptance_criteria": ["The behavior works."],
            "verification": ["Run the focused check."],
        }
        planner = {
            "gate": "plan", "agent": "planner", "objective": "Reassess the approved plan.",
            "strategy": "default", "allowed_paths": ["."],
            "acceptance_criteria": ["The plan is coherent."],
            "verification": ["Verify the plan."],
        }
        old_implementation = dict(implementation)
        if changed:
            old_implementation.update({"objective": "Implement the behavior.", "strategy": "default"})
        old_plan = {
            "schema": "cortex/orchestration-plan/v3", "task_id": "rework-loop",
            "host_capabilities": {},
            "waves": [
                {"wave_id": "wave-01", "gates": ["plan"], "status": "completed", "delegations": [planner]},
                {"wave_id": "wave-02", "gates": ["implementation"], "status": "pending", "delegations": [old_implementation]},
            ],
            "history": [],
        }
        state = {
            "task_id": "rework-loop", "revision": 4, "status": "active",
            "current_pipeline": ["plan", "implementation"],
            "parallel_groups": [["plan"], ["implementation"]],
            "completed_gates": ["plan"], "skipped_gates": [], "attempts": [],
            "pipeline_contract_version": 1,
            "plan_approval": {"policy": "auto", "status": "not_required", "history": []},
            "evidence": [], "pipeline_changes": [],
        }
        future = [{"wave_id": "wave-03", "gates": ["plan"], "status": "pending", "delegations": [planner]},
                  {"wave_id": "wave-04", "gates": ["implementation"], "status": "pending", "delegations": [implementation]}]
        return state, old_plan, future

    def _call_rework_replace(self, state, plan, future):
        task = {"user_request": "rework loop", "complexity": "C1", "acceptance_criteria": ["works"], "verification": ["check"]}
        patches = [
            mock.patch.object(orchestration_engine, "load_task_definition", return_value=task),
            mock.patch.object(orchestration_engine, "_normalize_orchestrate_waves", return_value=(future, {"pipeline": ["plan", "implementation"]})),
            mock.patch.object(orchestration_engine, "_validate_pending_implementation_retained"),
            mock.patch.object(orchestration_engine, "save_state"),
            mock.patch.object(orchestration_engine, "_write_orchestrate_plan"),
        ]
        return patches

    def test_passed_plan_identical_rework_is_idempotent_without_invalidation_or_new_attempt(self):
        state, plan, future = self._passed_plan_rework_fixture()
        with mock.patch.object(orchestration_engine, "update_pipeline") as update_pipeline:
            with mock.patch.object(orchestration_engine, "reassess_pipeline"):
                with mock.patch.object(orchestration_engine, "load_task_definition", return_value={"user_request": "rework loop"}), \
                     mock.patch.object(orchestration_engine, "_normalize_orchestrate_waves", return_value=(future, {"pipeline": ["plan", "implementation"]})), \
                     mock.patch.object(orchestration_engine, "_validate_pending_implementation_retained"), \
                     mock.patch.object(orchestration_engine, "save_state"), \
                     mock.patch.object(orchestration_engine, "_write_orchestrate_plan"):
                    with self.assertRaises(orchestration_engine.ReworkRequestIdempotent) as caught:
                        orchestration_engine._replace_future_orchestrate_waves(
                            {"project_root": str(self.project), "allow_rework": True, "reason": "same approved route"},
                            self.project, state, plan, future,
                        )
        self.assertEqual(caught.exception.state["completed_gates"], ["plan"])
        self.assertEqual(caught.exception.state["rework_history"][-1]["outcome"], "idempotent")
        self.assertFalse(update_pipeline.called)
        self.assertEqual(state["attempts"], [])

    def test_material_passed_plan_rework_is_recorded_once_as_planner_first(self):
        state, plan, future = self._passed_plan_rework_fixture(changed=True)
        updated = dict(state, completed_gates=[])
        with mock.patch.object(orchestration_engine, "update_pipeline", return_value={"state": updated}) as update_pipeline, \
             mock.patch.object(orchestration_engine, "reassess_pipeline", return_value={"state": updated}), \
             mock.patch.object(orchestration_engine, "load_task_definition", return_value={"user_request": "rework loop", "complexity": "C1"}), \
             mock.patch.object(orchestration_engine, "_normalize_orchestrate_waves", return_value=(future, {"pipeline": ["plan", "implementation"]})), \
             mock.patch.object(orchestration_engine, "_validate_pending_implementation_retained"), \
             mock.patch.object(orchestration_engine, "save_state"), \
             mock.patch.object(orchestration_engine, "_write_orchestrate_plan"):
            result_state, _ = orchestration_engine._replace_future_orchestrate_waves(
                {"project_root": str(self.project), "allow_rework": True, "reason": "new verified evidence"},
                self.project, state, plan, future,
            )
        self.assertTrue(update_pipeline.called)
        self.assertEqual(result_state["rework_history"][-1]["material_change"], True)
        self.assertEqual(result_state["rework_history"][-1]["outcome"], "applied")

    def test_repeated_material_rework_digest_pauses_before_invalidation_or_spawn(self):
        state, plan, future = self._passed_plan_rework_fixture(changed=True)
        candidate = {**plan, "waves": [*plan["waves"], *future]}
        digest = orchestration_engine.digest_text(json.dumps({
            "completed_gate_rework": ["plan"],
            "future_pipeline": orchestration_engine._semantic_future_pipeline(candidate),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        state["rework_history"] = [{"request_digest": digest, "material_change": True, "outcome": "applied"}]
        with mock.patch.object(orchestration_engine, "update_pipeline") as update_pipeline, \
             mock.patch.object(orchestration_engine, "load_task_definition", return_value={"user_request": "rework loop", "complexity": "C1"}), \
             mock.patch.object(orchestration_engine, "_normalize_orchestrate_waves", return_value=(future, {"pipeline": ["plan", "implementation"]})), \
             mock.patch.object(orchestration_engine, "_validate_pending_implementation_retained"), \
             mock.patch.object(orchestration_engine, "save_state"), \
             mock.patch.object(orchestration_engine, "_write_orchestrate_plan"):
            with self.assertRaises(orchestration_engine.ReworkCircuitBroken):
                orchestration_engine._replace_future_orchestrate_waves(
                    {"project_root": str(self.project), "allow_rework": True, "reason": "same new evidence"},
                    self.project, state, plan, future,
                )
        self.assertEqual(state["status"], "needs_input")
        self.assertEqual(state["rework_pauses"]["plan"]["reason"], "repeated_material_rework_digest")
        self.assertFalse(update_pipeline.called)

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
