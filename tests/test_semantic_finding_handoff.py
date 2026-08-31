"""End-to-end invariants for lossless finding handoff and truthful closure."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))

from cortex_runtime.domain_api import (  # noqa: E402
    close_task,
    consume_assignment_evidence,
    open_assignment,
    open_plan_review,
    open_task,
    publish_plan,
    publish_result,
    read_task,
    record_plan_review,
    open_steering,
    record_steering,
)
from cortex_runtime.v12_contract import record_ref, task_ref as compact_task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402


def _coverage(items: list[dict], status: str) -> list[dict]:
    return [
        {
            "item_ref": item["item_ref"],
            "status": status,
            "verification": [f"Reconciled {item['category']} item {item['item_ref']}."],
        }
        for item in items
    ]


class SemanticFindingHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory(prefix="cortex-finding-handoff-home-")
        self.project = tempfile.TemporaryDirectory(prefix="cortex-finding-handoff-project-")
        self.previous = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = self.home.name
        self.provenance = patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value={
                "build_digest": "sha256:" + "1" * 64,
                "candidate_digest": "sha256:" + "2" * 64,
                "source_digest": "sha256:" + "2" * 64,
                "catalogue_digest": "sha256:" + "3" * 64,
            },
        )
        self.provenance.start()

    def tearDown(self) -> None:
        self.provenance.stop()
        if self.previous is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous
        self.project.cleanup()
        self.home.cleanup()

    def _task(self, count: int = 3, *, label: str = "primary") -> tuple[str, list[dict]]:
        request = f"Preserve every independently supplied finding ({label})."
        opened = open_task(task={
            "project_root": self.project.name,
            "request_original": request,
            "user_language": "en",
            "outcomes": [
                {
                    "requirement": f"F-{index:02d}: preserve and resolve finding {index}.",
                    "acceptance": [f"F-{index:02d} has an explicit final disposition."],
                }
                for index in range(1, count + 1)
            ],
            "constraints": ["No finding may disappear between reports."],
        })
        task_ref = compact_task_ref(opened["task"]["task_id"])
        assert task_ref is not None
        state = read_task(task_ref=task_ref)
        return task_ref, state["effective_contract"]["items"]

    def test_profile_does_not_select_ownership_and_multi_outcome_scope_is_explicit(self) -> None:
        task_ref, items = self._task()
        selected = [item["item_ref"] for item in items if item["category"] == "outcome"][:1]
        evidence_assignment = open_assignment(task_ref=task_ref, mission={
            "role": "bounded discovery",
            "profile_name": "explorer",
            "responsibility": "evidence",
            "item_refs": selected,
            "goal": "Investigate one finding.",
            "constraints": "Read only; one finding.",
            "instructions": "Publish one exact disposition.",
        })
        consumed = consume_assignment_evidence(assignment_ref=evidence_assignment["assignment_ref"])
        self.assertEqual(
            [item["item_ref"] for item in consumed["effective_contract"]["assigned_items"]],
            selected,
        )
        self.assertEqual(consumed["effective_contract"]["assigned_items"][0]["assignment_role"], "evidence")
        self.assertEqual(consumed["assignment_context"]["responsibility"], "evidence")

        with self.assertRaises(V12ServiceError) as missing_scope:
            open_assignment(task_ref=task_ref, mission={
                "role": "broad discovery",
                "profile_name": "explorer",
                "responsibility": "evidence",
                "goal": "Investigate everything.",
                "constraints": "No exact selection.",
                "instructions": "Publish findings.",
            })
        self.assertEqual(missing_scope.exception.details["field"], "mission.item_refs")

    def test_two_outcomes_keep_linked_criteria_and_steer_without_contract_inflation(self) -> None:
        opened = open_task(task={
            "project_root": self.project.name,
            "request_original": "Deliver F-01 and F-02 with their stated checks.",
            "user_language": "en",
            "outcomes": [
                {"requirement": "F-01: deliver the first outcome.", "acceptance": ["F-01 passes its check."]},
                {"requirement": "F-02: deliver the second outcome.", "acceptance": ["F-02 passes its check."]},
            ],
            "constraints": ["Do not turn orchestration instructions into coverage."],
        })
        self.assertEqual(opened["task"]["verification_plan"], [])
        task_ref = compact_task_ref(opened["task"]["task_id"])
        assert task_ref is not None
        initial = read_task(task_ref=task_ref)["effective_contract"]
        self.assertEqual(len(initial["items"]), 2)
        self.assertEqual([item["category"] for item in initial["items"]], ["outcome", "outcome"])
        self.assertEqual(
            [item["acceptance_criteria"] for item in initial["items"]],
            [["F-01 passes its check."], ["F-02 passes its check."]],
        )
        self.assertEqual([item["verification_criteria"] for item in initial["items"]], [[], []])
        self.assertEqual(len(initial["task_constraints"]), 1)
        for ordinal, item in enumerate(initial["items"]):
            self.assertEqual(item["source_fragments"][0]["path"], f"task.outcomes[{ordinal}].requirement")
            self.assertEqual(item["source_fragments"][1]["path"], f"task.outcomes[{ordinal}].acceptance[0]")

        refs = [item["item_ref"] for item in initial["items"]]
        assignment = open_assignment(task_ref=task_ref, mission={
            "role": "delivery", "profile_name": "general", "responsibility": "delivery",
            "item_refs": refs, "goal": "Deliver both outcomes.",
            "constraints": "Preserve linked criteria.", "instructions": "Publish exactly two dispositions.",
        })
        consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
        self.assertEqual(consumed["publication_reconciliation"]["required_item_count"], 2)
        self.assertEqual(
            [item["item_ref"] for item in consumed["effective_contract"]["assigned_items"]], refs,
        )

        steering = open_steering(task_ref=task_ref, prompt="Strengthen F-01.", prompt_language="en")
        recorded = record_steering(
            task_ref=task_ref, binding_ref=steering["binding_ref"],
            response_original="Also verify F-01 in live-dev.", user_language="en",
            add=[{"outcome_ref": refs[0], "category": "verification", "text": "Verify F-01 in live-dev."}],
            retire_item_refs=[],
        )
        revised = read_task(task_ref=task_ref)["effective_contract"]
        self.assertEqual(len(revised["items"]), 2)
        revised_first = next(item for item in revised["items"] if item.get("supersedes_item_ref") == refs[0])
        self.assertEqual(revised_first["text"], "F-01: deliver the first outcome.")
        self.assertEqual(revised_first["verification_criteria"], ["Verify F-01 in live-dev."])
        self.assertEqual(revised_first["source_decision_ref"], recorded["decision"]["decision_ref"])
        self.assertEqual(revised_first["source_fragments"][-1]["source_type"], "user_steer")

    def test_plan_cannot_drop_an_item_or_request_generic_approval_with_unresolved_policy(self) -> None:
        task_ref, items = self._task()
        refs = [item["item_ref"] for item in items]
        assignment = open_assignment(task_ref=task_ref, mission={
            "role": "planner", "profile_name": "planner", "responsibility": "planning",
            "item_refs": refs, "goal": "Plan every finding.", "constraints": "Keep all findings.",
            "instructions": "Map every exact contract item.",
        })
        consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
        self.assertEqual(consumed["assignment_context"]["responsibility"], "planning")
        plan_items = consumed["effective_contract"]["planning_items"]
        base = {
            "schema": "cortex/report/plan/v3", "summary": "Plan every finding.",
            "scope": "Complete finding pool.",
            "stages": [{"owner": "implementer", "work": ["Resolve every finding."], "verification": ["Reconcile every item."]}],
            "verification": ["Compare the final matrix with the original contract."],
            "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "Planning itself runs no project command."}],
            "contract_coverage": _coverage(plan_items, "planned"),
        }
        with self.assertRaises(V12ServiceError) as omitted:
            publish_plan(
                continuation_ref=consumed["continuation_ref"],
                assignment_ref=assignment["assignment_ref"],
                evidence={**base, "contract_coverage": base["contract_coverage"][:-1]},
            )
        self.assertEqual(omitted.exception.details["reason"], "contract_coverage_incomplete")

        compatible_coverage = [dict(item) for item in base["contract_coverage"]]
        compatible_coverage.append({
            "item_ref": compatible_coverage[0]["item_ref"],
            "status": "planned",
            "verification": ["A second planned check for the same exact item."],
        })
        unresolved_plan = publish_plan(
            continuation_ref=consumed["continuation_ref"],
            assignment_ref=assignment["assignment_ref"],
            evidence={
                **base,
                "unresolved": ["Choose whether readiness mismatch rejects or downgrades closure."],
                "contract_coverage": compatible_coverage,
            },
        )
        report_ref = unresolved_plan["report"]["report_ref"]
        stored = read_task(task_ref=task_ref, report_refs=[report_ref])
        stored_coverage = stored["report_evidence"]["reports"][0]["chunks"][0]["content"]["contract_coverage"]
        self.assertEqual(len(stored_coverage), len(base["contract_coverage"]))
        self.assertEqual(
            stored_coverage[0]["verification"],
            [base["contract_coverage"][0]["verification"][0], "A second planned check for the same exact item."],
        )
        with self.assertRaises(V12ServiceError) as approval:
            open_plan_review(
                task_ref=task_ref,
                plan_ref=unresolved_plan["report"]["report_ref"],
                prompt="Approve the plan?",
                prompt_language="en",
            )
        self.assertEqual(approval.exception.code, "plan_clarification_required")

        conflict_task_ref, conflict_items = self._task(count=1, label="conflicting duplicate")
        conflict_assignment = open_assignment(task_ref=conflict_task_ref, mission={
            "role": "delivery owner", "profile_name": "explorer", "responsibility": "delivery",
            "item_refs": [item["item_ref"] for item in conflict_items], "goal": "Resolve one finding.",
            "constraints": "Keep one disposition.", "instructions": "Publish exact coverage.",
        })
        conflict_consumed = consume_assignment_evidence(assignment_ref=conflict_assignment["assignment_ref"])
        conflict_coverage = _coverage(conflict_consumed["effective_contract"]["assigned_items"], "complete")
        conflict_coverage.append({
            "item_ref": conflict_coverage[0]["item_ref"], "status": "partial",
            "verification": ["This conflicts with the completed disposition."],
        })
        with self.assertRaises(V12ServiceError) as conflict:
            publish_result(
                continuation_ref=conflict_consumed["continuation_ref"],
                assignment_ref=conflict_assignment["assignment_ref"],
                evidence={
                    "schema": "cortex/report/result/v3", "summary": "Conflicting dispositions.",
                    "outcome": "Cannot reconcile conflicting status.", "changes": [], "verification": [],
                    "risks": [], "deviations": [], "unresolved": ["Disposition conflict."],
                    "verification_facts": [{"state": "not_run", "summary": "Conflict blocks completion."}],
                    "documentation_impact": "No documentation impact.", "contract_coverage": conflict_coverage,
                },
                status="partial",
            )
        self.assertEqual(conflict.exception.details["reason"], "contract_coverage_duplicate")

    def test_coordinator_body_receipt_and_conformance_normalize_closure(self) -> None:
        task_ref, items = self._task(count=1)
        refs = [item["item_ref"] for item in items]
        assignment = open_assignment(task_ref=task_ref, mission={
            "role": "final audit owner", "profile_name": "explorer", "responsibility": "delivery",
            "item_refs": refs, "goal": "Deliver the final audit.", "constraints": "Read-only final result.",
            "instructions": "Resolve and reconcile every exact item.",
        })
        consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
        self.assertEqual(consumed["assignment_context"]["responsibility"], "delivery")
        assigned = consumed["effective_contract"]["assigned_items"]
        reconciliation = consumed["publication_reconciliation"]
        self.assertEqual(reconciliation["coverage_source"], "assigned_items")
        self.assertEqual(reconciliation["required_item_count"], len(assigned))
        self.assertEqual(
            reconciliation["required_item_refs"],
            [item["item_ref"] for item in assigned],
        )
        self.assertEqual(
            reconciliation["contract_coverage_template"],
            [{"item_ref": item["item_ref"]} for item in assigned],
        )
        publication = publish_result(
            continuation_ref=consumed["continuation_ref"],
            assignment_ref=assignment["assignment_ref"],
            evidence={
                "schema": "cortex/report/result/v3", "summary": "Every finding is resolved.",
                "outcome": "Final audit delivered.", "changes": [],
                "verification": ["Compared every disposition with the original contract."],
                "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "executed", "summary": "The exact finding matrix was reconciled."}],
                "documentation_impact": "No documentation change; this is a read-only audit.",
                "contract_coverage": _coverage(assigned, "complete"),
            },
        )
        report_ref = record_ref(publication["report"]["report_id"])
        self.assertIsNotNone(report_ref)
        before = read_task(task_ref=task_ref)
        self.assertEqual(before["aggregate_coverage"]["status"], "ready")
        self.assertEqual(before["conformance_review"]["status"], "not_ready")
        self.assertEqual(before["conformance_review"]["unconsumed_report_refs"], [report_ref])

        read_task(task_ref=task_ref, report_refs=[report_ref], sections=["body"])
        selected_only = read_task(task_ref=task_ref)
        self.assertEqual(selected_only["conformance_review"]["status"], "not_ready")
        self.assertEqual(selected_only["conformance_review"]["unconsumed_report_refs"], [report_ref])

        page = read_task(task_ref=task_ref, report_refs=[report_ref])
        self.assertFalse(page["report_evidence"]["has_more"])
        self.assertEqual(page["report_evidence"]["reports"][0]["chunks"][0]["content"]["contract_coverage"], _coverage(assigned, "complete"))
        after = read_task(task_ref=task_ref)
        self.assertEqual(after["conformance_review"]["status"], "ready")
        self.assertEqual(after["conformance_review"]["unconsumed_report_refs"], [])

        closed = close_task(task_ref=task_ref, verdict="ready")
        self.assertEqual(closed["closure"]["verdict"], "ready")
        self.assertEqual(closed["verdict_adjustment"], {"requested": "ready", "recorded": "ready"})

        blocked_ref, _ = self._task(count=1, label="premature closure")
        premature = close_task(task_ref=blocked_ref, verdict="ready")
        self.assertEqual(premature["closure"]["verdict"], "not_ready")
        self.assertEqual(premature["verdict_adjustment"], {"requested": "ready", "recorded": "not_ready"})

    def test_plan_revision_preserves_every_original_item_and_supersedes_exactly_once(self) -> None:
        task_ref, items = self._task(label="plan revision")
        refs = [item["item_ref"] for item in items]
        first = open_assignment(task_ref=task_ref, mission={
            "role": "planner", "profile_name": "planner", "responsibility": "planning",
            "item_refs": refs, "goal": "Plan every finding.", "constraints": "Keep all findings.",
            "instructions": "Map every exact contract item.",
        })
        first_consumed = consume_assignment_evidence(assignment_ref=first["assignment_ref"])
        first_body = {
            "schema": "cortex/report/plan/v3", "summary": "Plan every original finding.",
            "scope": "The complete original finding pool.",
            "stages": [{"owner": "implementer", "work": ["Resolve each exact finding."], "verification": ["Reconcile the original matrix."]}],
            "verification": ["Compare every final disposition to the original contract."],
            "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "Planning runs no project command."}],
            "contract_coverage": _coverage(first_consumed["effective_contract"]["planning_items"], "planned"),
        }
        first_publication = publish_plan(
            continuation_ref=first_consumed["continuation_ref"],
            assignment_ref=first["assignment_ref"], evidence=first_body,
        )
        first_report_ref = first_publication["report"]["report_ref"]
        opened = open_plan_review(
            task_ref=task_ref, plan_ref=first_report_ref,
            prompt="Revise the verification wording.", prompt_language="en",
        )
        revision = record_plan_review(
            task_ref=task_ref, binding_ref=opened["binding_ref"], outcome="request_revision",
            response_original="Keep all findings and strengthen verification.", user_language="en",
        )
        replacement = open_assignment(
            task_ref=task_ref,
            input_report_refs=[first_report_ref],
            input_decision_refs=[revision["decision_ref"]],
            mission={
                "role": "revision planner", "profile_name": "planner", "responsibility": "planning",
                "item_refs": refs, "goal": "Revise without dropping findings.",
                "constraints": "Preserve the complete original matrix.",
                "instructions": "Consume and supersede the exact predecessor plan.",
            },
        )
        self.assertEqual(replacement["relations"]["parent_assignment_ref"], first["assignment_ref"])
        replacement_consumed = consume_assignment_evidence(assignment_ref=replacement["assignment_ref"])
        self.assertEqual(
            {item["item_ref"] for item in replacement_consumed["effective_contract"]["planning_items"]},
            set(refs),
        )
        with self.assertRaises(V12ServiceError) as omitted:
            publish_plan(
                continuation_ref=replacement_consumed["continuation_ref"],
                assignment_ref=replacement["assignment_ref"],
                evidence={**first_body, "contract_coverage": first_body["contract_coverage"][:-1]},
            )
        self.assertEqual(omitted.exception.details["reason"], "contract_coverage_incomplete")
        revised = publish_plan(
            continuation_ref=replacement_consumed["continuation_ref"],
            assignment_ref=replacement["assignment_ref"],
            evidence={**first_body, "summary": "Revised plan retains every original finding."},
        )
        self.assertEqual(revised["report"]["supersedes_report_ref"], first_report_ref)

    def test_closure_requires_every_active_specialist_body_not_only_owner_coverage(self) -> None:
        task_ref, items = self._task(count=1, label="specialist body consumption")
        refs = [item["item_ref"] for item in items]

        def publish(*, profile: str, responsibility: str, role: str) -> str:
            assignment = open_assignment(task_ref=task_ref, mission={
                "role": role, "profile_name": profile, "responsibility": responsibility,
                "item_refs": refs, "goal": f"Publish {role} evidence.",
                "constraints": "Reconcile the exact item.", "instructions": "Publish one exact disposition.",
            })
            consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
            publication = publish_result(
                continuation_ref=consumed["continuation_ref"],
                assignment_ref=assignment["assignment_ref"],
                evidence={
                    "schema": "cortex/report/result/v3", "summary": f"{role} completed.",
                    "outcome": f"{role} reconciled the assigned item.", "changes": [],
                    "verification": ["Compared the exact item reference."],
                    "risks": [], "deviations": [], "unresolved": [],
                    "verification_facts": [{"state": "executed", "summary": f"{role} checked the item."}],
                    "documentation_impact": "No documentation impact.",
                    "contract_coverage": _coverage(consumed["effective_contract"]["assigned_items"], "complete"),
                },
            )
            compact = record_ref(publication["report"]["report_id"])
            assert compact is not None
            return compact

        specialist_ref = publish(profile="explorer", responsibility="evidence", role="specialist discovery")
        owner_ref = publish(profile="general", responsibility="delivery", role="final owner")
        read_task(task_ref=task_ref, report_refs=[owner_ref])
        owner_only = read_task(task_ref=task_ref)["conformance_review"]
        self.assertEqual(owner_only["status"], "not_ready")
        self.assertEqual(owner_only["unconsumed_report_refs"], [specialist_ref])
        read_task(task_ref=task_ref, report_refs=[specialist_ref])
        self.assertEqual(read_task(task_ref=task_ref)["conformance_review"]["status"], "ready")

    def test_evidence_recheck_explicitly_supersedes_partial_finding(self) -> None:
        task_ref, items = self._task(count=1, label="evidence recheck")
        refs = [item["item_ref"] for item in items]

        first = open_assignment(task_ref=task_ref, mission={
            "role": "first verification", "profile_name": "explorer", "responsibility": "evidence",
            "item_refs": refs, "goal": "Verify the finding.", "constraints": "Read only.",
            "instructions": "Publish the unresolved disposition.",
        })
        first_consumed = consume_assignment_evidence(assignment_ref=first["assignment_ref"])
        first_publication = publish_result(
            continuation_ref=first_consumed["continuation_ref"], assignment_ref=first["assignment_ref"],
            status="partial", evidence={
                "schema": "cortex/report/result/v3", "summary": "Finding remains unresolved.",
                "outcome": "Verification was incomplete.", "changes": [], "verification": ["Observed the blocker."],
                "risks": ["Finding remains open."], "deviations": [], "unresolved": ["Rerun the affected check."],
                "verification_facts": [{"state": "not_run", "summary": "Affected check was blocked."}],
                "documentation_impact": "No documentation impact.",
                "contract_coverage": _coverage(first_consumed["effective_contract"]["assigned_items"], "partial"),
            },
        )
        first_ref = record_ref(first_publication["report"]["report_id"])
        assert first_ref is not None
        replacement = open_assignment(
            task_ref=task_ref, input_report_refs=[first_ref], mission={
                "role": "verification recheck", "profile_name": "explorer", "responsibility": "evidence",
                "item_refs": refs, "goal": "Rerun the affected check.", "constraints": "Read only.",
                "instructions": "Consume and supersede the partial evidence.",
            },
        )
        self.assertEqual(replacement["relations"]["parent_assignment_ref"], first["assignment_ref"])
        replacement_consumed = consume_assignment_evidence(assignment_ref=replacement["assignment_ref"])
        replacement_publication = publish_result(
            continuation_ref=replacement_consumed["continuation_ref"], assignment_ref=replacement["assignment_ref"],
            evidence={
                "schema": "cortex/report/result/v3", "summary": "Recheck resolved the finding.",
                "outcome": "Verification completed.", "changes": [], "verification": ["Affected check passed."],
                "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "executed", "summary": "Affected check passed on recheck."}],
                "documentation_impact": "No documentation impact.",
                "contract_coverage": _coverage(replacement_consumed["effective_contract"]["assigned_items"], "complete"),
            },
        )
        replacement_ref = record_ref(replacement_publication["report"]["report_id"])
        assert replacement_ref is not None
        self.assertEqual(record_ref(replacement_publication["report"]["supersedes_report_id"]), first_ref)

        owner = open_assignment(task_ref=task_ref, mission={
            "role": "final owner", "profile_name": "general", "responsibility": "delivery",
            "item_refs": refs, "goal": "Complete the outcome.", "constraints": "Use verified evidence.",
            "instructions": "Publish the complete owner disposition.",
        })
        owner_consumed = consume_assignment_evidence(assignment_ref=owner["assignment_ref"])
        owner_publication = publish_result(
            continuation_ref=owner_consumed["continuation_ref"], assignment_ref=owner["assignment_ref"],
            evidence={
                "schema": "cortex/report/result/v3", "summary": "Outcome complete.",
                "outcome": "Final owner disposition is complete.", "changes": [], "verification": ["Recheck evidence passed."],
                "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "executed", "summary": "Owner reconciled the passed recheck."}],
                "documentation_impact": "No documentation impact.",
                "contract_coverage": _coverage(owner_consumed["effective_contract"]["assigned_items"], "complete"),
            },
        )
        owner_ref = record_ref(owner_publication["report"]["report_id"])
        assert owner_ref is not None
        read_task(task_ref=task_ref, report_refs=[replacement_ref, owner_ref])
        conformance = read_task(task_ref=task_ref)["conformance_review"]
        self.assertEqual(conformance["status"], "ready")
        self.assertNotIn(first_ref, [item["report_ref"] for item in conformance["required_report_manifests"]])


if __name__ == "__main__":
    unittest.main()
