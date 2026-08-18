"""Acceptance coverage for the revision-aware/fail-back epic slices.

These tests intentionally use the public Cortex control-plane functions and
temporary project roots.  They are kept separate from the older compatibility
suite so that failures identify the newer contracts directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control
from cortex_runtime import ledger_db


class RevisionAwareEpicAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def start(self, *, waves=None, objective="revision-aware acceptance") -> dict:
        return control.start_orchestration(
            {
                "project_root": str(self.project),
                "task": {
                    "user_request": objective,
                    "complexity": "C1",
                    "acceptance_criteria": ["The observable behavior is completed."],
                    "verification": ["Run the focused acceptance check."],
                },
                "waves": waves or [{"workers": [{"phase": "discover"}]}],
            }
        )

    def task_dir_and_state(self, started: dict) -> tuple[Path, dict]:
        ledger = self.project / ".codex" / "cortex"
        task_dir = next((ledger / "tasks").iterdir())
        return task_dir, control.load_task_state_for_artifact(task_dir)

    @staticmethod
    def report_for(attempt: dict, project: Path, *, changed_files: list[str] | None = None) -> dict:
        evidence = [control.dispatch_briefing_review_marker(attempt["briefing_digest"])]
        for label in ("Gate acceptance", "Gate verification", "Task acceptance", "Task verification"):
            for index in range(1, 9):
                evidence.append(
                    f"{label} {index}: PASS - observed repository and executed check evidence confirms this criterion"
                )
        return {
            "summary": f"{attempt['gate']} acceptance report",
            "findings": [],
            "questions": [],
            "changed_files": changed_files or [],
            "tests": [
                {
                    "command": "python3 -m unittest focused_acceptance",
                    "cwd": str(project),
                    "exit_code": 0,
                    "evidence": "Focused acceptance verification completed successfully with zero failures.",
                }
            ],
            "evidence": evidence,
            "uncertainty": [],
        }

    def confirm_running(self, started: dict, *, host_agent_id: str = "native.acceptance:01") -> tuple[Path, dict, dict]:
        task_dir, state = self.task_dir_and_state(started)
        attempt = state["attempts"][0]
        confirmed = control.confirm_host_spawn(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "principal": state["principal"],
                "expected_revision": state["revision"],
                "attempt_id": attempt["attempt_id"],
                "host_tool": attempt["spawn_request"]["host_tool"],
                "host_agent_id": host_agent_id,
                "host_task_name": attempt["spawn_request"]["task_name"],
                "host_model": attempt["spawn_request"]["expected_model"],
                "host_reasoning_effort": attempt["spawn_request"]["reasoning_effort"],
            }
        )
        self.assertTrue(confirmed["confirmed"], confirmed)
        return task_dir, control.load_task_state_for_artifact(task_dir), attempt

    def test_schema_v8_migration_creates_revision_session_question_trace_and_observation_tables(self) -> None:
        root = self.project / ".codex" / "cortex"
        ledger_db.ensure_database(root)
        with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
            connection.row_factory = sqlite3.Row
            migrations = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual(migrations[-1]["version"], 8)
            self.assertEqual(migrations[-1]["name"], "revision-aware-orchestration")
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {
                "task_revisions",
                "plan_revisions",
                "worker_sessions",
                "attempt_messages",
                "question_batches",
                "question_items",
                "question_answers",
                "orchestration_trace",
                "tool_observations",
            }.issubset(tables)
        )

    def test_worker_report_rejects_next_action_and_gate_findings_cannot_target_workers(self) -> None:
        started = self.start()
        _task_dir, state = self.task_dir_and_state(started)
        attempt = state["attempts"][0]
        report = self.report_for(attempt, self.project)
        report["next_action"] = "route work to security"
        rejected = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": attempt["attempt_id"],
                "profile": attempt["profile"],
                "report": report,
            }
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_validation_failed")
        self.assertIn("report must contain exactly", rejected["diagnostics"][0]["message"])

        with self.assertRaisesRegex(ValueError, "closure finding contains unknown fields"):
            control.sanitize_gate_result_payload(
                {
                    "decision": "rework",
                    "failure_class": "product",
                    "findings": [{
                        "fingerprint": "forbidden-routing-field",
                        "severity": "P1",
                        "status": "open",
                        "blocking": True,
                        "summary": "The control plane must select the corrective wave.",
                        "next_action": {"required": True, "target_gate": "security"},
                    }],
                    "verification": {"executed": ["focused QA"], "not_executed": [], "required_missing": [], "limitations": []},
                    "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
                }
            )

    def test_waiting_workers_response_is_silent_and_lists_only_allowed_visible_events(self) -> None:
        started = self.start()
        _, state, _ = self.confirm_running(started)
        waiting = control.manage_orchestration(
            {"project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect"}
        )
        self.assertEqual(waiting["outcome"], "waiting_workers")
        self.assertEqual(waiting["output_policy"], "silent")
        self.assertEqual(
            waiting["allowed_visible_events"],
            ["user_message", "worker_question", "worker_completed", "worker_failed", "blocking_error"],
        )
        self.assertEqual(waiting["context_handoff"]["state"]["status"], state["status"])

    def test_active_steer_preserves_task_ref_and_resumes_same_native_worker(self) -> None:
        started = self.start()
        _, state, attempt = self.confirm_running(started, host_agent_id="native.steer:01")
        task_count_before = len(list((self.project / ".codex" / "cortex" / "tasks").iterdir()))
        steered = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "steer",
                "payload": {"user_message": "Add audit logging to the result path.", "user_language": "en"},
            }
        )
        self.assertTrue(steered["ok"], steered)
        self.assertEqual(steered["task_ref"], started["task_ref"])
        self.assertEqual(len(list((self.project / ".codex" / "cortex" / "tasks").iterdir())), task_count_before)
        self.assertEqual(len(steered["dispatches"]), 1)
        dispatch = steered["dispatches"][0]
        self.assertEqual(dispatch["call"], "followup_task")
        self.assertEqual(dispatch["host_agent_id"], "native.steer:01")
        self.assertEqual(dispatch["arguments"]["target"], "native.steer:01")
        self.assertEqual(dispatch["attempt_id"], attempt["attempt_id"])
        self.assertNotIn("spawn_agent", dispatch["call"])
        self.assertEqual(steered["task_revision"], 2)

    def test_material_steer_rejects_report_without_current_revision_marker(self) -> None:
        started = self.start()
        task_dir, state, attempt = self.confirm_running(started, host_agent_id="native.marker:01")
        steered = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "steer",
                "payload": {"user_message": "Reconcile the new audit requirement.", "user_language": "en"},
            }
        )
        self.assertEqual(steered["task_revision"], 2)
        current = control.load_task_state_for_artifact(task_dir)
        current_attempt = current["attempts"][0]
        report = self.report_for(current_attempt, self.project)
        with self.assertRaisesRegex(ValueError, "Task revision reviewed: 2"):
            control.publish_worker_report(
                {
                    "project_root": str(self.project),
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": attempt["profile"],
                    "report": report,
                }
            )

    def test_qa_product_gate_result_rework_keeps_its_context_and_blocks_downstream_gates(self) -> None:
        started = self.start(
            objective="QA product defect must fail back before review",
            waves=[
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "qa", "profile": "build_verification"}]},
                {"workers": [{"phase": "review"}]},
            ],
        )
        task_dir, state = self.task_dir_and_state(started)
        implementation = state["attempts"][0]
        (self.project / "implementation.txt").write_text("implementation fixture\n", encoding="utf-8")
        implementation_report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": implementation["attempt_id"],
                "profile": implementation["profile"],
                "report": self.report_for(implementation, self.project, changed_files=["implementation.txt"]),
            }
        )
        self.assertTrue(implementation_report["ok"], implementation_report)
        qa_dispatch = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": started["step"],
                "results": [{"report_ref": implementation_report["report_ref"]}],
            }
        )
        self.assertTrue(qa_dispatch["ok"], qa_dispatch)
        state = control.load_task_state_for_artifact(task_dir)
        qa = next(item for item in state["attempts"] if item["gate"] == "qa" and not item.get("invalidated"))
        finding = {
            "fingerprint": "qa-product-defect-001",
            "severity": "P1",
            "status": "open",
            "blocking": True,
            "summary": "The product behavior is incorrect.",
        }
        gate_result = {
            "decision": "rework",
            "failure_class": "product",
            "findings": [finding],
            "verification": {
                "executed": ["focused QA"],
                "not_executed": [],
                "required_missing": [],
                "limitations": [],
            },
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        qa_report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": qa["attempt_id"],
                "profile": qa["profile"],
                "report": self.report_for(qa, self.project),
                "gate_result": gate_result,
            }
        )
        self.assertTrue(qa_report["ok"], qa_report)
        reworked = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": qa_dispatch["step"],
                "results": [{"report_ref": qa_report["report_ref"]}],
            }
        )
        self.assertTrue(reworked["ok"], reworked)
        after = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(after["closure_rework"]["qa"]["target_gate"], "implementation")
        self.assertIn("implementation", after["current_pipeline"])
        self.assertIn("qa", after["current_pipeline"])
        self.assertTrue(next(item for item in after["attempts"] if item["attempt_id"] == implementation["attempt_id"])["invalidated"])
        self.assertFalse(
            any(item["gate"] == "review" and not item.get("invalidated") for item in after["attempts"])
        )
        self.assertEqual(reworked["outcome"], "ready_to_spawn")
        self.assertEqual(reworked["dispatches"][0]["phase"], "implementation")
        corrective = next(
            item for item in after["attempts"]
            if item["gate"] == "implementation" and not item.get("invalidated")
        )
        self.assertIn(qa_report["report_ref"], corrective["context_report_ids"])

        # A generic implementation success is not evidence that it fixed the
        # QA finding.  The controller must send the issue back through QA,
        # then keep QA active until that verification closes the P1.
        (self.project / "unrelated.txt").write_text("unrelated change\n", encoding="utf-8")
        unrelated = self.report_for(corrective, self.project, changed_files=["unrelated.txt"])
        unrelated["evidence"].append(
            control._predecessor_review_marker(corrective["context_report_ids"])
        )
        unrelated_report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": corrective["attempt_id"],
                "profile": corrective["profile"],
                "report": unrelated,
            }
        )
        self.assertTrue(unrelated_report["ok"], unrelated_report)
        retry = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": reworked["step"],
                "results": [{"report_ref": unrelated_report["report_ref"]}],
            }
        )
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in retry["dispatches"]], ["qa"])
        retried = control.load_task_state_for_artifact(task_dir)
        corrective_qa = next(
            item for item in retried["attempts"]
            if item["gate"] == "qa" and not item.get("invalidated")
        )
        self.assertIn(qa_report["report_ref"], corrective_qa["context_report_ids"])

        generic_qa_report = self.report_for(corrective_qa, self.project)
        generic_qa_report["evidence"].append(
            control._predecessor_review_marker(corrective_qa["context_report_ids"])
        )
        generic_qa = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": corrective_qa["attempt_id"],
                "profile": corrective_qa["profile"],
                "report": generic_qa_report,
            }
        )
        self.assertTrue(generic_qa["ok"], generic_qa)
        held = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": retry["step"],
                "results": [{"report_ref": generic_qa["report_ref"]}],
            }
        )
        self.assertTrue(held["ok"], held)
        self.assertEqual([item["phase"] for item in held["dispatches"]], ["qa"])
        held_state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(held_state["orchestrate_gate_failure_counts"]["qa"], 1)
        self.assertFalse(
            any(item["gate"] in {"review", "security"} and not item.get("invalidated") for item in held_state["attempts"])
        )

    def test_environment_blocker_halts_future_waves_without_a_security_or_review_dispatch(self) -> None:
        started = self.start(
            objective="environment blocker must halt downstream work",
            waves=[
                {"workers": [{"phase": "qa", "profile": "build_verification"}]},
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
            ],
        )
        task_dir, state = self.task_dir_and_state(started)
        qa = state["attempts"][0]
        blocked_result = {
            "decision": "blocked",
            "failure_class": "environment",
            "findings": [{
                "fingerprint": "device-access-unavailable",
                "severity": "P1",
                "status": "open",
                "blocking": True,
                "summary": "The required device check cannot run in this environment.",
            }],
            "verification": {
                "executed": ["focused static QA"],
                "not_executed": ["required device check"],
                "required_missing": ["required device check"],
                "limitations": ["device is unavailable"],
            },
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": qa["attempt_id"],
                "profile": qa["profile"],
                "report": self.report_for(qa, self.project),
                "gate_result": blocked_result,
            }
        )
        self.assertTrue(report["ok"], report)
        advanced = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": started["step"],
                "results": [{"report_ref": report["report_ref"]}],
            }
        )
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["outcome"], "blocked")
        self.assertEqual(advanced["dispatches"], [])
        blocked_state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(blocked_state["status"], "blocked")
        self.assertFalse(any(item["gate"] == "review" for item in blocked_state["attempts"]))

    def test_control_plane_routes_a_qa_rework_to_canonical_implementation(self) -> None:
        started = self.start(
            objective="control plane selects remediation from a QA finding",
            waves=[
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "qa", "profile": "build_verification"}]},
                {"workers": [{"phase": "security"}]},
                {"workers": [{"phase": "review"}]},
            ],
        )
        task_dir, state = self.task_dir_and_state(started)
        implementation = state["attempts"][0]
        (self.project / "initial.txt").write_text("initial implementation\n", encoding="utf-8")
        initial_report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": implementation["attempt_id"],
                "profile": implementation["profile"],
                "report": self.report_for(implementation, self.project, changed_files=["initial.txt"]),
            }
        )
        self.assertTrue(initial_report["ok"], initial_report)
        qa_dispatch = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": started["step"],
                "results": [{"report_ref": initial_report["report_ref"]}],
            }
        )
        self.assertTrue(qa_dispatch["ok"], qa_dispatch)
        current = control.load_task_state_for_artifact(task_dir)
        qa = next(item for item in current["attempts"] if item["gate"] == "qa" and not item.get("invalidated"))
        result = {
            "decision": "rework",
            "failure_class": "product",
            "findings": [{
                "fingerprint": "qa-defect-route-001",
                "severity": "P1",
                "status": "open",
                "blocking": True,
                "summary": "Product behavior requires an implementation correction.",
            }],
            "verification": {"executed": ["focused QA"], "not_executed": [], "required_missing": [], "limitations": []},
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        qa_report = control.publish_worker_report(
            {
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "attempt_id": qa["attempt_id"],
                "profile": qa["profile"],
                "report": self.report_for(qa, self.project),
                "gate_result": result,
            }
        )
        self.assertTrue(qa_report["ok"], qa_report)
        reworked = control.continue_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": qa_dispatch["step"],
                "results": [{"report_ref": qa_report["report_ref"]}],
            }
        )
        self.assertTrue(reworked["ok"], reworked)
        self.assertEqual([item["phase"] for item in reworked["dispatches"]], ["implementation"])
        after = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(after["closure_rework"]["qa"]["target_gate"], "implementation")
        self.assertFalse(any(item["gate"] == "security" and not item.get("invalidated") for item in after["attempts"]))

    def test_reportless_subagent_stop_is_terminal_and_non_resumable(self) -> None:
        started = self.start()
        task_dir, state = self.task_dir_and_state(started)
        attempt = state["attempts"][0]
        parent_session = state["thread_id"]
        bound_parent = control.bind_host_session_from_hook(
            str(self.project), started["task_ref"], parent_session
        )
        self.assertTrue(bound_parent["bound"], bound_parent)
        bound_worker = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default", "native.stop:01", attempt["expected_model"]
        )
        self.assertTrue(bound_worker["bound"], bound_worker)
        stopped = control.finalize_host_worker_stop_from_hook(
            str(self.project), state["task_id"], parent_session, "native.stop:01"
        )
        self.assertEqual(stopped["outcome"], "native_worker_stopped_without_report")
        after = control.load_task_state_for_artifact(task_dir)
        stopped_attempt = after["attempts"][0]
        self.assertEqual(stopped_attempt["status"], "failed")
        self.assertFalse(stopped_attempt["host_resumable"])
        self.assertEqual(stopped_attempt["finalization_reason"], "native_worker_stopped_without_report")
        with sqlite3.connect(self.project / ".codex" / "cortex" / "cortex.db") as connection:
            session = connection.execute(
                "SELECT status, resumable, host_agent_id FROM worker_sessions WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            ).fetchone()
        self.assertEqual(tuple(session), ("terminated_unavailable", 0, "native.stop:01"))
        inspected = control.manage_orchestration(
            {"project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect"}
        )
        self.assertIn("status='failed'", inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])

    def test_reportless_plan_stop_requires_failed_receipt_before_retry(self) -> None:
        started = self.start(
            waves=[{"workers": [{"phase": "plan"}]}],
            objective="recover a plan dispatch that stopped before reporting",
        )
        task_dir, state, attempt = self.confirm_running(started, host_agent_id="native.plan-stop:01")
        stopped = control.finalize_host_worker_stop_from_hook(
            str(self.project), state["task_id"], state["thread_id"], "native.plan-stop:01"
        )
        self.assertEqual(stopped["outcome"], "native_worker_stopped_without_report")

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        stopped_worker = inspected["context_handoff"]["stopped_workers"][0]
        self.assertEqual(stopped_worker["failure_status"], "failed")
        self.assertEqual(stopped_worker["failure_reason"], "native_worker_stopped_without_report")
        self.assertEqual(stopped_worker["dispatch_ref"], attempt["dispatch_ref"])
        self.assertFalse(stopped_worker["resumable"])
        self.assertIn("status='failed'", inspected["next_action"])
        self.assertIn(attempt["dispatch_ref"], inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])

        retried = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_report",
                "dispatch_ref": attempt["dispatch_ref"],
            }],
        })
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["outcome"], "ready_to_spawn")
        self.assertEqual(retried["dispatches"][0]["phase"], "plan")

    def test_mixed_wave_reportless_stop_keeps_failed_slot_addressable(self) -> None:
        started = self.start(
            waves=[{"workers": [{"phase": "discover"}, {"phase": "discover"}]}],
            objective="recover one stopped worker in a mixed wave",
        )
        task_dir, state = self.task_dir_and_state(started)
        parent_session = state["thread_id"]
        self.assertTrue(
            control.bind_host_session_from_hook(
                str(self.project), started["task_ref"], parent_session,
            )["bound"]
        )
        attempts = list(state["attempts"])
        for index, attempt in enumerate(attempts, 1):
            confirmed = control.confirm_host_spawn({
                "project_root": str(self.project),
                "task_id": state["task_id"],
                "principal": state["principal"],
                "expected_revision": state["revision"],
                "attempt_id": attempt["attempt_id"],
                "host_tool": attempt["spawn_request"]["host_tool"],
                "host_agent_id": f"native.mixed:{index:02d}",
                "host_task_name": attempt["spawn_request"]["task_name"],
                "host_model": attempt["spawn_request"]["expected_model"],
                "host_reasoning_effort": attempt["spawn_request"]["reasoning_effort"],
            })
            self.assertTrue(confirmed["confirmed"], confirmed)
        stopped = control.finalize_host_worker_stop_from_hook(
            str(self.project), state["task_id"], parent_session, "native.mixed:01",
        )
        self.assertEqual(stopped["outcome"], "native_worker_stopped_without_report")

        current = control.load_task_state_for_artifact(task_dir)
        live_attempt = control._attempt(current, attempts[1]["attempt_id"])
        report = self.report_for(live_attempt, self.project)
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": current["task_id"],
            "attempt_id": live_attempt["attempt_id"],
            "profile": live_attempt["profile"],
            "report": report,
        })
        self.assertTrue(published["ok"], published)

        advanced = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [
                {
                    "worker": 1,
                    "status": "failed",
                    "reason": "native_worker_stopped_without_report",
                    "dispatch_ref": attempts[0]["dispatch_ref"],
                },
                {"worker": 2, "report_ref": published["report_ref"]},
            ],
        })
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["outcome"], "ready_to_spawn")
        self.assertEqual(advanced["dispatches"][0]["phase"], "discover")

    def test_repeated_reportless_stops_use_three_failure_budget(self) -> None:
        current = self.start(objective="bounded reportless stop recovery")
        parent_session = None
        for failure_number in range(1, control.MAX_ORCHESTRATE_GATE_FAILURES + 1):
            task_dir, state = self.task_dir_and_state(current)
            attempt = next(
                item for item in state["attempts"]
                if item.get("gate") in control.active_gates(state)
                and item.get("status") not in control.TERMINAL_ATTEMPT_STATUSES
                and not item.get("invalidated")
            )
            parent_session = parent_session or state["thread_id"]
            if failure_number == 1:
                bound_parent = control.bind_host_session_from_hook(
                    str(self.project), current["task_ref"], parent_session,
                )
                self.assertTrue(bound_parent["bound"], bound_parent)
            host_agent_id = f"native.stop:{failure_number:02d}"
            bound = control.bind_host_worker_from_hook(
                str(self.project), state["task_id"], parent_session, "default",
                host_agent_id, attempt["expected_model"],
            )
            self.assertTrue(bound["bound"], bound)
            stopped = control.finalize_host_worker_stop_from_hook(
                str(self.project), state["task_id"], parent_session, host_agent_id,
            )
            self.assertEqual(stopped["outcome"], "native_worker_stopped_without_report")
            after_stop = control.load_task_state_for_artifact(task_dir)
            stopped_attempt = control._attempt(after_stop, attempt["attempt_id"])
            self.assertEqual(stopped_attempt["status"], "failed")
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "step": current["step"],
                "results": [{
                    "status": "failed",
                    "reason": "native_worker_stopped_without_report",
                    "dispatch_ref": attempt["dispatch_ref"],
                }],
            })
            self.assertTrue(current["ok"], current)
            if failure_number < control.MAX_ORCHESTRATE_GATE_FAILURES:
                self.assertEqual(current["outcome"], "ready_to_spawn")
                self.assertEqual(len(current["dispatches"]), 1)
        self.assertEqual(current["outcome"], "blocked")
        self.assertEqual(current["dispatches"], [])
        _, final_state = self.task_dir_and_state(current)
        self.assertEqual(
            final_state["orchestrate_gate_failure_counts"]["discover"],
            control.MAX_ORCHESTRATE_GATE_FAILURES,
        )
        self.assertIn("rework budget exhausted", final_state["blocked_reason"])

    def test_localized_question_projection_keeps_canonical_option_ids(self) -> None:
        canonical = control._question_options(
            [
                {"option_id": "use_existing_schema", "label_en": "Use the existing schema"},
                {"option_id": "replace_schema", "label_en": "Replace the schema"},
            ]
        )
        question, localized = control._localized_question_view(
            {"question": "Which schema should be used?", "options": canonical},
            {
                "localized_question": "Какую схему использовать?",
                "localized_options": [
                    {"option_id": "use_existing_schema", "label": "Использовать существующую схему"},
                    {"option_id": "replace_schema", "label": "Заменить схему"},
                ],
            },
        )
        self.assertEqual(question, "Какую схему использовать?")
        form = control._question_form_schema(localized)
        choices = form["properties"]["selection"]["oneOf"]
        self.assertEqual([item["const"] for item in choices], ["use_existing_schema", "replace_schema"])
        self.assertEqual([item["title"] for item in choices], ["Использовать существующую схему", "Заменить схему"])
        answer, _ = control._question_answer_from_content(
            {"selection": "Использовать существующую схему", "custom_response": ""}, localized
        )
        self.assertEqual(answer["option_ids"], ["use_existing_schema"])
        with self.assertRaisesRegex(ValueError, "localized option_id"):
            control._localized_question_view(
                {"question": "Which schema?", "options": canonical},
                {"localized_options": [{"option_id": "wrong", "label": "Неверно"}, {"label": "Второе"}]},
            )

    def test_context_aware_report_markdown_preserves_punctuation_and_real_backslashes(self) -> None:
        record = {
            "report_id": "report-1",
            "producer": {"profile": "general"},
            "report": {
                "summary": r"v1.2 - (safe) C:\tmp",
                "findings": [r"v1.2 - (safe) C:\tmp"],
                "questions": [],
                "changed_files": [],
                "tests": [],
                "evidence": [],
                "uncertainty": [],
            },
        }
        markdown = control._report_markdown(record)
        self.assertIn(r"v1.2 - (safe) C:\tmp", markdown)
        self.assertNotIn("## Next Action", markdown)
        self.assertNotIn(r"v1\.2", markdown)
        self.assertNotIn(r"\(safe\)", markdown)

    def test_prune_selection_exposes_stable_period_and_full_reset_option_ids(self) -> None:
        selected = control.manage_orchestration({"project_root": str(self.project), "intent": "prune"})
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["outcome"], "awaiting_prune_selection")
        self.assertEqual(
            [item["option_id"] for item in selected["options"]],
            ["keep_1d", "keep_7d", "keep_30d", "full_reset"],
        )
        confirmation = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "intent": "prune",
                "payload": {"period": "full_reset"},
            }
        )
        self.assertEqual(confirmation["outcome"], "awaiting_full_reset_confirmation")
        self.assertEqual(confirmation["confirmation_required"], "RESET CORTEX")

    def test_full_reset_removes_only_cortex_root_after_exact_confirmation(self) -> None:
        cortex_root = self.project / ".codex" / "cortex"
        cortex_root.mkdir(parents=True)
        (cortex_root / "sentinel").write_text("cortex state", encoding="utf-8")
        keep = self.project / "project-data.txt"
        keep.write_text("retain", encoding="utf-8")
        removed = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "intent": "prune",
                "payload": {"period": "full_reset", "full_confirmation": "RESET CORTEX"},
            }
        )
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(removed["outcome"], "full_reset")
        self.assertFalse(cortex_root.exists())
        self.assertTrue(keep.is_file())

    def test_full_reset_refuses_symlinked_cortex_root_without_touching_target(self) -> None:
        target = Path(self.temp.name) / "outside-cortex"
        target.mkdir()
        sentinel = target / "sentinel"
        sentinel.write_text("outside", encoding="utf-8")
        codex_root = self.project / ".codex"
        codex_root.mkdir()
        (codex_root / "cortex").symlink_to(target, target_is_directory=True)
        rejected = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "intent": "prune",
                "payload": {"period": "full_reset", "full_confirmation": "RESET CORTEX"},
            }
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("symlink", rejected["diagnostics"][0]["message"])
        self.assertTrue(sentinel.is_file())

    def test_installer_dry_run_sets_approval_to_approve_without_real_codex_install(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "sync-cortex.sh"
        with tempfile.TemporaryDirectory() as home:
            environment = os.environ.copy()
            environment["HOME"] = home
            environment["CODEX_HOME"] = str(Path(home) / "codex")
            completed = subprocess.run(
                [str(script), "--dry-run"],
                cwd=script.parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("would set Cortex MCP default_tools_approval_mode=approve", completed.stdout)
            self.assertIn("No plugin or Codex configuration was changed", completed.stdout)
            self.assertFalse((Path(home) / "codex" / "config.toml").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
