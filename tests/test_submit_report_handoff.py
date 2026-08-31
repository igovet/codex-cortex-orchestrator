"""Receipt-level handoff coverage for finalized worker reports."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_contract import record_ref  # noqa: E402
from cortex_runtime.v12_service import submit_report  # noqa: E402
from cortex_runtime.v12_store import V12Store  # noqa: E402


class SubmitReportHandoffTests(unittest.TestCase):
    def test_finalized_plan_receipt_contains_verified_approval_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-submit-handoff-") as temporary:
            root = Path(temporary)
            with mock.patch.dict(os.environ, {"HOME": str(root / "home")}, clear=False):
                (root / "home").mkdir()
                project = root / "project"
                project.mkdir()
                store = V12Store(project)
                task = store.create_task(
                    objective="Receipt handoff",
                    user_request_original="Receipt handoff",
                    user_language="en",
                    task_contract_version="cortex/task-contract/v3-outcome-linked",
                    requirements=["Return a receipt"],
                    constraints=["Do not mutate production"],
                    acceptance_criteria=["Approval metadata is ready"],
                    verification_plan=["Run this test"],
                    context={},
                    idempotency_key="handoff-task",
                )[0]["task"]["task_id"]
                delegation = store.create_delegation(
                    task_id=task,
                    objective="Prepare plan",
                    role="planner",
                    profile_name="planner",
                    scope="Plan only",
                    instructions="Submit one finalized plan",
                    model="gpt-5.6-luna",
                    reasoning_effort="high",
                    idempotency_key="handoff-delegation",
                )[0]["delegation"]["delegation_id"]

                started = submit_report(
                    delegation_ref=record_ref(delegation),
                    mode="begin",
                    report_type="plan",
                    idempotency_key="begin-plan",
                )
                submit_report(
                    delegation_ref=record_ref(delegation),
                    mode="append",
                    report_ref=record_ref(started["report"]["report_id"]),
                    section="body",
                    content={"schema": "cortex/report/plan/v1", "summary": "Ready", "scope": [], "stages": [], "verification": []},
                    idempotency_key="append-plan",
                )
                receipt = submit_report(
                    delegation_ref=record_ref(delegation),
                    mode="finalize",
                    report_ref=record_ref(started["report"]["report_id"]),
                    status="completed",
                    idempotency_key="finalize-plan",
                )

                self.assertEqual(receipt["report"]["report_type"], "plan")
                self.assertEqual(receipt["report"]["storage_status"], "storage_valid")
                self.assertEqual(receipt["report"]["semantic_status"], "semantic_valid")
                self.assertEqual(receipt["approval_view"]["status"], "ready")
                self.assertTrue(receipt["approval_view"]["approval_handle"])
                self.assertEqual(
                    receipt["approval_view"]["report_content_digest"],
                    receipt["report"]["content_digest"],
                )
                self.assertTrue(receipt["approval_view"]["path"])


if __name__ == "__main__":
    unittest.main()
