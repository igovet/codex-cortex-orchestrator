"""Focused regression tests for advisory outcome coverage and conformance."""
from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store  # noqa: E402


class EffectiveContractCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-v12-coverage-")
        root = Path(self.temporary.name)
        self.home = root / "home"
        self.project = root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.environment = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.environment.start()
        os.environ.pop("CORTEX_HOST_STATE_DIR", None)
        os.environ.pop("CODEX_HOME", None)
        self.store = V12Store(self.project)
        self.task = self.store.create_task(
            objective="Aggregate evidence.", user_request_original="Aggregate evidence.", user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked", requirements=["Cover the requirement."],
            constraints=["Preserve compatibility."], acceptance_criteria=["Expose advisory conformance."],
            verification_plan=["Run focused tests."], context={}, idempotency_key="coverage-task",
        )[0]["task"]["task_id"]
        self.items = [item["item_ref"] for item in self.store.inspect_task(task_id=self.task, after_sequence=0)["effective_contract"]["items"]]
        self.item = self.items[0]
        self.delegation = self.store.create_delegation(
            task_id=self.task, objective="Own one item.", role="worker", profile_name="general",
            scope="One effective item.", instructions="Submit v2 evidence.", model="gpt-5.6-luna",
            reasoning_effort="high", outcome_assignments={"owned": self.items}, idempotency_key="coverage-delegation",
        )[0]["delegation"]["delegation_id"]

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _submit(self, *, key: str, coverage_status: str, verification: list[str], report_status: str = "completed", delegation: str | None = None) -> str:
        owner = self.delegation if delegation is None else delegation
        started = self.store.submit_report(
            task_id=self.task, delegation_id=owner, mode="begin", report_type="result", idempotency_key=f"{key}-begin",
        )[0]
        report_id = started["report"]["report_id"]
        self.store.submit_report(
            task_id=self.task, delegation_id=owner, mode="append", report_id=report_id, section="result",
            content={
                "schema": "cortex/report/result/v2", "summary": "V2 evidence.", "outcome": "recorded",
                "changes": [], "verification": ["Focused suite."], "risks": [], "deviations": [], "unresolved": [],
                "contract_coverage": [{"item_ref": item, "status": coverage_status, "verification": verification} for item in self.items],
            }, idempotency_key=f"{key}-append",
        )[0]
        self.store.submit_report(
            task_id=self.task, delegation_id=owner, mode="finalize", report_id=report_id, status=report_status,
            idempotency_key=f"{key}-finalize",
        )
        return report_id

    def test_unverified_and_contradictory_claims_drive_advisory_signals(self) -> None:
        self._submit(key="unverified", coverage_status="complete", verification=[])
        initial = self.store.inspect_task(task_id=self.task, after_sequence=0)
        self.assertEqual(initial["aggregate_coverage"]["status"], "ready_with_risks")
        self.assertEqual(initial["aggregate_coverage"]["items"][0]["status"], "unverified")
        self.assertEqual(initial["conformance_review"]["status"], "not_ready")
        self.assertTrue(initial["conformance_review"]["unconsumed_report_refs"])
        self.assertEqual(initial["conformance_review"]["effective_revision"], 1)

        self._submit(key="partial", coverage_status="partial", verification=["Observed failure."])
        contradictory = self.store.inspect_task(task_id=self.task, after_sequence=0)
        self.assertEqual(contradictory["aggregate_coverage"]["items"][0]["status"], "contradictory")
        self.assertEqual(contradictory["conformance_review"]["recommendation"], "rework")

    def test_non_completed_and_not_applicable_reports_do_not_make_coverage_ready(self) -> None:
        self._submit(key="partial-report", coverage_status="complete", verification=["Observed."], report_status="partial")
        partial = self.store.inspect_task(task_id=self.task, after_sequence=0)
        self.assertEqual(partial["aggregate_coverage"]["items"][0]["status"], "partial")

        stale_task = self.store.create_task(
            objective="Detect stale coverage.", user_request_original="Detect stale coverage.", user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked", requirements=["Require current coverage."],
            constraints=["Preserve evidence."], acceptance_criteria=["Show stale evidence."],
            verification_plan=["Inspect aggregate."], context={}, idempotency_key="stale-task",
        )[0]["task"]["task_id"]
        stale_item = self.store.inspect_task(task_id=stale_task, after_sequence=0)["effective_contract"]["items"][0]["item_ref"]
        stale_delegation = self.store.create_delegation(
            task_id=stale_task, objective="Own stale item.", role="worker", profile_name="general",
            scope="One item.", instructions="Submit stale evidence.", model="gpt-5.6-luna", reasoning_effort="high",
            outcome_assignments={"owned": [stale_item]}, idempotency_key="stale-delegation",
        )[0]["delegation"]["delegation_id"]
        started = self.store.submit_report(task_id=stale_task, delegation_id=stale_delegation, mode="begin", report_type="result", idempotency_key="stale-report-begin")[0]
        report_id = started["report"]["report_id"]
        self.store.submit_report(task_id=stale_task, delegation_id=stale_delegation, mode="append", report_id=report_id, section="result", content={"schema": "cortex/report/result/v2", "summary": "Stale evidence.", "outcome": "not applicable", "changes": [], "verification": [], "risks": [], "deviations": [], "unresolved": [], "contract_coverage": [{"item_ref": stale_item, "status": "not_applicable", "verification": []}]}, idempotency_key="stale-report-append")
        self.store.submit_report(task_id=stale_task, delegation_id=stale_delegation, mode="finalize", report_id=report_id, status="completed", idempotency_key="stale-report-finalize")
        stale = self.store.inspect_task(task_id=stale_task, after_sequence=0)
        self.assertEqual(stale["aggregate_coverage"]["items"][0]["status"], "stale")
        self.assertEqual(stale["conformance_review"]["status"], "not_ready")

        with self.assertRaises(Exception):
            # A distinct current owner cannot be introduced for the same item.
            self.store.create_delegation(
                task_id=self.task, objective="Duplicate owner.", role="worker", profile_name="general",
                scope="Invalid overlap.", instructions="Do not run.", model="gpt-5.6-luna", reasoning_effort="high",
                outcome_assignments={"owned": [self.item]}, idempotency_key="duplicate-owner",
            )

    def test_parent_replacement_supersedes_historical_claims_and_preserves_brief_scope(self) -> None:
        """A replacement owns current coverage; parent failures remain visible only as history."""
        self._submit(key="parent-partial", coverage_status="partial", verification=["Parent attempt."], report_status="failed")
        replacement = self.store.create_delegation(
            task_id=self.task, parent_delegation_id=self.delegation,
            objective="Replace the parent owner.", role="worker", profile_name="general",
            scope="Complete the inherited item.", instructions="Submit replacement evidence.",
            model="gpt-5.6-luna", reasoning_effort="high", outcome_assignments={"owned": self.items},
            idempotency_key="replacement-owner",
        )[0]
        replacement_id = replacement["delegation"]["delegation_id"]
        projected = self.store.read_delegation(delegation_id=replacement_id, after_sequence=0, limit=1)["worker_brief"]["effective_contract"]
        self.assertEqual(projected["revision"], 1)
        self.assertEqual(len(projected["assigned_items"]), 1)
        assigned = projected["assigned_items"][0]
        self.assertEqual(assigned["item_ref"], self.item)
        self.assertEqual(assigned["category"], "outcome")
        self.assertEqual(assigned["text"], "Cover the requirement.")
        self.assertEqual(assigned["acceptance_criteria"], ["Expose advisory conformance."])
        self.assertEqual(assigned["verification_criteria"], ["Run focused tests."])
        self.assertEqual(assigned["assignment_role"], "owned")
        self._submit(key="replacement-complete", coverage_status="complete", verification=["Replacement verified."], delegation=replacement_id)
        result = self.store.inspect_task(task_id=self.task, after_sequence=0)
        self.assertEqual(result["aggregate_coverage"]["status"], "ready")
        self.assertEqual(result["execution_outcome"]["outcome"], "completed")
        self.assertEqual(len(result["aggregate_coverage"]["items"][0]["superseded_report_refs"]), 1)

    def test_parent_replacement_without_manual_routing_inherits_only_parent_scope(self) -> None:
        """Parent-linked rework receives the parent's active owned scope exactly."""
        task = self.store.create_task(
            objective="Parent scope.", user_request_original="Parent scope.", user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked", requirements=["A", "B"],
            constraints=["C"], acceptance_criteria=["D"], verification_plan=["E"], context={}, idempotency_key="parent-task",
        )[0]["task"]["task_id"]
        items = [item["item_ref"] for item in self.store.inspect_task(task_id=task, after_sequence=0)["effective_contract"]["items"]]
        parent = self.store.create_delegation(
            task_id=task, objective="Own one item.", role="worker", profile_name="general",
            scope="One item.", instructions="Submit evidence.", model="gpt-5.6-luna",
            reasoning_effort="high", outcome_assignments={"owned": [items[0]]}, idempotency_key="single-parent",
        )[0]["delegation"]["delegation_id"]
        replacement = self.store.create_delegation(
            task_id=task, parent_delegation_id=parent, objective="Rework one item.", role="worker",
            profile_name="general", scope="Inherited.", instructions="Submit replacement evidence.",
            model="gpt-5.6-luna", reasoning_effort="high", idempotency_key="single-replacement",
        )[0]
        assigned = self.store.read_delegation(delegation_id=replacement["delegation"]["delegation_id"], after_sequence=0, limit=1)["worker_brief"]["effective_contract"]["assigned_items"]
        self.assertEqual([item["item_ref"] for item in assigned], [items[0]])
        self.assertTrue(all(item["assignment_role"] == "owned" for item in assigned))

    def test_initial_owner_without_predecessor_owns_current_effective_catalogue(self) -> None:
        """A bounded execution worker must not require a synthetic plan report."""
        task = self.store.create_task(
            objective="Direct bounded work.", user_request_original="Direct bounded work.", user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked", requirements=["Create one artifact."],
            constraints=["One worker."], acceptance_criteria=["Artifact exists."],
            verification_plan=["Verify the artifact."], context={}, idempotency_key="direct-owner-task",
        )[0]["task"]["task_id"]
        expected = {
            item["item_ref"]
            for item in self.store.inspect_task(task_id=task, after_sequence=0)["effective_contract"]["items"]
        }
        opened = self.store.create_delegation(
            task_id=task, objective="Create the artifact.", role="worker", profile_name="general",
            scope="Bounded direct implementation.", instructions="Create and verify it.",
            model="gpt-5.6-luna", reasoning_effort="high", idempotency_key="direct-owner",
            derive_assignment_scope=True,
        )[0]
        assigned = self.store.read_delegation(
            delegation_id=opened["delegation"]["delegation_id"], after_sequence=0, limit=1,
        )["worker_brief"]["effective_contract"]["assigned_items"]
        self.assertEqual({item["item_ref"] for item in assigned}, expected)
        self.assertTrue(all(item["assignment_role"] == "owned" for item in assigned))

    def test_debugger_completed_report_has_owned_aggregate_coverage(self) -> None:
        """A debugger fixes defects, so its completed result must cover owned items."""
        task = self.store.create_task(
            objective="Debug coverage attribution.", user_request_original="Debug coverage attribution.", user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked", requirements=["Fix the defect."],
            constraints=["Keep the correction focused."], acceptance_criteria=["The aggregate is covered."],
            verification_plan=["Run the regression."], context={}, idempotency_key="debugger-task",
        )[0]["task"]["task_id"]
        item_refs = [item["item_ref"] for item in self.store.inspect_task(task_id=task, after_sequence=0)["effective_contract"]["items"]]
        debugger = self.store.create_delegation(
            task_id=task, objective="Diagnose and fix the defect.", role="debugger", profile_name="debugger",
            scope="Fix the assigned defect.", instructions="Publish completed result evidence.", model="gpt-5.6-luna",
            reasoning_effort="high", outcome_assignments={"owned": item_refs}, idempotency_key="debugger-delegation",
        )[0]["delegation"]["delegation_id"]
        started = self.store.submit_report(
            task_id=task, delegation_id=debugger, mode="begin", report_type="result",
            idempotency_key="debugger-result-begin",
        )[0]
        report_id = started["report"]["report_id"]
        self.store.submit_report(
            task_id=task, delegation_id=debugger, mode="append", report_id=report_id, section="result",
            content={
                "schema": "cortex/report/result/v2", "summary": "Debugger evidence.", "outcome": "fixed",
                "changes": ["Focused correction."], "verification": ["Regression passed."], "risks": [],
                "deviations": [], "unresolved": [], "contract_coverage": [
                    {"item_ref": item, "status": "complete", "verification": ["Regression passed."]}
                    for item in item_refs
                ],
            }, idempotency_key="debugger-result-append",
        )
        self.store.submit_report(
            task_id=task, delegation_id=debugger, mode="finalize", report_id=report_id,
            status="completed", idempotency_key="debugger-result-finalize",
        )
        aggregate = self.store.inspect_task(task_id=task, after_sequence=0)["aggregate_coverage"]
        self.assertEqual(aggregate["status"], "ready")
        self.assertTrue(all(item["reason"] == "current_verified_claim" for item in aggregate["items"]))

    def test_active_assignment_snapshot_survives_linked_revision_and_old_item_is_stale(self) -> None:
        self.store.record_user_decision(
            task_id=self.task, subject_type="task", subject_id=self.task, decision_type="steer", prompt="Add a separate check.",
            response_original="Add a separate check.", user_language="en", steering_delta={"add": [{"category": "verification", "text": "Run a new independent check."}]},
            idempotency_key="unrelated-steer",
        )
        self._submit(key="survives-revision", coverage_status="complete", verification=["Still assigned after revision."])
        current = self.store.inspect_task(task_id=self.task, after_sequence=0)
        self.assertEqual(current["effective_contract"]["revision"], 2)
        self.assertEqual(len(current["effective_contract"]["items"]), 1)
        current_item = current["effective_contract"]["items"][0]
        self.assertEqual(current_item["verification_criteria"], ["Run focused tests.", "Run a new independent check."])
        self.assertEqual(current_item["supersedes_item_ref"], self.item)
        self.store.record_user_decision(
            task_id=self.task, subject_type="task", subject_id=self.task, decision_type="steer", prompt="Retire a covered item.",
            response_original="Retire a covered item.", user_language="en", steering_delta={"retire_item_refs": [current_item["item_ref"]], "add": []},
            idempotency_key="retire-steer",
        )
        with self.assertRaises(Exception):
            self.store.create_delegation(
                task_id=self.task, objective="Cannot own retired item.", role="worker", profile_name="general", scope="Invalid.",
                instructions="Do not submit.", model="gpt-5.6-luna", reasoning_effort="high", outcome_assignments={"owned": [self.item]},
                idempotency_key="retired-assignment",
            )

    def test_simultaneous_distinct_active_owner_attempts_allow_exactly_one(self) -> None:
        self.store.record_user_decision(
            task_id=self.task, subject_type="task", subject_id=self.task, decision_type="steer", prompt="Add a raced item.",
            response_original="Add a raced item.", user_language="en", steering_delta={"add": [{"category": "verification", "text": "Race active ownership."}]},
            idempotency_key="race-steer",
        )
        effective_items = self.store.inspect_task(task_id=self.task, after_sequence=0)["effective_contract"]["items"]
        self.assertEqual(len(effective_items), 1)
        self.assertIn("Race active ownership.", effective_items[0]["verification_criteria"])
        raced_item = effective_items[0]["item_ref"]
        def create(number: int) -> str:
            try:
                self.store.create_delegation(
                    task_id=self.task, objective=f"Concurrent owner {number}.", role="worker", profile_name="general",
                    scope="Race for one item.", instructions="Do not submit.", model="gpt-5.6-luna", reasoning_effort="high",
                    outcome_assignments={"owned": [raced_item]}, idempotency_key=f"concurrent-owner-{number}",
                )
                return "created"
            except Exception:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, (1, 2)))
        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(outcomes.count("rejected"), 1)
