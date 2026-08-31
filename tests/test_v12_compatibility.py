"""Historical-store compatibility plus semantic public API regression tests."""
from __future__ import annotations

import sqlite3
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_VERSION
from cortex_runtime.domain_api import (
    assess_governance, close_task, consume_assignment_evidence, open_assignment as _open_assignment,
    open_task, open_clarification, publish_documentation as _publish_documentation, publish_plan as _publish_plan, publish_result as _publish_result,
    read_task, record_clarification,
)
from cortex_runtime.v12_contract import task_ref
from cortex_runtime.v12_store import V12Store


def open_assignment(*, task_ref: str, mission: dict, **kwargs: object) -> dict:
    profile = mission.get("profile_name")
    responsibility = "planning" if profile == "planner" else "evidence" if profile in {"qa_engineer", "technical_writer", "explorer"} else "delivery"
    return _open_assignment(task_ref=task_ref, mission={**mission, "responsibility": responsibility}, **kwargs)


def _covered(assignment_ref: str, evidence: dict, kind: str) -> dict:
    store, assignment_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
    contract = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)["worker_brief"]["effective_contract"]
    items = contract.get("planning_items", []) if kind == "plan" else contract.get("assigned_items", [])
    status = "planned" if kind == "plan" else "complete"
    return {**evidence, "contract_coverage": [{"item_ref": item["item_ref"], "status": status, "verification": ["Fixture reconciled the assigned item."]} for item in items]}


def publish_plan(*, assignment_ref: str, evidence: dict, **kwargs: object) -> dict:
    return _publish_plan(assignment_ref=assignment_ref, evidence=_covered(assignment_ref, evidence, "plan"), **kwargs)


def publish_result(*, assignment_ref: str, evidence: dict, **kwargs: object) -> dict:
    return _publish_result(assignment_ref=assignment_ref, evidence=_covered(assignment_ref, evidence, "result"), **kwargs)


def publish_documentation(*, assignment_ref: str, evidence: dict, **kwargs: object) -> dict:
    return _publish_documentation(assignment_ref=assignment_ref, evidence=_covered(assignment_ref, evidence, "documentation"), **kwargs)


