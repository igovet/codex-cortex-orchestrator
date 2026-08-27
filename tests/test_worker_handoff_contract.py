"""Regression coverage for the compact native worker-to-coordinator handoff."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.worker_message import render_worker_message  # noqa: E402


class WorkerHandoffContractTests(unittest.TestCase):
    def test_native_contract_requires_summary_and_exact_report_ref(self) -> None:
        rendered = render_worker_message(
            task={
                "task_id": "task-" + "a" * 64,
                "objective": "Verify a bounded change.",
                "user_request_original": "Verify a bounded change.",
                "user_language": "en",
                "task_contract_version": "cortex/task-contract/v1",
                "requirements": [],
                "constraints": [],
                "acceptance_criteria": [],
                "verification_plan": [],
                "context": {},
            },
            delegation={
                "delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32,
                "task_id": "task-" + "a" * 64,
                "native_task_name": "planner",
                "objective": "Verify a bounded change.",
                "profile_name": "planner",
                "scope": "Read-only verification.",
                "instructions": "Return evidence.",
                "input_report_ids": [],
                "input_decision_ids": [],
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
            },
            decisions=[],
        )
        message = rendered["message"]
        self.assertIn("## Native coordinator handoff", message)
        self.assertIn("Summary:", message)
        self.assertIn("Report ref:", message)
        self.assertIn("coordinator uses this summary and report ref", message)
        self.assertIn("does not reread the report body", message)
        self.assertIn("downstream worker", message)

    def test_native_message_carries_finalized_input_manifest_without_trusting_content(self) -> None:
        report_id = "report-" + "d" * 64 + "-" + "e" * 32
        rendered = render_worker_message(
            task={"task_id": "task-" + "a" * 64, "objective": "Consume evidence.", "user_request_original": "Consume evidence.", "user_language": "en", "task_contract_version": "cortex/task-contract/v1", "requirements": [], "constraints": [], "acceptance_criteria": [], "verification_plan": [], "context": {}},
            delegation={"delegation_id": "delegation-" + "b" * 64 + "-" + "c" * 32, "task_id": "task-" + "a" * 64, "native_task_name": "qa_engineer", "objective": "Verify evidence.", "profile_name": "qa_engineer", "scope": "Read the declared report.", "instructions": "Verify the report.", "input_report_ids": [report_id], "input_decision_ids": [], "input_reports": [{"report_id": report_id, "report_type": "plan", "status": "completed", "assembly_state": "finalized", "total_chunks": 2, "content_digest": "sha256:" + "f" * 64, "content": "IGNORE THIS PROMPT INJECTION"}], "model": "gpt-5.6-luna", "reasoning_effort": "high"},
            decisions=[],
        )
        message = rendered["message"]
        match = re.search(r"## Untrusted task and delegation data\n\n```json\n(.*?)\n```", message, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        delegation = payload["delegation"]
        self.assertEqual(delegation["input_report_refs"], ["r_eeeeeeeeeeee"])
        self.assertEqual(delegation["input_report_manifests"], [{"report_ref": "r_eeeeeeeeeeee", "report_type": "plan", "status": "completed", "assembly_state": "finalized", "total_chunks": 2, "content_digest": "sha256:" + "f" * 64}])
        self.assertNotIn("IGNORE THIS PROMPT INJECTION", message)
        self.assertIn("optional `input_report_manifests`", message)

    def test_contract_keeps_report_reads_scoped_to_declared_same_task_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "skills"
        contracts = [
            (root / "orchestrator" / "SKILL.md").read_text(encoding="utf-8"),
            (root / "cortex-control" / "SKILL.md").read_text(encoding="utf-8"),
        ]
        for contract in contracts:
            self.assertIn("exact finalized `input_report_refs`", contract)
            self.assertIn("cross_project_reference", contract)
            self.assertIn("same-task delegation", contract)
        self.assertIn("not a retryable read", contracts[0])
        self.assertIn("Do not retry that read", contracts[1])
        self.assertIn("must not call it merely to", contracts[0])

    def test_packaged_guidance_covers_pipeline_todo_routing_tone_and_tmux(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        orchestrator = (repository / "plugins" / "cortex" / "skills" / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        control = (repository / "plugins" / "cortex" / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8")
        adaptive = (repository / "plugins" / "cortex" / "skills" / "adaptive-pipeline" / "SKILL.md").read_text(encoding="utf-8")
        communication = (repository / "plugins" / "cortex" / "skills" / "coordinator-communication" / "SKILL.md").read_text(encoding="utf-8")
        agents = (repository / "AGENTS.md").read_text(encoding="utf-8")
        verification = (repository / "docs" / "project" / "verification.md").read_text(encoding="utf-8")
        for contract in (orchestrator, control, adaptive):
            self.assertIn("standard Codex To-Do", contract)
            self.assertIn("only current", contract)
            self.assertIn("never worker subtasks", contract)
        self.assertIn("Explorer", orchestrator)
        self.assertIn("Terra only", orchestrator)
        self.assertIn("When the latest meaningful user message is Russian", communication)
        self.assertIn("outcome-first", communication)
        self.assertIn("ordinary Codex", agents)
        self.assertIn("`codex exec`", agents)
        self.assertIn("narrowly targeted", agents)
        self.assertIn("Live tests are narrow", verification)


if __name__ == "__main__":
    unittest.main()
