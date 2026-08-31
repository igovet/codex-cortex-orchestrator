"""Semantic marketplace gate for the installable Cortex 1.12.2 package.

The gate exercises the registry-derived public domain API. Retired report chunk and
phase APIs are intentionally absent; detailed migration and transport checks
live in their focused test modules.
"""
from __future__ import annotations

import ast
import json
import os
import re
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
    assess_governance, close_task, consume_assignment_evidence, open_assignment as _open_assignment,
    open_clarification, open_task, publish_documentation, publish_plan, publish_result,
    read_task, record_clarification, record_plan_review, record_steering,
)
from cortex_runtime.v12_contract import task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402

from cortex_runtime.semantic_registry import OPERATION_NAMES

TOOLS = OPERATION_NAMES
REQUIRED = {
    "open_task": {"task"},
    "read_task": set(), "open_clarification": {"prompt", "prompt_language"},
    "open_plan_review": {"plan_ref", "prompt", "prompt_language"},
    "record_clarification": {"binding_ref", "response_original", "user_language"},
    "record_plan_review": {"binding_ref", "response_original", "user_language", "outcome"},
    "record_steering": {"binding_ref", "response_original", "user_language"},
    "open_steering": {"prompt", "prompt_language"},
    "open_assignment": {"mission"},
    "consume_assignment_evidence": {"assignment_ref"}, "publish_plan": {"continuation_ref", "assignment_ref", "evidence"}, "publish_result": {"continuation_ref", "assignment_ref", "evidence"}, "publish_documentation": {"continuation_ref", "assignment_ref", "evidence"},
    "assess_governance": {"mode"}, "close_task": {"verdict"},
}


def open_assignment(*, task_ref: str, mission: dict, **kwargs: object) -> dict:
    responsibility = "planning" if mission.get("profile_name") == "planner" else "delivery"
    return _open_assignment(task_ref=task_ref, mission={**mission, "responsibility": responsibility}, **kwargs)


def _coverage(assignment: dict, *, planning: bool) -> list[dict]:
    contract = assignment.get("effective_contract", {})
    items = contract.get("planning_items" if planning else "assigned_items", [])
    return [{"item_ref": item["item_ref"], "status": "planned" if planning else "complete", "verification": ["Fixture reconciled this exact item."]} for item in items]


def plan_evidence(assignment: dict) -> dict:
    return {"schema": "cortex/report/plan/v3", "summary": "Complete plan.", "scope": "Complete contract.", "stages": [{"owner": "planner", "work": ["Map every requirement."], "verification": ["Check every item."]}], "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [], "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}], "documentation_impact": "No documentation changed; no affected paths.", "contract_coverage": _coverage(assignment, planning=True)}


def result_evidence(assignment: dict) -> dict:
    return {"schema": "cortex/report/result/v3", "summary": "Complete result.", "outcome": "completed", "changes": [], "verification": ["Inspected result."], "risks": [], "deviations": [], "unresolved": [], "verification_facts": [{"state": "not_run", "summary": "No project command."}], "documentation_impact": "No documentation changed; no affected paths.", "contract_coverage": _coverage(assignment, planning=False)}