class V12CompatibilityTests(unittest.TestCase):
    @staticmethod
    def _bootstrap(assignment: dict) -> str:
        # The one-time bootstrap lease is private server state.  A worker
        # supplies only the exact assignment locator for atomic resolution.
        return assignment["assignment_ref"]

    @staticmethod
    def _plan(assignment: dict) -> dict:
        return {
            "schema": "cortex/report/plan/v3", "summary": "Plan.", "scope": "Lifecycle.",
            "stages": [{"owner": "planner", "work": ["Map lifecycle."], "verification": ["Inspect all items."]}],
            "verification": ["Inspect publication."], "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "Planning only."}],
            "documentation_impact": "No documentation change; no affected paths.",
        }

    @staticmethod
    def _result(assignment: dict) -> dict:
        return {
            "schema": "cortex/report/result/v3", "summary": "Complete.", "outcome": "completed",
            "changes": [], "verification": ["Inspected."], "risks": [], "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "No project command."}],
            "documentation_impact": "No documentation change; no affected paths.",
        }

    def test_version_and_exact_semantic_catalogue(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.12.2")
        self.assertEqual(tuple(PUBLIC_TOOLS), (
            "open_task", "read_task", "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
            "open_assignment", "consume_assignment_evidence", "publish_plan", "publish_result",
            "publish_documentation", "assess_governance", "close_task",
        ))
        for contract in PUBLIC_TOOLS.values():
            self.assertFalse("idempotency_key" in contract["inputSchema"]["properties"])

    def test_fresh_store_records_v14_and_historical_rows_remain_readable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-v14-") as root:
            store = V12Store(root)
            task, _ = store.create_task(
                objective="Keep history.", user_request_original="Keep history.", user_language="en",
                task_contract_version="cortex/task-contract/v3-outcome-linked",
                requirements=["Read history."], constraints=["No reset."],
                acceptance_criteria=["Migration preserves rows."], verification_plan=["Migration preserves rows."],
                context={}, idempotency_key="historical-direct-store",
            )
            with sqlite3.connect(store.database_path) as connection:
                self.assertEqual(connection.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone(), (24, "v24-outcome-linked-contract"))
            self.assertEqual(store.inspect_task(task_id=task["task"]["task_id"], after_sequence=0, limit=50)["task"]["objective"], "Keep history.")

    def test_v23_migration_backfills_and_seals_assignment_scope(self) -> None:
        """A v22 shard upgrades automatically without rewriting ownership."""
        with tempfile.TemporaryDirectory(prefix="cortex-v23-upgrade-") as root:
            task = open_task(task={"project_root": root, "objective": "Upgrade scope.", "request_original": "Upgrade scope.", "user_language": "en", "outcomes": [{"requirement": "Preserve assignment scope.", "acceptance": ["The migration backfills immutable rows."]}], "constraints": ["No manual database action."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Plan.", "instructions": "Plan."})
            store = V12Store(root)
            with sqlite3.connect(store.database_path) as connection:
                assignment_id = connection.execute("SELECT delegation_id FROM delegations WHERE task_id=?", (task["task_id"],)).fetchone()[0]
                connection.execute("DROP TRIGGER assignment_scope_no_update")
                connection.execute("DROP TRIGGER assignment_scope_no_delete")
                connection.execute("DROP INDEX assignment_scope_task_revision")
                connection.execute("DROP TABLE assignment_scope_snapshots")
                connection.execute("DROP TABLE effective_contract_item_details")
                connection.execute("DELETE FROM schema_migrations WHERE version IN (23,24)")
                connection.commit()
            upgraded = V12Store(root)
            with sqlite3.connect(upgraded.database_path) as connection:
                self.assertGreater(
                    connection.execute("SELECT COUNT(*) FROM assignment_scope_snapshots WHERE assignment_id=?", (assignment_id,)).fetchone()[0],
                    0,
                )
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute("DELETE FROM assignment_scope_snapshots WHERE assignment_id=?", (assignment_id,))

    def test_all_domain_operations_have_concrete_first_call_schemas(self) -> None:
        for name, contract in PUBLIC_TOOLS.items():
            schema = contract["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertFalse(schema["additionalProperties"], name)
            if name == "read_task":
                self.assertEqual(schema.get("required"), [], name)
                self.assertIn("task_ref", schema["properties"], name)
            else:
                self.assertTrue(schema.get("required"), name)

    def test_domain_lifecycle_paging_publication_governance_and_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-") as root:
            task = open_task(task={"project_root": root, "objective": "Lifecycle.", "request_original": "Lifecycle.", "user_language": "en", "outcomes": [{"requirement": "Run lifecycle.", "acceptance": ["All semantic operations work."]}], "constraints": ["No reset."]})["task"]
            anchor = task_ref(task["task_id"])
            self.assertEqual(read_task(task_ref=anchor)["task"]["task_id"], task["task_id"])
            assignment = open_assignment(task_ref=anchor, mission={"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Plan.", "instructions": "Plan."})
            consumed = consume_assignment_evidence(assignment_ref=self._bootstrap(assignment))
            assignment["effective_contract"] = consumed.get("effective_contract", {})
            self.assertEqual(consumed["evidence"]["state"], "none")
            continuation = consumed["continuation_ref"]
            publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=self._plan(assignment))
            docs = open_assignment(task_ref=anchor, mission={"role": "writer", "profile_name": "technical_writer", "goal": "Docs.", "constraints": "Docs.", "instructions": "Docs."})
            docs_consumed = consume_assignment_evidence(assignment_ref=self._bootstrap(docs))
            docs["effective_contract"] = docs_consumed.get("effective_contract", {})
            docs_cont = docs_consumed["continuation_ref"]
            publish_documentation(continuation_ref=docs_cont, assignment_ref=docs["assignment_ref"], evidence={"schema": "cortex/report/synthesis/v3", "summary": "No impact.", "findings": [], "recommendations": [], "deviations": [], "unresolved": [], "risks": [], "verification": [], "documentation_impact": "No documentation impact."})
            result = open_assignment(task_ref=anchor, mission={"role": "implementation", "profile_name": "backend_dev", "goal": "Result.", "constraints": "Result.", "instructions": "Result."})
            result_consumed = consume_assignment_evidence(assignment_ref=self._bootstrap(result))
            result["effective_contract"] = result_consumed.get("effective_contract", {})
            result_cont = result_consumed["continuation_ref"]
            publish_result(continuation_ref=result_cont, assignment_ref=result["assignment_ref"], evidence=self._result(result))
            binding = open_clarification(task_ref=anchor, prompt="Confirm closure.", prompt_language="en")
            self.assertIn("decision", record_clarification(task_ref=anchor, binding_ref=binding["binding_ref"], response_original="Confirmed.", user_language="en"))
            self.assertIn("assessment", assess_governance(task_ref=anchor, mode="light"))
            self.assertIn("closure", close_task(task_ref=anchor, verdict="ready", evidence={"summary": "Evidence."}))


if __name__ == "__main__":
    unittest.main()
