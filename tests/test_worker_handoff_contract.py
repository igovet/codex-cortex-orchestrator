"""Regression coverage for the compact native worker-to-coordinator handoff."""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
