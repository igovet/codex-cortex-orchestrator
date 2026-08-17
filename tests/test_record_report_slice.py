"""Characterization and boundary tests for the RecordReport vertical slice."""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control
from cortex_runtime import reports
from cortex_runtime.ledger_db import list_projection_jobs
from cortex_runtime.record_report import facade
from cortex_runtime.record_report.domain import RecordReportCommand, RecordReportOutcome
from cortex_runtime.record_report.use_case import RecordReportUseCase


class _Mutation:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.commands: list[RecordReportCommand] = []

    def mutate(self, command: RecordReportCommand) -> RecordReportOutcome:
        self.commands.append(command)
        return RecordReportOutcome(self.result)


class _Projections:
    def __init__(self) -> None:
        self.calls: list[tuple[RecordReportOutcome, dict]] = []

    def restore(self, outcome: RecordReportOutcome, params: dict) -> None:
        self.calls.append((outcome, params))


class _UnitOfWork:
    def __init__(self) -> None:
        self.calls = 0

    def atomic(self, command: RecordReportCommand):
        del command
        outer = self

        class _Atomic:
            def __enter__(self):
                outer.calls += 1

            def __exit__(self, exc_type, exc, traceback):
                return False

        return _Atomic()


class RecordReportSliceTests(unittest.TestCase):
    def test_domain_ports_and_use_case_do_not_depend_on_runtime_facades(self):
        modules = ("domain.py", "ports.py", "use_case.py")
        root = SCRIPTS / "cortex_runtime" / "record_report"
        for name in modules:
            tree = ast.parse((root / name).read_text(encoding="utf-8"), filename=name)
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            self.assertFalse(
                any(value == "cortex" or value.startswith("cortex.") or value == "cortex_runtime.reports" for value in imports),
                f"{name} crosses the RecordReport dependency boundary: {imports}",
            )

    def test_public_report_callable_is_the_facade_callable(self):
        self.assertIs(control.record_report, reports.record_report)
        self.assertEqual(reports.record_report.__module__, facade.__name__)

    def test_use_case_preserves_rejection_and_projection_semantics(self):
        unit_of_work = _UnitOfWork()
        rejected_mutation = _Mutation({"recorded": False, "reason": "delegation_attempt_required"})
        rejected_projections = _Projections()
        rejected = RecordReportUseCase(
            mutation=rejected_mutation, projections=rejected_projections, unit_of_work=unit_of_work,
        ).execute({"task_id": "task"})
        self.assertEqual(rejected["reason"], "delegation_attempt_required")
        self.assertEqual(len(rejected_projections.calls), 0)

        finding = {"fingerprint": "slice-finding", "severity": "P2", "status": "open"}
        recorded_mutation = _Mutation({
            "idempotent": True,
            "report": {"report_id": "report-0001", "closure": {"findings": [finding]}},
        })
        recorded_projections = _Projections()
        result = RecordReportUseCase(
            mutation=recorded_mutation, projections=recorded_projections, unit_of_work=unit_of_work,
        ).execute({"task_id": "task", "submission_id": "same"})
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["report"]["closure"]["findings"], [finding])
        self.assertEqual(recorded_mutation.commands[0].params["submission_id"], "same")
        self.assertEqual(recorded_projections.calls[0][1]["task_id"], "task")
        self.assertEqual(unit_of_work.calls, 2)

    def test_real_report_idempotency_still_queues_report_projections(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            old_project = os.environ.get("CORTEX_PROJECT_ROOT")
            os.environ["CORTEX_PROJECT_ROOT"] = str(project)
            try:
                scope = {"project_root": str(project)}
                control.activate_orchestration({**scope, "user_command": "/cortex", "principal": "owner", "thread_id": "owner"})
                classified = control.classify_task({**scope, "complexity": "C1", "requirements": [], "principal": "owner"})
                created = control.init_task({
                    **scope, "task_id": "slice-task", "objective": "slice test", "complexity": "C1",
                    "classification_id": classified["classification_id"], "requirements": [],
                    "principal": "owner", "thread_id": "owner",
                })
                observed = control.status({**scope, "task_id": "slice-task", "principal": "owner"})
                delegated = control.record_delegation({
                    **scope, "task_id": "slice-task", "principal": "owner", "expected_revision": created["state"]["revision"],
                    "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
                    "task_kind": "discover", "risk": "low", "objective": "inspect", "ownership": "Read-only discovery",
                    "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite inspected paths"],
                })
                control.confirm_host_spawn({
                    **scope, "task_id": "slice-task", "principal": "owner", "expected_revision": delegated["state"]["revision"],
                    "attempt_id": delegated["attempt_id"], "host_agent_id": "record-report-slice",
                    "host_task_name": delegated["spawn_request"]["task_name"], "host_model": delegated["spawn_request"]["model"],
                })
                payload = {
                    **scope, "task_id": "slice-task", "principal": "owner", "attempt_id": delegated["attempt_id"],
                    "submission_id": "stable", "report": {
                        "summary": "slice report", "findings": [], "questions": [], "changed_files": [],
                        "tests": [], "evidence": ["characterization"], "uncertainty": [], "next_action": "advance",
                    },
                }
                first = control.record_report(payload)
                second = control.record_report(payload)
                self.assertFalse(first["idempotent"])
                self.assertTrue(second["idempotent"])
                ledger = project / ".codex" / "cortex"
                jobs = list_projection_jobs(ledger, task_id="slice-task", limit=20)
                projection_types = {job["projection_type"] for job in jobs}
                self.assertTrue({"report_json", "report_receipt", "report_markdown"}.issubset(projection_types))
            finally:
                if old_project is None:
                    os.environ.pop("CORTEX_PROJECT_ROOT", None)
                else:
                    os.environ["CORTEX_PROJECT_ROOT"] = old_project


if __name__ == "__main__":
    unittest.main()
