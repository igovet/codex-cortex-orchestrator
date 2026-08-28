"""Semantic marketplace gate for the installable Cortex 12.1.1 package.

The gate exercises the public ten-tool domain API. Retired report chunk and
phase APIs are intentionally absent; detailed migration and transport checks
live in their focused test modules.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_VERSION  # noqa: E402
from cortex_runtime.domain_api import (  # noqa: E402
    assess_governance, close_task, consume_assignment_evidence, open_assignment,
    open_decision, open_task, publish_documentation, publish_plan, publish_result,
    read_task, record_decision,
)
from cortex_runtime.v12_contract import record_ref, task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402

TOOLS = ("open_task", "read_task", "open_decision", "open_assignment", "consume_assignment_evidence", "publish_plan", "publish_result", "publish_documentation", "record_decision", "assess_governance", "close_task")
REQUIRED = {
    "open_task": {"project_root", "objective", "user_request_original", "user_language", "requirements", "constraints", "acceptance_criteria"},
    "read_task": {"task_ref"}, "open_decision": {"task_ref", "prompt", "prompt_language"}, "open_assignment": {"task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort"},
    "consume_assignment_evidence": {"assignment_ref"}, "publish_plan": {"assignment_ref", "evidence"}, "publish_result": {"assignment_ref", "evidence"}, "publish_documentation": {"assignment_ref", "evidence"},
    "record_decision": {"task_ref", "binding_ref", "response_original", "user_language"}, "assess_governance": {"task_ref", "mode"}, "close_task": {"task_ref", "verdict", "evidence"},
}


def plan_evidence(assignment: dict) -> dict:
    items = assignment["dispatch_brief"]["effective_contract"]["planning_items"]
    return {"schema": "cortex/report/plan/v3", "summary": "Complete plan.", "scope": "Complete contract.", "stages": [{"order": 1, "owner": "planner", "dependencies": [], "work": ["Map every requirement."], "verification": ["Check every item."]}], "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [], "evidence": [{"state": "not_run", "reason": "Planning does not execute project commands."}], "documentation_impact": {"status": "no_impact", "rationale": "No documentation changed.", "affected_surfaces": ["none"]}, "contract_coverage": [{"item_ref": item["item_ref"], "status": "complete", "verification": ["Mapped in stage 1."]} for item in items]}


def result_evidence() -> dict:
    return {"schema": "cortex/report/result/v3", "summary": "Complete result.", "outcome": "completed", "changes": [], "verification": ["Inspected result."], "risks": [], "deviations": [], "unresolved": [], "evidence": [{"state": "not_run", "reason": "No project command."}], "documentation_impact": {"status": "no_impact", "rationale": "No documentation changed.", "affected_surfaces": ["none"]}, "contract_coverage": []}


def report_rows(root: str) -> tuple[int, int, int]:
    from cortex_runtime.v12_store import V12Store
    with sqlite3.connect(V12Store(root).database_path) as db:
        return tuple(int(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in ("reports", "report_chunks", "report_operations"))


class MarketplaceReleaseGate(unittest.TestCase):
    def test_packaged_candidate_has_one_semantic_catalog(self) -> None:
        self.assertEqual(SERVER_VERSION, "12.1.1")
        self.assertEqual(tuple(PUBLIC_TOOLS), TOOLS)
        self.assertEqual(len(PUBLIC_TOOLS), 11)
        for name in TOOLS:
            contract = PUBLIC_TOOLS[name]
            schema = contract["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertTrue(REQUIRED[name] <= set(schema["required"]))
            output = contract.get("outputSchema")
            self.assertIsInstance(output, dict)
            self.assertIn("handles", output.get("properties", {}))
        self.assertNotIn("max_bytes", PUBLIC_TOOLS["consume_assignment_evidence"]["inputSchema"]["properties"])
        self.assertNotIn("idempotency_key", PUBLIC_TOOLS["publish_plan"]["inputSchema"]["properties"])

    def test_source_candidate_is_publishable_and_has_no_retired_control_plane(self) -> None:
        manifest = json.loads((ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text())
        self.assertEqual(manifest["name"], "cortex")
        self.assertTrue(str(manifest["version"]).startswith("12.1.1"))
        json.loads((ROOT / "plugins/cortex/.mcp.json").read_text())
        source_files = [ROOT / "plugins/cortex/scripts/cortex.py", *sorted(SCRIPTS.joinpath("cortex_runtime").glob("*.py"))]
        for path in source_files:
            ast.parse(path.read_text(), filename=str(path))
        source = "\n".join(path.read_text() for path in source_files)
        for retired in ("reliability_recovery_target", "SubagentStop", "read_worker_wave", "wait_agent"):
            self.assertNotIn(retired, source)

    def test_semantic_lifecycle_consumes_evidence_and_replays_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-") as root:
            task = open_task(project_root=root, objective="Durable lifecycle.", user_request_original="Durable lifecycle.", user_language="en", requirements=["Keep complete contract."], constraints=["Do not duplicate publications."], acceptance_criteria=["One immutable outcome per assignment."])["task"]
            tref = task_ref(task["task_id"])
            planner = open_assignment(task_ref=tref, objective="Prepare complete plan.", role="planner", profile_name="planner", scope="Planning.", instructions="Map contract.", model="gpt-5.6-luna", reasoning_effort="high")
            self.assertEqual(consume_assignment_evidence(assignment_ref=planner["assignment_ref"])["evidence"]["state"], "none")
            content = plan_evidence(planner)
            first = publish_plan(assignment_ref=planner["assignment_ref"], evidence=content)
            replay = publish_plan(assignment_ref=planner["assignment_ref"], evidence=content)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with self.assertRaises(V12ServiceError) as conflict:
                publish_plan(assignment_ref=planner["assignment_ref"], evidence={**content, "summary": "Mutated"})
            self.assertEqual(conflict.exception.code, "report_operation_conflict")
            verifier = open_assignment(task_ref=tref, objective="Verify implementation.", role="verification", profile_name="qa_engineer", scope="Verification.", instructions="Consume declared plan before publishing.", model="gpt-5.6-terra", reasoning_effort="high", input_report_refs=[record_ref(first["report"]["report_id"])])
            self.assertEqual(consume_assignment_evidence(assignment_ref=verifier["assignment_ref"])["evidence"]["state"], "consumed")
            outcome = publish_result(assignment_ref=verifier["assignment_ref"], evidence=result_evidence())
            self.assertFalse(outcome["replayed"])
            before = report_rows(root)
            with self.assertRaises(V12ServiceError) as second:
                publish_result(assignment_ref=verifier["assignment_ref"], evidence={"schema": "cortex/report/result/v3"})
            self.assertEqual(second.exception.code, "report_operation_conflict")
            self.assertEqual(report_rows(root), before)
            self.assertEqual(read_task(task_ref=tref)["task"]["task_id"], task["task_id"])

    def test_parallel_identical_publication_has_one_durable_slot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-race-") as root:
            task = open_task(project_root=root, objective="Concurrency.", user_request_original="Concurrency.", user_language="en", requirements=["Serialize writes."], constraints=["One slot."], acceptance_criteria=["One publication."])["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), objective="Plan.", role="planner", profile_name="planner", scope="Plan.", instructions="Plan.", model="gpt-5.6-luna", reasoning_effort="high")
            content = plan_evidence(assignment)
            results, failures = [], []
            def publish() -> None:
                try: results.append(publish_plan(assignment_ref=assignment["assignment_ref"], evidence=content))
                except Exception as exc: failures.append(exc)
            workers = [threading.Thread(target=publish) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(sum(bool(item["replayed"]) for item in results), 1)

    def test_decision_governance_closure_and_project_isolation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-a-") as first, tempfile.TemporaryDirectory(prefix="cortex-marketplace-b-") as second:
            task = open_task(project_root=first, objective="Approval.", user_request_original="Approval.", user_language="en", requirements=["Ask before approval."], constraints=["Keep isolated."], acceptance_criteria=["Close after approval."])["task"]
            tref = task_ref(task["task_id"])
            self.assertEqual(assess_governance(task_ref=tref, mode="minimal")["assessment"]["task_id"], task["task_id"])
            other = open_task(project_root=second, objective="Other.", user_request_original="Other.", user_language="en", requirements=["Other."], constraints=["Other."], acceptance_criteria=["Other."])["task"]
            self.assertNotEqual(task["task_id"], other["task_id"])
            self.assertNotEqual(read_task(task_ref=tref)["task"]["task_id"], other["task_id"])
            pending = open_decision(task_ref=tref, prompt="Theme?", prompt_language="en")
            binding_ref = pending.get("binding_ref")
            self.assertIsInstance(binding_ref, str)
            self.assertTrue(binding_ref)
            decision = record_decision(task_ref=tref, binding_ref=binding_ref, response_original="Warm light", user_language="en")
            self.assertTrue(decision["decision"]["decision_id"])
            closed = close_task(task_ref=tref, verdict="ready", evidence={"summary": "Approved and verified."})
            self.assertEqual(closed["closure"]["verdict"], "ready")


if __name__ == "__main__":
    unittest.main()
