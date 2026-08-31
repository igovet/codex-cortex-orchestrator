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

    def test_flat_task_open_and_state_read_expose_only_task_ref_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task, outcomes = self._task(root)
            self.assertEqual(set(task), {"task_ref", "replayed"})
            state = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(state["data"]["effective_contract"]["items"], outcomes)
            rendered = repr(state)
            for name in ("item_ref", "report_ref", "decision_ref", "digest", "cursor", "handles"):
                self.assertNotIn(name, rendered)

    def test_assignment_requires_governance_assessment(self) -> None:
        outcome = {"outcome": "Inspect the artifact.", "acceptance": ["Evidence is returned."], "constraints": [], "verification": []}
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task = open_task(
                project_root=root, request_original="Inspect it.", user_language="en",
                outcomes=[outcome], constraints=["Read-only inspection."],
            )
            with self.assertRaisesRegex(V12ServiceError, "governance assessment is required"):
                self._assignment(task["task_ref"], outcome, "pre-governance")

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
                    outcomes=[outcome["outcome"]], report_policy="none",
                )
                planner_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', planner["native_dispatch"]["message"]).group(1)
                context: dict = {}
                read_task(task_ref=planner_ref, view="assignment", _connection_context=context)
                publish_plan(
                    task_ref=planner_ref, summary=f"Required plan {label}.", scope="Secured flow.",
                    review_policy="required",
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

    def test_open_assignment_returns_only_native_dispatch_and_replay_state(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            result = self._assignment(task["task_ref"], outcomes[0], "audit")
            self.assertEqual(set(result), {"native_dispatch", "replayed"})
            self.assertNotIn("assignment_ref", repr(result))
            self.assertNotIn("continuation_ref", repr(result))
            self.assertRegex(result["native_dispatch"]["message"], r'"task_ref":"t_[0-9a-f]{12}_[0-9a-f]{32}"')
            message = result["native_dispatch"]["message"]
            self.assertNotIn("Build the artifact.", message)
            self.assertNotIn("Verify audit.", message)
            self.assertNotIn("Read-only bounded scope.", message)
            self.assertLess(len(message.encode("utf-8")), 6_000)

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

    def test_restart_reconciles_consumed_assignment_without_model_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "restart")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            first = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            restarted = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            self.assertEqual(first["data"]["effective_contract"], restarted["data"]["effective_contract"])
            self.assertNotIn("cursor", repr(restarted))

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

    def test_partial_plan_is_accepted_once_and_is_immediately_active_evidence(self) -> None:
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
            assignment = open_assignment(
                task_ref=task["task_ref"], role="bounded planner", profile_name="explorer",
                model="gpt-5.6-luna", reasoning_effort="high", responsibility="planning",
                goal="Prepare the next plan.", scope="Only the first semantic outcome.",
                instructions="Publish observed planning evidence.", outcomes=[outcomes[0]["outcome"]], report_policy="none",
            )
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            context: dict = {}
            bootstrap = read_task(task_ref=worker_ref, view="assignment", _connection_context=context)
            self.assertEqual(bootstrap["data"]["assignment_context"]["responsibility"], "planning")
            self.assertEqual(bootstrap["data"]["assignment_context"]["project_root"], str(Path(root).resolve()))
            self.assertEqual(len(bootstrap["data"]["effective_contract"]["planning_items"]), 1)

            with self.assertRaisesRegex(V12ServiceError, "review policy is invalid"):
                publish_plan(
                    task_ref=worker_ref, summary="Invalid policy.", scope="First outcome only.",
                    review_policy="optional", stages=[{"owner": "implementation", "work": ["Implement."], "verification": ["Check."]}],
                    verification_facts=[{"state": "not_run", "summary": "Not started."}],
                    outcome_coverage=[{"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Planned."]}],
                    risks=[], unresolved=[], status="partial", _connection_context=context,
                )

            published = publish_plan(
                task_ref=worker_ref, summary="A bounded plan with an explicit uncertainty.", scope="First outcome only.",
                review_policy="informational",
                stages=[{"owner": "implementation", "work": ["Implement the bounded change."], "verification": ["Run the focused check."]}],
                verification_facts=[{"state": "not_run", "summary": "Execution belongs to the implementation worker."}],
                outcome_coverage=[{"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Sequence established; implementation remains."]}],
                risks=["Implementation remains."], unresolved=["Runtime result is not available yet."], status="partial",
                _connection_context=context,
            )
            self.assertEqual(published["state"], "published")
            self.assertFalse(published["replayed"])

            replayed = publish_plan(
                task_ref=worker_ref, summary="A bounded plan with an explicit uncertainty.", scope="First outcome only.",
                review_policy="informational",
                stages=[{"owner": "implementation", "work": ["Implement the bounded change."], "verification": ["Run the focused check."]}],
                verification_facts=[{"state": "not_run", "summary": "Execution belongs to the implementation worker."}],
                outcome_coverage=[{"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Sequence established; implementation remains."]}],
                risks=["Implementation remains."], unresolved=["Runtime result is not available yet."], status="partial",
                _connection_context=context,
            )
            self.assertTrue(replayed["replayed"])

            # Review disposition is part of the immutable publication payload:
            # changing only that value must conflict instead of silently
            # reusing an informational plan as a required one (or vice versa).
            with self.assertRaisesRegex(V12ServiceError, "different terminal report"):
                publish_plan(
                    task_ref=worker_ref, summary="A bounded plan with an explicit uncertainty.", scope="First outcome only.",
                    review_policy="required",
                    stages=[{"owner": "implementation", "work": ["Implement the bounded change."], "verification": ["Run the focused check."]}],
                    verification_facts=[{"state": "not_run", "summary": "Execution belongs to the implementation worker."}],
                    outcome_coverage=[{"outcome": outcomes[0]["outcome"], "status": "partial", "verification": ["Sequence established; implementation remains."]}],
                    risks=["Implementation remains."], unresolved=["Runtime result is not available yet."], status="partial",
                    _connection_context=context,
                )

            # A worker may inspect neutral state after its mandatory bootstrap;
            # this advertised view must not trip a hidden capability gate.
            worker_state = read_task(task_ref=worker_ref, view="state", _connection_context=context)
            self.assertEqual(worker_state["view"], "state")

            handoff = read_task(task_ref=task["task_ref"], view="evidence", report_policy="active_plan", _connection_context={})
            self.assertEqual(len(handoff["data"]["reports"]), 1)
            self.assertIn("A bounded plan", repr(handoff["data"]))
            self.assertEqual(handoff["data"]["reports"][0]["review_policy"], "informational")
            self.assertEqual(handoff["data"]["consumption_receipts"][0]["reader_kind"], "coordinator")

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
                outcomes=[outcome["outcome"]], report_policy="none",
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
                review_policy="required",
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

    def test_unique_outcome_name_resolves_current_user_refined_revision(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            refined = dict(outcomes[0]) | {"acceptance": ["The refined criterion also works."]}
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
                outcomes=[outcome["outcome"]], report_policy="none",
            )
            planner_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', planner["native_dispatch"]["message"]).group(1)
            planner_context: dict = {}
            read_task(task_ref=planner_ref, view="assignment", _connection_context=planner_context)
            publish_plan(
                task_ref=planner_ref, summary="Plan with visible uncertainty.", scope="Bounded.",
                review_policy="required",
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

            self.assertEqual(close_task(task_ref=task["task_ref"], verdict="ready")["state"], "closed")
            closed = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(closed["data"]["advisory_closure"]["latest_record"]["verdict"], "ready")

    def test_version_and_catalogue_remain_current(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.13.1")
        self.assertEqual(len(PUBLIC_TOOLS), 14)


if __name__ == "__main__":
    unittest.main()
