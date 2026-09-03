from __future__ import annotations

import re
import tempfile
import unittest
from unittest.mock import patch

from cortex import PUBLIC_TOOLS
from cortex_runtime.domain_api import (
    assess_governance,
    close_task,
    open_assignment,
    open_clarification,
    open_task,
    publish_result,
    read_state,
    read_task,
    record_clarification,
)
from cortex_runtime.v12_service import V12ServiceError


PROVENANCE = {
    name: "sha256:" + "a" * 64
    for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")
}


class MandatoryClosureReviewTests(unittest.TestCase):
    def _task(self, root: str) -> tuple[str, dict[str, object]]:
        outcome = {
            "outcome": "Deliver the checked result.",
            "acceptance": ["The result is complete."],
            "constraints": [],
            "verification": [],
        }
        task = open_task(
            project_root=root,
            request_original="Deliver the checked result.",
            user_language="en",
            outcomes=[outcome],
            constraints=["Keep the task reusable for requested rework."],
        )
        assess_governance(task_ref=task["task_ref"], mode="minimal", rationale="Closure review fixture.")
        return task["task_ref"], outcome

    @staticmethod
    def _review(task_ref: str, outcome: str) -> None:
        opened = open_clarification(
            task_ref=task_ref,
            prompt="Check the current result. Choose whether to revise the task or close it.",
            prompt_language="en",
            purpose="closure_review",
            options=["revise", "close"],
        )
        assert opened["state"] == "pending_closure_review"
        recorded = record_clarification(
            task_ref=task_ref,
            response_original=outcome,
            user_language="en",
            outcome=outcome,
        )
        assert recorded["state"] == "closure_review_recorded"

    def test_direct_close_is_rejected_until_current_review_is_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            with self.assertRaisesRegex(V12ServiceError, "closure review") as rejected:
                close_task(task_ref=task_ref, verdict="ready")
            self.assertEqual(rejected.exception.code, "closure_review_required")
            state = read_state(task_ref=task_ref, )
            self.assertEqual(state["data"]["closure_record_status"], "not_recorded")

    def test_revise_keeps_same_task_open_and_allows_follow_up_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task_ref, outcome = self._task(root)
            self._review(task_ref, "revise")
            with self.assertRaisesRegex(V12ServiceError, "requested revision") as rejected:
                close_task(task_ref=task_ref, verdict="ready")
            self.assertEqual(rejected.exception.code, "closure_revision_requested")
            assignment = open_assignment(
                task_ref=task_ref,
                role="follow-up verifier",
                profile_name="explorer",
                model="gpt-5.6-luna",
                reasoning_effort="low",
                responsibility="evidence",
                goal="Recheck the revised result.",
                scope="The current result.",
                instructions="Inspect the current task and report evidence.",
                outcomes=[outcome["outcome"]],
                report_policy="none",
            )
            self.assertIn("native_dispatch", assignment)
            self.assertNotIn("closed", repr(read_state(task_ref=task_ref, )))

    def test_close_choice_authorizes_close_once(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            self._review(task_ref, "close")
            closed = close_task(task_ref=task_ref, verdict="ready")
            self.assertEqual(closed["state"], "closed")
            self.assertEqual(closed["data"], {"human_views": []})
            state = read_state(task_ref=task_ref, )
            self.assertEqual(state["data"]["closure_verdict"], "ready")
            self.assertEqual(close_task(task_ref=task_ref, verdict="ready")["state"], "closed")

    def test_revision_review_can_be_reopened_on_the_same_task(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            self._review(task_ref, "revise")
            with self.assertRaises(V12ServiceError):
                close_task(task_ref=task_ref, verdict="ready")
            # The same localized prompt must create a fresh generation rather
            # than replaying the consumed revision decision.
            self._review(task_ref, "close")
            self.assertEqual(close_task(task_ref=task_ref, verdict="ready")["state"], "closed")

    def test_accepted_review_is_stale_after_new_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task_ref, outcome = self._task(root)
            self._review(task_ref, "close")
            open_assignment(
                task_ref=task_ref,
                role="new verifier",
                profile_name="explorer",
                model="gpt-5.6-luna",
                reasoning_effort="low",
                responsibility="evidence",
                goal="Perform a final check.",
                scope="The current result.",
                instructions="Inspect and report.",
                outcomes=[outcome["outcome"]],
                report_policy="none",
            )
            with self.assertRaisesRegex(V12ServiceError, "stale") as rejected:
                close_task(task_ref=task_ref, verdict="ready")
            self.assertEqual(rejected.exception.code, "closure_review_stale")

    def test_accepted_review_is_stale_after_new_result(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE,
        ):
            task_ref, outcome = self._task(root)
            assignment = open_assignment(
                task_ref=task_ref,
                role="result verifier",
                profile_name="explorer",
                model="gpt-5.6-luna",
                reasoning_effort="low",
                responsibility="evidence",
                goal="Publish the checked result.",
                scope="The current result.",
                instructions="Consume the assignment and publish one complete result.",
                outcomes=[outcome["outcome"]],
                report_policy="none",
            )
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                assignment["native_dispatch"]["message"],
            ).group(1)
            worker_context: dict[str, object] = {}
            read_task(task_ref=worker_ref, _connection_context=worker_context)
            self._review(task_ref, "close")
            publish_result(
                task_ref=worker_ref,
                summary="Checked result.",
                outcome="The result was checked after closure review.",
                changes=[],
                verification_facts=[{"state": "executed", "summary": "Focused result check passed."}],
                outcome_coverage=[{
                    "outcome": outcome["outcome"],
                    "status": "complete",
                    "verification": ["Focused result check passed."],
                }],
                documentation_impact="No documentation impact.",
                risks=[],
                unresolved=[],
                status="completed",
                _connection_context=worker_context,
            )
            with self.assertRaisesRegex(V12ServiceError, "stale") as rejected:
                close_task(task_ref=task_ref, verdict="ready")
            self.assertEqual(rejected.exception.code, "closure_review_stale")

    def test_closure_options_and_outcomes_are_exact_and_ordinary_clarification_survives(self) -> None:
        schema = PUBLIC_TOOLS["open_clarification"]["inputSchema"]
        self.assertEqual(schema["properties"]["options"]["items"]["enum"], ["revise", "close"])
        record_schema = PUBLIC_TOOLS["record_clarification"]["inputSchema"]
        self.assertNotIn("outcome", record_schema["required"])
        self.assertEqual(record_schema["properties"]["outcome"]["enum"], ["revise", "close"])
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            with self.assertRaisesRegex(V12ServiceError, "exactly revise and close"):
                open_clarification(task_ref=task_ref, prompt="Review.", prompt_language="en", purpose="closure_review", options=["close", "revise"])
            opened = open_clarification(task_ref=task_ref, prompt="Which detail?", prompt_language="en", purpose="clarification")
            self.assertEqual(opened["state"], "pending_clarification")
            with self.assertRaisesRegex(V12ServiceError, "ordinary clarification"):
                record_clarification(
                    task_ref=task_ref, response_original="The detail is explicit.",
                    user_language="en", outcome="close",
                )
            answered = record_clarification(task_ref=task_ref, response_original="The detail is explicit.", user_language="en")
            self.assertEqual(answered["state"], "clarification_recorded")
            open_clarification(
                task_ref=task_ref,
                prompt="Check the current result.",
                prompt_language="en",
                purpose="closure_review",
                options=["revise", "close"],
            )
            with self.assertRaisesRegex(V12ServiceError, "closure review requires"):
                record_clarification(task_ref=task_ref, response_original="maybe", user_language="en")


if __name__ == "__main__":
    unittest.main()
