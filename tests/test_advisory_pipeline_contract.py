"""Acceptance contract for orchestrator-owned pipeline decisions.

These tests deliberately exercise the public/low-level boundaries where the
old Cortex policy layer could turn an advisory convention into a pipeline
veto.  They are intentionally independent of a live worker and contain no
project mutations: a worker/host is not needed to prove that a policy finding
is not a user question or a hard stop.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control  # noqa: F401  # composition root binds runtime symbols
from cortex_runtime import attempt_protocol, gate_transitions, mcp_api, orchestration_engine


class AdvisoryPipelineContractTests(unittest.TestCase):
    @staticmethod
    def _state(**overrides: object) -> dict[str, object]:
        state: dict[str, object] = {
            "task_id": "task-advisory-contract",
            "status": "active",
            "revision": 3,
            "current_pipeline": ["implementation", "documentation", "close"],
            "completed_gates": [],
            "skipped_gates": [],
            "attempts": [],
            "governance": {"effective_mode": "full"},
            "plan_approval": {"policy": "required", "status": "approved"},
        }
        state.update(overrides)
        return state

    def test_full_governance_skip_is_assessment_not_policy_veto(self) -> None:
        """Full governance may recommend a review, but cannot deny a choice."""
        decision = gate_transitions._validate_skip(
            self._state(),
            {"skip_reason": "The orchestrator selected the executable route."},
            "governance_activation",
            "skipped",
            None,
        )
        # A non-None result is allowed only when it is explicitly advisory;
        # the retired ``mandatory_gate`` result is a pipeline veto.
        if decision is not None:
            self.assertNotEqual(decision.get("reason"), "mandatory_gate")
            self.assertFalse(decision.get("policy_veto", True))
            self.assertIn(decision.get("severity"), {"info", "warning", "high"})

    def test_full_governance_canonicalization_preserves_chosen_order(self) -> None:
        chosen = ["implementation", "close", "governance_close", "governance_activation"]
        state = self._state()
        normalized = control.canonicalize_full_governance_pipeline(state, chosen)
        self.assertEqual(normalized, chosen)
        advice = state.get("pipeline_advice") or []
        self.assertTrue(any(item.get("code") == "governance_pipeline_recommendation" for item in advice))
        recommendation = next(item for item in advice if item.get("code") == "governance_pipeline_recommendation")
        self.assertEqual(recommendation["recommended_pipeline"], [
            "governance_activation", "implementation", "governance_close", "close",
        ])

    def test_approved_plan_delivery_gap_is_advisory_only(self) -> None:
        state = self._state(
            completed_gates=["plan"],
            plan_approval={"policy": "auto", "status": "approved"},
            attempts=[],
        )
        manifest = {"work_packages": [{"artifact_path": "planning/package.json"}]}
        package = {
            "package": {
                "microtasks": [{"profile": "backend_dev"}],
                "allowed_paths": ["plugins/cortex"],
            }
        }
        with mock.patch.object(orchestration_engine, "current_planning_manifest", return_value=manifest), \
             mock.patch.object(orchestration_engine, "read_immutable_json_artifact", return_value=(package, {})):
            required, missing = orchestration_engine._approved_plan_delivery_gap(
                Path("/tmp/advisory-task"), state, {"waves": []}
            )
        self.assertEqual(required, [])
        self.assertEqual(missing, ["implementation", "qa", "review", "documentation", "close"])
        advice = state.get("pipeline_advice") or []
        self.assertTrue(any(item.get("code") == "approved_plan_delivery_coverage_advisory" for item in advice))

    def test_missing_documentation_is_advisory_and_does_not_require_user(self) -> None:
        decision = gate_transitions._validate_skip(
            self._state(require_delegation=True),
            {"skip_reason": "Documentation is intentionally deferred by the orchestrator."},
            "documentation",
            "skipped",
            None,
        )
        if decision is not None:
            self.assertNotEqual(decision.get("reason"), "mandatory_gate")
            self.assertFalse(decision.get("requires_user_decision", False))
            self.assertFalse(decision.get("policy_veto", True))

    def test_stale_plan_basis_is_advice_and_chosen_implementation_remains_executable(self) -> None:
        """A stale recommendation must not override the coordinator choice."""
        state = self._state(
            current_pipeline=["implementation", "qa"],
            completed_gates=["plan"],
            plan_approval={"policy": "required", "status": "approved", "plan_result_ref": "attempt-plan"},
            attempts=[{
                "attempt_id": "attempt-implementation",
                "gate": "implementation",
                "status": "running",
                "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
                "invalidated": False,
            }],
            chosen_pipeline=["implementation", "qa"],
            recommended_pipeline=["plan", "implementation", "qa"],
            pipeline_advice=[{"code": "stale_plan", "severity": "warning"}],
        )
        plan = {"semantic_pipeline_version": 2, "waves": []}
        with mock.patch.object(orchestration_engine, "_pipeline_contract_version", return_value=2), \
             mock.patch.object(orchestration_engine, "active_gates", return_value=["implementation"]), \
             mock.patch.object(orchestration_engine, "_current_plan_basis", return_value={
                 "plan_revision": "new", "plan_result_ref": "attempt-plan",
                 "verified_predecessor_digest": "new", "semantic_pipeline_version": 2,
                 "semantic_future_pipeline_digest": "new",
             }):
            # A coordinator-owned chosen route is executable even when the
            # advisory plan basis differs.  The implementation must not throw
            # PlanReapprovalRequired from this condition.
            try:
                orchestration_engine._assert_approved_plan_fresh(
                    Path("/tmp/advisory-task"), state, plan
                )
            except orchestration_engine.PlanReapprovalRequired as exc:
                self.fail(f"stale plan advice became a policy veto: {exc}")
        summary = orchestration_engine._orchestrate_summary(state)
        self.assertEqual(summary.get("chosen_pipeline"), ["implementation", "qa"])
        self.assertEqual(summary.get("recommended_pipeline"), ["plan", "implementation", "qa"])

    def test_material_rework_without_planner_is_admitted_with_advice(self) -> None:
        """Planner-first is a recommendation, not an authorization boundary."""
        response = orchestration_engine._orchestrate_response(
            "advance",
            self._state(
                current_pipeline=["implementation", "qa"],
                chosen_pipeline=["implementation", "qa"],
                recommended_pipeline=["plan", "implementation", "qa"],
                pipeline_advice=[{"code": "planner_recommended", "severity": "warning"}],
            ),
            diagnostics=[{"code": "material_rework", "severity": "warning"}],
        )
        self.assertTrue(response["ok"])
        self.assertNotIn(response["state"], {"blocked", "plan_reapproval_required", "rework_preflight_required"})
        self.assertFalse(response["user_view"]["requires_user_decision"])
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])

    def test_three_identical_failures_remain_forward_progress_with_strong_advice(self) -> None:
        response = orchestration_engine._orchestrate_response(
            "advance",
            self._state(
                rework_progress={"implementation": {"consecutive_identical_iterations": 3}},
                pipeline_advice=[{"code": "repeated_failure", "severity": "high"}],
                attempts=[{
                    "attempt_id": "attempt-implementation",
                    "gate": "implementation",
                    "status": "running",
                    "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
                    "invalidated": False,
                }],
            ),
            diagnostics=[{"code": "repeated_failure", "severity": "high"}],
        )
        self.assertTrue(response["ok"])
        self.assertFalse(response["user_view"]["requires_user_decision"])
        self.assertNotIn(response["state"], {"blocked", "needs_input"})
        self.assertNotIn("resolve the Cortex blocker", response["next_action"].lower())

    def test_internal_worker_and_ledger_recovery_never_becomes_user_question(self) -> None:
        for code in ("worker_result_missing", "ledger_recovery_required", "dispatch_recovery_required"):
            with self.subTest(code=code):
                response = mcp_api.v3_response(
                    {
                        "ok": False,
                        "state": "error",
                        "code": code,
                        "diagnostics": [{"code": code, "severity": "high"}],
                        "result": {"outcome": "technical_recovery", "requires_user_decision": False},
                    },
                    "task-advisory-contract",
                    native_arguments=lambda request: {},
                    public_schema="cortex/test/v1",
                    coordinator_lock="internal-only",
                    include_result=True,
                )
                self.assertFalse(response["user_view"]["requires_user_decision"])
                self.assertNotEqual(response["user_view"]["message_type"], "decision_required")

    def test_real_requirement_ambiguity_is_the_only_matrix_case_that_can_pause_chat(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": False,
                "state": "needs_input",
                "code": "question_required",
                "result": {
                    "outcome": "question",
                    "question": "Which user-visible behavior should be authoritative?",
                    "requires_user_decision": True,
                },
            },
            "task-advisory-contract",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="internal-only",
            include_result=True,
        )
        self.assertTrue(response["user_view"]["requires_user_decision"])
        self.assertEqual(response["user_view"]["message_type"], "decision_required")
        self.assertTrue(mcp_api._is_user_decision_event("needs_input", response["result"]))

    def test_required_plan_approval_payload_is_durable_explicit_intent(self) -> None:
        state = self._state(
            plan_approval={"policy": "required", "status": "pending_plan", "user_requested": True},
            plan_approval_user_requested=True,
        )
        summary = orchestration_engine._orchestrate_summary(state)
        self.assertTrue(summary["plan_approval"]["user_requested"])
        self.assertFalse(mcp_api._explicit_plan_approval_requested({
            "task": {"user_request": "show me the plan first", "plan_approval": "required"},
        }))
        self.assertTrue(mcp_api._explicit_plan_approval_requested({
            "task": {
                "user_request": "show me the plan first",
                "plan_approval": "required",
                "plan_approval_user_requested": True,
            },
        }))

        # A legacy policy projection without the user-authored task field is
        # advisory and must not be promoted into a visible approval request.
        self.assertFalse(mcp_api._explicit_plan_approval_requested({
            "state_summary": {"plan_approval": {"policy": "required", "status": "pending_plan"}},
        }))

    def test_legacy_plan_pause_and_non_terminal_completion_are_recoverable(self) -> None:
        legacy = self._state(
            plan_approval={"policy": "required", "status": "awaiting_user"},
        )
        self.assertFalse(control._plan_approval_is_pending(legacy))
        self.assertTrue(control._reconcile_legacy_plan_approval(legacy))
        self.assertEqual(legacy["plan_approval"]["status"], "not_required")
        self.assertTrue(any(item["code"] == "legacy_plan_approval_ignored" for item in legacy["pipeline_advice"]))

        lifecycle = self._state(
            status="completed",
            require_handoff=True,
            attempts=[{"attempt_id": "attempt-live", "gate": "implementation", "status": "running"}],
        )
        control.validate_completion_invariants(lifecycle)
        self.assertEqual(lifecycle["status"], "active")
        self.assertTrue(any(item["code"] == "completion_attempts_still_running" for item in lifecycle["completion_advice"]))

    def test_governance_trigger_at_continue_boundary_is_advice_not_pipeline_veto(self) -> None:
        state = self._state(
            governance={"effective_mode": "minimal"},
            chosen_pipeline=["implementation", "qa"],
            recommended_pipeline=["implementation", "qa"],
        )
        task = {"user_request": "continue the implementation", "complexity": "C2", "governance_mode": "auto"}
        with mock.patch.object(control, "ledger_root", return_value=Path("/tmp/advisory-ledger")), \
             mock.patch.object(control, "resolve_governance", return_value={"effective_mode": "full", "reasons": ["security"]}):
            assessment = control._governance_boundary_recheck(
                {"project_root": "/tmp/advisory-project"},
                task,
                state,
                future_waves={"risk_triggers": ["security"]},
                results=[],
            )
        self.assertEqual(assessment["advisory"]["recommended_mode"], "full")
        self.assertEqual(assessment["advisory"]["recommended_pipeline"], ["implementation", "qa"])
        self.assertTrue(assessment["advisory"]["chosen_pipeline_unchanged"])
        self.assertEqual(state["chosen_pipeline"], ["implementation", "qa"])

    def test_stale_needs_input_projection_is_silent_recovery_but_real_question_remains_visible(self) -> None:
        stale = self._state(status="needs_input", attempts=[])
        self.assertEqual(orchestration_engine._orchestrate_state_name(stale), "recovery_pending")
        stale_response = orchestration_engine._orchestrate_response("inspect", stale)
        self.assertFalse(stale_response["user_view"]["requires_user_decision"])
        self.assertIn("automatically", stale_response["user_view"]["next_step"].lower())

        question = self._state(
            status="needs_input",
            attempts=[{
                "attempt_id": "attempt-question",
                "gate": "implementation",
                "status": "running",
                "lifecycle_status": "paused_awaiting_user",
                "invalidated": False,
            }],
        )
        self.assertEqual(orchestration_engine._orchestrate_state_name(question), "needs_input")


if __name__ == "__main__":
    unittest.main()
