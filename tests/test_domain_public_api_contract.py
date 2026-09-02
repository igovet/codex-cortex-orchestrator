from __future__ import annotations

import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cortex import PUBLIC_TOOLS, SERVER_VERSION
from cortex_runtime.domain_api import (
    assess_governance, close_task, open_assignment, open_clarification,
    open_plan_review, open_steering, open_task, publish_documentation,
    publish_plan, publish_result, read_task, record_clarification,
    record_plan_review, record_steering,
)
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_store import V12Store


PROVENANCE = {name: "sha256:" + "a" * 64 for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")}


class DomainPublicApiContractTests(unittest.TestCase):
    def _task(self, root: str, outcomes: list[dict] | None = None) -> tuple[dict, list[dict]]:
        outcomes = outcomes or [{"outcome": "Build the artifact.", "acceptance": ["The artifact works."], "constraints": [], "verification": []}]
        task = open_task(project_root=root, request_original="Build it.", user_language="en", outcomes=outcomes, constraints=["Keep public identity minimal."])
        assess_governance(task_ref=task["task_ref"], mode="minimal", rationale="Bounded test fixture.")
        return task, outcomes

    def _assignment(self, task_ref: str, outcome: dict, role: str) -> dict:
        return open_assignment(task_ref=task_ref, role=role, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high", responsibility="evidence",
                               goal=f"Verify {role}.", scope="Read-only bounded scope.", instructions="Inspect and report.",
                               outcomes=[outcome["outcome"]], report_policy="none")

    def _publish_result(self, worker_ref: str, outcome: dict, context: dict) -> dict:
        return publish_result(
            task_ref=worker_ref,
            summary="Recovered worker result.",
            outcome="The assigned result is complete.",
            changes=[],
            verification_facts=[{"state": "executed", "summary": "Focused recovery check passed."}],
            outcome_coverage=[{
                "outcome": outcome["outcome"],
                "status": "complete",
                "verification": ["Focused recovery check passed."],
            }],
            documentation_impact="Recovery behavior is covered by repository documentation.",
            risks=[], unresolved=[], status="completed",
            _connection_context=context,
        )

    def test_flat_task_open_and_state_read_expose_only_task_ref_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task, outcomes = self._task(root)
            self.assertEqual(set(task), {"task_ref", "replayed"})
            state = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(state["data"]["effective_contract"]["items"], outcomes)
            rendered = repr(state)
            for name in ("item_ref", "report_ref", "decision_ref", "digest", "cursor", "handles"):
                self.assertNotIn(name, rendered)

    def test_state_binds_exact_outcomes_to_post_steering_delivery_assignability(self) -> None:
        owned = [
            {"outcome": "Deliver the initial API.", "acceptance": ["The API works."], "constraints": [], "verification": []},
            {"outcome": "Document the initial API.", "acceptance": ["The API is documented."], "constraints": [], "verification": []},
        ]
        added = [
            {"outcome": "Add the steered export.", "acceptance": ["The export works."], "constraints": [], "verification": []},
            {"outcome": "Verify the steered export.", "acceptance": ["The export is verified."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, owned)
            initial = open_assignment(
                task_ref=task["task_ref"], role="initial owner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Deliver the initial outcomes.", scope="Initial revision.",
                instructions="Complete the immutable initial scope.",
                outcomes=[item["outcome"] for item in owned], report_policy="none",
            )
            initial_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                initial["native_dispatch"]["message"],
            ).group(1)
            context: dict = {}
            read_task(task_ref=initial_ref, view="assignment", _connection_context=context)
            publish_result(
                task_ref=initial_ref, summary="Initial outcomes complete.", outcome="Complete.",
                changes=[],
                verification_facts=[{"state": "executed", "summary": "Initial checks passed."}],
                outcome_coverage=[
                    {"outcome": item["outcome"], "status": "complete", "verification": ["Initial checks passed."]}
                    for item in owned
                ],
                documentation_impact="Documentation is complete.", risks=[], unresolved=[],
                status="completed", _connection_context=context,
            )

            open_steering(task_ref=task["task_ref"], prompt="Add the export outcomes?", prompt_language="en")
            record_steering(
                task_ref=task["task_ref"], response_original="Add both export outcomes.",
                user_language="en", add=added, retire=[],
            )
            state = read_task(task_ref=task["task_ref"], view="state")
            dispositions = {
                item["outcome"]: (
                    item["ownership"], item["status"], item["delivery_assignability"]
                )
                for item in state["data"]["aggregate_coverage"]["items"]
            }
            self.assertEqual(
                dispositions,
                {
                    owned[0]["outcome"]: ("owned", "complete", "not_assignable_terminal_owner"),
                    owned[1]["outcome"]: ("owned", "complete", "not_assignable_terminal_owner"),
                    added[0]["outcome"]: ("unowned", "missing", "assignable"),
                    added[1]["outcome"]: ("unowned", "missing", "assignable"),
                },
            )
            assignment_scope = state["data"]["aggregate_coverage"]["assignment_scope"]
            self.assertEqual(
                assignment_scope,
                {
                    "planning": "complete_current_contract_server_derived",
                    "delivery_outcomes": [item["outcome"] for item in added],
                    "evidence_outcomes": [item["outcome"] for item in owned + added],
                    "terminal_rework": "steering_revision_required",
                    "terminal_outcomes": [item["outcome"] for item in owned],
                },
            )

            before_conflict = read_task(task_ref=task["task_ref"], view="state")["data"]["aggregate_coverage"]
            with self.assertRaises(V12ServiceError) as conflict:
                open_assignment(
                    task_ref=task["task_ref"], role="unsafe mixed owner", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Incorrectly redeliver every current outcome.", scope="Mixed scope.",
                    instructions="This request must fail closed.",
                    outcomes=[item["outcome"] for item in owned + added], report_policy="none",
                )
            self.assertEqual(conflict.exception.code, "outcome_assignment_conflict")
            self.assertEqual(
                read_task(task_ref=task["task_ref"], view="state")["data"]["aggregate_coverage"],
                before_conflict,
            )

            new_only = open_assignment(
                task_ref=task["task_ref"], role="steered owner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Deliver only the new outcomes.", scope="Steered revision only.",
                instructions="Consume and deliver only the assignable outcomes.",
                report_policy="none",
            )
            self.assertFalse(new_only["replayed"])

    def test_completed_owner_advertises_no_delivery_rework_until_steering_revision(self) -> None:
        outcome = {
            "outcome": "Deliver the bounded API.",
            "acceptance": ["The API works."],
            "constraints": [],
            "verification": ["Focused checks pass."],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            owner = open_assignment(
                task_ref=task["task_ref"], role="initial delivery", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Deliver the bounded API.", scope="Bounded delivery scope.",
                instructions="Implement and verify the current outcome.",
                outcomes=[outcome["outcome"]], report_policy="none",
            )
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                owner["native_dispatch"]["message"],
            ).group(1)
            context: dict = {}
            read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            publish_result(
                task_ref=worker_ref, summary="Delivery complete.", outcome="Complete.", changes=[],
                verification_facts=[{"state": "executed", "summary": "Focused checks passed."}],
                outcome_coverage=[{"outcome": outcome["outcome"], "status": "complete", "verification": ["Focused checks passed."]}],
                documentation_impact="No documentation impact.", risks=[], unresolved=[],
                status="completed", _connection_context=context,
            )

            state = read_task(task_ref=task["task_ref"], view="state")["data"]
            scope = state["aggregate_coverage"]["assignment_scope"]
            self.assertEqual(scope["delivery_outcomes"], [])
            self.assertEqual(scope["evidence_outcomes"], [outcome["outcome"]])
            self.assertEqual(scope["terminal_outcomes"], [outcome["outcome"]])
            self.assertEqual(scope["terminal_rework"], "steering_revision_required")

            before_rework = state["aggregate_coverage"]
            with self.assertRaises(V12ServiceError) as conflict:
                open_assignment(
                    task_ref=task["task_ref"], role="invalid implicit rework", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Attempt delivery without a new contract revision.", scope="No assignable outcomes.",
                    instructions="This must fail without claiming terminal outcomes.", report_policy="none",
                )
            self.assertEqual(conflict.exception.code, "outcome_assignment_conflict")
            self.assertEqual(
                read_task(task_ref=task["task_ref"], view="state")["data"]["aggregate_coverage"],
                before_rework,
            )

            replacement = {
                **outcome,
                "acceptance": ["The API works, including the reviewed edge case."],
            }
            open_steering(task_ref=task["task_ref"], prompt="Apply the reviewed correction?", prompt_language="en")
            record_steering(
                task_ref=task["task_ref"], response_original="Apply the reviewed correction.",
                user_language="en", add=[replacement], retire=[outcome],
            )
            revised = read_task(task_ref=task["task_ref"], view="state")["data"]
            revised_scope = revised["aggregate_coverage"]["assignment_scope"]
            self.assertEqual(revised_scope["delivery_outcomes"], [replacement["outcome"]])
            self.assertEqual(revised_scope["terminal_rework"], "not_applicable")
            self.assertEqual(revised_scope["terminal_outcomes"], [])

            replacement_owner = open_assignment(
                task_ref=task["task_ref"], role="implicit complete rework", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Deliver the complete revised scope.", scope="All advertised delivery outcomes.",
                instructions="Consume and deliver the complete server-derived current scope.", report_policy="none",
            )
            self.assertFalse(replacement_owner["replayed"])

    def test_assignment_requires_governance_assessment(self) -> None:
        outcome = {"outcome": "Inspect the artifact.", "acceptance": ["Evidence is returned."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task = open_task(
                project_root=root, request_original="Inspect it.", user_language="en",
                outcomes=[outcome], constraints=["Read-only inspection."],
            )
            with self.assertRaisesRegex(V12ServiceError, "governance assessment is required"):
                self._assignment(task["task_ref"], outcome, "pre-governance")

    def test_worker_scoped_task_ref_cannot_assess_governance(self) -> None:
        outcome = {"outcome": "Inspect the artifact.", "acceptance": ["Evidence is returned."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            assignment = self._assignment(task["task_ref"], outcome, "planner boundary")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)

            # Even a schema-complete call cannot turn a native worker into a
            # coordinator or append governance state through its scoped ref.
            with self.assertRaises(V12ServiceError) as rejected:
                assess_governance(
                    task_ref=worker_ref,
                    mode="full",
                    rationale="A worker must not own this lifecycle decision.",
                )
            self.assertIn(rejected.exception.code, {"invalid_identifier", "invalid_task_ref", "task_not_found"})

    def test_full_governance_delivery_requires_exact_current_plan_approval(self) -> None:
        outcome = {"outcome": "Deliver the secured change.", "acceptance": ["The secured flow works."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, [outcome])
            assess_governance(task_ref=task["task_ref"], mode="full", rationale="Authentication-sensitive delivery.")

            with self.assertRaisesRegex(V12ServiceError, "required-review plan"):
                open_assignment(
                    task_ref=task["task_ref"], role="implementer", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Implement.", scope="Secured flow.", instructions="Deliver only after approval.",
                    outcomes=[outcome["outcome"]], report_policy="none",
                )

            def publish_required_plan(label: str) -> None:
                planner = open_assignment(
                    task_ref=task["task_ref"], role=f"planner {label}", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                    goal=f"Plan {label}.", scope="Secured flow.", instructions="Publish an exact plan.",
                    report_policy="none",
                )
                planner_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', planner["native_dispatch"]["message"]).group(1)
                context: dict = {}
                read_task(task_ref=planner_ref, view="assignment", _connection_context=context)
                publish_plan(
                    task_ref=planner_ref, summary=f"Required plan {label}.", scope="Secured flow.",
                    stages=[{"owner": "implementer", "work": [f"Implement {label}."], "verification": ["Run security checks."]}],
                    verification_facts=[{"state": "not_run", "summary": "Delivery awaits approval."}],
                    outcome_coverage=[{"outcome": outcome["outcome"], "status": "blocked", "verification": ["Approval pending."]}],
                    risks=["Authentication risk."], unresolved=[], status="blocked",
                    _connection_context=context,
                )

            publish_required_plan("v1")
            with self.assertRaisesRegex(V12ServiceError, "not been explicitly approved"):
                open_assignment(
                    task_ref=task["task_ref"], role="implementer", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Implement.", scope="Secured flow.", instructions="Deliver only after approval.",
                    outcomes=[outcome["outcome"]], report_policy="active_plan",
                )
            open_plan_review(task_ref=task["task_ref"], prompt="Approve v1?", prompt_language="en")
            record_plan_review(task_ref=task["task_ref"], outcome="approve", response_original="Approve v1.", user_language="en")
            approved = open_assignment(
                task_ref=task["task_ref"], role="implementer", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Implement.", scope="Secured flow.", instructions="Use the approved plan.",
                outcomes=[outcome["outcome"]], report_policy="active_plan",
            )
            self.assertIn("native_dispatch", approved)

            publish_required_plan("v2")
            with self.assertRaisesRegex(V12ServiceError, "not been explicitly approved"):
                open_assignment(
                    task_ref=task["task_ref"], role="second implementer", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Implement v2.", scope="Secured flow.", instructions="Do not reuse v1 approval.",
                    outcomes=[outcome["outcome"]], report_policy="active_plan",
                )

    def test_material_steering_invalidates_old_plan_and_approval(self) -> None:
        original = {
            "outcome": "Deliver reset.",
            "acceptance": ["Reset works."],
            "constraints": [],
            "verification": ["Reset tests pass."],
        }
        replacement = {
            "outcome": "Deliver contains.",
            "acceptance": ["Contains is read-only."],
            "constraints": ["Time must be finite."],
            "verification": ["Contains tests pass."],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [original])
            assess_governance(task_ref=task["task_ref"], mode="light", rationale="Reviewed product change.")
            planner = open_assignment(
                task_ref=task["task_ref"], role="planner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                goal="Plan reset.", scope="Current contract.", instructions="Publish the required plan.",
                report_policy="none",
            )
            planner_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                planner["native_dispatch"]["message"],
            ).group(1)
            planner_context: dict = {}
            read_task(task_ref=planner_ref, view="assignment", _connection_context=planner_context)
            publish_plan(
                task_ref=planner_ref, summary="Reset plan.", scope="Current contract.",
                stages=[{"owner": "implementer", "work": ["Implement reset."], "verification": ["Run reset tests."]}],
                verification_facts=[{"state": "not_run", "summary": "Delivery awaits approval."}],
                outcome_coverage=[{"outcome": original["outcome"], "status": "blocked", "verification": ["Approval pending."]}],
                risks=[], unresolved=[], status="blocked", _connection_context=planner_context,
            )
            open_plan_review(task_ref=task["task_ref"], prompt="Approve reset plan?", prompt_language="en")
            record_plan_review(
                task_ref=task["task_ref"], outcome="approve",
                response_original="Approve reset plan.", user_language="en",
            )

            open_steering(task_ref=task["task_ref"], prompt="Replace reset with contains?", prompt_language="en")
            record_steering(
                task_ref=task["task_ref"], response_original="Use contains instead.", user_language="en",
                add=[replacement], retire=[original],
            )

            with self.assertRaises(V12ServiceError) as review_error:
                open_plan_review(task_ref=task["task_ref"], prompt="Reuse old plan?", prompt_language="en")
            self.assertEqual(review_error.exception.code, "approval_view_required")
            with self.assertRaises(V12ServiceError) as delivery_error:
                open_assignment(
                    task_ref=task["task_ref"], role="implementer", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                    goal="Implement contains.", scope="Current contract.", instructions="Do not reuse the reset plan.",
                    outcomes=[replacement["outcome"]], report_policy="active_plan",
                )
            self.assertEqual(delivery_error.exception.code, "plan_approval_required")

    def test_current_approved_multi_outcome_plan_admits_fullstack_delivery(self) -> None:
        outcomes = [
            {"outcome": "Implement API.", "acceptance": ["API works."], "constraints": [], "verification": ["API checked."]},
            {"outcome": "Add tests.", "acceptance": ["Tests exist."], "constraints": [], "verification": ["Tests pass."]},
            {"outcome": "Update README.", "acceptance": ["README is current."], "constraints": [], "verification": ["README checked."]},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, outcomes)
            assess_governance(task_ref=task["task_ref"], mode="light", rationale="Cross-surface change.")
            planner = open_assignment(
                task_ref=task["task_ref"], role="planner", profile_name="planner",
                model="gpt-5.6-terra", reasoning_effort="high", responsibility="planning",
                goal="Plan all outcomes.", scope="Current contract.", instructions="Publish one required plan.",
                report_policy="none",
            )
            planner_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                planner["native_dispatch"]["message"],
            ).group(1)
            planner_context: dict = {}
            read_task(task_ref=planner_ref, view="assignment", _connection_context=planner_context)
            publish_plan(
                task_ref=planner_ref, summary="Current multi-outcome plan.", scope="Current contract.",
                stages=[{"owner": "developer", "work": ["Implement all outcomes."], "verification": ["Run all checks."]}],
                verification_facts=[{"state": "not_run", "summary": "Delivery awaits approval."}],
                outcome_coverage=[
                    {"outcome": item["outcome"], "status": "blocked", "verification": ["Approval pending."]}
                    for item in outcomes
                ],
                risks=[], unresolved=[], status="blocked", _connection_context=planner_context,
            )
            open_plan_review(task_ref=task["task_ref"], prompt="Approve current plan?", prompt_language="en")
            record_plan_review(
                task_ref=task["task_ref"], outcome="approve",
                response_original="Approve current plan.", user_language="en",
            )

            delivery = open_assignment(
                task_ref=task["task_ref"], role="developer", profile_name="fullstack_dev",
                model="gpt-5.6-terra", reasoning_effort="high", responsibility="delivery",
                goal="Deliver all outcomes.", scope="Current contract.", instructions="Use the approved plan.",
                outcomes=[item["outcome"] for item in outcomes], report_policy="active_plan",
            )
            self.assertFalse(delivery["replayed"])

    def test_open_assignment_returns_only_native_dispatch_and_replay_state(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            result = self._assignment(task["task_ref"], outcomes[0], "audit")
            self.assertEqual(set(result), {"native_dispatch", "replayed"})
            self.assertNotIn("model", result["native_dispatch"])
            self.assertEqual(result["native_dispatch"]["reasoning_effort"], "high")
            self.assertEqual(
                list(result["native_dispatch"]),
                ["fork_turns", "task_name", "reasoning_effort", "message"],
            )
            self.assertNotIn("assignment_ref", repr(result))
            self.assertNotIn("continuation_ref", repr(result))
            self.assertRegex(result["native_dispatch"]["message"], r'"task_ref":"t_[0-9a-f]{12}_[0-9a-f]{32}"')
            message = result["native_dispatch"]["message"]
            self.assertNotIn("Build the artifact.", message)
            self.assertNotIn("Verify audit.", message)
            self.assertNotIn("Read-only bounded scope.", message)
            self.assertNotIn("Codebase Memory as the mandatory first evidence route", message)
            self.assertLess(len(message.encode("utf-8")), 1_024)
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', message).group(1)
            assignment = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            context = assignment["data"]["assignment_context"]
            self.assertIn("Codebase Memory as the mandatory first evidence route", context["common_policy"])
            self.assertEqual(context["profile_name"], "explorer")
            self.assertTrue(context["profile_instructions"])

    def test_parallel_workers_bind_distinct_assignments_even_when_read_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Audit A.", "acceptance": ["A verified."], "constraints": [], "verification": []},
            {"outcome": "Audit B.", "acceptance": ["B verified."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            with ThreadPoolExecutor(max_workers=2) as pool:
                assignments = list(pool.map(lambda pair: self._assignment(task["task_ref"], pair[1], pair[0]), (("a", outcomes[0]), ("b", outcomes[1]))))
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            self.assertEqual(len(set(worker_refs)), 2)
            contexts = [{}, {}]
            second = read_task(task_ref=worker_refs[1], view="assignment", _connection_context=contexts[1])
            first = read_task(task_ref=worker_refs[0], view="assignment", _connection_context=contexts[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])
            self.assertIn("Audit A.", repr(first))
            self.assertNotIn("Audit B.", repr(first["data"]["effective_contract"]))
            self.assertIn("Audit B.", repr(second))
            self.assertNotIn("Audit A.", repr(second["data"]["effective_contract"]))

    def test_consumed_assignment_is_not_bearer_authority_for_a_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "restart")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            context: dict = {}
            first = read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            self.assertIn("effective_contract", first["data"])
            with self.assertRaises(V12ServiceError) as fresh:
                read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            self.assertEqual(fresh.exception.code, "connection_lost")
            with self.assertRaises(V12ServiceError) as terminal_repeat:
                read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            self.assertEqual(terminal_repeat.exception.code, "assignment_stale")

    def test_fresh_connection_cannot_recover_consumed_worker_publication(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "reconnect")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)

            with self.assertRaises(V12ServiceError) as unconsumed:
                self._publish_result(worker_ref, outcomes[0], {})
            self.assertEqual(unconsumed.exception.code, "assignment_not_consumed")

            original_context: dict = {}
            read_task(
                task_ref=worker_ref, view="assignment",
                _connection_context=original_context,
            )
            fresh_context: dict = {}
            with self.assertRaises(V12ServiceError) as copied_publication:
                self._publish_result(worker_ref, outcomes[0], fresh_context)
            self.assertEqual(copied_publication.exception.code, "assignment_not_consumed")
            with self.assertRaises(V12ServiceError) as copied_read:
                read_task(
                    task_ref=worker_ref, view="assignment",
                    _connection_context=fresh_context,
                )
            self.assertEqual(copied_read.exception.code, "connection_lost")

            store = V12Store(Path(root))
            with store._connection() as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM report_operations"
                    ).fetchone()[0],
                    0,
                )
            published = self._publish_result(
                worker_ref, outcomes[0], original_context,
            )
            self.assertEqual(published["state"], "published")
            self.assertFalse(published["replayed"])

            evidence = read_task(
                task_ref=task["task_ref"], view="evidence",
                report_policy="all_finalized", _connection_context={},
            )
            self.assertEqual(len(evidence["data"]["reports"]), 1)
            self.assertIn("Recovered worker result.", repr(evidence["data"]))
            with store._connection() as connection:
                capability = connection.execute(
                    "SELECT state,continuation_ref FROM worker_capabilities",
                ).fetchall()
                operations = connection.execute(
                    "SELECT COUNT(*) FROM report_operations",
                ).fetchone()[0]
            self.assertEqual(len(capability), 1)
            self.assertEqual(capability[0]["state"], "consumed")
            self.assertEqual(capability[0]["continuation_ref"], original_context["continuation_ref"])
            self.assertEqual(operations, 1)

    def test_reconnect_rejects_partial_different_foreign_and_malformed_bindings(self) -> None:
        outcomes = [
            {"outcome": "Recover A.", "acceptance": ["A is isolated."], "constraints": [], "verification": []},
            {"outcome": "Recover B.", "acceptance": ["B is isolated."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, _ = self._task(root, outcomes)
            assignments = [
                self._assignment(task["task_ref"], outcome, label)
                for label, outcome in zip(("a", "b"), outcomes)
            ]
            worker_refs = [
                re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    assignment["native_dispatch"]["message"],
                ).group(1)
                for assignment in assignments
            ]
            bound_contexts = [{}, {}]
            for worker_ref, context in zip(worker_refs, bound_contexts):
                read_task(
                    task_ref=worker_ref, view="assignment",
                    _connection_context=context,
                )

            partial = {"actor": "worker"}
            with self.assertRaises(V12ServiceError) as partial_error:
                self._publish_result(worker_refs[0], outcomes[0], partial)
            self.assertEqual(partial_error.exception.code, "assignment_not_consumed")
            self.assertEqual(partial, {"actor": "worker"})

            different = dict(bound_contexts[1])
            with self.assertRaises(V12ServiceError) as different_error:
                self._publish_result(worker_refs[0], outcomes[0], different)
            self.assertEqual(different_error.exception.code, "wrong_connection")
            self.assertEqual(different, bound_contexts[1])

            foreign_task, _ = self._task(tempfile.mkdtemp(dir=root), [{
                "outcome": "Foreign task.", "acceptance": ["Remain isolated."],
                "constraints": [], "verification": [],
            }])
            foreign_ref = foreign_task["task_ref"] + "_" + worker_refs[0].rsplit("_", 1)[1]
            for rejected_ref in (foreign_ref, worker_refs[0][:-1] + "z"):
                with self.subTest(task_ref=rejected_ref), self.assertRaises(V12ServiceError):
                    self._publish_result(rejected_ref, outcomes[0], {})

            store = V12Store(Path(root))
            with store._connection() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM report_operations").fetchone()[0],
                    0,
                )

    def test_reconnect_rejects_provenance_dispatch_and_durable_state_drift(self) -> None:
        def fixture(root: str, label: str) -> tuple[dict, dict, str]:
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], label)
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            return task, outcomes[0], worker_ref

        with patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "provenance drift")
                changed = dict(PROVENANCE, source_digest="sha256:" + "b" * 64)
                with patch(
                    "cortex_runtime.domain_api._worker_capability_provenance",
                    return_value=changed,
                ), self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "dispatch drift")
                store = V12Store(Path(root))
                with store._connection() as connection:
                    connection.execute(
                        "UPDATE worker_capabilities SET dispatch_digest=?",
                        ("sha256:" + "c" * 64,),
                    )
                with self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

            with tempfile.TemporaryDirectory() as root:
                _task, outcome, worker_ref = fixture(root, "durable stale")
                store = V12Store(Path(root))
                with store._connection() as connection:
                    connection.execute("UPDATE worker_capabilities SET state='stale'")
                with self.assertRaises(V12ServiceError) as rejected:
                    self._publish_result(worker_ref, outcome, {})
                self.assertEqual(rejected.exception.code, "assignment_not_consumed")

    def test_reconnect_keeps_consumed_assignment_revision_after_steering(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "steered reconnect")
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            worker_context: dict = {}
            original = read_task(
                task_ref=worker_ref, view="assignment",
                _connection_context=worker_context,
            )
            self.assertEqual(original["data"]["effective_contract"]["revision"], 1)

            refined = dict(outcomes[0]) | {"acceptance": ["A newer task revision works."]}
            open_steering(
                task_ref=task["task_ref"], prompt="Apply the newer task revision?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Apply it.",
                user_language="en", add=[refined], retire=[outcomes[0]],
            )
            current = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(current["data"]["effective_contract"]["revision"], 2)

            published = self._publish_result(
                worker_ref, outcomes[0], worker_context,
            )
            self.assertEqual(published["state"], "published")
            evidence = read_task(
                task_ref=task["task_ref"], view="evidence",
                report_policy="all_finalized", _connection_context={},
            )
            self.assertEqual(len(evidence["data"]["reports"]), 1)

    def test_assignment_read_preserves_exact_source_limits_and_negative_requirements(self) -> None:
        """A worker must receive the normalized contract, not an attachment shorthand."""
        outcome = {
            "outcome": "Implement OTP verification for handler VerifyCode.",
            "acceptance": [
                "OTP is exactly 6 digits and expires after 10 minutes.",
                "Reject the request after 5 incorrect attempts.",
            ],
            "constraints": [
                "Resend cooldown is exactly 60 seconds.",
                "Never reveal whether an email address is registered.",
            ],
            "verification": [
                "Test expiry at 10:00 and rejection on attempt 6.",
                "Verify the unregistered-email response is indistinguishable.",
            ],
        }
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, [outcome])
            assignment = self._assignment(task["task_ref"], outcome, "audit")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            read = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            item = read["data"]["effective_contract"]["assigned_items"][0]
            self.assertEqual(item["outcome"], outcome["outcome"])
            self.assertEqual(item["acceptance"], outcome["acceptance"])
            self.assertEqual(item["constraints"], outcome["constraints"])
            self.assertEqual(item["verification"], outcome["verification"])

    def test_parallel_workers_publish_to_their_exact_assignments_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Implement A.", "acceptance": ["A works."], "constraints": [], "verification": []},
            {"outcome": "Implement B.", "acceptance": ["B works."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            assignments = [self._assignment(task["task_ref"], outcome, label) for label, outcome in zip(("a", "b"), outcomes)]
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            contexts = [{}, {}]
            read_task(task_ref=worker_refs[0], view="assignment", _connection_context=contexts[0])
            read_task(task_ref=worker_refs[1], view="assignment", _connection_context=contexts[1])

            def publish(index: int) -> dict:
                return publish_result(
                    task_ref=worker_refs[index], summary=f"Result {index}.", outcome="Verified.", changes=[],
                    verification_facts=[{"state": "executed", "summary": f"Check {index} passed."}],
                    outcome_coverage=[{"outcome": outcomes[index]["outcome"], "status": "complete", "verification": [f"Check {index} passed."]}],
                    documentation_impact="No documentation change.", risks=[], unresolved=[], status="completed",
                    _connection_context=contexts[index],
                )

            second, first = publish(1), publish(0)
            self.assertEqual(second["task_ref"], worker_refs[1])
            self.assertEqual(first["task_ref"], worker_refs[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])

    def test_all_finalized_evidence_from_multiple_authors_keeps_assignment_public(self) -> None:
        outcome = {
            "outcome": "Verify the bounded change.",
            "acceptance": ["The verification evidence is durable."],
            "constraints": [],
            "verification": [],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            for label in ("first reviewer", "second reviewer"):
                assignment = self._assignment(task["task_ref"], outcome, label)
                worker_ref = re.search(
                    r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                    assignment["native_dispatch"]["message"],
                ).group(1)
                context: dict = {}
                read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
                publish_result(
                    task_ref=worker_ref,
                    summary=f"{label} completed.",
                    outcome="Verification passed.",
                    changes=[],
                    verification_facts=[{"state": "executed", "summary": f"{label} check passed."}],
                    outcome_coverage=[{
                        "outcome": outcome["outcome"],
                        "status": "complete",
                        "verification": [f"{label} check passed."],
                    }],
                    documentation_impact="No documentation change.",
                    risks=[], unresolved=[], status="completed",
                    _connection_context=context,
                )

            # ``all_finalized`` intentionally selects both reports.  Their
            # authors are distinct, so there is no single predecessor to
            # infer; the server must preserve both inputs and leave that
            # optional relation unset instead of exposing an internal field.
            assignment = open_assignment(
                task_ref=task["task_ref"], role="aggregate reviewer", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="evidence",
                goal="Review all finalized evidence.", scope="The bounded verification outcome.",
                instructions="Consume every finalized evidence report and publish the aggregate result.",
                outcomes=[outcome["outcome"]], report_policy="all_finalized",
            )
            self.assertIn("native_dispatch", assignment)

    def test_open_assignment_never_exposes_private_lineage_error_fields(self) -> None:
        outcome = {
            "outcome": "Inspect the assignment boundary.",
            "acceptance": ["The public error is sanitized."],
            "constraints": [],
            "verification": [],
        }
        private_fields = ("input_report_refs", "input_decision_refs", "parent_assignment_ref")
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            for private_field in private_fields:
                with self.subTest(private_field=private_field), patch(
                    "cortex_runtime.domain_api.ledger.create_delegation",
                    side_effect=V12ServiceError(
                        "internal lineage failure", code="invalid_argument",
                        details={"field": private_field},
                    ),
                ):
                    with self.assertRaises(V12ServiceError) as rejected:
                        self._assignment(task["task_ref"], outcome, f"sanitization {private_field}")
                    self.assertEqual(rejected.exception.code, "ledger_error")
                    self.assertNotIn(private_field, repr(rejected.exception.details))
                    self.assertNotIn(private_field, str(rejected.exception))

    def test_partial_plan_for_server_derived_complete_scope_is_immediately_active_evidence(self) -> None:
        """Regression for the real planner failure observed in live orchestration.

        A schema-valid partial plan used to fail first as
        ``contract_coverage_invalid`` and then as ``approval_view_mismatch``.
        It also stayed invisible to ``active_plan`` until user approval, which
        made the planner-to-coordinator handoff empty.
        """
        outcomes = [
            {"outcome": "Plan the bounded change.", "acceptance": ["The sequence is explicit."], "constraints": [], "verification": []},
            {"outcome": "Identify remaining uncertainty.", "acceptance": ["The uncertainty is visible."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            with self.assertRaises(V12ServiceError) as stale_shape:
                open_assignment(
                    task_ref=task["task_ref"], role="legacy planner", profile_name="explorer",
                    model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                    goal="Plan one copied outcome.", scope="Invalid caller-selected scope.",
                    instructions="This shape must not mutate.",
                    outcomes=[outcomes[0]["outcome"]], report_policy="none",
                )
            self.assertEqual(stale_shape.exception.code, "invalid_argument")
            assignment = open_assignment(
                task_ref=task["task_ref"], role="bounded planner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                goal="Prepare the next plan.", scope="The complete current contract.",
                instructions="Publish observed planning evidence.", report_policy="none",
            )
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            context: dict = {}
            bootstrap = read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            self.assertEqual(bootstrap["data"]["assignment_context"]["responsibility"], "planning")
            self.assertEqual(bootstrap["data"]["assignment_context"]["project_root"], str(Path(root).resolve()))
            self.assertEqual(len(bootstrap["data"]["effective_contract"]["planning_items"]), 2)

            published = publish_plan(
                task_ref=worker_ref, summary="A bounded plan with an explicit uncertainty.", scope="Complete current contract.",
                stages=[{"owner": "implementation", "work": ["Implement the bounded change."], "verification": ["Run the focused check."]}],
                verification_facts=[{"state": "not_run", "summary": "Execution belongs to the implementation worker."}],
                outcome_coverage=[
                    {"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Sequence established; implementation remains."]},
                    {"outcome": outcomes[1]["outcome"], "status": "partial", "verification": ["Uncertainty remains visible."]},
                ],
                risks=["Implementation remains."], unresolved=["Runtime result is not available yet."], status="partial",
                _connection_context=context,
            )
            self.assertEqual(published["state"], "published")
            self.assertFalse(published["replayed"])

            replayed = publish_plan(
                task_ref=worker_ref, summary="A bounded plan with an explicit uncertainty.", scope="Complete current contract.",
                stages=[{"owner": "implementation", "work": ["Implement the bounded change."], "verification": ["Run the focused check."]}],
                verification_facts=[{"state": "not_run", "summary": "Execution belongs to the implementation worker."}],
                outcome_coverage=[
                    {"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Sequence established; implementation remains."]},
                    {"outcome": outcomes[1]["outcome"], "status": "partial", "verification": ["Uncertainty remains visible."]},
                ],
                risks=["Implementation remains."], unresolved=["Runtime result is not available yet."], status="partial",
                _connection_context=context,
            )
            self.assertTrue(replayed["replayed"])

            # A worker may inspect neutral state after its mandatory bootstrap;
            # this advertised view must not trip a hidden capability gate.
            worker_state = read_task(task_ref=worker_ref, view="state", _connection_context=context)
            self.assertEqual(worker_state["view"], "state")

            handoff = read_task(task_ref=task["task_ref"], view="evidence", report_policy="active_plan", _connection_context={})
            self.assertEqual(len(handoff["data"]["reports"]), 1)
            self.assertIn("A bounded plan", repr(handoff["data"]))
            self.assertEqual(handoff["data"]["reports"][0]["review_policy"], "informational")
            self.assertEqual(handoff["data"]["consumption_receipts"][0]["reader_kind"], "coordinator")
            with self.assertRaises(V12ServiceError) as rejected_review:
                open_plan_review(task_ref=task["task_ref"], prompt="Review informational plan?", prompt_language="en")
            self.assertEqual(rejected_review.exception.code, "approval_view_required")

    def test_planning_assignment_cannot_publish_supplementary_result_after_plan(self) -> None:
        outcome = {
            "outcome": "Plan the bounded change.",
            "acceptance": ["Exactly one planning publication owns the current plan."],
            "constraints": [],
            "verification": [],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task, _ = self._task(root, [outcome])
            assignment = open_assignment(
                task_ref=task["task_ref"], role="planner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                goal="Publish the terminal plan.", scope="Bounded planning only.",
                instructions="Complete discovery before publishing.",
                report_policy="none",
            )
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            context: dict = {}
            read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            common = {
                "task_ref": worker_ref,
                "verification_facts": [{"state": "not_run", "summary": "Planning does not execute delivery."}],
                "outcome_coverage": [{"outcome": outcome["outcome"], "status": "planned", "verification": ["Mapped completely."]}],
                "risks": [], "unresolved": [], "status": "completed",
                "_connection_context": context,
            }
            publish_plan(
                summary="Complete terminal plan.", scope="Bounded planning only.",
                stages=[{"owner": "implementation", "work": ["Implement."], "verification": ["Run focused tests."]}],
                **common,
            )
            before = read_task(
                task_ref=task["task_ref"], view="evidence", report_policy="all_finalized",
                _connection_context={},
            )
            self.assertEqual(len(before["data"]["reports"]), 1)

            with self.assertRaises(V12ServiceError) as rejected:
                publish_result(
                    summary="Late supplementary evidence.", outcome="Discovery continued after the plan.",
                    changes=[], documentation_impact="None.", **common,
                )
            self.assertEqual(rejected.exception.code, "publication_kind_not_permitted")

            after = read_task(
                task_ref=task["task_ref"], view="evidence", report_policy="all_finalized",
                _connection_context={},
            )
            self.assertEqual(len(after["data"]["reports"]), 1)
            self.assertIn("Complete terminal plan.", repr(after["data"]["reports"][0]))
            self.assertNotIn("Late supplementary evidence.", repr(after["data"]))

    def test_planning_publication_reports_exact_mismatched_outcome_position(self) -> None:
        outcomes = [
            {
                "outcome": f"Planner outcome {index}.",
                "acceptance": [f"Planner outcome {index} is covered."],
                "constraints": [],
                "verification": [],
            }
            for index in range(7)
        ]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task, _ = self._task(root, outcomes)
            planner = open_assignment(
                task_ref=task["task_ref"], role="planner", profile_name="planner",
                model="gpt-5.6-terra", reasoning_effort="high",
                responsibility="planning", goal="Plan every exact outcome.",
                scope="The complete current contract.",
                instructions="Publish one terminal plan from the exact assignment.",
                report_policy="none",
            )
            planner_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                planner["native_dispatch"]["message"],
            ).group(1)
            context: dict = {}
            assignment = read_task(
                task_ref=planner_ref, view="assignment",
                _connection_context=context,
            )
            self.assertEqual(
                assignment["data"]["publication_reconciliation"]
                ["required_outcomes"],
                [item["outcome"] for item in outcomes],
            )
            coverage = [
                {
                    "outcome": item["outcome"],
                    "status": "planned",
                    "verification": ["Mapped exactly."],
                }
                for item in outcomes
            ]
            coverage[6] = {
                **coverage[6],
                "outcome": "Invented replacement for the seventh outcome.",
            }
            with self.assertRaises(V12ServiceError) as rejected:
                publish_plan(
                    task_ref=planner_ref, summary="Plan prepared.",
                    scope="The complete current contract.",
                    stages=[{
                        "owner": "developer", "work": ["Implement the plan."],
                        "verification": ["Run focused checks."],
                    }],
                    verification_facts=[{
                        "state": "not_run", "summary": "Delivery awaits review.",
                    }],
                    outcome_coverage=coverage, risks=[], unresolved=[],
                    status="blocked", _connection_context=context,
                )
            self.assertEqual(rejected.exception.code, "outcome_item_not_found")
            self.assertEqual(
                rejected.exception.details.get("path"),
                "$.outcome_coverage[6]",
            )

    def test_unique_outcome_name_resolves_current_user_refined_revision(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            refined = dict(outcomes[0]) | {
                "acceptance": [
                    *outcomes[0]["acceptance"],
                    "The refined criterion also works.",
                ],
            }
            open_steering(task_ref=task["task_ref"], prompt="Apply the requested refinement?", prompt_language="en")
            record_steering(
                task_ref=task["task_ref"], response_original="Apply it.", user_language="en",
                add=[refined], retire=[outcomes[0]],
            )
            assignment = open_assignment(
                task_ref=task["task_ref"], role="worker", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="low", responsibility="delivery",
                goal="Deliver.", scope="Unique semantic outcome.", instructions="Consume and publish.",
                outcomes=[outcomes[0]["outcome"]], report_policy="none",
            )
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            current = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            self.assertIn("The refined criterion also works.", repr(current))
            self.assertIn("The artifact works.", repr(current["data"]["effective_contract"]))

    def test_all_fourteen_public_operations_follow_llm_selected_flow(self) -> None:
        outcome = {"outcome": "Deliver the checked change.", "acceptance": ["The evidence is durable."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, [outcome])                       # open_task
            self.assertEqual(read_task(task_ref=task["task_ref"], view="state")["view"], "state")

            open_clarification(task_ref=task["task_ref"], prompt="Proceed?", prompt_language="en")
            # A pending decision is neutral state, not a backend workflow lock.
            pending_assignment = open_assignment(
                task_ref=task["task_ref"], role="parallel observer", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="low", responsibility="evidence",
                goal="Observe independently.", scope="Read-only.", instructions="Await assignment evidence.",
                outcomes=[outcome["outcome"]], report_policy="none",
            )
            self.assertIn("native_dispatch", pending_assignment)
            self.assertEqual(assess_governance(task_ref=task["task_ref"], mode="light", rationale="Bounded.")["state"], "governance_assessed")
            record_clarification(task_ref=task["task_ref"], response_original="Proceed.", user_language="en")

            planner = open_assignment(
                task_ref=task["task_ref"], role="planner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                goal="Plan the change.", scope="The semantic outcome.", instructions="Publish the plan.",
                report_policy="none",
            )
            planner_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', planner["native_dispatch"]["message"]).group(1)
            planner_context: dict = {}
            read_task(task_ref=planner_ref, view="assignment", _connection_context=planner_context)
            publish_plan(
                task_ref=planner_ref, summary="Plan with visible uncertainty.", scope="Bounded.",
                stages=[{"owner": "worker", "work": ["Implement."], "verification": ["Check."]}],
                verification_facts=[{"state": "not_run", "summary": "Implementation has not run."}],
                outcome_coverage=[{"outcome": outcome["outcome"], "status": "blocked", "verification": ["User review is requested."]}],
                risks=[], unresolved=["The LLM chose to request review despite this uncertainty."], status="blocked",
                _connection_context=planner_context,
            )
            # ``unresolved`` is evidence; it does not let the backend veto the
            # LLM's explicit decision to ask for plan review.
            open_plan_review(task_ref=task["task_ref"], prompt="Approve this plan?", prompt_language="en")
            record_plan_review(task_ref=task["task_ref"], outcome="approve", response_original="Approved.", user_language="en")

            open_steering(task_ref=task["task_ref"], prompt="Any scope change?", prompt_language="en")
            record_steering(task_ref=task["task_ref"], response_original="No change.", user_language="en", add=[], retire=[])

            worker = open_assignment(
                task_ref=task["task_ref"], role="implementer", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="delivery",
                goal="Implement and document.", scope="The semantic outcome.", instructions="Publish evidence.",
                outcomes=[outcome["outcome"]], report_policy="active_plan",
            )
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', worker["native_dispatch"]["message"]).group(1)
            worker_context: dict = {}
            assignment_view = read_task(task_ref=worker_ref, view="assignment", _connection_context=worker_context)
            self.assertIn("Plan with visible uncertainty", repr(assignment_view))
            worker_evidence = read_task(task_ref=worker_ref, view="evidence", report_policy="active_plan", _connection_context=worker_context)
            self.assertIn("Plan with visible uncertainty", repr(worker_evidence))
            common = {
                "task_ref": worker_ref,
                "verification_facts": [{"state": "executed", "summary": "Focused check passed."}],
                "outcome_coverage": [{"outcome": outcome["outcome"], "status": "complete", "verification": ["Focused check passed."]}],
                "risks": [], "unresolved": [], "status": "completed", "_connection_context": worker_context,
            }
            self.assertEqual(publish_result(summary="Implemented.", outcome="Complete.", changes=[], documentation_impact="Checked.", **common)["state"], "published")
            self.assertEqual(publish_documentation(summary="Documentation assessed.", findings=[], recommendations=[], documentation_impact="No update required.", **common)["state"], "published")

            open_clarification(
                task_ref=task["task_ref"],
                prompt="Review the current result: revise this task or close it?",
                prompt_language="en", purpose="closure_review", options=["revise", "close"],
            )
            record_clarification(
                task_ref=task["task_ref"], response_original="Close the task.",
                user_language="en", outcome="close",
            )
            self.assertEqual(close_task(task_ref=task["task_ref"], verdict="ready")["state"], "closed")
            closed = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(closed["data"]["advisory_closure"]["latest_record"]["verdict"], "ready")

    def test_version_and_catalogue_remain_current(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.14.10")
        self.assertEqual(len(PUBLIC_TOOLS), 14)


if __name__ == "__main__":
    unittest.main()
