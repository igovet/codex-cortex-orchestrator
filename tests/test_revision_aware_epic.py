"""Acceptance coverage for the revision-aware orchestration contract."""
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
from cortex_runtime import ledger_db, orchestration_engine, mcp_api, questions


class RevisionAwareEpicAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.host_store = Path(self.temp.name) / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)
        self._previous_host_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)

    def tearDown(self) -> None:
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
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
        ledger = control.ledger_root_path({"project_root": str(self.project)})
        task_dir = next((ledger / "tasks").iterdir())
        return task_dir, control.load_task_state_for_artifact(task_dir)

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
        root = control.ledger_root({"project_root": str(self.project)})
        with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
            connection.row_factory = sqlite3.Row
            migrations = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual(
                [(row["version"], row["name"]) for row in migrations[-1:]],
                [
                    (15, "canonical-current-ledger"),
                ],
            )
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
            {"task_ref": started["task_ref"], "intent": "inspect"}
        )
        self.assertEqual(waiting["outcome"], "waiting_workers")
        self.assertEqual(waiting["output_policy"], "silent")
        self.assertEqual(
            waiting["allowed_visible_events"],
            [],
        )
        self.assertIsNone(waiting["user_view"])
        self.assertEqual(waiting["context_handoff"]["state"]["status"], state["status"])

    def test_canonical_requirement_array_preserves_revised_plan_traceability(self) -> None:
        requirement = "preserve this atomic requirement"
        planning = {
            "work_packages": [{"id": "core", "microtasks": [{"id": "core_change"}]}],
            "requirement_coverage": [{
                "requirement": requirement,
                "plan_refs": ["core"],
                "verification": ["Run the focused check."],
                "status": "covered",
            }],
        }
        control._validate_latest_intent_coverage(
            {
                "requirements": [requirement],
                "current_user_intent": requirement,
                "current_user_intent_revision": 2,
                "active_steers": [{"task_revision": 2}],
            },
            planning,
        )

    def test_russian_question_user_view_uses_localized_scaffolding(self) -> None:
        interaction = questions._chat_question_interaction(
            question_ref="question-0001",
            questions=[{
                "localized_question": "Какой вариант выбрать?",
                "canonical_question": "Which option should be selected?",
                "localized_header": "Выбор варианта",
                "options": [{
                    "option_id": "safe",
                    "label_localized": "Безопасный вариант",
                    "label_en": "Safe option",
                    "description_localized": "Сохраняет текущие ограничения.",
                }],
                "recommended_option_ids": ["safe"],
                "recommendation": "Choose the safe option.",
                "context": "The worker needs a decision.",
            }],
            user_language="ru",
            communication_profile="compact",
        )
        self.assertEqual(interaction["user_view"]["message"], "Какой вариант выбрать?")
        self.assertIn("безопасно продолжить", interaction["user_view"]["why_it_matters"])
        self.assertEqual(interaction["user_view"]["next_step"], "Ответьте на вопрос или укажите ограничения.")
        self.assertNotIn("The worker", str(interaction["user_view"]))

    def test_question_user_view_redacts_private_paths_and_refs(self) -> None:
        interaction = questions._chat_question_interaction(
            question_ref="question-private-task-ref",
            questions=[{
                "question": "Which behavior should task_ref=private-task-ref preserve?",
                "context": "See private.py and attempt_id=attempt-0001 before deciding.",
                "options": [{
                    "option_id": "safe",
                    "label_en": "Use private.py",
                    "description": "Preserve dispatch_ref=dispatch-0001.",
                }],
                "recommended_option_ids": ["safe"],
                "recommendation": "Choose the safe option.",
            }],
            user_language="en",
        )
        visible = interaction["user_view"]
        self.assertNotIn("private.py", str(visible))
        self.assertNotIn("task_ref", str(visible))
        self.assertNotIn("attempt_id", str(visible))
        self.assertNotIn("dispatch_ref", str(visible))
        self.assertEqual(visible["profile"], "natural")
        self.assertTrue(visible["quality"]["ok"])
        self.assertIn("private.py", str(interaction["internal"]))

    def test_unsafe_question_options_remain_distinguishable_in_public_view(self) -> None:
        interaction = questions._chat_question_interaction(
            question_ref="question-private-options",
            questions=[{
                "question": "Which behavior should be selected?",
                "options": [
                    {"option_id": "one", "label_en": "Use plugins/cortex/one.py", "description": "See task_ref=one."},
                    {"option_id": "two", "label_en": "Use plugins/cortex/two.py", "description": "See task_ref=two."},
                ],
                "recommended_option_ids": ["one"],
                "recommendation": "Choose the first bounded behavior.",
            }],
        )
        options = interaction["user_view"]["options"]
        self.assertEqual([item["number"] for item in options], [1, 2])
        self.assertEqual([item["label"] for item in options], ["Option 1", "Option 2"])
        self.assertNotEqual(options[0]["description"], options[1]["description"])
        self.assertNotIn("plugins/cortex", str(interaction["user_view"]))
        self.assertTrue(interaction["user_view"]["quality"]["ok"])

    def test_v3_boundary_segregates_internal_fields_and_localizes_plan_view(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": True,
                "state": "awaiting_plan_approval",
                "wave_id": "wave-1",
                "user_language": "ru",
                "result": {"plan_review": {
                    "summary": "Проверенный план",
                    "work_packages": [{"title": "Проверить реализацию"}, {"title": "Запустить проверки"}],
                    "risks": ["Нужна явная проверка результата"],
                }},
            },
            "private-task-ref",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="LOCK",
            include_result=True,
        )
        self.assertEqual(response["user_view"]["profile"], "natural")
        self.assertTrue(response["user_view"]["message"].startswith("План:"))
        self.assertEqual(response["user_view"]["risks"], ["Нужна явная проверка результата"])
        self.assertNotIn("private-task-ref", str(response["user_view"]))
        self.assertEqual(response["internal"]["task_ref"], "private-task-ref")

    def test_inspect_does_not_wait_when_only_terminal_results_remain(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": True,
                "operation": "inspect",
                "state": "waiting_workers",
                "wave_id": "wave-12",
                "result": {
                    "context_handoff": {
                        "active_workers": [],
                        "pending_dispatches": [],
                        "stopped_workers": [],
                        "completed_results": [{
                            "attempt_id": "attempt-12",
                            "dispatch_ref": "dispatch-12",
                            "attempt_result_ref": "attempt-result-12",
                            "lifecycle_status": "blocked",
                        }],
                    },
                },
            },
            "task-12",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="LOCK",
            include_result=True,
        )
        self.assertIn("read_worker_result", response["next_action"])
        self.assertIn("terminal_continuation", response["next_action"])
        self.assertNotIn("Wait only on these exact persisted native child ids", response["next_action"])

    def test_public_boundary_does_not_turn_technical_needs_input_into_a_question(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": True,
                "state": "needs_input",
                "wave_id": "wave-recovery",
                "next_action": "call manage_orchestration with intent=inspect for the same task",
                "result": {"outcome": "technical_recovery", "requires_user_decision": False},
            },
            "task-recovery",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="LOCK",
            include_result=True,
        )
        self.assertFalse(response["requires_user_decision"])
        self.assertFalse(response["user_view"]["requires_user_decision"])
        self.assertNotEqual(response["user_view"]["message_type"], "decision_required")
        self.assertEqual(response["user_view"]["message_type"], "Progress update")
        self.assertIn("manage_orchestration", response["next_action"])

    def test_public_boundary_allows_only_explicit_question_to_pause_chat(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": True,
                "state": "needs_input",
                "wave_id": "wave-question",
                "next_action": "surface the exact question and resume the same task",
                "result": {
                    "outcome": "awaiting_user",
                    "question": "Which permitted option should be used?",
                },
            },
            "task-question",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="LOCK",
            include_result=True,
        )
        self.assertTrue(response["requires_user_decision"])
        self.assertTrue(response["user_view"]["requires_user_decision"])
        self.assertEqual(response["user_view"]["message_type"], "decision_required")

    def test_public_error_keeps_technical_recovery_out_of_user_blocker(self) -> None:
        response = mcp_api.v3_response(
            {
                "ok": False,
                "state": "needs_input",
                "code": "attempt_invalidated",
                "next_action": {
                    "operation": "manage_orchestration",
                    "arguments": {"intent": "inspect"},
                },
                "result": {"outcome": "technical_recovery", "requires_user_decision": False},
            },
            "task-invalidated",
            native_arguments=lambda request: {},
            public_schema="cortex/test/v1",
            coordinator_lock="COORDINATOR LOCK: internal only",
            include_result=True,
        )
        self.assertFalse(response["requires_user_decision"])
        self.assertFalse(response["user_view"]["requires_user_decision"])
        self.assertNotEqual(response["user_view"]["message_type"], "decision_required")
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertNotIn("blocker", response["next_action"].lower())
        self.assertIn("manage_orchestration", response["next_action"])

    def test_active_steer_preserves_task_ref_and_resumes_same_native_worker(self) -> None:
        started = self.start()
        _, state, attempt = self.confirm_running(started, host_agent_id="native.steer:01")
        ledger = control.ledger_root_path({"project_root": str(self.project)})
        task_count_before = len(list((ledger / "tasks").iterdir()))
        steered = control.manage_orchestration(
            {
                "task_ref": started["task_ref"],
                "intent": "steer",
                "payload": {"user_message": "Add audit logging to the result path.", "user_language": "en"},
            }
        )
        self.assertTrue(steered["ok"], steered)
        self.assertEqual(steered["task_ref"], started["task_ref"])
        self.assertEqual(len(list((ledger / "tasks").iterdir())), task_count_before)
        self.assertEqual(len(steered["dispatches"]), 1)
        dispatch = steered["dispatches"][0]
        self.assertEqual(dispatch["call"], "followup_task")
        self.assertEqual(dispatch["host_agent_id"], "native.steer:01")
        self.assertEqual(dispatch["arguments"]["target"], "native.steer:01")
        self.assertEqual(dispatch["attempt_id"], attempt["attempt_id"])
        self.assertNotIn("spawn_agent", dispatch["call"])
        self.assertEqual(steered["task_revision"], 2)













    def test_native_worker_stop_reason_is_classified_as_worker(self) -> None:
        self.assertEqual(
            orchestration_engine._failure_class_from_completion(
                {"reason": "native_worker_stopped_without_result"}
            ),
            "worker",
        )

    def test_localized_question_projection_keeps_canonical_option_ids(self) -> None:
        canonical = control._question_options(
            [
                {"option_id": "use_existing_schema", "label_en": "Use the existing schema"},
                {"option_id": "replace_schema", "label_en": "Replace the schema"},
            ]
        )
        question, localized = control._localized_question_view(
            {
                "question": "Which schema should be used?",
                "options": canonical,
                "recommendation": "Use the existing schema to minimize migration risk.",
                "recommended_option_ids": ["use_existing_schema"],
            },
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
                {
                    "question": "Which schema?",
                    "options": canonical,
                    "recommendation": "Use the existing schema.",
                    "recommended_option_ids": ["use_existing_schema"],
                },
                {"localized_options": [{"option_id": "wrong", "label": "Неверно"}, {"label": "Второе"}]},
            )


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
        cortex_root = control.ledger_root({"project_root": str(self.project)})
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
        self.assertFalse((self.project / ".codex" / "cortex" / "cortex.db").exists())
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
        self.assertTrue(rejected["ok"])
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

    def test_installer_accepts_disabled_granular_mcp_elicitations_for_chat_flow(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "sync-cortex.sh"
        with tempfile.TemporaryDirectory() as home:
            codex_home = Path(home) / "codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "approval_policy = { granular = { mcp_elicitations = false } }\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["HOME"] = home
            environment["CODEX_HOME"] = str(codex_home)

            completed = subprocess.run(
                [str(script), "--dry-run"],
                cwd=script.parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertNotIn("mcp_elicitations", completed.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
