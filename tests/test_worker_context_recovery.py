"""Focused v11 hook telemetry and capability-handoff contract coverage."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins/cortex/scripts"
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import cortex_hook
import cortex
from cortex_runtime.briefings import (
    WORKER_BOOTSTRAP_RECOVERY_CONTRACT,
    host_bootstrap_repair_message,
)

host_spawn_bootstrap = cortex.host_spawn_bootstrap


class WorkerContextRecoveryTests(unittest.TestCase):
    def test_hook_classifies_only_exact_native_v2_lifecycle_events(self):
        self.assertEqual(
            cortex_hook.telemetry_record({"hook_event_name": "PostToolUse", "tool_name": "spawn_agent"}),
            {"schema": "cortex/hook-telemetry/v11", "event": "native_spawn_agent"},
        )
        self.assertEqual(
            cortex_hook.telemetry_record({"hook_event_name": "PostToolUse", "tool_name": "wait_agent"}),
            {"schema": "cortex/hook-telemetry/v11", "event": "native_wait_agent"},
        )
        self.assertEqual(
            cortex_hook.telemetry_record({"hook_event_name": "SubagentStart", "agent_id": "private-child"}),
            {"schema": "cortex/hook-telemetry/v11", "event": "subagentstart"},
        )
        self.assertIsNone(cortex_hook.telemetry_record({"hook_event_name": "PostToolUse", "tool_name": "Agent"}))
        self.assertIsNone(cortex_hook.telemetry_record({"hook_event_name": "PreToolUse", "tool_name": "wait"}))

    def test_hook_never_reflects_or_reconstructs_identity(self):
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "spawn_agent",
            "agent_id": "private-child-id",
            "session_id": "private-session-id",
            "cwd": "/private/project",
            "tool_input": {"message": "assignment_ref=private-bearer"},
            "tool_response": {"result": {"task_ref": "task-private"}},
        }
        rendered = json.dumps(cortex_hook.hook_response(event), sort_keys=True)
        for private in ("private-child-id", "private-session-id", "/private/project", "private-bearer", "task-private"):
            self.assertNotIn(private, rendered)
        self.assertNotIn("additionalContext", cortex_hook.hook_response(event)["hookSpecificOutput"])
        self.assertNotIn("telemetry", cortex_hook.hook_response(event)["hookSpecificOutput"])

    def test_hook_response_matches_the_installed_codex_event_output_schemas(self):
        # Codex 0.149.0's generated command-output schemas use strict nested
        # objects. These are the fields this hook emits; PostToolUse also has
        # a Codex-only updatedMCPToolOutput field that Cortex does not use.
        # SubagentStop and Stop do not allow hookSpecificOutput.
        allowed_fields = {
            "SessionStart": {"hookEventName", "additionalContext"},
            "SubagentStart": {"hookEventName", "additionalContext"},
            "PostToolUse": {"hookEventName", "additionalContext"},
        }
        for event_name in cortex_hook.LIFECYCLE_EVENTS:
            event = {"hook_event_name": event_name}
            if event_name == "PostToolUse":
                event["tool_name"] = "wait"
            payload = cortex_hook.hook_response(event)
            if event_name not in allowed_fields:
                self.assertEqual(payload, {}, event_name)
                continue
            self.assertEqual(set(payload), {"hookSpecificOutput"}, event_name)
            specific = payload["hookSpecificOutput"]
            self.assertTrue(set(specific) <= allowed_fields[event_name], event_name)
            self.assertEqual(specific["hookEventName"], event_name)
            self.assertNotIn("telemetry", specific)

    def test_stdio_hook_emits_valid_json_and_event_specific_schema_for_all_hooks(self):
        command = [sys.executable, str(SCRIPTS / "cortex_hook.py")]
        expected_nested = {
            "SessionStart": {"hookEventName"},
            "SubagentStart": {"hookEventName"},
            "PostToolUse": {"hookEventName"},
            "SubagentStop": set(),
            "Stop": set(),
        }
        for event_name, expected_fields in expected_nested.items():
            event = {"hook_event_name": event_name}
            if event_name == "PostToolUse":
                event["tool_name"] = "wait"
            completed = subprocess.run(
                command,
                input=json.dumps(event),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(completed.stderr, "", event_name)
            payload = json.loads(completed.stdout)
            if not expected_fields:
                self.assertEqual(payload, {}, event_name)
                continue
            specific = payload["hookSpecificOutput"]
            self.assertEqual(set(specific), expected_fields, event_name)
            self.assertEqual(specific["hookEventName"], event_name)
            self.assertNotIn("telemetry", specific)

    def test_compaction_handoff_and_explicit_loss_are_private_and_fail_closed(self):
        handoff = cortex_hook.hook_response({"hook_event_name": "SessionStart", "source": "compact"})
        message = handoff["hookSpecificOutput"]["additionalContext"]
        self.assertIn("task_ref and coordinator_ref", message)
        self.assertIn("bounded private handoff", message)
        self.assertIn("fail closed", message)
        self.assertNotIn("inspect", message.lower())

        lost = cortex_hook.hook_response({
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cortex_capability_present": False,
        })
        message = lost["hookSpecificOutput"]["additionalContext"]
        self.assertIn("MISSING_FAIL_CLOSED", message)
        self.assertNotIn("recover", message.lower())
        self.assertNotIn("spawn", message.lower())

    def test_hook_module_has_no_runtime_or_environment_fallback(self):
        source = (SCRIPTS / "cortex_hook.py").read_text(encoding="utf-8")
        forbidden = (
            "import cortex",
            "cortex_runtime",
            "sqlite3",
            "os.environ",
            "bind_host_",
            "finalize_host_",
            "ledger_root",
            "briefing_path",
        )
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_hook_registration_is_limited_to_native_lifecycle_telemetry(self):
        hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "SubagentStart", "SubagentStop", "Stop", "PostToolUse"})
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "^(spawn_agent|wait|wait_agent)$")
        self.assertNotIn("PreToolUse", hooks)

    def test_hook_script_emits_only_sanitized_json(self):
        command = [sys.executable, str(SCRIPTS / "cortex_hook.py")]
        completed = subprocess.run(
            command,
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "tool_name": "wait",
                "agent_id": "private-child-id",
                "tool_response": {"error": "private diagnostic"},
            }),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["hookSpecificOutput"], {"hookEventName": "PostToolUse"})
        self.assertNotIn("telemetry", payload["hookSpecificOutput"])
        self.assertNotIn("private-child-id", completed.stdout)
        self.assertNotIn("private diagnostic", completed.stdout)

    def test_bundled_v11_cards_keep_capability_roles_separate(self):
        profiles = json.loads((PLUGIN / "profiles.json").read_text(encoding="utf-8"))
        cards = profiles["shared_worker_contract"]["operation_cards"]
        self.assertEqual(profiles["schema"], "cortex/profile-contract/v1")
        self.assertEqual(profiles["orchestration_protocol"], "cortex/orchestration/v11")
        self.assertIn("{task_ref, coordinator_ref}", cards["start_orchestration"]["purpose"])
        self.assertEqual(
            cards["read_worker_result"]["input"],
            "worker: {task_ref, assignment_ref, attempt_result_ref}; coordinator: {task_ref, coordinator_ref, step}",
        )
        self.assertIn("{task_ref, coordinator_ref, step, results}", cards["continue_orchestration"]["input"])
        self.assertNotIn("repair_planning", cards)
        self.assertIn("repair_capsule", profiles["shared_worker_contract"]["complete_attempt_v11"])

        briefing = (SCRIPTS / "cortex_runtime/briefings.py").read_text(encoding="utf-8")
        self.assertIn("canonical conditional v11 worker briefing", briefing)
        self.assertNotIn("task_ref + coordinator_ref + server-derived step", briefing)
        self.assertIn("server-derived step", json.dumps(profiles))
        self.assertNotIn("coordinator_principal", briefing)
        self.assertNotIn("coordinator_thread_id", briefing)

    def test_native_bootstrap_carries_only_the_worker_capability_pair(self):
        bootstrap = host_spawn_bootstrap(
            profile="explorer",
            dispatch_ref="dispatch-1",
            briefing_path=Path("/private/briefing.md"),
            briefing_digest="sha256:private",
            task_id="internal-task",
            attempt_id="internal-attempt",
            project_root=Path("/private/project"),
            task_ref="task-abc",
            assignment_ref="assignment-abc",
        )
        self.assertIn('read_dispatch_briefing({"task_ref":"task-abc","assignment_ref":"assignment-abc"})', bootstrap)
        self.assertNotIn("coordinator_ref", bootstrap)
        self.assertNotIn("internal-task", bootstrap)
        self.assertNotIn("internal-attempt", bootstrap)
        self.assertNotIn("/private/project", bootstrap)

    def test_missing_bootstrap_pair_has_zero_call_one_repair_same_child_contract(self):
        profiles = json.loads((PLUGIN / "profiles.json").read_text(encoding="utf-8"))
        contract = profiles["shared_worker_contract"]["worker_bootstrap_recovery"]
        self.assertEqual(contract, WORKER_BOOTSTRAP_RECOVERY_CONTRACT)
        self.assertEqual(contract["calls_before_complete_pair"], 0)
        self.assertEqual(contract["repair_primitive"], "followup_task")
        self.assertEqual(contract["repair_target"], "same_native_child")
        self.assertEqual(contract["max_repairs"], 1)
        self.assertFalse(contract["replacement_spawn"])
        self.assertFalse(contract["ambient_reconstruction"])
        self.assertEqual(
            contract["terminal_management"],
            "manage_orchestration(intent=finalize_bootstrap_failure, payload={dispatch_ref,reason_code:bootstrap_missing_identity})",
        )
        missing_final = contract["missing_final"].replace("[...]", "[task_ref]")
        self.assertNotIn("task-abc", missing_final)
        self.assertNotIn("assignment-abc", missing_final)

        bootstrap = host_spawn_bootstrap(
            profile="explorer", dispatch_ref="dispatch-1",
            briefing_path=Path("/private/briefing.md"), briefing_digest="sha256:private",
            task_id="internal-task", attempt_id="internal-attempt",
            project_root=Path("/private/project"), task_ref="task-abc",
            assignment_ref="assignment-abc",
        )
        self.assertLess(bootstrap.index("Before any Cortex call"), bootstrap.index("Otherwise first call"))
        for marker in (
            "zero Cortex/project calls", "fail closed", "never infer a ref",
            "read_dispatch_briefing", "complete=true", "obey the complete briefing",
        ):
            self.assertIn(marker, bootstrap)
        for duplicate_protocol in (
            "dispatch_ref", "repair_capsule", "attempt_result_ref", "Fallback path:",
            "/private/briefing.md", "complete_attempt",
        ):
            self.assertNotIn(duplicate_protocol, bootstrap)

        repair = host_bootstrap_repair_message(task_ref="task-abc", assignment_ref="assignment-abc")
        self.assertEqual(repair, host_bootstrap_repair_message(task_ref="task-abc", assignment_ref="assignment-abc"))
        self.assertIn('"task_ref":"task-abc"', repair)
        self.assertIn('"assignment_ref":"assignment-abc"', repair)
        self.assertIn("Use exact server call unchanged", repair)
        self.assertIn("no gate-passed acknowledgement", repair)
        self.assertIn('read_dispatch_briefing({"task_ref":"task-abc","assignment_ref":"assignment-abc"})', repair)
        self.assertIn("continue the original assignment through complete_attempt", repair)
        self.assertTrue(repair.endswith("final exactly ATTEMPT_COMPLETED."))

        for relative in ("skills/cortex-control/SKILL.md", "skills/orchestrator/SKILL.md"):
            text = (PLUGIN / relative).read_text(encoding="utf-8")
            normalized = " ".join(text.split())
            self.assertIn("CORTEX_WORKER_BOOTSTRAP_MISSING", normalized)
            self.assertIn("followup_task", normalized)
            self.assertIn("same native child", normalized)
            self.assertIn("zero Cortex/project calls", normalized)
        self.assertIn("finalize_bootstrap_failure", (PLUGIN / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8"))

    def test_model_facing_terminal_and_bootstrap_rules_have_relevant_surface_parity(self):
        profiles_text = (PLUGIN / "profiles.json").read_text(encoding="utf-8")
        briefing_source = (SCRIPTS / "cortex_runtime/briefings.py").read_text(encoding="utf-8")
        bootstrap = host_spawn_bootstrap(
            profile="explorer", dispatch_ref="dispatch-1",
            briefing_path=Path("/private/briefing.md"), briefing_digest="sha256:private",
            task_id="internal-task", attempt_id="internal-attempt",
            project_root=Path("/private/project"), task_ref="task-abc",
            assignment_ref="assignment-abc",
        )
        terminal_surfaces = {
            "briefing": briefing_source,
            "cortex_control": (PLUGIN / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8"),
            "orchestrator": (PLUGIN / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8"),
            "profiles": profiles_text,
            "harvest_overlay": (PLUGIN / "skills/knowledge-harvest/SKILL.md").read_text(encoding="utf-8"),
        }
        for surface, text in terminal_surfaces.items():
            normalized = " ".join(text.split())
            with self.subTest(surface=surface):
                self.assertRegex(normalized, r"repair(?:_| )capsule")
                for marker in ("terminal=true", "attempt_result_ref"):
                    self.assertIn(marker, normalized)
                self.assertRegex(normalized, r"(?:retryable=false|nonretryable)")
                self.assertRegex(normalized, r"(?i)opaque")

        bootstrap_surfaces = {
            "cortex_control": terminal_surfaces["cortex_control"],
            "orchestrator": terminal_surfaces["orchestrator"],
            "compaction_overlay": (PLUGIN / "skills/context-compaction/SKILL.md").read_text(encoding="utf-8"),
            "harvest_overlay": terminal_surfaces["harvest_overlay"],
        }
        for surface, text in bootstrap_surfaces.items():
            normalized = " ".join(text.split())
            with self.subTest(surface=surface):
                for marker in ("CORTEX_WORKER_BOOTSTRAP_MISSING", "followup_task"):
                    self.assertIn(marker, normalized)
                self.assertRegex(normalized, r"(?i)(zero Cortex/project calls|zero calls)")
                self.assertRegex(normalized, r"(?i)(same native child|same child|same-child|same_native_child)")
        self.assertIn("CORTEX_WORKER_BOOTSTRAP_MISSING", bootstrap)
        self.assertNotIn("followup_task", bootstrap)

        # The profile carries the same rule as closed machine-readable fields;
        # do not duplicate a prose prompt solely to satisfy text matching.
        recovery = json.loads(profiles_text)["shared_worker_contract"]["worker_bootstrap_recovery"]
        self.assertEqual(recovery["missing_final"], WORKER_BOOTSTRAP_RECOVERY_CONTRACT["missing_final"])
        self.assertEqual(recovery["calls_before_complete_pair"], 0)
        self.assertEqual(recovery["repair_primitive"], "followup_task")
        self.assertEqual(recovery["repair_target"], "same_native_child")
        self.assertEqual(recovery["max_repairs"], 1)
        self.assertFalse(recovery["replacement_spawn"])
        self.assertFalse(recovery["ambient_reconstruction"])

        # Lifecycle overlays that do not own worker bootstrap/terminal routing
        # remain concise instead of copying the protocol into every prompt.
        for relative in ("skills/adaptive-pipeline/SKILL.md", "skills/output-validation/SKILL.md"):
            text = (PLUGIN / relative).read_text(encoding="utf-8")
            self.assertNotIn("CORTEX_WORKER_BOOTSTRAP_MISSING", text, relative)

    def test_bundled_skills_preserve_pair_or_fail_closed(self):
        control = (PLUGIN / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        orchestrator = (PLUGIN / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        handoff = (PLUGIN / "skills/context-compaction/SKILL.md").read_text(encoding="utf-8")
        for text in (control, orchestrator, handoff):
            self.assertIn("task_ref", text)
            self.assertIn("coordinator_ref", text)
            self.assertIn("fail closed", text.lower())
        self.assertIn("spawn_agent", control)
        self.assertIn("wait_agent", control)
        self.assertNotIn("`create_thread`", control)
        self.assertNotIn("repair_planning", control)


if __name__ == "__main__":
    unittest.main()
