"""Focused contract checks for semantic report publication and evidence routing."""
from __future__ import annotations

import sys
import tempfile
import sqlite3
import threading
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_VERSION  # noqa: E402
from cortex_runtime.domain_api import (  # noqa: E402
    consume_assignment_evidence, open_assignment, open_task, publish_documentation,
    publish_plan, publish_result,
)
from cortex_runtime.v12_contract import task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402


class DomainPublicApiContractTests(unittest.TestCase):
    @staticmethod
    def _plan_evidence(assignment: dict) -> dict:
        items = assignment["dispatch_brief"]["effective_contract"]["planning_items"]
        return {
            "schema": "cortex/report/plan/v3", "summary": "Complete plan.",
            "scope": "The complete task contract.",
            "stages": [{"order": 1, "owner": "planner", "dependencies": [], "work": ["Map the contract."], "verification": ["Check every item."]}],
            "verification": ["Inspect the resulting report."], "risks": [], "deviations": [], "unresolved": [],
            "evidence": [{"state": "not_run", "reason": "Planning does not execute project commands."}],
            "documentation_impact": {"status": "no_impact", "rationale": "The plan does not change documentation.", "affected_surfaces": ["none"]},
            "contract_coverage": [{"item_ref": item["item_ref"], "status": "complete", "verification": ["Mapped in stage 1."]} for item in items],
        }

    @staticmethod
    def _result_evidence() -> dict:
        return {
            "schema": "cortex/report/result/v3", "summary": "Complete result.", "outcome": "completed",
            "changes": [], "verification": ["Inspected the result."], "risks": [], "deviations": [], "unresolved": [],
            "evidence": [{"state": "not_run", "reason": "This contract test has no project command."}],
            "documentation_impact": {"status": "no_impact", "rationale": "No documentation surface changed.", "affected_surfaces": ["none"]},
            "contract_coverage": [],
        }

    @staticmethod
    def _row_counts(root: str) -> tuple[int, int, int]:
        from cortex_runtime.v12_store import V12Store
        with sqlite3.connect(V12Store(root).database_path) as connection:
            return tuple(int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in ("reports", "report_chunks", "report_operations"))
    def test_catalog_is_semantic_and_report_publication_has_no_caller_key(self) -> None:
        self.assertEqual(SERVER_VERSION, "12.1.1")
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

    def test_semantic_lifecycle_replays_and_conflicts_without_public_write_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-test-") as root:
            task = open_task(
                project_root=root, objective="Test domain lifecycle.", user_request_original="Test domain lifecycle.",
                user_language="en", requirements=["Keep a durable task."], constraints=["Do not use a caller write key."],
                acceptance_criteria=["The semantic lifecycle is atomic."],
            )["task"]
            assignment = open_assignment(
                task_ref=task_ref(task["task_id"]), objective="Prepare a plan.", role="planner", profile_name="planner",
                scope="Planning.", instructions="Produce plan evidence.", model="gpt-5.6-luna", reasoning_effort="high",
            )
            evidence = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
            self.assertEqual(evidence["evidence"]["state"], "none")
            content = self._plan_evidence(assignment)
            first = publish_plan(assignment_ref=assignment["assignment_ref"], evidence=content)
            replay = publish_plan(assignment_ref=assignment["assignment_ref"], evidence=content)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with self.assertRaises(V12ServiceError) as raised:
                publish_plan(assignment_ref=assignment["assignment_ref"], evidence={**content, "summary": "Changed."})
            self.assertEqual(raised.exception.code, "report_operation_conflict")

    def test_rejected_publications_are_validated_before_any_row_is_written(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-negative-") as root:
            task = open_task(project_root=root, objective="Reject invalid evidence.", user_request_original="Reject invalid evidence.", user_language="en", requirements=["Keep rows empty."], constraints=["Reject malformed envelopes."], acceptance_criteria=["No partial publication."])['task']
            assignment = open_assignment(task_ref=task_ref(task['task_id']), objective="Plan.", role="planner", profile_name="planner", scope="Plan.", instructions="Plan.", model="gpt-5.6-luna", reasoning_effort="high")
            valid = self._plan_evidence(assignment)
            cases = (
                ("noncanonical", {**valid, "schema": "cortex/report/plan/v2"}),
                ("wrong-schema", {**valid, "schema": "cortex/report/result/v3"}),
                ("missing-summary", {key: value for key, value in valid.items() if key != "summary"}),
                ("missing-evidence", {**valid, "evidence": []}),
                ("missing-impact", {key: value for key, value in valid.items() if key != "documentation_impact"}),
                ("missing-coverage", {key: value for key, value in valid.items() if key != "contract_coverage"}),
                ("bad-stage", {**valid, "stages": [{**valid["stages"][0], "order": 2}]}),
            )
            for name, content in cases:
                before = self._row_counts(root)
                with self.subTest(name=name):
                    with self.assertRaises(V12ServiceError) as raised:
                        publish_plan(assignment_ref=assignment["assignment_ref"], evidence=content)
                    self.assertEqual(raised.exception.code, "report_incomplete")
                    self.assertEqual(self._row_counts(root), before)

    def test_concurrent_identical_publication_uses_one_logical_slot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-concurrent-") as root:
            task = open_task(project_root=root, objective="Concurrent.", user_request_original="Concurrent.", user_language="en", requirements=["Serialize."], constraints=["No duplicate slot."], acceptance_criteria=["One publication."])["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), objective="Plan.", role="planner", profile_name="planner", scope="Plan.", instructions="Plan.", model="gpt-5.6-luna", reasoning_effort="high")
            content = self._plan_evidence(assignment)
            results, failures = [], []
            def publish() -> None:
                try:
                    results.append(publish_plan(assignment_ref=assignment["assignment_ref"], evidence=content))
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
