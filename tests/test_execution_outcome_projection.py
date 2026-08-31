"""Focused regression coverage for neutral public execution-outcome projections."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store  # noqa: E402


class ExecutionOutcomeProjectionTests(unittest.TestCase):
    def test_task_and_closure_responses_expose_canonical_result_outcomes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-v12-outcome-") as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            project.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("CORTEX_HOST_STATE_DIR", None)
                os.environ.pop("CODEX_HOME", None)
                store = V12Store(project)
                task = store.create_task(
                    objective="Expose neutral report evidence.",
                    user_request_original="Expose neutral report evidence.",
                    user_language="en",
                    task_contract_version="cortex/task-contract/v3-outcome-linked",
                    requirements=["Preserve neutral report evidence."],
                    constraints=["Keep advisory closure independent."],
                    acceptance_criteria=["Task and closure projections expose neutral counts."],
                    verification_plan=["Inspect both public projections."],
                    context={},
                    idempotency_key="outcome-task",
                )[0]["task"]["task_id"]
                delegation = store.create_delegation(
                    task_id=task,
                    objective="Submit one canonical result.",
                    role="worker",
                    profile_name="general",
                    scope="Own the result evidence.",
                    instructions="Submit the result evidence.",
                    model="gpt-5.6-luna",
                    reasoning_effort="high",
                    idempotency_key="outcome-delegation",
                )[0]["delegation"]["delegation_id"]
                def submit(*, key: str, status: str, content: object) -> dict[str, object]:
                    started = store.submit_report(task_id=task, delegation_id=delegation, mode="begin", report_type="result", idempotency_key=f"{key}-begin")[0]
                    report_id = started["report"]["report_id"]
                    store.submit_report(task_id=task, delegation_id=delegation, mode="append", report_id=report_id, section="result", content=content, idempotency_key=f"{key}-append")
                    return store.submit_report(task_id=task, delegation_id=delegation, mode="finalize", report_id=report_id, status=status, idempotency_key=f"{key}-finalize")[0]
                initial = store.inspect_task(task_id=task, after_sequence=0)
                self.assertEqual(initial["execution_outcome"], {
                    "evidence_status": "no_finalized_reports",
                    "finalized_report_count": 0,
                    "completed_report_count": 0,
                    "effective_revision": 1,
                    "coverage_status": "rework",
                    "outcome": "incomplete",
                })
                submit(key="outcome-noncanonical-report", status="completed", content={"schema": "not-canonical", "outcome": "ignored"})
                noncanonical = store.inspect_task(task_id=task, after_sequence=0)
                self.assertEqual(noncanonical["execution_outcome"], {
                    "evidence_status": "finalized_reports_present",
                    "finalized_report_count": 1,
                    "completed_report_count": 0,
                    "effective_revision": 1,
                    "coverage_status": "rework",
                    "outcome": "incomplete",
                })
                report = submit(key="outcome-report", status="completed", content={
                        "schema": "cortex/report/result/v1",
                        "summary": "The worker recorded result evidence.",
                        "outcome": "evidence_recorded",
                        "changes": [],
                        "verification": [],
                        "risks": [],
                    })
                self.assertEqual(report["report"]["assembly_state"], "finalized")
                inspected = store.inspect_task(task_id=task, after_sequence=0)
                self.assertEqual(inspected["execution_outcome"], {
                    "evidence_status": "finalized_reports_present",
                    "finalized_report_count": 2,
                    "completed_report_count": 1,
                    "effective_revision": 1,
                    "coverage_status": "rework",
                    "outcome": "incomplete",
                })
                closure = store.submit_governance_closure(
                    task_id=task,
                    subject_type="task",
                    subject_id=task,
                    verdict="ready",
                    evidence={"result": "evidence_recorded"},
                    unresolved_risks=[],
                    follow_ups=[],
                    initiative_status=None,
                    completion_notes=None,
                    idempotency_key="outcome-closure",
                )[0]
                self.assertEqual(closure["execution_outcome"], inspected["execution_outcome"])
                # A recorded advisory verdict must be accompanied by the
                # current conformance projection; the verdict alone is not a
                # readiness gate.
                self.assertEqual(closure["conformance_review"], inspected["conformance_review"])
                self.assertEqual(closure["conformance_review"]["status"], "not_ready")
                self.assertEqual(closure["conformance_review"]["recommendation"], "rework")
                submit(key="outcome-failed-report", status="failed", content={
                        "schema": "cortex/report/result/v1",
                        "summary": "A later canonical result remained incomplete.",
                        "outcome": "worker_detail_not_exposed",
                        "changes": [],
                        "verification": [],
                        "risks": ["Acceptance remains incomplete."],
                    })
                later_evidence = store.inspect_task(task_id=task, after_sequence=0)
                self.assertEqual(later_evidence["execution_outcome"], {
                    "evidence_status": "finalized_reports_present",
                    "finalized_report_count": 3,
                    "completed_report_count": 1,
                    "effective_revision": 1,
                    "coverage_status": "rework",
                    "outcome": "incomplete",
                })
                self.assertEqual(
                    set(later_evidence["execution_outcome"]),
                    {"evidence_status", "finalized_report_count", "completed_report_count", "effective_revision", "coverage_status", "outcome"},
                )


if __name__ == "__main__":
    unittest.main()
