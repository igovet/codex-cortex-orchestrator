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
            "next_action": "advance",
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

    def test_qa_product_gate_result_rework_reopens_implementation_and_blocks_review(self) -> None:
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
            "next_action": {
                "required": True,
                "target_gate": "implementation",
                "description": "Correct the product behavior before review.",
            },
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

    def test_recoverable_subagent_stop_keeps_attempt_running_and_resumable(self) -> None:
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
        self.assertEqual(stopped["outcome"], "native_worker_stopped_recoverable")
        after = control.load_task_state_for_artifact(task_dir)
        stopped_attempt = after["attempts"][0]
        self.assertEqual(stopped_attempt["status"], "running")
        self.assertTrue(stopped_attempt["host_resumable"])
        self.assertNotIn("finalized_at", stopped_attempt)
        with sqlite3.connect(self.project / ".codex" / "cortex" / "cortex.db") as connection:
            session = connection.execute(
                "SELECT status, resumable, host_agent_id FROM worker_sessions WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            ).fetchone()
        self.assertEqual(tuple(session), ("stopped_recoverable", 1, "native.stop:01"))

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
                "next_action": r"Keep C:\tmp.",
            },
        }
        markdown = control._report_markdown(record)
        self.assertIn(r"v1.2 - (safe) C:\tmp", markdown)
        self.assertIn("Keep C:\\tmp.", markdown)
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
