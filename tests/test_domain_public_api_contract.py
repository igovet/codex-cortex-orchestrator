"""Focused contract checks for semantic report publication and evidence routing."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import sqlite3
import threading
import os
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_VERSION  # noqa: E402
from cortex_runtime.domain_api import (  # noqa: E402
    assess_governance, consume_assignment_evidence, open_assignment, open_task, open_clarification, publish_documentation,
    publish_plan, publish_result, open_steering, record_clarification, record_steering, read_task,
)
from cortex_runtime.v12_contract import record_ref, task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402
from cortex_runtime.v12_store import V12Store  # noqa: E402
from cortex_runtime.v12_projections import human_view as projection_human_view  # noqa: E402


class DomainPublicApiContractTests(unittest.TestCase):
    def test_catalog_keeps_server_owned_identity_and_routing_private(self) -> None:
        """Public assignment calls cannot manually select derived scope/routing."""
        assignment = PUBLIC_TOOLS["open_assignment"]["inputSchema"]
        properties = set(assignment["properties"])
        self.assertNotIn("outcome_assignments", properties)
        self.assertNotIn("model", properties)
        self.assertNotIn("reasoning_effort", properties)
        self.assertNotIn("delegation_id", properties)
        self.assertNotIn("dispatch_correlation", properties)
        self.assertNotIn("parent_assignment_ref", properties)
        for name in ("open_clarification", "open_steering"):
            self.assertNotIn("decision_id", set(PUBLIC_TOOLS[name]["inputSchema"]["properties"]))
        self.assertNotIn("subject_ref", set(PUBLIC_TOOLS["open_clarification"]["inputSchema"]["properties"]))

    def test_first_attempt_assignment_admission_matches_governance_and_profile_class(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-admission-") as root:
            light_task = open_task(task={
                "project_root": root, "objective": "Preflight profile admission.",
                "request_original": "Preflight profile admission.", "user_language": "en",
                "outcomes": [{"requirement": "Route correction once.", "acceptance": ["No rejected first assignment attempt."]}],
                "constraints": ["Use the current governance evidence."],
            })["task"]
            light_ref = task_ref(light_task["task_id"])
            assess_governance(task_ref=light_ref, mode="light")
            review = open_assignment(task_ref=light_ref, mission={
                "role": "test-only corrective QA", "profile_name": "qa_engineer",
                "goal": "Correct test-only evidence.", "constraints": "No production mutation.",
                "instructions": "Publish independent QA evidence.",
            })
            self.assertRegex(review["assignment_ref"], r"^d_[0-9a-f]{12}$")
            with self.assertRaises(V12ServiceError) as raised:
                open_assignment(task_ref=light_ref, mission={
                    "role": "production correction", "profile_name": "backend_dev",
                    "goal": "Change production source.", "constraints": "Bounded mutation.",
                    "instructions": "Implement the correction.",
                })
            self.assertEqual(raised.exception.code, "planning_predecessor_required")

        with tempfile.TemporaryDirectory(prefix="cortex-domain-minimal-admission-") as root:
            minimal_task = open_task(task={
                "project_root": root, "objective": "Keep bounded owner work minimal.",
                "request_original": "Keep bounded owner work minimal.", "user_language": "en",
                "outcomes": [{"requirement": "Apply one bounded correction.", "acceptance": ["Owner assignment opens first try."]}],
                "constraints": ["Planning is not required."],
            })["task"]
            minimal_ref = task_ref(minimal_task["task_id"])
            assess_governance(task_ref=minimal_ref, mode="minimal")
            owner = open_assignment(task_ref=minimal_ref, mission={
                "role": "bounded production correction", "profile_name": "backend_dev",
                "goal": "Apply one correction.", "constraints": "One ownership surface.",
                "instructions": "Implement and verify the bounded correction.",
            })
            self.assertRegex(owner["assignment_ref"], r"^d_[0-9a-f]{12}$")

    def test_owner_rework_predecessor_is_derived_from_current_input_report_author(self) -> None:
        """Review evidence plus the current owner report needs no caller parent."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-rework-lineage-") as root:
            task = open_task(task={
                "project_root": root,
                "objective": "Derive rework lineage.",
                "request_original": "Derive rework lineage.",
                "user_language": "en",
                "outcomes": [{
                    "requirement": "Implement one accessible artifact.",
                    "acceptance": ["Independent review can route a correction."],
                }],
                "constraints": ["Do not reconstruct assignment identity."],
            })["task"]
            tref = task_ref(task["task_id"])

            planner = open_assignment(task_ref=tref, mission={
                "role": "planner", "profile_name": "planner", "goal": "Plan the work.",
                "constraints": "Planning only.", "instructions": "Publish the plan.",
            })
            plan_ref = publish_plan(
                continuation_ref=self._worker_continuation(planner),
                assignment_ref=planner["assignment_ref"], evidence=self._plan_evidence(planner),
            )["report"]["report_ref"]

            first_owner = open_assignment(task_ref=tref, input_report_refs=[plan_ref], mission={
                "role": "implementer", "profile_name": "frontend_dev", "goal": "Implement.",
                "constraints": "Implementation only.", "instructions": "Publish the result.",
            })
            first_publication = publish_result(
                continuation_ref=self._worker_continuation(first_owner),
                assignment_ref=first_owner["assignment_ref"], evidence=self._result_evidence(),
            )
            first_ref = record_ref(first_publication["report"]["report_id"])

            current_owner = open_assignment(task_ref=tref, input_report_refs=[first_ref], mission={
                "role": "follow-on implementer", "profile_name": "frontend_dev", "goal": "Apply follow-on work.",
                "constraints": "Preserve prior work.", "instructions": "Publish the corrected result.",
            })
            self.assertEqual(current_owner["relations"], {
                "parent_assignment_ref": first_owner["assignment_ref"],
            })
            current_publication = publish_result(
                continuation_ref=self._worker_continuation(current_owner),
                assignment_ref=current_owner["assignment_ref"], evidence=self._result_evidence(),
            )
            current_ref = record_ref(current_publication["report"]["report_id"])

            reviewer = open_assignment(task_ref=tref, input_report_refs=[current_ref], mission={
                "role": "independent verifier", "profile_name": "qa_engineer", "goal": "Review.",
                "constraints": "Do not modify.", "instructions": "Publish review evidence.",
            })
            review_publication = publish_result(
                continuation_ref=self._worker_continuation(reviewer),
                assignment_ref=reviewer["assignment_ref"], evidence=self._result_evidence(),
            )
            review_ref = record_ref(review_publication["report"]["report_id"])

            rework = open_assignment(task_ref=tref, input_report_refs=[review_ref, current_ref], mission={
                "role": "accessibility rework", "profile_name": "accessibility_fixer", "goal": "Fix the finding.",
                "constraints": "Narrow correction.", "instructions": "Publish corrected evidence.",
            })
            self.assertEqual(rework["relations"], {
                "parent_assignment_ref": current_owner["assignment_ref"],
            })
            self.assertNotEqual(rework["assignment_ref"], current_owner["assignment_ref"])

    def setUp(self) -> None:
        """Keep compact-locator resolution out of the user's durable profile."""
        self._codex_home = tempfile.TemporaryDirectory(prefix="cortex-domain-codex-home-")
        self._previous_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = self._codex_home.name

    def tearDown(self) -> None:
        if self._previous_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self._previous_codex_home
        self._codex_home.cleanup()

    @staticmethod
    def _plan_evidence(assignment: dict) -> dict:
        return {
            "schema": "cortex/report/plan/v3", "summary": "Complete plan.",
            "scope": "The complete task contract.",
            "stages": [{"owner": "planner", "work": ["Map the contract."], "verification": ["Check every item."]}],
            "verification": ["Inspect the resulting report."], "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}],
            "documentation_impact": "The plan does not change documentation; no affected paths.",
        }

    @staticmethod
    def _result_evidence(assignment: dict | None = None) -> dict:
        return {
            "schema": "cortex/report/result/v3", "summary": "Complete result.", "outcome": "completed",
            "changes": [], "verification": ["Inspected the result."], "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "This contract test has no project command."}],
            "documentation_impact": "No documentation surface changed; no affected paths.",
        }

    @staticmethod
    def _worker_continuation(assignment: dict) -> str:
        consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
        assignment["effective_contract"] = consumed.get("effective_contract", {})
        return str(consumed["continuation_ref"])

    @staticmethod
    def _row_counts(root: str) -> tuple[int, int, int]:
        from cortex_runtime.v12_store import V12Store
        with sqlite3.connect(V12Store(root).database_path) as connection:
            return tuple(int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in ("reports", "report_chunks", "report_operations"))
    def test_catalog_is_semantic_and_report_publication_has_no_caller_key(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.12.2")
        publication_tools = ("publish_plan", "publish_result", "publish_documentation")
        for name in publication_tools:
            publication = PUBLIC_TOOLS[name]
            self.assertNotIn("idempotency_key", publication["inputSchema"]["properties"])
            self.assertNotIn("idempotency_key", publication["inputSchema"].get("required", []))
            self.assertIn("atomically", publication["description"].lower())
            self.assertIn("replay", publication["description"].lower())

    def test_read_contracts_route_decisions_away_from_report_body_reads(self) -> None:
        delegation = PUBLIC_TOOLS["open_assignment"]
        reports = PUBLIC_TOOLS["consume_assignment_evidence"]
        delegation_text = delegation["description"].lower()
        reports_text = reports["description"].lower()
        self.assertIn("decision", delegation_text)
        self.assertIn("report evidence", delegation_text)
        self.assertIn("report evidence", reports_text)
        self.assertIn("declared", reports_text)
        self.assertIn("decision", reports_text)

    def test_worker_bootstrap_capability_is_not_public_or_model_supplied(self) -> None:
        contract = PUBLIC_TOOLS["consume_assignment_evidence"]
        schema = contract["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"assignment_ref", "cursor"})
        self.assertNotIn("bootstrap_capability", schema["properties"])

    def test_semantic_lifecycle_replays_and_conflicts_without_public_write_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-test-") as root:
            task = open_task(task={"project_root": root, "objective": "Test domain lifecycle.", "request_original": "Test domain lifecycle.", "user_language": "en", "outcomes": [{"requirement": "Keep a durable task.", "acceptance": ["The semantic lifecycle is atomic."]}], "constraints": ["Do not use a caller write key."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Prepare a plan.", "constraints": "Planning.", "instructions": "Produce plan evidence."})
            evidence = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
            self.assertEqual(evidence["evidence"]["state"], "none")
            assignment["effective_contract"] = evidence.get("effective_contract", {})
            content = self._plan_evidence(assignment)
            continuation = str(evidence["continuation_ref"])
            first = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=content)
            replay = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=content)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            relation = first["approval_view"]
            self.assertEqual(relation["status"], "ready")
            self.assertEqual(relation["report_content_digest"], first["report"]["content_digest"])
            self.assertIsInstance(relation["approval_handle"], str)
            self.assertIsInstance(relation["content_digest"], str)
            self.assertIsInstance(relation["source_sequence"], int)
            self.assertEqual(replay["approval_view"], relation)
            with self.assertRaises(V12ServiceError) as raised:
                publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence={**content, "summary": "Changed."})
            self.assertEqual(raised.exception.code, "report_operation_conflict")

    def test_inflight_worker_survives_steering_revision_and_reconciles_before_publication(self) -> None:
        """A task revision must not stale an already-owned worker continuation."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-steering-inflight-") as root:
            task = open_task(task={
                "project_root": root, "objective": "In-flight steering.",
                "request_original": "In-flight steering.", "user_language": "en",
                "outcomes": [{"requirement": "Keep the active worker valid.", "acceptance": ["The worker publishes once after steering."]}],
                "constraints": ["Preserve one assignment."],
            })["task"]
            tref = task_ref(task["task_id"])
            assignment = open_assignment(task_ref=tref, mission={
                "role": "implementation", "profile_name": "planner",
                "goal": "Implement the requested page.",
                "constraints": "Stay within the assignment.",
                "instructions": "Implement and report the result.",
            })
            first = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
            continuation = first["continuation_ref"]
            steering = open_steering(task_ref=tref, prompt="May I add one verification requirement?", prompt_language="en", assignment_ref=assignment["assignment_ref"])
            binding = steering["binding_ref"]
            recorded = record_steering(task_ref=tref, binding_ref=binding,
                response_original="Yes, add the verification requirement.", user_language="en",
                add=[{"category": "verification", "text": "Verify the final page."}], retire_item_refs=[])
            self.assertFalse(recorded["replayed"])
            refreshed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
            self.assertEqual(refreshed["continuation_ref"], continuation)
            # The continuation remains bound to the assignment's revision-N
            # snapshot even though the task now exposes revision N+1.
            self.assertEqual(refreshed["effective_contract"]["revision"], 1)
            current = read_task(task_ref=tref)
            self.assertEqual(current["effective_contract"]["revision"], 2)
            added = next(item for item in current["effective_contract"]["items"] if item["text"] == "Verify the final page.")
            self.assertEqual(added["created_revision"], 2)
            published = publish_result(
                continuation_ref=continuation,
                assignment_ref=assignment["assignment_ref"],
                evidence=self._result_evidence(assignment),
            )
            self.assertFalse(published["replayed"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                report_id = published["report"]["report_id"]
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM report_contract_coverage c "
                        "JOIN effective_contract_items i ON i.item_id=c.item_id "
                        "WHERE c.report_id=? AND i.text=?",
                        (str(report_id), "Verify the final page."),
                    ).fetchone()[0],
                    0,
                )
            replay = publish_result(
                continuation_ref=continuation,
                assignment_ref=assignment["assignment_ref"],
                evidence=self._result_evidence(assignment),
            )
            self.assertTrue(replay["replayed"])

    def test_parent_linked_owner_claims_new_unowned_steering_items(self) -> None:
        """A follow-on owns both predecessor scope and the newly added revision."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-steering-follow-on-") as root:
            task = open_task(task={
                "project_root": root,
                "request_original": "Implement and extend one artifact.",
                "user_language": "en",
                "outcomes": [{
                    "requirement": "Implement the original artifact.",
                    "acceptance": ["The original artifact is complete."],
                }],
                "constraints": ["Keep ownership server-derived."],
            })["task"]
            tref = task_ref(task["task_id"])
            planner = open_assignment(task_ref=tref, mission={
                "role": "planner", "profile_name": "planner", "goal": "Plan.",
                "constraints": "Planning only.", "instructions": "Publish a plan.",
            })
            plan = publish_plan(
                continuation_ref=self._worker_continuation(planner),
                assignment_ref=planner["assignment_ref"], evidence=self._plan_evidence(planner),
            )
            owner = open_assignment(
                task_ref=tref, input_report_refs=[plan["report"]["report_ref"]], mission={
                    "role": "implementer", "profile_name": "frontend_dev", "goal": "Implement.",
                    "constraints": "Implementation only.", "instructions": "Publish the result.",
                },
            )
            owner_evidence = consume_assignment_evidence(assignment_ref=owner["assignment_ref"])
            steering = open_steering(
                task_ref=tref, assignment_ref=owner["assignment_ref"],
                prompt="Add the revised behavior.", prompt_language="en",
            )
            recorded = record_steering(
                task_ref=tref, binding_ref=steering["binding_ref"],
                response_original="Add the revised behavior.", user_language="en",
                add=[
                    {"category": "requirement", "text": "Implement the revised behavior."},
                    {"category": "acceptance", "text": "The revised behavior is complete."},
                    {"category": "verification", "text": "Verify the revised behavior."},
                ], retire_item_refs=[],
            )
            first = publish_result(
                continuation_ref=owner_evidence["continuation_ref"],
                assignment_ref=owner["assignment_ref"], evidence=self._result_evidence(),
            )
            follow_on = open_assignment(
                task_ref=tref, input_report_refs=[record_ref(first["report"]["report_id"])],
                input_decision_refs=[recorded["decision"]["decision_ref"]], mission={
                    "role": "follow-on implementer", "profile_name": "frontend_dev", "goal": "Apply revision.",
                    "constraints": "Preserve prior work.", "instructions": "Publish the revised result.",
                },
            )
            self.assertEqual(follow_on["relations"]["parent_assignment_ref"], owner["assignment_ref"])
            follow_on_evidence = consume_assignment_evidence(assignment_ref=follow_on["assignment_ref"])
            self.assertEqual(follow_on_evidence["effective_contract"]["revision"], 2)
            revised_texts = {
                item["text"] for item in follow_on_evidence["effective_contract"]["assigned_items"]
                if "revised behavior" in item["text"]
            }
            self.assertEqual(revised_texts, {
                "Implement the revised behavior.",
                "The revised behavior is complete.",
                "Verify the revised behavior.",
            })
            publication = publish_result(
                continuation_ref=follow_on_evidence["continuation_ref"],
                assignment_ref=follow_on["assignment_ref"], evidence=self._result_evidence(),
            )
            with sqlite3.connect(V12Store(root).database_path) as connection:
                covered = connection.execute(
                    "SELECT COUNT(*) FROM report_contract_coverage c "
                    "JOIN effective_contract_items i ON i.item_id=c.item_id "
                    "WHERE c.report_id=? AND i.created_revision=2",
                    (publication["report"]["report_id"],),
                ).fetchone()[0]
            self.assertEqual(covered, 3)

    def test_clarification_response_can_amend_contract_atomically(self) -> None:
        """A clarification answer and its user delta share one decision receipt."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-clarification-delta-") as root:
            task = open_task(task={
                "project_root": root, "objective": "Clarification delta.",
                "request_original": "Clarification delta.", "user_language": "en",
                "outcomes": [{"requirement": "Build the page.", "acceptance": ["The theme is user-approved."]}],
                "constraints": ["No external assets."],
            })["task"]
            tref = task_ref(task["task_id"])
            opened = open_clarification(task_ref=tref, prompt="Which theme should the page use?", prompt_language="en")
            args = {
                "response_original": "Use a warm light theme.", "user_language": "en",
                "add": [{"category": "requirement", "text": "Use a warm light theme."}],
            }
            first = record_clarification(task_ref=tref, binding_ref=opened["binding_ref"], response_original=args["response_original"], user_language=args["user_language"])
            self.assertFalse(first["replayed"])
            self.assertIn(first["clarification_hold"]["state"], {"answered", "coordinator_completed"})
            replay = record_clarification(task_ref=tref, binding_ref=opened["binding_ref"], response_original=args["response_original"], user_language=args["user_language"])
            self.assertTrue(replay["replayed"])
            current = V12Store(root).inspect_task(task_id=task["task_id"], after_sequence=0)["effective_contract"]
            self.assertEqual(current["revision"], 1)
            self.assertFalse(any(item["text"] == "Use a warm light theme." for item in current["items"]))

    def test_terminal_publication_derives_immutable_assignment_coverage(self) -> None:
        """The server persists complete scope without caller-authored item identity."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-coverage-gate-") as root:
            task = open_task(task={
                "project_root": root, "objective": "Coverage gate.",
                "request_original": "Coverage gate.", "user_language": "en",
                "outcomes": [{"requirement": "Require server-owned coverage.", "acceptance": ["Every assigned item is covered atomically."]}],
                "constraints": ["No incomplete terminal report."],
            })["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={
                "role": "implementation", "profile_name": "planner", "goal": "Produce result.",
                "constraints": "Stay in scope.", "instructions": "Produce complete evidence.",
            })
            continuation = self._worker_continuation(assignment)
            self.assertNotIn("contract_coverage", PUBLIC_TOOLS["publish_result"]["inputSchema"]["properties"]["evidence"]["properties"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                assignment_id = connection.execute("SELECT assignment_id FROM assignment_scope_snapshots LIMIT 1").fetchone()[0]
                snapshot_count = connection.execute("SELECT COUNT(DISTINCT item_id) FROM assignment_scope_snapshots WHERE assignment_id=?", (assignment_id,)).fetchone()[0]
            published = publish_result(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=self._result_evidence(assignment))
            self.assertFalse(published["replayed"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                assigned = connection.execute(
                    "SELECT COUNT(DISTINCT item_id) FROM assignment_scope_snapshots WHERE assignment_id=(SELECT delegation_id FROM reports WHERE report_id=?)",
                    (published["report"]["report_id"],),
                ).fetchone()[0]
                covered = connection.execute(
                    "SELECT COUNT(*) FROM report_contract_coverage WHERE report_id=?",
                    (published["report"]["report_id"],),
                ).fetchone()[0]
            self.assertEqual(covered, assigned)
            self.assertEqual(covered, snapshot_count)

            docs_task = open_task(task={
                "project_root": root, "objective": "Documentation coverage gate.",
                "request_original": "Documentation coverage gate.", "user_language": "en",
                "outcomes": [{"requirement": "Document the result.", "acceptance": ["Documentation impact is covered."]}],
                "constraints": ["No incomplete documentation report."],
            })["task"]
            docs_assignment = open_assignment(task_ref=task_ref(docs_task["task_id"]), mission={
                "role": "technical_writer", "profile_name": "planner", "goal": "Assess documentation.",
                "constraints": "Stay in scope.", "instructions": "Report documentation impact.",
            })
            docs_continuation = self._worker_continuation(docs_assignment)
            docs_evidence = {"schema": "cortex/report/synthesis/v3", "summary": "Documentation assessed.", "findings": [], "recommendations": [], "verification": [], "risks": [], "deviations": [], "unresolved": [], "documentation_impact": "No documentation changes are required."}
            docs_published = publish_documentation(continuation_ref=docs_continuation, assignment_ref=docs_assignment["assignment_ref"], evidence=docs_evidence)
            self.assertFalse(docs_published["replayed"])

    def test_server_defaults_have_one_canonical_publication_identity(self) -> None:
        """Omitted bookkeeping and explicit empty values are the same command."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-defaults-") as root:
            task = open_task(task={"project_root": root, "objective": "Canonical defaults.", "request_original": "Canonical defaults.", "user_language": "en", "outcomes": [{"requirement": "Own mechanical defaults on the server.", "acceptance": ["Equivalent publications replay one slot."]}], "constraints": ["Do not require caller bookkeeping."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Prepare a plan.", "constraints": "Planning.", "instructions": "Produce plan evidence."})
            continuation = self._worker_continuation(assignment)
            explicit = self._plan_evidence(assignment)
            explicit["stages"] = [
                {"order": 0, "dependencies": [], "owner": "planner", "work": ["Map the contract."], "verification": ["Check the map."]},
                {"order": 1, "dependencies": [0], "owner": "implementation", "work": ["Implement the plan."], "verification": ["Check the result."]},
            ]
            omitted = {
                key: value for key, value in explicit.items()
                if key not in {"risks", "deviations", "unresolved"}
            }
            first = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=omitted)
            replay = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=explicit)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(first["report"], replay["report"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                stored = json.loads(connection.execute("SELECT content_json FROM report_chunks").fetchone()[0])
            self.assertEqual([stage["order"] for stage in stored["stages"]], [1, 2])
            self.assertEqual([stage["dependencies"] for stage in stored["stages"]], [[], [1]])

    def test_assignment_reconciles_an_interrupted_dispatch_and_preserves_replacement_identity(self) -> None:
        """A lost host response cannot mint a second logical assignment.

        The public call deliberately has no caller retry key.  The server's
        canonical mutation identity must therefore make an exact repeated
        request replay the committed assignment, while a materially changed
        mission remains a distinct assignment.
        """
        with tempfile.TemporaryDirectory(prefix="cortex-domain-assignment-replay-") as root:
            task = open_task(task={"project_root": root, "objective": "Assignment replay.", "request_original": "Assignment replay.", "user_language": "en", "outcomes": [{"requirement": "Keep one dispatch.", "acceptance": ["An interrupted dispatch is reconciled."]}], "constraints": ["No duplicate worker."]})["task"]
            tref = task_ref(task["task_id"])
            mission = {"role": "planner", "profile_name": "planner", "goal": "Prepare the plan.", "constraints": "One bounded plan.", "instructions": "Map the contract."}
            first = open_assignment(task_ref=tref, mission=mission)
            # Simulate the host losing the response before worker spawn.  No
            # consume call is made: the durable assignment and its bootstrap
            # lease are still pending and must be returned unchanged.
            replay = open_assignment(task_ref=tref, mission={**mission, "goal": "Prepare the plan now.", "instructions": "Map the same contract."})
            self.assertEqual(replay["assignment_ref"], first["assignment_ref"])
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM delegations").fetchone()[0], 1)
            replacement = open_assignment(
                task_ref=tref,
                mission={**mission, "constraints": "A materially different execution scope."},
            )
            self.assertNotEqual(replacement["assignment_ref"], first["assignment_ref"])
            with sqlite3.connect(V12Store(root).database_path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM delegations").fetchone()[0], 2)

    def test_plan_publication_rolls_back_if_ready_relation_cannot_materialize(self) -> None:
        """Canonical publication never exposes a plan without its exact view."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-plan-atomic-") as root:
            task = open_task(task={"project_root": root, "objective": "Atomic plan.", "request_original": "Atomic plan.", "user_language": "en", "outcomes": [{"requirement": "Keep relation atomic.", "acceptance": ["The ready plan relation commits with publication."]}], "constraints": ["No partial report."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Prepare plan.", "constraints": "Planning.", "instructions": "Produce evidence."})
            continuation = self._worker_continuation(assignment)
            with patch("cortex_runtime.v12_projections._safe_write", side_effect=OSError("forced view failure")):
                with self.assertRaises(V12ServiceError) as raised:
                    publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=self._plan_evidence(assignment))
            self.assertEqual(raised.exception.code, "storage_unavailable")
            from cortex_runtime.v12_store import V12Store
            with sqlite3.connect(V12Store(root).database_path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM reports WHERE report_type='plan'").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_operations WHERE kind='plan'").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM approval_handles").fetchone()[0], 0)

    def test_immutable_plan_approval_view_allows_exact_snapshot_not_global_staleness(self) -> None:
        """An unrelated later task event must not invalidate the plan snapshot."""
        with tempfile.TemporaryDirectory(prefix="cortex-domain-approval-fresh-") as root:
            task = open_task(task={"project_root": root, "objective": "Approval snapshot.", "request_original": "Approval snapshot.", "user_language": "en", "outcomes": [{"requirement": "Keep exact plan view.", "acceptance": ["Use exact immutable revision."]}], "constraints": ["No stale approval."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Prepare a plan.", "constraints": "Planning.", "instructions": "Produce plan evidence."})
            continuation = self._worker_continuation(assignment)
            published = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=self._plan_evidence(assignment))
            store = V12Store(root)
            report_id = store._read(lambda connection: str(connection.execute(
                "SELECT report_id FROM reports WHERE task_id=? AND report_type='plan'", (task["task_id"],)
            ).fetchone()[0]))
            relative = f"plans/revisions/{report_id}.md"
            store._write(lambda connection: store._timeline(
                connection, event_type="unrelated_advisory", entity_type="task", entity_id=task["task_id"],
                payload={}, task_id=task["task_id"],
            ))
            self.assertEqual(projection_human_view(store, task["task_id"], relative)["status"], "stale")
            snapshot = store.human_view(task["task_id"], relative, require_fresh=False)
            self.assertEqual(snapshot["status"], "ready")
            self.assertEqual(snapshot["content_digest"], published["approval_view"]["content_digest"])

    def test_rejected_publications_are_validated_before_any_row_is_written(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-negative-") as root:
            task = open_task(task={"project_root": root, "objective": "Reject invalid evidence.", "request_original": "Reject invalid evidence.", "user_language": "en", "outcomes": [{"requirement": "Keep rows empty.", "acceptance": ["No partial publication."]}], "constraints": ["Reject malformed envelopes."]})['task']
            assignment = open_assignment(task_ref=task_ref(task['task_id']), mission={"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Plan.", "instructions": "Plan."})
            continuation = self._worker_continuation(assignment)
            valid = self._plan_evidence(assignment)
            cases = (
                ("noncanonical", {**valid, "schema": "cortex/report/plan/v2"}),
                ("wrong-schema", {**valid, "schema": "cortex/report/result/v3"}),
                ("missing-summary", {key: value for key, value in valid.items() if key != "summary"}),
                ("missing-verification-facts", {**valid, "verification_facts": []}),
            )
            for name, content in cases:
                before = self._row_counts(root)
                with self.subTest(name=name):
                    with self.assertRaises(V12ServiceError) as raised:
                        publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=content)
                    self.assertEqual(raised.exception.code, "report_incomplete")
                    self.assertEqual(self._row_counts(root), before)

    def test_concurrent_identical_publication_uses_one_logical_slot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-concurrent-") as root:
            task = open_task(task={"project_root": root, "objective": "Concurrent.", "request_original": "Concurrent.", "user_language": "en", "outcomes": [{"requirement": "Serialize.", "acceptance": ["One publication."]}], "constraints": ["No duplicate slot."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Plan.", "instructions": "Plan."})
            continuation = self._worker_continuation(assignment)
            content = self._plan_evidence(assignment)
            results, failures = [], []
            def publish() -> None:
                try:
                    results.append(publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=content))
                except Exception as exc:  # assertion below keeps thread faults visible
                    failures.append(exc)
            workers = [threading.Thread(target=publish) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(bool(item["replayed"]) for item in results), 1)


if __name__ == "__main__":
    unittest.main()
