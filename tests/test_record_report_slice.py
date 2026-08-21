"""Characterization and boundary tests for the RecordReport vertical slice."""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


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


class RecordReportSliceTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.set_up_host_private_control_store()

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()

    def test_rejected_draft_preserves_structured_paths_and_fixes(self):
        result = reports._draft_record_failure({
            "outcome": "needs_correction",
            "code": "report_validation_failed",
            "next_action": "generic fallback that must not replace field fixes",
            "diagnostics": [{
                "code": "report_validation_failed",
                "path": "report.tests[1].evidence",
                "message": "result evidence is not concrete",
                "fix": "Replace report.tests[1].evidence with observed output.",
            }],
        }, draft_ref="draft-regression")

        self.assertTrue(result["draft_persisted"])
        self.assertEqual(result["draft_ref"], "draft-regression")
        self.assertEqual(result["diagnostics"][0]["path"], "report.tests[1].evidence")
        self.assertEqual(result["diagnostics"][0]["fix"], "Replace report.tests[1].evidence with observed output.")

    def test_generic_value_error_with_diagnostics_is_aggregated(self):
        class StructuredValueError(ValueError):
            diagnostics = [
                {"code": "report_field_missing", "path": "report.summary", "message": "summary missing", "fix": "Add summary."},
                {"code": "report_field_missing", "path": "report.evidence", "message": "evidence missing", "fix": "Add evidence."},
            ]

        diagnostics = reports._validation_diagnostics(StructuredValueError("report validation failed"))
        self.assertEqual([item["path"] for item in diagnostics or []], ["report.summary", "report.evidence"])
        self.assertEqual([item["fix"] for item in diagnostics or []], ["Add summary.", "Add evidence."])

    def test_gate_validation_returns_all_test_diagnostics_and_preserves_failure_evidence(self):
        report = {
            "summary": "review",
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": [
                {"command": "pytest", "cwd": ".", "exit_code": "0", "evidence": ""},
                {"command": "...", "cwd": "../outside", "exit_code": 1, "evidence": "observed failure"},
            ],
            "evidence": ["Observed failing check: pytest reported one assertion failure."],
            "uncertainty": [],
        }
        with mock.patch.object(control, "load_task_definition", return_value={"project_root": "."}), \
             mock.patch.object(control, "_validate_dispatch_briefing_review", return_value={}), \
             mock.patch.object(control, "_result_contract_markers", return_value=[]):
            with self.assertRaises(control.ReportValidationError) as raised:
                control._validate_gate_result_report(
                    Path("."), {}, {"gate": "review", "attempt_id": "attempt-1"}, report,
                )
        diagnostics = raised.exception.diagnostics
        paths = {item["path"] for item in diagnostics}
        self.assertIn("report.tests[0].exit_code", paths)
        self.assertIn("report.tests[0].evidence", paths)
        self.assertIn("report.tests[1].command", paths)
        self.assertIn("report.tests[1].cwd", paths)
        self.assertIn("report.tests", paths)
        self.assertEqual(report["evidence"], ["Observed failing check: pytest reported one assertion failure."])

    def test_gate_markers_use_whole_words_and_reject_duplicates(self):
        report = {
            "summary": "review",
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": [{"command": "pytest", "cwd": ".", "exit_code": 0, "evidence": "passed"}],
            "evidence": [
                "Gate acceptance 1: PASS - observed unknowns are documented in the findings",
            ],
            "uncertainty": [],
        }
        with mock.patch.object(control, "load_task_definition", return_value={"project_root": "."}), \
             mock.patch.object(control, "_validate_dispatch_briefing_review", return_value={}), \
             mock.patch.object(control, "_validate_result_artifacts", return_value={}), \
             mock.patch.object(control, "_result_contract_markers", return_value=[("Gate acceptance 1: PASS - ", "criterion")]):
            # ``unknowns`` is a concrete plural noun, not the unresolved marker
            # ``unknown`` and must not be rejected as a substring false positive.
            control._validate_gate_result_report(
                Path("."), {}, {"gate": "review", "attempt_id": "attempt-1"}, report,
            )
            report["evidence"].append("Gate acceptance 1: PASS - second observed proof")
            with self.assertRaisesRegex(ValueError, "expected exactly one PASS/BLOCKED marker"):
                control._validate_gate_result_report(
                    Path("."), {}, {"gate": "review", "attempt_id": "attempt-1"}, report,
                )

    def test_gate_validation_rejects_non_string_evidence_items(self):
        report = {
            "summary": "review",
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": [{"command": "pytest", "cwd": ".", "exit_code": 0, "evidence": "passed"}],
            "evidence": [123],
            "uncertainty": [],
        }
        with mock.patch.object(control, "load_task_definition", return_value={"project_root": "."}), \
             mock.patch.object(control, "_validate_dispatch_briefing_review", return_value={}), \
             mock.patch.object(control, "_validate_result_artifacts", return_value={}), \
             mock.patch.object(control, "_result_contract_markers", return_value=[]):
            with self.assertRaises(control.ReportValidationError) as raised:
                control._validate_gate_result_report(
                    Path("."), {}, {"gate": "review", "attempt_id": "attempt-1"}, report,
                )
        self.assertIn("report.evidence", {item["path"] for item in raised.exception.diagnostics})

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
                        "tests": [], "evidence": ["characterization"], "uncertainty": [],
                    },
                }
                first = control.record_report(payload)
                second = control.record_report(payload)
                self.assertFalse(first["idempotent"])
                self.assertTrue(second["idempotent"])
                ledger = control.ledger_root_path(scope)
                jobs = list_projection_jobs(ledger, task_id="slice-task", limit=20)
                projection_types = {job["projection_type"] for job in jobs}
                self.assertTrue({"report_json", "report_receipt", "report_markdown"}.issubset(projection_types))
            finally:
                if old_project is None:
                    os.environ.pop("CORTEX_PROJECT_ROOT", None)
                else:
                    os.environ["CORTEX_PROJECT_ROOT"] = old_project

    def test_slow_draft_read_does_not_hold_project_lock_for_an_independent_report(self):
        """A worker editor must not stall report persistence for another task.

        Draft bytes are intentionally read before the short authoritative
        state-lock transaction.  The final transaction still serializes each
        task's report index and immutable artifacts, preserving the existing
        same-task exactly-once boundary.
        """
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            old_project = os.environ.get("CORTEX_PROJECT_ROOT")
            os.environ["CORTEX_PROJECT_ROOT"] = str(project)
            try:
                scope = {"project_root": str(project)}

                def worker_draft(task_id: str) -> dict:
                    owner = f"owner-{task_id}"
                    control.activate_orchestration({
                        **scope, "user_command": "/cortex", "principal": owner, "thread_id": owner,
                    })
                    classified = control.classify_task({**scope, "complexity": "C1", "requirements": [], "principal": owner})
                    created = control.init_task({
                        **scope, "task_id": task_id, "objective": task_id, "complexity": "C1",
                        "classification_id": classified["classification_id"], "requirements": [],
                        "principal": owner, "thread_id": owner,
                    })
                    observed = control.status({**scope, "task_id": task_id, "principal": owner})
                    delegated = control.record_delegation({
                        **scope, "task_id": task_id, "principal": owner, "expected_revision": created["state"]["revision"],
                        "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
                        "task_kind": "discover", "risk": "low", "objective": "inspect", "ownership": "Read-only discovery",
                        "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite inspected paths"],
                    })
                    control.confirm_host_spawn({
                        **scope, "task_id": task_id, "principal": owner, "expected_revision": delegated["state"]["revision"],
                        "attempt_id": delegated["attempt_id"], "host_agent_id": f"{task_id}-worker",
                        "host_task_name": delegated["spawn_request"]["task_name"], "host_model": delegated["spawn_request"]["model"],
                    })
                    identity = {
                        **scope, "task_id": task_id, "attempt_id": delegated["attempt_id"], "profile": "explorer",
                    }
                    template = control.get_report_template(identity)
                    self.assertTrue(template["ok"], template)
                    draft_path = Path(template["draft_path"])
                    envelope = json.loads(draft_path.read_text(encoding="utf-8"))
                    envelope["report"].update({
                        "summary": f"{task_id} report",
                        "findings": [], "questions": [], "changed_files": [], "tests": [],
                        "evidence": [
                            envelope["report"]["evidence"][0],
                            "Gate acceptance 1: PASS - Report findings observed.",
                            "Gate verification 1: PASS - Cite inspected paths observed.",
                        ],
                        "uncertainty": [],
                    })
                    reports._write_report_draft_file(draft_path, envelope)
                    return {**identity, "draft_ref": template["draft_ref"]}

                first = worker_draft("slow-draft")
                second = worker_draft("independent-draft")
                slow_read_started = threading.Event()
                permit_slow_read = threading.Event()
                independent_done = threading.Event()
                results: dict[str, dict] = {}
                failures: list[tuple[str, BaseException]] = []
                original_read = reports._read_private_report_draft
                delayed_once = False
                delayed_lock = threading.Lock()

                def slow_read(path: Path) -> str:
                    nonlocal delayed_once
                    with delayed_lock:
                        delay = not delayed_once
                        delayed_once = True
                    if delay:
                        slow_read_started.set()
                        if not permit_slow_read.wait(timeout=5):
                            raise TimeoutError("test did not release slow draft reader")
                    return original_read(path)

                def publish(name: str, payload: dict, done: threading.Event | None = None) -> None:
                    try:
                        results[name] = control.publish_worker_report(payload)
                    except BaseException as exc:  # pragma: no cover - asserted below.
                        failures.append((name, exc))
                    finally:
                        if done is not None:
                            done.set()

                with mock.patch.object(reports, "_read_private_report_draft", side_effect=slow_read):
                    slow_thread = threading.Thread(target=publish, args=("slow", first))
                    slow_thread.start()
                    self.assertTrue(slow_read_started.wait(timeout=2), "slow draft read did not start")
                    independent_thread = threading.Thread(
                        target=publish, args=("independent", second, independent_done),
                    )
                    independent_thread.start()
                    self.assertTrue(
                        independent_done.wait(timeout=2),
                        "independent task report waited for another task's draft filesystem read",
                    )
                    permit_slow_read.set()
                    slow_thread.join(timeout=5)
                    independent_thread.join(timeout=5)

                self.assertFalse(failures)
                self.assertTrue(results["slow"]["ok"], results["slow"])
                self.assertTrue(results["independent"]["ok"], results["independent"])
                self.assertEqual(
                    [item["report_id"] for item in control.list_task_reports({**scope, "task_id": "slow-draft", "principal": "owner-slow-draft"})["reports"]],
                    ["report-0001"],
                )
                self.assertEqual(
                    [item["report_id"] for item in control.list_task_reports({**scope, "task_id": "independent-draft", "principal": "owner-independent-draft"})["reports"]],
                    ["report-0001"],
                )
            finally:
                if old_project is None:
                    os.environ.pop("CORTEX_PROJECT_ROOT", None)
                else:
                    os.environ["CORTEX_PROJECT_ROOT"] = old_project

    def test_manifest_cas_after_preliminary_recheck_keeps_draft_and_attempt_retryable(self):
        """A source change in the final commit window must not consume the attempt."""
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
                    **scope, "task_id": "cas-task", "objective": "cas test", "complexity": "C1",
                    "classification_id": classified["classification_id"], "requirements": [],
                    "principal": "owner", "thread_id": "owner",
                })
                observed = control.status({**scope, "task_id": "cas-task", "principal": "owner"})
                delegated = control.record_delegation({
                    **scope, "task_id": "cas-task", "principal": "owner", "expected_revision": created["state"]["revision"],
                    "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
                    "task_kind": "discover", "risk": "low", "objective": "inspect", "ownership": "Read-only discovery",
                    "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite inspected paths"],
                })
                control.confirm_host_spawn({
                    **scope, "task_id": "cas-task", "principal": "owner", "expected_revision": delegated["state"]["revision"],
                    "attempt_id": delegated["attempt_id"], "host_agent_id": "cas-worker",
                    "host_task_name": delegated["spawn_request"]["task_name"], "host_model": delegated["spawn_request"]["model"],
                })
                identity = {**scope, "task_id": "cas-task", "attempt_id": delegated["attempt_id"], "profile": "explorer"}
                template = control.get_report_template(identity)
                draft_path = Path(template["draft_path"])
                envelope = json.loads(draft_path.read_text(encoding="utf-8"))
                envelope["report"].update({
                    "summary": "cas report", "findings": [], "questions": [], "changed_files": [], "tests": [],
                    "evidence": [envelope["report"]["evidence"][0], "Gate acceptance 1: PASS - Report findings observed.", "Gate verification 1: PASS - Cite inspected paths observed."],
                    "uncertainty": [],
                })
                reports._write_report_draft_file(draft_path, envelope)
                payload = {**identity, "draft_ref": template["draft_ref"]}
                original = reports._revalidate_prepared_result_manifest
                calls = 0

                def fail_on_final(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise reports.StaleReportPreparationError("project manifest changed during report preparation")
                    return original(*args, **kwargs)

                with mock.patch.object(reports, "_revalidate_prepared_result_manifest", side_effect=fail_on_final):
                    stale = control.publish_worker_report(payload)
                self.assertEqual(stale["outcome"], "stale_preparation")
                self.assertFalse(stale["attempt_budget_consumed"])
                self.assertTrue(draft_path.exists(), "stale preparation must retain the worker draft")
                self.assertEqual(control.list_task_reports({**scope, "task_id": "cas-task", "principal": "owner"})["reports"], [])

                accepted = control.publish_worker_report(payload)
                self.assertTrue(accepted["ok"], accepted)
                self.assertEqual(accepted["report_ref"], "report-0001")
            finally:
                if old_project is None:
                    os.environ.pop("CORTEX_PROJECT_ROOT", None)
                else:
                    os.environ["CORTEX_PROJECT_ROOT"] = old_project


if __name__ == "__main__":
    unittest.main()