def report_rows(root: str) -> tuple[int, int, int]:
    from cortex_runtime.v12_store import V12Store
    with sqlite3.connect(V12Store(root).database_path) as db:
        return tuple(int(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in ("reports", "report_chunks", "report_operations"))


def worker_continuation(assignment: dict) -> str:
    consumed = consume_assignment_evidence(assignment_ref=assignment["assignment_ref"])
    assignment["effective_contract"] = consumed.get("effective_contract", {})
    return str(consumed["continuation_ref"])


class MarketplaceReleaseGate(unittest.TestCase):
    def setUp(self) -> None:
        """Keep compact-reference resolution out of the user's stable profile."""
        self._isolated_codex_home = tempfile.TemporaryDirectory(prefix="cortex-marketplace-codex-")
        self._previous_codex_home = os.environ.get("CODEX_HOME")
        self._previous_home = os.environ.get("HOME")
        os.environ["CODEX_HOME"] = self._isolated_codex_home.name
        os.environ["HOME"] = self._isolated_codex_home.name

    def tearDown(self) -> None:
        if self._previous_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self._previous_codex_home
        if self._previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._previous_home
        self._isolated_codex_home.cleanup()

    def test_packaged_candidate_has_one_semantic_catalog(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.12.2")
        self.assertEqual(tuple(PUBLIC_TOOLS), TOOLS)
        self.assertEqual(len(PUBLIC_TOOLS), len(TOOLS))
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
        self.assertTrue(str(manifest["version"]).startswith("1.12.2"))
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"].encode("utf-8")), 128)
        hooks = json.loads((ROOT / "plugins/cortex/hooks/hooks.json").read_text())
        session_end = hooks["hooks"]["SessionEnd"]
        self.assertTrue(session_end)
        self.assertTrue(all(
            handler["timeout"] <= 3
            for group in session_end
            for handler in group["hooks"]
        ))
        mcp = json.loads((ROOT / "plugins/cortex/.mcp.json").read_text())
        server = mcp["mcpServers"]["cortex"]
        self.assertEqual(server["env_vars"], ["CODEX_HOME", "CORTEX_SESSION_NONCE", "CORTEX_RAW_DIAGNOSTIC"])
        self.assertNotIn("env", server)
        source_files = [ROOT / "plugins/cortex/scripts/cortex.py", *sorted(SCRIPTS.joinpath("cortex_runtime").glob("*.py"))]
        for path in source_files:
            ast.parse(path.read_text(), filename=str(path))
        source = "\n".join(path.read_text() for path in source_files)
        for retired in ("reliability_recovery_target", "SubagentStop", "read_worker_wave", "wait_agent"):
            self.assertNotIn(retired, source)

    def test_semantic_lifecycle_consumes_evidence_and_replays_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-") as root:
            task = open_task(task={"project_root": root, "objective": "Durable lifecycle.", "request_original": "Durable lifecycle.", "user_language": "en", "outcomes": [{"requirement": "Keep complete contract.", "acceptance": ["One immutable outcome per assignment."]}], "constraints": ["Do not duplicate publications."]})["task"]
            tref = task_ref(task["task_id"])
            planner = open_assignment(task_ref=tref, mission={"role": "planner", "profile_name": "planner", "goal": "Prepare complete plan.", "constraints": "Planning.", "instructions": "Map contract."})
            continuation = worker_continuation(planner)
            content = plan_evidence(planner)
            first = publish_plan(continuation_ref=continuation, assignment_ref=planner["assignment_ref"], evidence=content)
            replay = publish_plan(continuation_ref=continuation, assignment_ref=planner["assignment_ref"], evidence=content)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with self.assertRaises(V12ServiceError) as conflict:
                publish_plan(continuation_ref=continuation, assignment_ref=planner["assignment_ref"], evidence={**content, "summary": "Mutated"})
            self.assertEqual(conflict.exception.code, "report_operation_conflict")
            report = first["report"]
            report_ref = report["report_ref"]
            self.assertIsInstance(report_ref, str)
            self.assertNotIn("report_id", report)
            verifier = open_assignment(task_ref=tref, mission={"role": "verification", "profile_name": "qa_engineer", "goal": "Verify implementation.", "constraints": "Verification.", "instructions": "Consume declared plan before publishing."}, input_report_refs=[report_ref])
            verifier_continuation = worker_continuation(verifier)
            outcome = publish_result(continuation_ref=verifier_continuation, assignment_ref=verifier["assignment_ref"], evidence=result_evidence(verifier))
            self.assertFalse(outcome["replayed"])
            before = report_rows(root)
            with self.assertRaises(V12ServiceError) as second:
                publish_result(continuation_ref=verifier_continuation, assignment_ref=verifier["assignment_ref"], evidence={"schema": "cortex/report/result/v3"})
            self.assertEqual(second.exception.code, "report_operation_conflict")
            self.assertEqual(report_rows(root), before)
            self.assertEqual(read_task(task_ref=tref)["task"]["task_id"], task["task_id"])

    def test_parallel_identical_publication_has_one_durable_slot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-race-") as root:
            task = open_task(task={"project_root": root, "objective": "Concurrency.", "request_original": "Concurrency.", "user_language": "en", "outcomes": [{"requirement": "Serialize writes.", "acceptance": ["One publication."]}], "constraints": ["One slot."]})["task"]
            assignment = open_assignment(task_ref=task_ref(task["task_id"]), mission={"role": "planner", "profile_name": "planner", "goal": "Plan.", "constraints": "Plan.", "instructions": "Plan."})
            continuation = worker_continuation(assignment)
            content = plan_evidence(assignment)
            results, failures = [], []
            def publish() -> None:
                try: results.append(publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=content))
                except Exception as exc: failures.append(exc)
            workers = [threading.Thread(target=publish) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(sum(bool(item["replayed"]) for item in results), 1)

    def test_decision_governance_closure_and_project_isolation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-marketplace-a-") as first, tempfile.TemporaryDirectory(prefix="cortex-marketplace-b-") as second:
            task = open_task(task={"project_root": first, "objective": "Approval.", "request_original": "Approval.", "user_language": "en", "outcomes": [{"requirement": "Ask before approval.", "acceptance": ["Close after approval."]}], "constraints": ["Keep isolated."]})["task"]
            tref = task_ref(task["task_id"])
            self.assertEqual(assess_governance(task_ref=tref, mode="minimal")["assessment"]["task_id"], task["task_id"])
            other = open_task(task={"project_root": second, "objective": "Other.", "request_original": "Other.", "user_language": "en", "outcomes": [{"requirement": "Other.", "acceptance": ["Other."]}], "constraints": ["Other."]})["task"]
            self.assertNotEqual(task["task_id"], other["task_id"])
            self.assertNotEqual(read_task(task_ref=tref)["task"]["task_id"], other["task_id"])
            pending = open_clarification(task_ref=tref, prompt="Theme?", prompt_language="en")
            binding_ref = pending.get("binding_ref")
            self.assertIsInstance(binding_ref, str)
            self.assertTrue(binding_ref)
            decision = record_clarification(task_ref=tref, binding_ref=binding_ref, response_original="Warm light", user_language="en")
            decision_view = decision["decision"]
            self.assertTrue(decision_view["decision_ref"])
            self.assertNotIn("decision_id", decision_view)
            closed = close_task(task_ref=tref, verdict="ready", evidence={"summary": "Approved and verified."})
            self.assertEqual(closed["closure"]["verdict"], "not_ready")
            self.assertEqual(closed["verdict_adjustment"], {"requested": "ready", "recorded": "not_ready"})


if __name__ == "__main__":
    unittest.main()
