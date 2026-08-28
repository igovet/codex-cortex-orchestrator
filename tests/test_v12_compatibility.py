"""Historical-store compatibility plus semantic public API regression tests."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_VERSION
from cortex_runtime.domain_api import (
    assess_governance, close_task, consume_assignment_evidence, open_assignment,
    open_task, open_decision, publish_documentation, publish_plan, publish_result, read_task, record_decision,
)
from cortex_runtime.v12_contract import task_ref
from cortex_runtime.v12_store import V12Store


class V12CompatibilityTests(unittest.TestCase):
    @staticmethod
    def _plan(assignment: dict) -> dict:
        items = assignment["dispatch_brief"]["effective_contract"]["planning_items"]
        return {
            "schema": "cortex/report/plan/v3", "summary": "Plan.", "scope": "Lifecycle.",
            "stages": [{"order": 1, "owner": "planner", "dependencies": [], "work": ["Map lifecycle."], "verification": ["Inspect all items."]}],
            "verification": ["Inspect publication."], "risks": [], "deviations": [], "unresolved": [],
            "evidence": [{"state": "not_run", "reason": "Planning only."}],
            "documentation_impact": {"status": "no_impact", "rationale": "No documentation change.", "affected_surfaces": ["none"]},
            "contract_coverage": [{"item_ref": item["item_ref"], "status": "complete", "verification": ["Mapped."]} for item in items],
        }

    @staticmethod
    def _result() -> dict:
        return {
            "schema": "cortex/report/result/v3", "summary": "Complete.", "outcome": "completed",
            "changes": [], "verification": ["Inspected."], "risks": [], "deviations": [], "unresolved": [],
            "evidence": [{"state": "not_run", "reason": "No project command."}],
            "documentation_impact": {"status": "no_impact", "rationale": "No documentation change.", "affected_surfaces": ["none"]},
            "contract_coverage": [],
        }

    def test_version_and_exact_semantic_catalogue(self) -> None:
        self.assertEqual(SERVER_VERSION, "12.1.1")
        self.assertEqual(tuple(PUBLIC_TOOLS), (
            "open_task", "read_task", "open_decision", "open_assignment", "consume_assignment_evidence",
            "publish_plan", "publish_result", "publish_documentation", "record_decision",
            "assess_governance", "close_task",
        ))
        for contract in PUBLIC_TOOLS.values():
            self.assertFalse("idempotency_key" in contract["inputSchema"]["properties"])

    def test_fresh_store_records_v14_and_historical_rows_remain_readable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-v14-") as root:
            store = V12Store(root)
            task, _ = store.create_task(
                objective="Keep history.", user_request_original="Keep history.", user_language="en",
                task_contract_version="cortex/task-contract/v2-criteria-derived",
                requirements=["Read history."], constraints=["No reset."],
                acceptance_criteria=["Migration preserves rows."], verification_plan=["Migration preserves rows."],
                context={}, idempotency_key="historical-direct-store",
            )
            with sqlite3.connect(store.database_path) as connection:
                self.assertEqual(connection.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone(), (15, "v15-durable-clarification-bindings"))
            self.assertEqual(store.inspect_task(task_id=task["task"]["task_id"], after_sequence=0, limit=50)["task"]["objective"], "Keep history.")

    def test_all_domain_operations_have_concrete_first_call_schemas(self) -> None:
        for name, contract in PUBLIC_TOOLS.items():
            schema = contract["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertFalse(schema["additionalProperties"], name)
            self.assertTrue(schema.get("required"), name)

    def test_domain_lifecycle_paging_publication_governance_and_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-domain-") as root:
            task = open_task(project_root=root, objective="Lifecycle.", user_request_original="Lifecycle.", user_language="en", requirements=["Run lifecycle."], constraints=["No reset."], acceptance_criteria=["All semantic operations work."])["task"]
            anchor = task_ref(task["task_id"])
            self.assertEqual(read_task(task_ref=anchor)["task"]["task_id"], task["task_id"])
            assignment = open_assignment(task_ref=anchor, objective="Plan.", role="planner", profile_name="planner", scope="Plan.", instructions="Plan.", model="gpt-5.6-luna", reasoning_effort="high")
            self.assertEqual(consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])["evidence"]["state"], "none")
            publish_plan(assignment_ref=assignment["assignment_ref"], evidence=self._plan(assignment))
            docs = open_assignment(task_ref=anchor, objective="Docs.", role="writer", profile_name="technical_writer", scope="Docs.", instructions="Docs.", model="gpt-5.6-luna", reasoning_effort="high")
            publish_documentation(assignment_ref=docs["assignment_ref"], evidence={"schema": "cortex/report/synthesis/v2", "summary": "No impact.", "findings": [], "recommendations": [], "contract_coverage": [], "deviations": [], "unresolved": [], "risks": [], "verification": []})
            result = open_assignment(task_ref=anchor, objective="Result.", role="implementation", profile_name="backend_dev", scope="Result.", instructions="Result.", model="gpt-5.6-luna", reasoning_effort="high")
            publish_result(assignment_ref=result["assignment_ref"], evidence=self._result())
            binding = open_decision(task_ref=anchor, prompt="Confirm closure.", prompt_language="en")
            self.assertIn("decision", record_decision(task_ref=anchor, binding_ref=binding["binding_ref"], response_original="Confirmed.", user_language="en"))
            self.assertIn("assessment", assess_governance(task_ref=anchor, mode="light"))
            self.assertIn("closure", close_task(task_ref=anchor, verdict="ready", evidence={"summary": "Evidence."}))


if __name__ == "__main__":
    unittest.main()
