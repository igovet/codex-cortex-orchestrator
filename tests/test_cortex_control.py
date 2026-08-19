import io
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
import cortex_hook
from cortex_runtime import orchestration_engine
from cortex_runtime import mcp_api
from cortex_runtime import reports as runtime_reports


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"
        self._handlers = {}
        for handler, _ in control.TOOLS.values():
            name = handler.__name__
            if name in self._handlers:
                continue
            original = getattr(control, name)
            self._handlers[name] = original
            setattr(control, name, lambda params, original=original: original({**params, "project_root": str(self.project)}))

    def tearDown(self):
        for name, handler in self._handlers.items():
            setattr(control, name, handler)
        self.temp.cleanup()

    def activate(self, principal="thread-a"):
        return control.activate_orchestration({"user_command": "/cortex", "principal": principal, "thread_id": principal})

    def task_state(self, task_dir: Path) -> dict:
        return control.load_task_state_for_artifact(task_dir)

    def task_definition(self, task_dir: Path) -> dict:
        return control.load_task_definition(task_dir)

    def write_task_state(self, state: dict) -> None:
        control.db_update_task_state(self.ledger, state)

    def task_document(self, task_dir: Path, key: str) -> dict:
        state = self.task_state(task_dir)
        document = control.db_get_task_document(self.ledger, state["task_id"], key)
        self.assertIsNotNone(document)
        return document

    def reconcile_projections(self, *, worker_id="control-plane-test"):
        """Explicitly materialize optional exports before filesystem assertions."""
        from cortex_runtime.projection_service import reconcile
        return reconcile(self.ledger, worker_id=worker_id)

    @staticmethod
    def briefing_from_request(request):
        path = Path(request["briefing_path"])
        return path.read_text(encoding="utf-8")

    @classmethod
    def briefing_from_response(cls, response, index=0):
        return cls.briefing_from_request(response["dispatches"][index])

    def init(self, task_id="demo", complexity="C1"):
        self.activate()
        classified = control.classify_task({"complexity": complexity, "requirements": [], "principal": "thread-a"})
        return control.init_task({"task_id": task_id, "objective": "test objective", "complexity": complexity, "classification_id": classified["classification_id"], "principal": "thread-a"})

    def write_canonical_harvest_project_docs(self):
        project_docs = self.project / "docs/project"
        project_docs.mkdir(parents=True, exist_ok=True)
        (project_docs / "index.md").write_text(
            "# Project knowledge\n\n"
            "This index records verified repository behavior, operating boundaries, evidence, and durable project guidance.\n\n"
            "- [Conventions](conventions.md)\n"
            "- [Verification](verification.md)\n"
            "- [Decisions](decisions.md)\n"
            "- [Gotchas](gotchas.md)\n",
            encoding="utf-8",
        )
        canonical_content = {
            "conventions.md": "# Conventions\n\nThe repository follows verified naming, structure, configuration, and implementation conventions recorded from current source evidence.\n",
            "verification.md": "# Verification\n\nThe repository uses focused automated checks and source inspection to verify behavior, failure paths, and integration boundaries.\n",
            "decisions.md": "# Decisions\n\nNo explicit architectural decision record was found; this verified evidence boundary is retained for future investigation.\n",
            "gotchas.md": "# Gotchas\n\nNo confirmed project-specific gotcha was found; this verified absence must be reconsidered when repository behavior changes.\n",
        }
        for name, content in canonical_content.items():
            (project_docs / name).write_text(content, encoding="utf-8")
        return project_docs

    def delegate(self, state, task_id, gate, agent, **extra):
        observed = control.status({"task_id": task_id, "principal": state.get("principal", "thread-a")})
        default_model = (
            "gpt-5.6-luna" if agent == "explorer"
            else "gpt-5.6-sol" if agent == "security_auditor" or gate == "security"
            else "gpt-5.6-terra"
        )
        contract = {"task_kind": gate, "risk": "moderate", "requested_model": default_model, "requested_reasoning_effort": "medium", "ownership": f"Own {gate}", "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"], "verification": ["Report evidence"]}
        delegated = control.record_delegation({"task_id": task_id, "principal": state.get("principal", "thread-a"), "expected_revision": state["revision"], "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent, "objective": "test delegation", **contract, **extra})
        confirmed = control.confirm_host_spawn({
            "task_id": task_id,
            "principal": state.get("principal", "thread-a"),
            "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"],
            "host_tool": delegated["spawn_request"]["host_tool"],
            "host_agent_id": f"test-host-{delegated['attempt_id']}",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
            "host_reasoning_effort": delegated["spawn_request"]["reasoning_effort"],
        })
        return {**delegated, "state": confirmed["state"], "host_spawn": confirmed["host_spawn"]}

    def report(self, task_id, attempt_id, principal="thread-a", submission_id="final"):
        return control.record_report({"task_id": task_id, "principal": principal, "attempt_id": attempt_id, "submission_id": submission_id, "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": []}})

    def facade_start(self, task_id, waves, *, complexity="C1", submission_id=None, host_capabilities=None):
        return control.orchestrate({
            "operation": "start",
            "submission_id": submission_id or f"{task_id}-start",
            "principal": "thread-a",
            "thread_id": "thread-a",
            "task": {"task_id": task_id, "objective": f"facade task {task_id}", "complexity": complexity},
            "waves": waves,
            "host_capabilities": host_capabilities or {
                "spawn_agent_models": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
                "create_thread_models": ["gpt-5.6-luna"],
            },
        })

    def _report_for_attempt(self, task_dir, attempt, report=None):
        report = self._report_with_briefing(
            attempt,
            report or self.v3_report(f"{attempt['gate']} worker completed"),
        )
        package = self.task_document(task_dir, f"dispatch:{attempt['attempt_id']}")
        context_ids = package.get("context_report_ids") or []
        if context_ids and not any(str(item).startswith("Predecessor review:") for item in report["evidence"]):
            report["evidence"].append(control._predecessor_review_marker(context_ids))
        if attempt["gate"] == "implementation" and not report.get("changed_files"):
            relative = f"implementation-{attempt['attempt_id']}.txt"
            (self.project / relative).write_text("verified implementation fixture\n", encoding="utf-8")
            report["changed_files"] = [relative]
        return report, package

    @staticmethod
    def _report_with_briefing(attempt, report):
        report = dict(report)
        report["evidence"] = list(report.get("evidence", []))
        briefing_marker = control.dispatch_briefing_review_marker(attempt["briefing_digest"])
        if briefing_marker not in report["evidence"]:
            report["evidence"].append(briefing_marker)
        return report

    def _publish_attempt_report(self, task_dir, state, attempt, report=None):
        report, package = self._report_for_attempt(task_dir, attempt, report)
        payload = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        }
        if attempt["gate"] in {"review", "close"}:
            payload["closure"] = {
                "decision": "pass",
                "findings": [],
                "verification": {"executed": ["focused regression"], "not_executed": [], "required_missing": [], "limitations": []},
                "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
            }
        if attempt["gate"] == "plan":
            payload["planning"] = self.v3_planning()
        if attempt["gate"] == "scope":
            payload["scoping"] = self.v3_scoping()
        published = control.publish_worker_report(payload)
        self.assertTrue(published["ok"], published)
        return published["report_ref"]

    def _publish_closure_report(self, task_dir, state, attempt, closure, report=None, submission_id=None):
        """Publish a review/close report with an explicit closure sibling."""
        report = self._report_with_briefing(attempt, report or self.v3_report(f"{attempt['gate']} completed"))
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
            "closure": closure,
        })
        self.assertTrue(published["ok"], published)
        return published["report_ref"]

    def v3_results(self, response, reports=None):
        registry = control._operation_registry(self.ledger)
        task_ref = response.get("task_ref")
        task_id = next(
            task_id for task_id, record in registry["tasks"].items()
            if record.get("start", {}).get("task_ref") == task_ref
        )
        task_dir, state, _ = control._v3_task_state(self.ledger, task_id)
        attempts = [
            item for item in state["attempts"]
            if item.get("status") not in control.TERMINAL_ATTEMPT_STATUSES
            and item.get("gate") in control.active_gates(state)
        ]
        report_values = reports if isinstance(reports, list) else [reports] * len(attempts)
        results = []
        for slot, (attempt, report) in enumerate(zip(attempts, report_values), 1):
            result = {"report_ref": self._publish_attempt_report(task_dir, state, attempt, report)}
            if len(attempts) > 1:
                result["worker"] = slot
            results.append(result)
        return results

    def facade_completion(self, spawn_request, *, status="passed", report=None, **overrides):
        payload = {
            "attempt_id": spawn_request["attempt_id"],
            "host_tool": spawn_request["host_tool"],
            "host_agent_id": f"host-{spawn_request['attempt_id']}",
            "host_task_name": spawn_request["task_name"],
            "host_model": spawn_request.get("model") or spawn_request["expected_model"],
            "host_reasoning_effort": spawn_request["reasoning_effort"],
            "status": status,
        }
        if status == "passed":
            task_id = next(
                task_id for task_id in control.read_task_index(self.ledger)
                if any(
                    item.get("attempt_id") == spawn_request["attempt_id"]
                    and (item.get("spawn_request") or {}).get("task_name") == spawn_request.get("task_name")
                    for item in control._v3_task_state(self.ledger, task_id)[1].get("attempts", [])
                )
            )
            task_dir, state, _ = control._v3_task_state(self.ledger, task_id)
            attempt = control._attempt(state, spawn_request["attempt_id"])
            payload["report_ref"] = self._publish_attempt_report(task_dir, state, attempt, report)
        return {**payload, **overrides}

    @staticmethod
    def v3_report(summary="v3 worker completed"):
        evidence = ["v3 report evidence"]
        for label in ("Gate acceptance", "Gate verification", "Task acceptance", "Task verification"):
            for index in range(1, 9):
                evidence.append(
                    f"{label} {index}: PASS - observed repository and executed check evidence confirms this criterion"
                )
        return {
            "summary": summary,
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": [{
                "command": "python3 -m unittest focused_test",
                "cwd": ".",
                "exit_code": 0,
                "evidence": "Focused verification completed successfully with zero failures.",
            }],
            "evidence": evidence,
            "uncertainty": [],
        }

    @staticmethod
    def v3_planning():
        return {
            "overview": "Split the approved outcome into independently verifiable work packages.",
            "work_packages": [{
                "id": "core",
                "title": "Core delivery",
                "objective": "Implement the bounded core change.",
                "allowed_paths": ["src"],
                "microtasks": [{
                    "id": "core_change",
                    "title": "Implement the core change",
                    "objective": "Make the requested behavior work.",
                    "profile": "backend_dev",
                    "allowed_paths": ["src"],
                    "acceptance_criteria": ["Requested behavior is implemented."],
                    "verification": ["Run focused tests."],
                }],
            }],
        }

    @staticmethod
    def v3_scoping():
        return {
            "overview": "Map the repository evidence needed before solution design.",
            "context_files": [],
            "discovery_domains": [{
                "id": "runtime",
                "title": "Runtime contract",
                "objective": "Trace the current behavior and its executable boundaries.",
                "paths": ["."],
                "context": ["Current source, tests, schemas, and executable configuration are authoritative."],
                "depends_on": [],
                "acceptance_criteria": ["The discovery report identifies the current behavior and relevant ownership boundaries."],
                "verification": ["Confirm consequential claims in current source or tests."],
            }],
        }

    def v3_start(self, objective="v3 task", waves=None, **task_overrides):
        if (
            waves is not None
            and "plan_approval" not in task_overrides
            and not any(
                str(worker.get("phase") or "").strip().lower() in {"plan", "planning"}
                for wave in waves
                for worker in wave.get("workers", [])
            )
        ):
            task_overrides["plan_approval"] = "auto"
        task = {
            "user_request": objective,
            "acceptance_criteria": ["The requested observable outcome is completed end to end."],
            "verification": ["Run and record an authoritative check for the requested outcome."],
            **task_overrides,
        }
        arguments = {
            "project_root": str(self.project),
            "task": task,
        }
        if waves is not None:
            arguments["waves"] = waves
        return control.start_orchestration(arguments)

    def test_v3_start_binds_codex_session_for_documented_compact_hook(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "bind the host session for recovery",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
            self.assertTrue(started["ok"])
            task_dir = next((self.ledger / "tasks").iterdir())
            state = control.load_task_state_for_artifact(task_dir)
            self.assertEqual(state["thread_id"], state["principal"])
            bound = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "PostToolUse",
                    "session_id": "host-session-42",
                    "cwd": str(self.project),
                    "tool_name": "mcp__cortex__start_orchestration",
                    "tool_input": {"project_root": str(self.project)},
                    "tool_response": {"structuredContent": started},
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
            bound_payload = json.loads(bound.stdout)
            self.assertEqual(bound_payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            self.assertIn(
                "CORTEX DISPATCH REQUIRED NOW",
                bound_payload["hookSpecificOutput"]["additionalContext"],
            )
            bindings = control._host_session_bindings(self.ledger)
            self.assertEqual(bindings["tasks"][state["task_id"]], "host-session-42")
            self.assertEqual(cortex_hook.active_task(self.ledger, "host-session-42"), state["task_id"])
            self.assertFalse((self.ledger / "active-tasks.json").exists())
            subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "PostToolUse",
                    "session_id": "different-host-session",
                    "cwd": str(self.project),
                    "tool_name": "mcp__cortex__start_orchestration",
                    "tool_input": {"project_root": str(self.project)},
                    "tool_response": {"structuredContent": started},
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
            bindings = control._host_session_bindings(self.ledger)
            self.assertEqual(bindings["tasks"][state["task_id"]], "host-session-42")
            self.assertIsNone(cortex_hook.active_task(self.ledger, "different-host-session"))
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "SessionStart",
                    "session_id": "host-session-42",
                    "cwd": str(self.project),
                    "source": "clear",
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CONTEXT RECOVERY", context)
        self.assertIn(started["task_ref"], context)
        self.assertIn("manage_orchestration(intent='inspect'", context)

    def test_subagent_start_persists_native_child_for_compaction_recovery(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "persist a native worker across context compaction",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-parent-session"
        subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "mcp__cortex__start_orchestration",
                "tool_input": {"project_root": str(self.project)},
                "tool_response": {"structuredContent": started},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        bindings = control._host_session_bindings(self.ledger)
        self.assertEqual(bindings["tasks"][state["task_id"]], parent_session)
        self.assertEqual(cortex_hook.active_task(self.ledger, parent_session), state["task_id"])
        launched = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStart",
                "session_id": parent_session,
                "agent_id": "native.Child:01",
                "agent_type": "default",
                "model": attempt["expected_model"],
                "turn_id": "turn-native-child-01",
                "permission_mode": "default",
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        payload = json.loads(launched.stdout)
        self.assertIn("hookSpecificOutput", payload, launched.stderr)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertNotIn("HOST BINDING BLOCKER", payload["hookSpecificOutput"]["additionalContext"])
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["host_spawn"]["agent_id"], "native.Child:01")
        self.assertEqual(attempt["host_spawn"]["task_name"], attempt["spawn_request"]["task_name"])
        self.assertEqual(attempt["host_spawn"]["model"], attempt["expected_model"])

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["outcome"], "waiting_workers")
        self.assertEqual(inspected["dispatches"], [])
        self.assertEqual(inspected["result"]["pending_dispatches"], [])
        active = inspected["context_handoff"]["active_workers"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["host_agent_id"], "native.Child:01")
        self.assertEqual(active[0]["dispatch_ref"], attempt["dispatch_ref"])
        self.assertIn("native.Child:01", inspected["next_action"])
        self.assertIn("Do not restart, replay, or respawn", inspected["next_action"])

        wait_any = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "wait",
                "tool_input": {"action": "wait", "receiver_thread_ids": []},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        self.assertEqual(wait_any.stdout.strip(), "{}")

    def test_subagent_start_recovers_when_post_tool_session_hook_was_untrusted(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "recover one exact pending native worker",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertNotIn(state["task_id"], control._host_session_bindings(self.ledger)["tasks"])

        launched = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStart",
                "session_id": "host-recovered-session",
                "agent_id": "native.Recovered:01",
                "agent_type": "default",
                "model": attempt["expected_model"],
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        payload = json.loads(launched.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertNotIn("HOST BINDING BLOCKER", payload["hookSpecificOutput"]["additionalContext"])
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["attempts"][0]["status"], "running")
        self.assertEqual(state["attempts"][0]["host_spawn"]["agent_id"], "native.Recovered:01")
        bindings = control._host_session_bindings(self.ledger)
        self.assertEqual(bindings["tasks"][state["task_id"]], "host-recovered-session")

    def test_subagent_start_recovery_fails_closed_for_ambiguous_generic_workers(self):
        first = self.v3_start(
            "first pending worker for ambiguity",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        second = self.v3_start(
            "second pending worker for ambiguity",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        self.assertNotEqual(first["task_ref"], second["task_ref"])
        states = [
            control.load_task_state_for_artifact(path)
            for path in (self.ledger / "tasks").iterdir()
        ]
        models = {state["attempts"][0]["expected_model"] for state in states}
        self.assertEqual(len(models), 1)
        recovered = cortex_hook.pending_task_from_subagent_start(self.ledger, {
            "hook_event_name": "SubagentStart",
            "agent_type": "default",
            "model": next(iter(models)),
        })
        self.assertIsNone(recovered)

    def test_subagent_stop_without_report_is_terminal_and_bounded(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "stop a native worker without a report",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-stop-parent"
        control.bind_host_session_from_hook(str(self.project), started["task_ref"], parent_session)
        bound = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            "native.Stop:01", attempt["expected_model"],
        )
        self.assertTrue(bound["bound"], bound)
        stopped = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStop",
                "session_id": parent_session,
                "agent_id": "native.Stop:01",
                "agent_type": "default",
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        self.assertEqual(stopped.stdout.strip(), "{}", stopped.stderr)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(attempt["host_stop_outcome"], "native_worker_stopped_without_report")
        self.assertEqual(attempt["status"], "failed")
        self.assertFalse(attempt["host_resumable"])
        self.assertEqual(attempt["finalization_reason"], "native_worker_stopped_without_report")
        waited = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "wait",
                "tool_input": {"receiver_thread_ids": ["native.Stop:01"]},
                "tool_response": {"status": "completed"},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        wait_output = json.loads(waited.stdout)
        wait_context = wait_output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(wait_output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("stopped without a report and is terminal failed", wait_context)
        self.assertIn("status='failed'", wait_context)
        self.assertIn("native_worker_stopped_without_report", wait_context)
        self.assertNotIn("followup_task", wait_context)
        self.assertIn(started["task_ref"], wait_context)
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertEqual(inspected["context_handoff"]["stopped_workers"][0]["host_agent_id"], "native.Stop:01")
        self.assertFalse(inspected["context_handoff"]["stopped_workers"][0]["resumable"])
        self.assertIn("status='failed'", inspected["next_action"])
        self.assertIn(attempt["dispatch_ref"], inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])
        self.assertNotIn("Wait only on", inspected["next_action"])
        failed = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_report",
                "dispatch_ref": attempt["dispatch_ref"],
            }],
        })
        self.assertTrue(failed["ok"], failed)
        self.assertEqual(failed["outcome"], "ready_to_spawn")

    def test_legacy_reportless_stop_is_terminal_during_compaction_recovery(self):
        started = self.v3_start(
            "recover a legacy reportless stop",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        confirmed = control.confirm_host_spawn({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "expected_revision": state["revision"],
            "attempt_id": attempt["attempt_id"],
            "host_tool": attempt["spawn_request"]["host_tool"],
            "host_agent_id": "native.LegacyStop:01",
            "host_task_name": attempt["spawn_request"]["task_name"],
            "host_model": attempt["spawn_request"]["expected_model"],
            "host_reasoning_effort": attempt["spawn_request"]["reasoning_effort"],
        })
        self.assertTrue(confirmed["confirmed"], confirmed)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        # Simulate the pre-fix ledger record left by an interrupted
        # SubagentStop hook.  Recovery must fail it closed instead of
        # advertising followup_task to the dead native child.
        attempt["host_stopped_at"] = control.now()
        attempt["host_stop_outcome"] = "native_worker_stopped_recoverable"
        attempt["host_resumable"] = True
        self.write_task_state(state)

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        stopped = inspected["context_handoff"]["stopped_workers"][0]
        self.assertEqual(stopped["failure_status"], "failed")
        self.assertEqual(stopped["failure_reason"], "native_worker_stopped_without_report")
        self.assertFalse(stopped["resumable"])
        self.assertIn(attempt["dispatch_ref"], inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])

    def test_compaction_ignores_stale_question_ref_after_native_stop(self):
        started = self.v3_start(
            "discard stale question recovery target",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        confirmed = control.confirm_host_spawn({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "expected_revision": state["revision"],
            "attempt_id": attempt["attempt_id"],
            "host_tool": attempt["spawn_request"]["host_tool"],
            "host_agent_id": "native.StaleQuestion:01",
            "host_task_name": attempt["spawn_request"]["task_name"],
            "host_model": attempt["spawn_request"]["expected_model"],
            "host_reasoning_effort": attempt["spawn_request"]["reasoning_effort"],
        })
        self.assertTrue(confirmed["confirmed"], confirmed)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        attempt.update({
            "host_stopped_at": control.now(),
            "host_stop_outcome": "awaiting_user",
            "host_question_refs": ["question-answered"],
            "host_resumable": True,
        })
        self.write_task_state(state)

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        stopped = inspected["context_handoff"]["stopped_workers"][0]
        self.assertEqual(stopped["failure_status"], "failed")
        self.assertEqual(stopped["failure_reason"], "native_worker_stopped_without_report")
        self.assertEqual(stopped["question_refs"], [])
        self.assertFalse(stopped["resumable"])
        self.assertNotIn("question-answered", inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])

    def test_compaction_recovers_report_from_canonical_index_when_stop_metadata_is_incomplete(self):
        started = self.v3_start(
            "recover canonical report after interrupted stop hook",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        confirmed = control.confirm_host_spawn({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "expected_revision": state["revision"],
            "attempt_id": attempt["attempt_id"],
            "host_tool": attempt["spawn_request"]["host_tool"],
            "host_agent_id": "native.ReportIndex:01",
            "host_task_name": attempt["spawn_request"]["task_name"],
            "host_model": attempt["spawn_request"]["expected_model"],
            "host_reasoning_effort": attempt["spawn_request"]["reasoning_effort"],
        })
        self.assertTrue(confirmed["confirmed"], confirmed)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("canonical report survives stop")),
        })
        self.assertTrue(published["ok"], published)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        attempt.update({
            "host_stopped_at": control.now(),
            "host_stop_outcome": "native_worker_stopped_recoverable",
            "host_resumable": True,
        })
        attempt.pop("host_report_refs", None)
        self.write_task_state(state)

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        stopped = inspected["context_handoff"]["stopped_workers"][0]
        self.assertEqual(stopped["report_refs"], [published["report_ref"]])
        self.assertIsNone(stopped["failure_status"])
        self.assertFalse(stopped["resumable"])
        self.assertIn(published["report_ref"], inspected["next_action"])
        self.assertNotIn("native_worker_stopped_without_report", inspected["next_action"])

    def test_post_wait_stop_context_directs_terminal_failure(self):
        event = {"hook_event_name": "PostToolUse", "tool_name": "wait"}
        stopped = {"attempts": [{
            "attempt_id": "close-01",
            "status": "failed",
            "dispatch_ref": "dispatch-close-01",
            "host_stop_outcome": "native_worker_stopped_without_report",
            "host_spawn": {"agent_id": "native.Close:01", "task_name": "close_01_abcd1234"},
        }]}
        context = cortex_hook.stopped_worker_after_wait_context(event, stopped, "task-test")
        self.assertIn("status='failed'", context)
        self.assertIn("dispatch-close-01", context)
        self.assertIn("native_worker_stopped_without_report", context)
        self.assertNotIn("followup_task", context)
        self.assertIn("manage_orchestration(intent='inspect'", context)

        for status, outcome in (("passed", "report_recorded"), ("running", None)):
            state = {"attempts": [{
                "attempt_id": "close-02",
                "status": status,
                "host_stop_outcome": outcome,
            }]}
            self.assertIsNone(cortex_hook.stopped_worker_after_wait_context(event, state, "task-test"))

    def test_post_wait_stop_context_finds_earlier_reportless_attempt(self):
        event = {"hook_event_name": "PostToolUse", "tool_name": "wait"}
        state = {
            "current_gates": ["discover"],
            "attempts": [
                {
                    "attempt_id": "discover-01",
                    "gate": "discover",
                    "status": "failed",
                    "dispatch_ref": "dispatch-discover-01",
                    "host_stop_outcome": "native_worker_stopped_without_report",
                    "host_spawn": {
                        "agent_id": "native.Discover:01",
                        "task_name": "explorer_discover_01_abcd1234",
                    },
                },
                {
                    "attempt_id": "discover-02",
                    "gate": "discover",
                    "status": "passed",
                    "dispatch_ref": "dispatch-discover-02",
                    "host_stop_outcome": "report_recorded",
                    "host_spawn": {
                        "agent_id": "native.Discover:02",
                        "task_name": "explorer_discover_02_efgh5678",
                    },
                },
            ],
        }

        context = cortex_hook.stopped_worker_after_wait_context(event, state, "task-test")

        self.assertIn("discover-01", context)
        self.assertIn("dispatch-discover-01", context)
        self.assertNotIn("discover-02", context)

    def test_subagent_stop_after_report_recovers_report_instead_of_waiting(self):
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "record before native worker stop",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-report-stop-parent"
        control.bind_host_session_from_hook(str(self.project), started["task_ref"], parent_session)
        bound = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            "native.ReportStop:01", attempt["expected_model"],
        )
        self.assertTrue(bound["bound"], bound)
        recorded = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("report survived native stop")),
        })
        self.assertTrue(recorded["ok"], recorded)
        stopped = control.finalize_host_worker_stop_from_hook(
            str(self.project), state["task_id"], parent_session, "native.ReportStop:01",
        )
        self.assertEqual(stopped["outcome"], "report_recorded")
        self.assertEqual(stopped["report_refs"], [recorded["report_ref"]])
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertEqual(
            inspected["context_handoff"]["stopped_workers"][0]["report_refs"],
            [recorded["report_ref"]],
        )
        self.assertFalse(inspected["context_handoff"]["stopped_workers"][0]["resumable"])
        self.assertNotIn("followup_task", inspected["context_handoff"]["next_action"])
        self.assertIn(recorded["report_ref"], inspected["next_action"])
        self.assertIn("Never wait on or respawn", inspected["next_action"])

    def test_subagent_stop_with_open_question_remains_resumable_not_failed(self):
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "pause a native worker for a material question",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-question-stop-parent"
        control.bind_host_session_from_hook(str(self.project), started["task_ref"], parent_session)
        bound = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            "native.QuestionStop:01", attempt["expected_model"],
        )
        self.assertTrue(bound["bound"], bound)
        question = control.worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "action": "ask",
            "question": "Which externally visible behavior should be authoritative?",
        })
        self.assertTrue(question["ok"], question)
        stopped = control.finalize_host_worker_stop_from_hook(
            str(self.project), state["task_id"], parent_session, "native.QuestionStop:01",
        )
        self.assertEqual(stopped["outcome"], "awaiting_user")
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["attempts"][0]["status"], "running")
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertEqual(
            inspected["context_handoff"]["stopped_workers"][0]["question_refs"],
            [question["question_ref"]],
        )
        self.assertIn(question["question_ref"], inspected["next_action"])
        self.assertIn("manage_orchestration(intent=question)", inspected["next_action"])
        answered = control.answer_worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "question_id": question["question_ref"],
            "submission_id": "answer-question-stop",
            "answer": "Preserve the current public behavior.",
            "resume_context": {"source": "main_chat", "same_attempt": attempt["attempt_id"]},
        })
        self.assertEqual(answered["question"]["status"], "answered")
        resumed = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            "native.QuestionStop:01", attempt["expected_model"],
        )
        self.assertTrue(resumed["bound"], resumed)
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["stopped_workers"], [])
        self.assertEqual(
            inspected["context_handoff"]["active_workers"][0]["host_agent_id"],
            "native.QuestionStop:01",
        )

    def test_compaction_inspect_recovers_only_still_pending_immutable_dispatch(self):
        started = self.v3_start(
            "recover one unstarted immutable dispatch",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["outcome"], "ready_to_spawn")
        self.assertEqual(len(inspected["dispatches"]), 1)
        recovered = inspected["dispatches"][0]
        original = started["dispatches"][0]
        for key in ("dispatch_ref", "briefing_path", "briefing_digest"):
            self.assertEqual(recovered[key], original[key])
        pending = inspected["context_handoff"]["pending_dispatches"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["dispatch_ref"], original["dispatch_ref"])
        self.assertEqual(pending[0]["briefing_path"], original["briefing_path"])
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertIn("invoke_only_the_matching_top_level_inspect_dispatch", pending[0]["recovery_authority"])

    def test_v3_shared_host_session_hook_binding_fails_closed_until_unambiguous(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            starts = [
                self.v3_start(
                    f"shared host task {index}",
                    waves=[{"workers": [{"phase": "discover"}]}],
                )
                for index in range(1, 4)
            ]
            self.assertTrue(all(item["ok"] for item in starts))
            for started in starts:
                subprocess.run(
                    [sys.executable, str(hook)],
                    input=json.dumps({
                        "hook_event_name": "PostToolUse",
                        "session_id": "shared-host",
                        "cwd": str(self.project),
                        "tool_name": "mcp__cortex__start_orchestration",
                        "tool_input": {"project_root": str(self.project)},
                        "tool_response": {"structuredContent": started},
                    }),
                    text=True,
                    capture_output=True,
                    env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                    check=True,
                )
            self.assertFalse((self.ledger / "active-tasks.json").exists())
            self.assertIsNone(cortex_hook.active_task(self.ledger, "shared-host"))
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({"hook_event_name": "SessionStart", "session_id": "shared-host", "cwd": str(self.project), "source": "compact"}),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_ROOT": "", "CORTEX_PROJECT_ROOT": str(self.project)},
                check=True,
            )
            self.assertEqual(completed.stdout.strip(), "{}")

            index = control.read_task_index(self.ledger)
            bindings = control._host_session_bindings(self.ledger)
            task_ids = sorted(task_id for task_id, session in bindings["tasks"].items() if session == "shared-host")
            self.assertEqual(len(task_ids), 3)
            for task_id in task_ids[:2]:
                task_dir = self.ledger / "tasks" / index[task_id]["directory"]
                state = self.task_state(task_dir)
                state["status"] = "completed"
                self.write_task_state(state)
                control.remove_active_mapping(self.ledger, task_id, str(state.get("thread_id") or ""))
            self.assertEqual(cortex_hook.active_task(self.ledger, "shared-host"), task_ids[2])

    def test_orchestration_is_inactive_until_main_chat_command(self):
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "inactive", "objective": "nope", "complexity": "C1", "principal": "thread-a"})
        activated = self.activate()
        self.assertTrue(activated["active"])
        self.assertEqual(activated["activation"]["coordinator"], "main")
        self.assertEqual(activated["activation"]["identity_assurance"], "caller_asserted_principal_and_thread")
        self.assertEqual(activated["activation"]["dispatch_attestation"], "not_host_attested")
        self.assertTrue(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_requires_exact_command_and_rejects_agent_profile(self):
        with self.assertRaisesRegex(ValueError, "Cortex skill route"):
            control.activate_orchestration({"user_command": "please orchestrate", "principal": "thread-a", "thread_id": "thread-a"})
        with self.assertRaisesRegex(ValueError, "does not accept an agent profile"):
            control.activate_orchestration({"agent": "general", "user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
        self.assertFalse((self.ledger / "activations.json").exists())
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_activation_without_token_returns_recoverable_next_action(self):
        result = control.activate_orchestration({"principal": "thread-a", "thread_id": "thread-a"})
        self.assertFalse(result["active"])
        self.assertTrue(result["recoverable"])
        self.assertIn("cortex:orchestrator", result["next_action"])
        self.assertNotIn("/cortex", result["next_action"])

    def test_default_ledger_is_project_local(self):
        with self.assertRaisesRegex(ValueError, "project_root is required"):
            control.ledger_root()
        root = control.ledger_root({"project_root": str(self.project)})
        self.assertEqual(root, self.ledger)
        self.assertTrue((root / "tasks").is_dir())

    def test_project_root_rejects_system_and_home_roots_before_manifest_capture(self):
        task = {
            "user_request": "Do not scan the host filesystem",
            "complexity": "C1",
            "acceptance_criteria": ["The request is rejected before a manifest is captured."],
            "verification": ["Observe the public validation response."],
        }
        with mock.patch.object(control, "capture_project_manifest") as manifest:
            for unsafe_root in (Path("/"), Path.home().absolute(), Path("/tmp")):
                response = self._handlers["start_orchestration"]({
                    "project_root": str(unsafe_root),
                    "task": task,
                })
                self.assertFalse(response["ok"])
                self.assertIn("specific repository or worktree", response["diagnostics"][0]["message"])
        manifest.assert_not_called()

    def test_plugin_local_mcp_requires_and_honors_explicit_project_root(self):
        """The plugin's cwd must never become the durable task workspace."""
        previous_cwd = os.getcwd()
        try:
            os.chdir(control.PLUGIN_ROOT)
            with self.assertRaisesRegex(ValueError, "project_root is required"):
                self._handlers["activate_orchestration"]({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
            with tempfile.TemporaryDirectory() as project:
                root = Path(project)
                arguments = {"project_root": project}
                activated = self._handlers["activate_orchestration"]({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a", **arguments})
                self.assertTrue(activated["active"])
                self.assertEqual(activated["ledger_root"], str(root / ".codex/cortex"))
                classified = self._handlers["classify_task"]({"complexity": "C1", "requirements": [], "principal": "thread-a", **arguments})
                created = self._handlers["init_task"]({"task_id": "plugin-cwd", "objective": "workspace binding", "complexity": "C1", "classification_id": classified["classification_id"], "principal": "thread-a", **arguments})
                self.assertEqual(created["ledger_root"], str(root / ".codex/cortex"))
                self.assertTrue((root / ".codex/cortex/cortex.db").is_file())
                self.assertFalse((root / ".codex/cortex/tasks" / created["task_directory"]).exists())
                observed = self._handlers["status"]({"task_id": "plugin-cwd", "principal": "thread-a", **arguments})
                self.assertEqual(observed["task"]["project_root"], project)
                self.assertEqual(observed["ledger_root"], str(root / ".codex/cortex"))
        finally:
            os.chdir(previous_cwd)

    def test_tasks_receive_project_local_sequence_numbers(self):
        first = self.init(task_id="first-task")
        control.deactivate_orchestration({"user_command": "/normal", "principal": "thread-a"})
        self.activate()
        second = self.init(task_id="second-task")
        root = self.ledger
        self.assertEqual(first["task_number"], 1)
        self.assertEqual(second["task_number"], 2)
        self.assertEqual(control.db_task_artifact_path(root, "first-task"), root / "tasks" / "0001-first-task")
        self.assertEqual(control.db_task_artifact_path(root, "second-task"), root / "tasks" / "0002-second-task")
        self.assertFalse((root / "tasks" / "0001-first-task").exists())
        self.assertFalse((root / "tasks" / "0002-second-task").exists())
        self.assertEqual(control.status({"task_id": "first-task", "principal": "thread-a"})["task"]["task_number"], 1)

    def test_activation_persists_until_main_chat_returns_to_normal(self):
        self.activate()
        first_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        control.init_task({"task_id": "first-task", "objective": "one task", "complexity": "C1", "classification_id": first_classification["classification_id"], "principal": "thread-a"})
        second_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "second-task", "objective": "second", "complexity": "C1", "classification_id": second_classification["classification_id"], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "Cortex skill route"):
            control.deactivate_orchestration({"user_command": "normal", "principal": "thread-a"})
        control.deactivate_orchestration({"user_command": "/normal", "principal": "thread-a"})
        self.assertFalse(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_status_infers_the_only_bound_activation(self):
        self.activate()
        inferred = control.activation_status({})
        self.assertTrue(inferred["active"])
        self.assertTrue(inferred["identity_inferred"])
        self.assertEqual(inferred["activation"]["principal"], "thread-a")

    def test_resumed_root_coordinator_alias_does_not_lose_task_ownership(self):
        self.activate(principal="root")
        classified = control.classify_task({
            "complexity": "C1", "requirements": [], "principal": "root", "thread_id": "root",
        })
        control.init_task({
            "task_id": "root-alias", "objective": "resume root task",
            "classification_id": classified["classification_id"],
            "principal": "root", "thread_id": "root",
        })
        resumed = control.status({"task_id": "root-alias", "principal": "/root"})
        self.assertTrue(resumed["active"])
        self.assertEqual(resumed["state"]["principal"], "root")

    def test_init_consumes_classification_contract_without_duplicate_inputs(self):
        self.activate()
        requirements = ["implementation, verification, and documentation", "preserve the durable ledger"]
        classified = control.classify_task({"complexity": "C2", "requirements": requirements, "principal": "thread-a"})
        created = control.init_task({
            "task_id": "receipt-contract", "objective": "consume the immutable classification contract",
            "classification_id": classified["classification_id"], "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], requirements)

    def test_init_ignores_duplicate_inputs_and_consumes_authoritative_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C2", "requirements": ["original"], "principal": "thread-a"})
        created = control.init_task({
            "task_id": "mismatched-contract", "objective": "consume authoritative receipt",
            "classification_id": classified["classification_id"], "complexity": "C3", "requirements": ["replacement"],
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], ["original"])

    def test_init_ignores_truncated_c3_pipeline_and_uses_classification_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C3", "requirements": ["cross-system database security work"], "principal": "thread-a"})
        truncated = ["plan", "discover", "implementation", "qa", "review"]
        created = control.init_task({
            "task_id": "c3-truncated-pipeline", "objective": "consume authoritative C3 pipeline",
            "classification_id": classified["classification_id"], "pipeline": truncated,
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["current_pipeline"], classified["pipeline"])
        self.assertIn("documentation", created["state"]["current_pipeline"])
        self.assertIn("close", created["state"]["current_pipeline"])
        self.assertEqual(created["pipeline_correction"], {
            "requested": truncated, "used": classified["pipeline"], "source": "classification_receipt",
        })

    def test_init_resumes_existing_task_with_objective_correction_and_rebinds_activation(self):
        first = self.init(task_id="resume-existing", complexity="C2")
        control.activate_orchestration({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
        classified = control.classify_task({"complexity": "C3", "requirements": ["repeat audit"], "principal": "thread-a"})
        resumed = control.init_task({
            "task_id": "resume-existing", "objective": "different generated wording",
            "classification_id": classified["classification_id"], "principal": "thread-a", "thread_id": "thread-a",
        })
        self.assertFalse(resumed["created"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["state"]["revision"], first["state"]["revision"])
        self.assertEqual(resumed["objective_correction"]["used"], "test objective")
        self.assertTrue(control.status({"task_id": "resume-existing", "principal": "thread-a"})["active"])

    def test_incomplete_classification_receipt_is_never_repaired_from_caller_input(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["preserve compatibility"], "principal": "thread-a"})
        receipt = control.db_get_classification(self.ledger, classified["classification_id"])
        del receipt["requirements"]
        control.db_put_classification(self.ledger, receipt)
        with self.assertRaisesRegex(ValueError, "classification receipt requirements are invalid"):
            control.init_task({
                "task_id": "legacy-receipt", "objective": "require explicit legacy inputs",
                "classification_id": classified["classification_id"], "principal": "thread-a",
            })
        with self.assertRaisesRegex(ValueError, "classification receipt requirements are invalid"):
            control.init_task({
                "task_id": "legacy-receipt", "objective": "require explicit legacy inputs",
                "classification_id": classified["classification_id"], "requirements": ["preserve compatibility"],
                "principal": "thread-a",
            })

    def test_classifier_uses_boundaries(self):
        self.activate()
        self.assertNotIn("ux", control.classify_task({"complexity": "C2", "requirements": ["build verification"], "principal": "thread-a"})["pipeline"])
        self.assertIn("ux", control.classify_task({"complexity": "C2", "requirements": ["UI review"], "principal": "thread-a"})["pipeline"])

    def test_c3_pipeline_is_dynamic_and_does_not_assume_database_work(self):
        self.activate()
        plain = control.classify_task({
            "complexity": "C3",
            "requirements": ["Update the plugin manifest and CI packaging while preserving security invariants"],
            "principal": "thread-a",
        })
        self.assertNotIn("database_architecture", plain["pipeline"])
        self.assertNotIn("database_architecture", plain["conditional_gates"])
        self.assertIn("security", plain["pipeline"])

        database = control.classify_task({
            "complexity": "C3",
            "requirements": ["Perform a PostgreSQL schema migration and validate the transaction model"],
            "principal": "thread-a",
        })
        self.assertIn("database_architecture", database["pipeline"])
        self.assertEqual(database["conditional_gates"].count("database_architecture"), 1)
        self.assertIn("database_architecture", database["conditional_gate_reasons"])

    def test_generic_manifest_schema_does_not_trigger_database_gate(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C3",
            "requirements": ["Validate the JSON manifest schema and API contract"],
            "principal": "thread-a",
        })
        self.assertNotIn("database_architecture", classified["pipeline"])

    def test_explicit_orchestrator_pipeline_controls_all_optional_gates(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C3",
            "requirements": ["The orchestrator selected the gates from the task shape"],
            "pipeline": ["plan", "discover", "architecture", "implementation", "qa", "review"],
            "principal": "thread-a",
        })
        self.assertEqual(classified["pipeline_source"], "orchestrator")
        self.assertEqual(classified["pipeline"], [
            "plan", "discover", "architecture", "implementation", "qa", "review",
            "documentation", "close",
        ])
        self.assertEqual(classified["conditional_gates"], [])
        self.assertEqual(classified["pipeline_corrections"], [
            {"gate": "documentation", "reason": "mandatory C3 audit gate"},
            {"gate": "close", "reason": "mandatory C3 audit gate"},
        ])

    def test_classify_canonicalizes_human_pipeline_aliases(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C3",
            "requirements": ["The orchestrator selected the gates from the task shape"],
            "pipeline": ["discovery", "planning", "verification"],
            "parallel_groups": [["discovery"], ["planning", "verification"]],
            "principal": "thread-a",
        })
        self.assertEqual(classified["pipeline_source"], "orchestrator")
        self.assertEqual(classified["pipeline"], [
            "discover", "plan", "qa", "documentation", "close",
        ])
        self.assertEqual(classified["parallel_groups"], [
            ["discover"], ["plan", "qa"], ["documentation"], ["close"],
        ])
        self.assertEqual(classified["pipeline_corrections"][:3], [
            {"from": "discovery", "to": "discover", "reason": "canonical gate alias"},
            {"from": "planning", "to": "plan", "reason": "canonical gate alias"},
            {"from": "verification", "to": "qa", "reason": "canonical gate alias"},
        ])

    def test_classify_still_rejects_unknown_pipeline_gate_ids(self):
        self.activate()
        with self.assertRaisesRegex(ValueError, "pipeline contains unsupported gate ids: mystery"):
            control.classify_task({
                "complexity": "C2",
                "requirements": ["unknown gate should fail closed"],
                "pipeline": ["mystery"],
                "principal": "thread-a",
            })

    def test_reassessment_accepts_orchestrator_selected_full_replacement(self):
        state = self.init(task_id="explicit-reassessment", complexity="C2")["state"]
        revised = control.reassess_pipeline({
            "task_id": "explicit-reassessment",
            "principal": "thread-a",
            "expected_revision": state["revision"],
            "signals": ["discovery confirmed no database or security scope"],
            "pipeline": ["plan", "discover", "implementation", "review"],
            "intent": "resequence",
            "decision": "updated",
            "reason": "Remove unnecessary QA after discovery; keep the orchestrator-selected gates.",
            "apply": True,
        })
        self.assertTrue(revised["applied"])
        self.assertEqual(revised["pipeline_source"], "orchestrator")
        self.assertEqual(revised["state"]["current_pipeline"], [
            "plan", "discover", "implementation", "review", "documentation", "close",
        ])
        self.assertEqual(revised["state"]["parallel_groups"], [
            ["plan"], ["discover"], ["implementation"], ["review"],
            ["documentation"], ["close"],
        ])
        self.assertNotIn("database_architecture", revised["state"]["current_pipeline"])

    def test_security_sol_route_uses_complexity_effort_floor(self):
        for complexity, expected_effort in (("C1", "medium"), ("C2", "high"), ("C3", "xhigh")):
            with self.subTest(complexity=complexity):
                route = control.resolve_dispatch_route({
                    "agent": "security_auditor", "task_kind": "security", "risk": "low",
                    "complexity": complexity, "requested_model": "gpt-5.6-sol",
                    "requested_reasoning_effort": "low",
                })
                self.assertEqual(route["policy_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_reasoning_effort"], expected_effort)
                self.assertEqual(route["model_choice_reason"], "security_policy")

        with self.assertRaisesRegex(ValueError, "security work always uses"):
            control.resolve_dispatch_route({
                "agent": "security_auditor", "task_kind": "security", "risk": "low",
                "complexity": "C1", "requested_model": "gpt-5.6-terra",
            })
        with self.assertRaisesRegex(ValueError, "security work always uses"):
            control.resolve_dispatch_route({
                "agent": "security_auditor", "task_kind": "security", "risk": "low",
                "complexity": "C1", "user_requested_model": "gpt-5.6-terra",
            })

    def test_security_profile_normalizes_contradictory_lightweight_kind_to_sol(self):
        route = control.resolve_dispatch_route({"agent": "security_auditor", "task_kind": "reading", "risk": "low", "complexity": "C1"})
        self.assertEqual(route["task_kind"], "security")
        self.assertEqual(route["policy_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")

    def test_security_gate_normalizes_contradictory_lightweight_kind_to_sol_in_delegation(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["security"], "principal": "thread-a"})
        state = control.init_task({"task_id": "security-route", "objective": "security routing", "complexity": "C1", "pipeline": ["security", "close"], "classification_id": classified["classification_id"], "principal": "thread-a"})["state"]
        delegation = self.delegate(state, "security-route", "security", "security_auditor", task_kind="reading", risk="low", requested_reasoning_effort="high")
        self.assertEqual(delegation["spawn_request"]["model"], "gpt-5.6-sol")
        self.assertEqual(delegation["state"]["attempts"][-1]["task_kind"], "security")

    def test_each_lightweight_dispatch_routes_independently_of_task_complexity(self):
        for complexity, risk, expected_model, expected_effort in (
            ("C1", "low", "gpt-5.6-luna", "medium"),
            ("C2", "low", "gpt-5.6-luna", "medium"),
            ("C3", "moderate", "gpt-5.6-luna", "medium"),
            ("C1", "high", "gpt-5.6-luna", "high"),
            ("C2", "critical", "gpt-5.6-luna", "xhigh"),
        ):
            with self.subTest(complexity=complexity, risk=risk):
                route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": "reading", "risk": risk, "complexity": complexity})
                self.assertEqual(route["policy_model"], expected_model)
                self.assertEqual(route["selected_model"], expected_model)
                self.assertEqual(route["selected_reasoning_effort"], expected_effort)

    def test_explorer_keeps_luna_and_coordinator_selected_effort(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "runtime_investigation",
            "risk": "moderate",
            "complexity": "C3",
            "requested_reasoning_effort": "medium",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_reasoning_effort"], "medium")
        self.assertEqual(route["model_choice_reason"], "explorer_policy")
        self.assertTrue(route["read_only"])
        with self.assertRaisesRegex(ValueError, "explorer always uses"):
            control.resolve_dispatch_route({
                "agent": "explorer", "task_kind": "runtime_investigation", "risk": "moderate",
                "complexity": "C3", "requested_model": "gpt-5.6-terra",
            })

    def test_configured_default_luna_omits_native_model_and_separates_expectation(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "reading",
            "risk": "low",
            "complexity": "C1",
            "configured_default_model": "gpt-5.6-luna",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "requested_reasoning_effort": "high",
        })
        self.assertEqual(route["expected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["model_resolution"], "configured_default")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_configured_default_delegation_omits_model_key_but_confirms_actual_luna(self):
        state = self.init(task_id="configured-default-luna") ["state"]
        observed = control.status({"task_id": "configured-default-luna", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "configured-default-luna", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
            "configured_default_model": "gpt-5.6-luna",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "objective": "configured Luna", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Report findings"],
            "verification": ["Cite paths"],
        })
        request = delegated["spawn_request"]
        self.assertEqual(request["host_tool"], "spawn_agent")
        self.assertNotIn("model", request)
        self.assertEqual(request["expected_model"], "gpt-5.6-luna")
        self.assertEqual(request["model_resolution"], "configured_default")
        self.assertEqual(request["reasoning_effort"], "medium")
        self.assertEqual(request["fork_turns"], "none")
        confirmed = control.confirm_host_spawn({
            "task_id": "configured-default-luna", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_tool": "spawn_agent", "host_agent_id": "luna-child",
            "host_task_name": request["task_name"], "host_model": "gpt-5.6-luna",
            "host_reasoning_effort": request["reasoning_effort"],
        })
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["host_spawn"]["model_verification"], "verified")

    def test_configured_default_keeps_explicit_terra_override(self):
        route = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "implementation", "risk": "moderate", "complexity": "C2",
            "configured_default_model": "gpt-5.6-luna", "requested_model": "gpt-5.6-terra",
            "requested_reasoning_effort": "high",
        })
        self.assertEqual(route["selected_model"], "gpt-5.6-terra")
        self.assertEqual(route["model_resolution"], "explicit_override")

    def test_orchestrate_prefers_confirmed_luna_default_over_explicit_catalog(self):
        for suffix, spawn_models in (
            ("luna-catalog", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]),
            ("terra-catalog", ["gpt-5.6-sol", "gpt-5.6-terra"]),
        ):
            with self.subTest(spawn_models=spawn_models):
                started = self.facade_start(
                    f"configured-default-{suffix}",
                    [{"wave_id": "discover", "delegations": [{
                        "gate": "discover", "agent": "explorer",
                        "requested_reasoning_effort": "high",
                    }]}],
                    host_capabilities={
                        "spawn_agent_models": spawn_models,
                        "create_thread_models": ["gpt-5.6-luna"],
                        "spawn_agent_default_model": "gpt-5.6-luna",
                    },
                )
                request = started["spawn_requests"][0]
                self.assertEqual(request["host_tool"], "spawn_agent")
                self.assertNotIn("model", request)
                self.assertEqual(request["expected_model"], "gpt-5.6-luna")
                self.assertEqual(request["model_resolution"], "configured_default")
                self.assertEqual(request["reasoning_effort"], "high")

    def test_configured_default_preserves_independent_effort(self):
        for effort in ("medium", "high", "xhigh"):
            with self.subTest(effort=effort):
                started = self.facade_start(
                    f"configured-effort-{effort}",
                    [{"wave_id": "discover", "delegations": [{
                        "gate": "discover", "agent": "explorer",
                        "requested_reasoning_effort": effort,
                    }]}],
                    host_capabilities={
                        "spawn_agent_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
                        "spawn_agent_default_model": "gpt-5.6-luna",
                    },
                )
                request = started["spawn_requests"][0]
                self.assertNotIn("model", request)
                self.assertEqual(request["reasoning_effort"], effort)

    def test_orchestrate_without_default_uses_explicit_luna_then_hidden_terra(self):
        explicit = self.facade_start(
            "explicit-luna-no-default",
            [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}],
            host_capabilities={"spawn_agent_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]},
        )["spawn_requests"][0]
        self.assertEqual(explicit["host_tool"], "spawn_agent")
        self.assertEqual(explicit["model"], "gpt-5.6-luna")
        self.assertEqual(explicit["expected_model"], "gpt-5.6-luna")
        self.assertEqual(explicit["model_resolution"], "explicit_override")

        fallback = self.facade_start(
            "terra-fallback-no-default",
            [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}],
            host_capabilities={"spawn_agent_models": ["gpt-5.6-sol", "gpt-5.6-terra"]},
        )["spawn_requests"][0]
        self.assertEqual(fallback["host_tool"], "spawn_agent")
        self.assertEqual(fallback["model"], "gpt-5.6-terra")
        self.assertEqual(fallback["expected_model"], "gpt-5.6-terra")
        self.assertEqual(fallback["model_resolution"], "explicit_override")

    def test_explicit_luna_and_terra_overrides_keep_adaptive_effort_floor(self):
        for agent in ("planner", "general", "code_reviewer"):
            for requested_model, requested_effort, expected_effort in (
                ("gpt-5.6-luna", "low", "xhigh"),
                ("gpt-5.6-luna", "high", "xhigh"),
                ("gpt-5.6-luna", "max", "max"),
                ("gpt-5.6-terra", "low", "high"),
                ("gpt-5.6-terra", "high", "high"),
                ("gpt-5.6-terra", "max", "max"),
            ):
                with self.subTest(agent=agent, model=requested_model, effort=requested_effort):
                    route = control.resolve_dispatch_route({
                        "agent": agent, "task_kind": "implementation", "risk": "moderate",
                        "complexity": "C2", "requested_model": requested_model,
                        "requested_reasoning_effort": requested_effort,
                    })
                    self.assertEqual(route["selected_model"], requested_model)
                    self.assertEqual(route["selected_reasoning_effort"], expected_effort)

    def test_effort_above_max_is_rejected_for_every_model_route(self):
        cases = (
            {"agent": "explorer", "task_kind": "discovery", "requested_model": "gpt-5.6-luna"},
            {"agent": "planner", "task_kind": "planning", "requested_model": "gpt-5.6-luna"},
            {"agent": "general", "task_kind": "implementation", "requested_model": "gpt-5.6-terra"},
            {
                "agent": "general", "task_kind": "implementation",
                "requested_model": "gpt-5.6-sol", "user_requested_model": "gpt-5.6-sol",
            },
            {"agent": "security_auditor", "task_kind": "security", "requested_model": "gpt-5.6-sol"},
        )
        for case in cases:
            with self.subTest(agent=case["agent"], model=case["requested_model"]):
                with self.assertRaisesRegex(ValueError, "supported effort"):
                    control.resolve_dispatch_route({
                        **case, "risk": "high", "complexity": "C3",
                        "requested_reasoning_effort": "ultra",
                    })

    def test_explorer_model_is_profile_bound_not_task_kind_bound(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "implementation",
            "risk": "high",
            "complexity": "C3",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_terra_task_kinds_route_uncertain_work_independently_of_risk(self):
        for task_kind in ("diagnosis", "research", "runtime_investigation", "root_cause_analysis", "code_review", "long_context", "integration_conflict"):
            with self.subTest(task_kind=task_kind):
                route = control.resolve_dispatch_route({
                    "agent": "general",
                    "task_kind": task_kind,
                    "risk": "low",
                    "complexity": "C1",
                })
                self.assertEqual(route["policy_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_reasoning_effort"], "high")
                self.assertEqual(route["policy_reason"], "terra_task_kind")
        bounded_analysis = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "analysis", "risk": "low", "complexity": "C1",
        })
        self.assertEqual(bounded_analysis["selected_model"], "gpt-5.6-luna")
        self.assertEqual(bounded_analysis["selected_reasoning_effort"], "high")

    def test_record_gate_returns_revision_correction_instead_of_stale_revision_error(self):
        state = self.init(task_id="gate-revision", complexity="C1")["state"]
        result = control.record_gate({
            "task_id": "gate-revision",
            "principal": "thread-a",
            "expected_revision": state["revision"] + 7,
            "gate": "plan",
            "outcome": "skipped",
            "skip_reason": "read-only routing test",
        })
        self.assertTrue(result["state"])
        self.assertEqual(result["revision_correction"], {"requested": state["revision"] + 7, "used": state["revision"]})

    def test_planner_defaults_follow_complexity(self):
        simple = control.resolve_dispatch_route({
            "agent": "planner", "task_kind": "planning", "risk": "low", "complexity": "C1",
        })
        self.assertEqual(simple["selected_model"], "gpt-5.6-luna")
        self.assertEqual(simple["selected_reasoning_effort"], "high")
        route = control.resolve_dispatch_route({
            "agent": "planner", "task_kind": "reading", "risk": "low", "complexity": "C2",
        })
        self.assertEqual(route["selected_model"], "gpt-5.6-terra")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_every_ordinary_profile_follows_its_canonical_model_class(self):
        for agent in control.MODEL_PROFILE_CLASSES["efficient"]:
            with self.subTest(profile_class="efficient", agent=agent):
                route = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "documentation", "risk": "moderate", "complexity": "C3",
                })
                self.assertEqual(route["selected_model"], "gpt-5.6-luna")
                self.assertEqual(route["selected_reasoning_effort"], "xhigh")
                self.assertEqual(route["policy_reason"], "efficient_profile")
        for agent in control.MODEL_PROFILE_CLASSES["deep"]:
            with self.subTest(profile_class="deep", agent=agent):
                route = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "analysis", "risk": "low", "complexity": "C1",
                })
                self.assertEqual(route["selected_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_reasoning_effort"], "high")
                self.assertEqual(route["policy_reason"], "deep_profile")
        for agent in control.MODEL_PROFILE_CLASSES["adaptive"]:
            with self.subTest(profile_class="adaptive", agent=agent, complexity="C1"):
                simple = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "implementation", "risk": "low", "complexity": "C1",
                })
                self.assertEqual(simple["selected_model"], "gpt-5.6-luna")
                self.assertEqual(simple["selected_reasoning_effort"], "high")
                self.assertEqual(simple["policy_reason"], "bounded_adaptive_work")
            with self.subTest(profile_class="adaptive", agent=agent, complexity="C2"):
                consequential = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "implementation", "risk": "moderate", "complexity": "C2",
                })
                expected_model = "gpt-5.6-terra" if agent == "planner" else "gpt-5.6-luna"
                expected_effort = "high" if agent == "planner" else "xhigh"
                expected_reason = "complex_planning" if agent == "planner" else "bounded_adaptive_work"
                self.assertEqual(consequential["selected_model"], expected_model)
                self.assertEqual(consequential["selected_reasoning_effort"], expected_effort)
                self.assertEqual(consequential["policy_reason"], expected_reason)

    def test_lightweight_categories_route_to_luna_with_multi_agent_v2(self):
        for agent, task_kind in (("explorer", "reading"), ("explorer", "discover"), ("explorer", "read_discovery"), ("explorer", "read_only_audit"), ("explorer", "comparative_audit"), ("explorer", "comparative-audit"), ("general", "data_gathering"), ("general", "crud_edit"), ("general", "small_fix")):
            for effort in ("high", "xhigh"):
                with self.subTest(agent=agent, task_kind=task_kind, effort=effort):
                    route = control.resolve_dispatch_route({"agent": agent, "task_kind": task_kind, "risk": "low", "complexity": "C1", "requested_reasoning_effort": effort})
                    self.assertEqual(route["policy_model"], "gpt-5.6-luna")
                    self.assertEqual(route["selected_model"], "gpt-5.6-luna")
                    self.assertEqual(route["selected_reasoning_effort"], effort)
        for task_kind, expected_model in (
            ("implementation", "gpt-5.6-luna"),
            ("tests", "gpt-5.6-luna"),
            ("debugging", "gpt-5.6-terra"),
            ("architecture", "gpt-5.6-terra"),
            ("migration", "gpt-5.6-terra"),
        ):
            with self.subTest(non_lightweight=task_kind):
                route = control.resolve_dispatch_route({"agent": "general", "task_kind": task_kind, "risk": "low", "complexity": "C1"})
                self.assertEqual(route["policy_model"], expected_model)
                self.assertEqual(route["selected_model"], expected_model)
                self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_terra_style_task_kind_is_canonicalized_at_the_mcp_boundary(self):
        for supplied, expected, model in (("Code Review", "code_review", "gpt-5.6-luna"), ("READ-ONLY", "read_only", "gpt-5.6-luna"), ("data   gathering", "data_gathering", "gpt-5.6-luna")):
            with self.subTest(supplied=supplied):
                route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": supplied, "risk": "low", "complexity": "C1"})
                self.assertEqual(route["task_kind"], expected)
                self.assertEqual(route["selected_model"], model)
        documentation_audit = control.resolve_dispatch_route({
            "agent": "technical_writer",
            "task_kind": "read_only_audit",
            "risk": "low",
            "complexity": "C3",
        })
        self.assertEqual(documentation_audit["selected_model"], "gpt-5.6-luna")
        self.assertTrue(documentation_audit["read_only"])
        with self.assertRaisesRegex(ValueError, "must contain only"):
            control.resolve_dispatch_route({"agent": "explorer", "task_kind": "review/execute", "risk": "low", "complexity": "C1"})

    def test_record_delegation_accepts_human_readable_task_kind(self):
        state = self.init(task_id="terra-task-kind")["state"]
        delegation = self.delegate(state, "terra-task-kind", "discover", "explorer", task_kind="Code Review")
        self.assertEqual(delegation["state"]["attempts"][-1]["task_kind"], "code_review")

    def test_luna_can_be_explicitly_requested_for_lightweight_dispatch(self):
        route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": "reading", "risk": "low", "complexity": "C1", "requested_model": "gpt-5.6-luna"})
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")

    def test_luna_route_falls_back_to_terra_when_the_native_host_does_not_offer_luna(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "reading",
            "risk": "low",
            "complexity": "C1",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-terra")
        self.assertEqual(route["fallback_reason"], "host_model_unavailable")
        self.assertEqual(route["fallback_from_model"], "gpt-5.6-luna")
        self.assertEqual(route["host_available_models"], ["gpt-5.6-sol", "gpt-5.6-terra"])

    def test_luna_route_requires_terra_when_the_native_host_does_not_offer_luna(self):
        with self.assertRaisesRegex(ValueError, "native host does not expose required model gpt-5.6-luna"):
            control.resolve_dispatch_route({
                "agent": "explorer",
                "task_kind": "reading",
                "risk": "low",
                "complexity": "C1",
                "available_models": ["gpt-5.6-sol"],
            })

    def test_delegation_records_hidden_terra_fallback_without_model_mismatch(self):
        state = self.init(task_id="host-model-fallback")["state"]
        delegation = self.delegate(
            state,
            "host-model-fallback",
            "discover",
            "explorer",
            task_kind="implementation",
            available_models=["gpt-5.6-sol", "gpt-5.6-terra"],
            available_thread_models=["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        )
        attempt = delegation["state"]["attempts"][-1]
        self.assertEqual(attempt["policy_model"], "gpt-5.6-luna")
        self.assertEqual(attempt["selected_model"], "gpt-5.6-terra")
        self.assertEqual(attempt["expected_model"], "gpt-5.6-terra")
        self.assertEqual(attempt["model_resolution"], "explicit_override")
        self.assertEqual(attempt["selected_reasoning_effort"], "medium")
        self.assertEqual(attempt["fallback_reason"], "host_model_unavailable")
        self.assertEqual(attempt["fallback_from_model"], "gpt-5.6-luna")
        self.assertEqual(attempt["luna_fallback"], "terra")
        self.assertEqual(attempt["spawn_request"]["host_tool"], "spawn_agent")
        self.assertEqual(attempt["spawn_request"]["model"], "gpt-5.6-terra")
        self.assertEqual(attempt["host_spawn"]["model_verification"], "verified")

    def test_explicit_visible_thread_keeps_luna_and_dynamic_reasoning_effort(self):
        state = self.init(task_id="visible-luna-thread")["state"]
        observed = control.status({"task_id": "visible-luna-thread", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "visible-luna-thread", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
            "dispatch_mode": "visible_thread",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "available_thread_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            "objective": "Inspect a narrow question", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Report findings"],
            "verification": ["Cite paths"],
        })
        request = delegated["spawn_request"]
        self.assertEqual(request["host_tool"], "create_thread")
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertEqual(request["reasoning_effort"], "medium")
        self.assertEqual(request["thread_environment"], "local")
        self.assertEqual(request["prompt"], request["message"])
        briefing = self.briefing_from_request(request)
        self.assertIn("visible user-owned task", briefing)
        self.assertIn("Emit English only in every message", briefing)
        attempt = delegated["state"]["attempts"][-1]
        self.assertTrue(attempt["user_owned_thread"])
        self.assertEqual(attempt["visibility"], "visible")
        confirmed = control.confirm_host_spawn({
            "task_id": "visible-luna-thread", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_tool": "create_thread", "host_agent_id": "thread-visible-123",
            "host_task_name": request["task_name"], "host_model": "gpt-5.6-luna",
            "host_reasoning_effort": "medium",
        })
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["host_spawn"]["tool"], "create_thread")

    def test_luna_host_fallback_uses_hidden_terra_without_changing_effort(self):
        state = self.init(task_id="terra-fallback-opt-out")['state']
        delegated = self.delegate(
            state,
            "terra-fallback-opt-out",
            "discover",
            "explorer",
            task_kind="discovery",
            available_models=["gpt-5.6-sol", "gpt-5.6-terra"],
            luna_fallback="terra",
        )
        attempt = delegated["state"]["attempts"][-1]
        self.assertEqual(delegated["spawn_request"]["host_tool"], "spawn_agent")
        self.assertEqual(delegated["spawn_request"]["model"], "gpt-5.6-terra")
        self.assertEqual(delegated["spawn_request"]["reasoning_effort"], "medium")
        self.assertEqual(attempt["fallback_reason"], "host_model_unavailable")
        self.assertEqual(attempt["fallback_from_model"], "gpt-5.6-luna")
        self.assertFalse(attempt["user_owned_thread"])

    def test_visible_thread_can_opt_into_an_isolated_worktree(self):
        state = self.init(task_id="visible-worktree-thread")["state"]
        observed = control.status({"task_id": "visible-worktree-thread", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "visible-worktree-thread", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
            "dispatch_mode": "visible_thread", "thread_environment": "worktree",
            "available_thread_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            "objective": "Inspect a narrow question", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Report findings"],
            "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["spawn_request"]["thread_environment"], "worktree")
        self.assertEqual(delegated["state"]["attempts"][-1]["thread_environment"], "worktree")

    def test_thread_environment_is_rejected_for_hidden_subagents(self):
        state = self.init(task_id="hidden-thread-environment")["state"]
        observed = control.status({"task_id": "hidden-thread-environment", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "applies only to visible_thread"):
            control.record_delegation({
                "task_id": "hidden-thread-environment", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
                "thread_environment": "local", "objective": "Inspect", "ownership": "Read-only",
                "allowed_paths": ["."], "acceptance_criteria": ["Report"], "verification": ["Cite"],
            })

    def test_visible_thread_requires_its_own_host_model_catalog(self):
        state = self.init(task_id="visible-thread-catalog")["state"]
        observed = control.status({"task_id": "visible-thread-catalog", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "visible_thread requires exact available_thread_models"):
            control.record_delegation({
                "task_id": "visible-thread-catalog", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
                "dispatch_mode": "visible_thread", "objective": "Inspect", "ownership": "Read-only",
                "allowed_paths": ["."], "acceptance_criteria": ["Report"], "verification": ["Cite"],
            })

    def test_visible_thread_is_rejected_as_a_luna_fallback(self):
        state = self.init(task_id="luna-thread-fallback")["state"]
        observed = control.status({"task_id": "luna-thread-fallback", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "luna_fallback must be terra"):
            control.record_delegation({
                "task_id": "luna-thread-fallback", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "discover", "risk": "low",
                "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
                "available_thread_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
                "luna_fallback": "visible_thread",
                "objective": "Inspect a narrow question", "ownership": "Read-only discovery",
                "allowed_paths": ["."], "acceptance_criteria": ["Report findings"],
                "verification": ["Cite paths"],
            })

    def test_luna_thread_fallback_keeps_hidden_luna_when_spawn_agent_supports_it(self):
        state = self.init(task_id="luna-fallback-not-needed")["state"]
        delegated = self.delegate(
            state,
            "luna-fallback-not-needed",
            "discover",
            "explorer",
            task_kind="discover",
            available_models=["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            available_thread_models=["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            luna_fallback="terra",
        )
        self.assertEqual(delegated["spawn_request"]["host_tool"], "spawn_agent")
        self.assertEqual(delegated["spawn_request"]["model"], "gpt-5.6-luna")

    def test_adaptive_profile_matrix_uses_bounded_luna_and_high_cost_terra(self):
        for complexity in ("C1", "C2", "C3"):
            for risk in ("low", "moderate", "high", "critical"):
                with self.subTest(complexity=complexity, risk=risk):
                    route = control.resolve_dispatch_route({"agent": "general", "task_kind": "implementation", "risk": risk, "complexity": complexity})
                    expected_model = "gpt-5.6-luna" if risk in {"low", "moderate"} else "gpt-5.6-terra"
                    effort_map = (
                        control.LUNA_BOUNDED_EFFORT_BY_COMPLEXITY
                        if expected_model == "gpt-5.6-luna"
                        else control.TERRA_EFFORT_BY_COMPLEXITY
                    )
                    expected_effort = control.higher_effort(
                        effort_map[complexity],
                        control.MODEL_EFFORT_FLOOR_BY_RISK[risk],
                    )
                    self.assertEqual(route["policy_model"], expected_model)
                    self.assertEqual(route["selected_model"], expected_model)
                    self.assertEqual(route["selected_reasoning_effort"], expected_effort)
                    self.assertEqual(
                        route["selected_reasoning_effort"] == "max",
                        expected_model == "gpt-5.6-luna" and complexity == "C3",
                    )

    def test_non_security_sol_requires_explicit_user_model_request(self):
        with self.assertRaisesRegex(ValueError, "requires user_requested_model"):
            control.resolve_dispatch_route({
                "agent": "general", "task_kind": "implementation", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-sol",
            })
        with self.assertRaisesRegex(ValueError, "must match requested_model"):
            control.resolve_dispatch_route({
                "agent": "general", "task_kind": "implementation", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-terra", "user_requested_model": "gpt-5.6-sol",
            })

    def test_explicit_user_request_permits_non_security_sol(self):
        route = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "migration", "risk": "critical", "complexity": "C3",
            "user_requested_model": "gpt-5.6-sol",
            "requested_reasoning_effort": "xhigh",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-terra")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_reasoning_effort"], "xhigh")
        self.assertEqual(route["model_choice_reason"], "explicit_user_request")
        self.assertEqual(route["user_requested_model"], "gpt-5.6-sol")

    def test_public_worker_preserves_user_requested_sol_provenance(self):
        started = self.v3_start(
            "user selected Sol",
            waves=[{"workers": [{
                "phase": "implementation", "profile": "general",
                "user_requested_model": "sol", "effort": "high",
            }]}],
            complexity="C1",
        )
        self.assertTrue(started["ok"])
        self.assertEqual(started["dispatches"][0]["arguments"]["model"], "gpt-5.6-sol")
        task_dir = next((self.ledger / "tasks").iterdir())
        attempt = control.load_task_state_for_artifact(task_dir)["attempts"][0]
        self.assertEqual(attempt["requested_model"], "gpt-5.6-sol")
        self.assertEqual(attempt["user_requested_model"], "gpt-5.6-sol")

    def test_explorer_rejects_user_requested_sol(self):
        with self.assertRaisesRegex(ValueError, "explorer always uses"):
            control.resolve_dispatch_route({
                "agent": "explorer", "task_kind": "discovery", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-sol", "user_requested_model": "gpt-5.6-sol",
            })

    def test_evidence_is_required_to_pass(self):
        result = self.init()
        state = result["state"]
        delegation = self.delegate(state, "demo", "discover", "explorer")
        self.assertEqual(delegation["state"]["attempts"][-1]["display_name"], "Explorer Objective")
        delegation_file = self.task_document(control.db_task_artifact_path(self.ledger, "demo"), f"dispatch:{delegation['attempt_id']}")
        self.assertEqual(delegation_file["display_name"], "Explorer Objective")
        self.assertEqual(delegation_file["profile"], "explorer")
        self.assertEqual(delegation_file["selected_model"], "gpt-5.6-luna")
        self.assertEqual(delegation_file["selected_reasoning_effort"], "medium")
        self.assertFalse(delegation_file["user_facing"])
        self.assertEqual(delegation_file["question_route"]["mode"], "pull")
        self.assertEqual(delegation_file["question_route"]["worker_tool"], "cortex.question")
        self.assertEqual(delegation_file["question_route"]["publish_tool"], "publish_worker_question")
        self.assertEqual(delegation_file["question_route"]["coordinator_ui_tool"], "cortex.question")
        self.assertEqual(delegation_file["escalation_route"], "main_chat")
        self.assertEqual(delegation_file["handoff_route"], "main_chat")
        pending = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "discover", "outcome": "passed"})
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_evidence")
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "discover", "attempt_id": delegation["attempt_id"], "kind": "report", "summary": "inspection completed", "command": "Authorization: Bearer <TOKEN>"})
        self.assertIn("<REDACTED>", evidence["state"]["evidence"][0]["command"])
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        self.assertEqual(closed["state"]["current_gates"], ["implementation"])

    def test_documentation_evidence_alias_is_canonicalized(self):
        created = self.init(task_id="documentation-alias", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate documentation contract",
        })
        delegation = self.delegate(narrowed["state"], "documentation-alias", "documentation", "technical_writer")
        report = self.report("documentation-alias", delegation["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": report["state"]["revision"],
            "gate": "documentation",
            "attempt_id": delegation["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "kind": "documentation_sync",
            "decision": "updated",
            "summary": "documentation is synchronized",
        })
        self.assertEqual(evidence["evidence"]["kind"], "documentation")
        self.assertEqual(evidence["state"]["documentation_receipt"]["attempt_id"], delegation["attempt_id"])
        passed = control.record_gate({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "documentation",
            "outcome": "passed",
        })
        self.assertIn("documentation", passed["state"]["completed_gates"])

    def test_documentation_gate_rejects_noncanonical_receipt_state(self):
        created = self.init(task_id="documentation-noncanonical", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-noncanonical",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate noncanonical documentation receipt",
        })
        delegation = self.delegate(narrowed["state"], "documentation-noncanonical", "documentation", "technical_writer")
        report = self.report("documentation-noncanonical", delegation["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "documentation-noncanonical",
            "principal": "thread-a",
            "expected_revision": report["state"]["revision"],
            "gate": "documentation",
            "attempt_id": delegation["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "kind": "documentation_sync",
            "decision": "updated",
            "summary": "noncanonical documentation evidence",
        })
        task_dir = self.ledger / "tasks" / "0001-documentation-noncanonical"
        current = self.task_state(task_dir)
        current["evidence"][-1]["kind"] = "documentation_sync"
        current["documentation_receipt"] = None
        self.write_task_state(current)
        status = control.status({"task_id": "documentation-noncanonical", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "SQLite evidence record failed reconciliation"):
            control.record_gate({
                "task_id": "documentation-noncanonical",
                "principal": "thread-a",
                "expected_revision": status["state"]["revision"],
                "gate": "documentation",
                "outcome": "passed",
            })

    def test_gate_ignores_tampered_evidence_projection(self):
        created = self.init(task_id="evidence-reconciliation", complexity="C1")
        state = created["state"]
        evidence = control.record_evidence({
            "task_id": "evidence-reconciliation", "principal": "thread-a",
            "expected_revision": state["revision"], "gate": "discover", "summary": "Observed repository evidence.",
        })
        task_dir = self.ledger / "tasks/0001-evidence-reconciliation"
        canonical = control.db_get_artifact_for_export_path(
            self.ledger, "evidence-reconciliation", "evidence/evidence-0001.json",
        )
        self.assertIsNotNone(canonical)
        self.reconcile_projections(worker_id="evidence-projection-test")
        path = task_dir / "evidence/evidence-0001.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["summary"] = "tampered"
        path.write_text(json.dumps(record), encoding="utf-8")
        passed = control.record_gate({
            "task_id": "evidence-reconciliation", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed",
        })
        self.assertIn("discover", passed["state"]["completed_gates"])

    def test_documentation_retry_cannot_spawn_duplicate_worker(self):
        created = self.init(task_id="documentation-no-loop", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-no-loop",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate duplicate-dispatch guard",
        })
        delegation = self.delegate(narrowed["state"], "documentation-no-loop", "documentation", "technical_writer")
        self.report("documentation-no-loop", delegation["attempt_id"])
        status = control.status({"task_id": "documentation-no-loop", "principal": "thread-a"})
        duplicate = control.record_delegation({
            "task_id": "documentation-no-loop",
            "principal": "thread-a",
            "expected_revision": status["state"]["revision"],
            "status_receipt": status["status_receipt"],
            "gate": "documentation",
            "agent": "technical_writer",
            "task_kind": "documentation",
            "risk": "low",
            "objective": "retry documentation",
            "ownership": "Own documentation",
            "allowed_paths": ["."],
            "acceptance_criteria": ["Publish documentation evidence"],
            "verification": ["Review docs"],
        })
        self.assertFalse(duplicate["recorded"])
        self.assertEqual(duplicate["reason"], "documentation_attempt_already_available")
        self.assertEqual(duplicate["next_action"], "record_evidence")
        self.assertEqual(len(duplicate["state"]["attempts"]), 1)

    def test_missing_report_attempt_can_be_finalized_and_remains_visible(self):
        state = self.init(task_id="missing-report", complexity="C2")["state"]
        delegation = self.delegate(state, "missing-report", "plan", "planner")
        missing_reason = control.finalize_attempt({
            "task_id": "missing-report",
            "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"],
            "status": "failed",
        })
        self.assertFalse(missing_reason["recorded"])
        self.assertEqual(missing_reason["next_action"], "retry_finalize_attempt_with_reason")
        self.assertEqual(missing_reason["required_fields"], ["reason"])
        finalized = control.finalize_attempt({
            "task_id": "missing-report",
            "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"],
            "status": "failed",
            "reason": "worker stopped before publishing a report",
        })
        self.assertEqual(finalized["status"], "failed")
        self.assertEqual(finalized["state"]["attempts"][0]["status"], "failed")
        self.assertEqual(finalized["state"]["attempts"][0]["finalization_reason"], "worker stopped before publishing a report")
        observed = control.status({"task_id": "missing-report", "principal": "thread-a"})
        self.assertEqual(observed["state"]["attempts"][0]["status"], "failed")

    def test_terminal_non_success_attempt_does_not_block_gate_completion(self):
        state = self.init(task_id="terminal-attempt", complexity="C2")["state"]
        abandoned = self.delegate(state, "terminal-attempt", "discover", "explorer")
        finalized = control.finalize_attempt({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": abandoned["state"]["revision"],
            "attempt_id": abandoned["attempt_id"],
            "status": "cancelled",
            "reason": "host worker timed out",
        })
        replacement = self.delegate(finalized["state"], "terminal-attempt", "discover", "explorer")
        report = self.report("terminal-attempt", replacement["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": replacement["state"]["revision"],
            "gate": "discover",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the gate",
        })
        closed = control.record_gate({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "discover",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[abandoned["attempt_id"]], "cancelled")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gates"], ["plan"])

    def test_failed_terminal_attempt_without_evidence_does_not_block_gate_completion(self):
        state = self.init(task_id="failed-only-attempt", complexity="C2")["state"]
        failed = self.delegate(state, "failed-only-attempt", "discover", "explorer")
        finalized = control.finalize_attempt({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": failed["state"]["revision"],
            "attempt_id": failed["attempt_id"],
            "status": "failed",
            "reason": "worker failed before producing a report",
        })
        closed = control.record_gate({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"],
            "gate": "discover",
            "outcome": "passed",
        })
        self.assertEqual(closed["state"]["attempts"][0]["status"], "failed")
        self.assertEqual(closed["state"]["current_gates"], ["plan"])

    def test_active_running_attempt_without_evidence_still_blocks_gate(self):
        state = self.init(task_id="active-attempt", complexity="C2")["state"]
        delegation = self.delegate(state, "active-attempt", "discover", "explorer")
        pending = control.record_gate({
                "task_id": "active-attempt",
                "principal": "thread-a",
                "expected_revision": delegation["state"]["revision"],
                "gate": "discover",
                "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["reason"], "evidence_required")

    def test_invalidated_running_attempt_can_be_superseded_and_no_longer_blocks_gate(self):
        state = self.init(task_id="invalidated-attempt", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-attempt", "discover", "explorer")
        reworked = control.update_pipeline({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": original["state"]["revision"],
            "operations": [{"op": "rework", "gate": "discover"}],
            "allow_rework": True,
        })
        missing_reason = control.finalize_attempt({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "superseded",
        })
        self.assertFalse(missing_reason["recorded"])
        self.assertEqual(missing_reason["next_action"], "retry_finalize_attempt_with_reason")
        with self.assertRaisesRegex(ValueError, "only be finalized as superseded"):
            control.finalize_attempt({
                "task_id": "invalidated-attempt",
                "principal": "thread-a",
                "expected_revision": reworked["state"]["revision"],
                "attempt_id": original["attempt_id"],
                "status": "cancelled",
                "reason": "rework replaced this attempt",
            })
        superseded = control.finalize_attempt({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "superseded",
            "reason": "rework replaced this attempt",
        })
        old_attempt = superseded["state"]["attempts"][0]
        self.assertTrue(old_attempt["invalidated"])
        self.assertEqual(old_attempt["status"], "superseded")

        replacement = self.delegate(superseded["state"], "invalidated-attempt", "discover", "explorer")
        self.assertNotEqual(
            old_attempt["spawn_request"]["task_name"],
            replacement["spawn_request"]["task_name"],
        )
        self.assertNotEqual(
            old_attempt["host_spawn"]["agent_id"],
            replacement["host_spawn"]["agent_id"],
        )
        report = self.report("invalidated-attempt", replacement["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": replacement["state"]["revision"],
            "gate": "discover",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the reworked gate",
        })
        closed = control.record_gate({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "discover",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[original["attempt_id"]], "superseded")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gates"], ["plan"])

    def test_invalidated_terminal_attempt_remains_idempotent(self):
        state = self.init(task_id="invalidated-terminal", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-terminal", "discover", "explorer")
        failed = control.finalize_attempt({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": original["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "failed",
            "reason": "worker stopped",
        })
        reworked = control.update_pipeline({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": failed["state"]["revision"],
            "operations": [{"op": "rework", "gate": "discover"}],
            "allow_rework": True,
        })
        replay = control.finalize_attempt({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "failed",
        })
        self.assertTrue(replay["idempotent"])
        self.assertTrue(replay["state"]["attempts"][0]["invalidated"])

    def test_record_delegation_propagates_exact_spawn_request(self):
        state = self.init(task_id="spawn-contract")["state"]
        delegation = self.delegate(
            state,
            "spawn-contract",
            "discover",
            "general",
            requested_model="gpt-5.6-terra",
            requested_reasoning_effort="high",
            task_kind="implementation",
        )
        expected = {
            "host_tool": "spawn_agent",
            "profile": "general",
            "display_name": "General Objective",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
        }
        package = self.task_document(control.db_task_artifact_path(self.ledger, "spawn-contract"), f"dispatch:{delegation['attempt_id']}")
        self.assertEqual({key: delegation["spawn_request"][key] for key in expected}, expected)
        self.assertRegex(
            delegation["spawn_request"]["task_name"],
            r"^general_objective_01_[0-9a-f]{8}$",
        )
        self.assertNotEqual(delegation["spawn_request"]["task_name"], delegation["spawn_request"]["display_name"])
        self.assertEqual({key: package["spawn_request"][key] for key in expected}, expected)
        self.assertEqual({key: delegation["state"]["attempts"][-1]["spawn_request"][key] for key in expected}, expected)
        briefing = self.briefing_from_request(delegation["spawn_request"])
        self.assertIn("# Cortex Worker Briefing v2", briefing)
        assignment = json.loads(briefing.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["profile"], "general")
        self.assertEqual(delegation["state"]["attempts"][-1]["dispatch_correlation"], "coordinator_recorded_host_spawn")

    def test_delegation_ignores_orphan_briefing_export_when_allocating_attempt(self):
        state = self.init(task_id="orphan-briefing-recovery")["state"]
        task_dir = control.db_task_artifact_path(self.ledger, "orphan-briefing-recovery")
        delegations = task_dir / "delegations"
        delegations.mkdir(parents=True, exist_ok=True)
        (delegations / "discover-07.dispatch-orphan123.briefing.md").write_text(
            "orphaned immutable briefing\n", encoding="utf-8"
        )
        delegation = self.delegate(state, "orphan-briefing-recovery", "discover", "explorer")
        self.assertEqual(delegation["attempt_id"], "discover-01")
        self.assertNotEqual(Path(delegation["spawn_request"]["briefing_path"]), delegations / "discover-07.dispatch-orphan123.briefing.md")
        self.assertEqual(self.task_state(task_dir)["attempts"][0]["attempt_id"], "discover-01")

    def test_native_worker_task_name_compacts_long_request_derived_task_ids(self):
        long_task_id = "cortex-orchestrator-local-plugin-cache-ca-bd8a9ad4"
        task_name = control.native_worker_task_name("planner", long_task_id, "plan-01")
        self.assertRegex(task_name, r"^planner_worker_01_[0-9a-f]{8}$")
        self.assertNotIn("plugin", task_name)
        self.assertNotIn("cache", task_name)
        self.assertNotIn("-", task_name)
        self.assertLessEqual(len(task_name), 80)
        self.assertNotEqual(
            task_name,
            control.native_worker_task_name(
                "planner", "cortex-orchestrator-local-plugin-cache-7f3e2a19", "plan-01"
            ),
        )

    def test_native_worker_task_names_obey_host_contract_for_every_profile(self):
        names = {
            control.native_worker_task_name(profile, "harvest-refresh", "plan-01")
            for profile in control.PROFILES
        }
        self.assertEqual(len(names), len(control.PROFILES))
        for task_name in names:
            self.assertRegex(task_name, r"^[a-z0-9_]{1,80}$")
            self.assertNotIn("-", task_name)

    def test_worker_display_name_humanizes_multiword_profiles_without_identity_suffix(self):
        self.assertEqual(control.worker_display_name("security_auditor", "Auth"), "Security Auditor Auth")
        self.assertEqual(control.worker_display_name("explorer", "Trading"), "Explorer Trading")

    def test_worker_module_label_prefers_explicit_feature_domain_over_command_words(self):
        request = (
            "$cortex:orchestrator harvest Run a source-backed full knowledge harvest. "
            "The authentication feature documentation must cover failures and recovery."
        )
        self.assertEqual(control.worker_module_label(request, ["."], "plan"), "Authentication")

        live_request = (
            "$cortex:orchestrator harvest Perform a complete source-backed Cortex harvest. "
            "Do not request post-plan user approval. Document the actual pricing behavior, "
            "positive and negative scenarios, state, interfaces, and recovery."
        )
        self.assertEqual(control.worker_module_label(live_request, ["."], "plan"), "Pricing")
        self.assertEqual(
            control.worker_display_name("planner", control.worker_module_label(live_request, ["."], "plan")),
            "Planner Pricing",
        )

        repository_request = (
            "$cortex:orchestrator harvest Perform a complete source-backed harvest. "
            "Complete every canonical phase through independent review and close verification."
        )
        self.assertEqual(control.worker_module_label(repository_request, ["."], "plan"), "Repository")
        self.assertEqual(
            control.worker_display_name(
                "planner", control.worker_module_label(repository_request, ["."], "plan")
            ),
            "Planner Repository",
        )

        refresh_request = (
            "$cortex:orchestrator harvest-refresh Refresh the repository knowledge exhaustively "
            "from current source, tests, configuration, and existing documentation."
        )
        self.assertEqual(control.worker_module_label(refresh_request, ["."], "plan"), "Repository")
        self.assertEqual(
            control.worker_display_name(
                "planner", control.worker_module_label(refresh_request, ["."], "plan")
            ),
            "Planner Repository",
        )

    def test_native_worker_task_name_remains_unique_after_hyphen_normalization(self):
        dashed = control.native_worker_task_name("planner", "harvest-refresh", "plan-01")
        underscored = control.native_worker_task_name("planner", "harvest_refresh", "plan_01")
        self.assertNotEqual(dashed, underscored)
        self.assertRegex(dashed, r"^[a-z0-9_]{1,80}$")
        self.assertRegex(underscored, r"^[a-z0-9_]{1,80}$")

    def test_host_spawn_confirmation_requires_the_exact_native_task_name(self):
        state = self.init(task_id="host-name-contract")["state"]
        observed = control.status({"task_id": "host-name-contract", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-name-contract", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        expected_task_name = delegated["spawn_request"]["task_name"]
        self.assertRegex(expected_task_name, r"^explorer_objective_01_[0-9a-f]{8}$")
        briefing = self.briefing_from_request(delegated["spawn_request"])
        self.assertIn("Use attempt_id='discover-01' exactly", briefing)
        self.assertIn("stable lowercase submission_id", briefing)
        report_fields = ", ".join(control.REPORT_FIELDS)
        self.assertIn(f"exactly {len(control.REPORT_FIELDS)} keys: {report_fields}", briefing)
        self.assertIn("byte-identical retry", briefing)
        self.assertIn("Do not activate or initialize Cortex", briefing)
        confirmed = control.confirm_host_spawn({
                "task_id": "host-name-contract", "principal": "thread-a",
                "expected_revision": delegated["state"]["revision"],
                "attempt_id": delegated["attempt_id"], "host_agent_id": "desktop-child-123",
                "host_task_name": expected_task_name, "host_model": delegated["spawn_request"]["model"],
            })
        self.assertTrue(confirmed["confirmed"])
        self.assertIsNone(confirmed["task_name_correction"])
        self.assertEqual(confirmed["host_spawn"]["task_name"], expected_task_name)

    def test_host_spawn_confirmation_rejects_reused_native_child_id(self):
        self.init(task_id="host-id-reuse")
        result = control.prepare_delegations({
            "task_id": "host-id-reuse", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        first, second = result["spawn_requests"]
        first_attempt_id, second_attempt_id = result["attempts"]
        first_confirmed = control.confirm_host_spawn({
            "task_id": "host-id-reuse", "principal": "thread-a", "expected_revision": result["state"]["revision"],
            "attempt_id": first_attempt_id, "host_tool": "spawn_agent", "host_agent_id": "same-native-child",
            "host_task_name": first["task_name"], "host_model": first.get("model") or first["expected_model"],
            "host_reasoning_effort": first["reasoning_effort"],
        })
        self.assertTrue(first_confirmed["confirmed"])
        reused = control.confirm_host_spawn({
            "task_id": "host-id-reuse", "principal": "thread-a", "expected_revision": first_confirmed["state"]["revision"],
            "attempt_id": second_attempt_id, "host_tool": "spawn_agent", "host_agent_id": "same-native-child",
            "host_task_name": second["task_name"], "host_model": second.get("model") or second["expected_model"],
            "host_reasoning_effort": second["reasoning_effort"],
        })
        self.assertFalse(reused["confirmed"])
        self.assertTrue(reused["recoverable"])
        self.assertEqual(reused["reason"], "host_agent_id_reused")
        self.assertEqual(reused["state"]["attempts"][1]["status"], control.AWAITING_HOST_SPAWN)

    def test_host_spawn_confirmation_without_host_fields_is_recoverable(self):
        state = self.init(task_id="host-fields")["state"]
        observed = control.status({"task_id": "host-fields", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-fields", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "plan", "agent": "planner",
            "task_kind": "planning", "risk": "low", "objective": "plan",
            "ownership": "Own plan", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        recovered = control.confirm_host_spawn({
            "task_id": "host-fields", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
        })
        self.assertFalse(recovered["confirmed"])
        self.assertTrue(recovered["recoverable"])
        self.assertIn("host_agent_id", recovered["next_action"])

    def test_host_spawn_confirmation_requires_the_actual_host_model(self):
        state = self.init(task_id="host-model-required")["state"]
        observed = control.status({"task_id": "host-model-required", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-model-required", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        recovered = control.confirm_host_spawn({
            "task_id": "host-model-required", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "desktop-child-model-required",
            "host_task_name": delegated["spawn_request"]["task_name"],
        })
        self.assertFalse(recovered["confirmed"])
        self.assertTrue(recovered["recoverable"])
        self.assertEqual(recovered["reason"], "host_model_required")
        self.assertEqual(recovered["required_fields"], ["host_model"])
        self.assertEqual(recovered["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_host_model_mismatch_fails_attempt_without_accepting_report(self):
        state = self.init(task_id="host-model-mismatch")["state"]
        observed = control.status({"task_id": "host-model-mismatch", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-model-mismatch", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["spawn_request"]["model"], "gpt-5.6-luna")
        mismatch = control.confirm_host_spawn({
            "task_id": "host-model-mismatch", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "desktop-child-terra-fallback",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": "gpt-5.6-terra",
        })
        self.assertFalse(mismatch["confirmed"])
        self.assertTrue(mismatch["failed"])
        self.assertEqual(mismatch["reason"], "host_model_mismatch")
        self.assertEqual(mismatch["expected_model"], "gpt-5.6-luna")
        self.assertEqual(mismatch["actual_model"], "gpt-5.6-terra")
        self.assertEqual(mismatch["state"]["attempts"][-1]["status"], "failed")
        self.assertEqual(mismatch["state"]["attempts"][-1]["model_verification"], "mismatch")

    def test_worker_profile_alias_can_publish_its_own_report_with_correction(self):
        state = self.init(task_id="worker-report-alias")["state"]
        observed = control.status({"task_id": "worker-report-alias", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "worker-report-alias", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "plan", "agent": "planner",
            "task_kind": "read_only_audit", "risk": "low", "objective": "plan",
            "ownership": "Own plan", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        confirmed = control.confirm_host_spawn({
            "task_id": "worker-report-alias", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "planner", "host_task_name": delegated["spawn_request"]["task_name"], "host_model": delegated["spawn_request"]["model"],
        })
        report = control.record_report({
            "task_id": "worker-report-alias", "principal": "planner", "attempt_id": delegated["attempt_id"],
            "submission_id": "worker-report", "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"], "uncertainty": []},
        })
        self.assertEqual(report["principal_correction"], {"requested": "planner", "used": "thread-a"})

    def test_worker_host_agent_alias_can_publish_its_own_report_with_correction(self):
        state = self.init(task_id="worker-host-alias")["state"]
        delegated = self.delegate(state, "worker-host-alias", "plan", "planner")
        host_id = delegated["host_spawn"]["agent_id"]
        report = control.record_report({
            "task_id": "worker-host-alias", "principal": host_id,
            "attempt_id": delegated["attempt_id"], "submission_id": "host-report",
            "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"],
            "uncertainty": []},
        })
        self.assertEqual(report["principal_correction"], {"requested": host_id, "used": "thread-a"})
        self.assertEqual(report["report"]["attempt_id"], delegated["attempt_id"])

    def test_worker_report_infers_missing_attempt_and_submission_identifiers(self):
        state = self.init(task_id="worker-report-inference")["state"]
        delegated = self.delegate(state, "worker-report-inference", "plan", "planner", task_kind="planning", risk="low")
        report = control.record_report({
            "task_id": "worker-report-inference", "principal": "planner",
            "attempt_id": "", "submission_id": "",
            "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"],
            "uncertainty": []},
        })
        self.assertFalse(report["idempotent"])
        self.assertEqual(report["report"]["attempt_id"], delegated["attempt_id"])
        self.assertTrue(report["report"]["submission_id"].startswith(f"submission-{delegated['attempt_id']}-report-"))
        self.assertEqual(report["principal_correction"], {"requested": "planner", "used": "thread-a"})

    def test_worker_report_requires_attempt_when_worker_identity_is_ambiguous(self):
        state = self.init(task_id="worker-report-ambiguous")["state"]
        first = self.delegate(state, "worker-report-ambiguous", "plan", "planner", task_kind="planning", risk="low", parallel=True)
        second = self.delegate(first["state"], "worker-report-ambiguous", "plan", "planner", task_kind="planning", risk="low", parallel=True)
        result = control.record_report({
            "task_id": "worker-report-ambiguous", "principal": "planner",
            "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"],
            "uncertainty": []},
        })
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "delegation_attempt_required")
        self.assertEqual(result["candidate_attempt_ids"], [first["attempt_id"], second["attempt_id"]])

    def test_delegation_requires_native_host_spawn_confirmation(self):
        state = self.init(task_id="host-spawn") ["state"]
        observed = control.status({"task_id": "host-spawn", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-spawn", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)
        early_report = self.report("host-spawn", delegated["attempt_id"])
        self.assertTrue(early_report["host_confirmation_pending"])
        confirmed = control.confirm_host_spawn({
            "task_id": "host-spawn", "principal": "thread-a", "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"], "host_agent_id": "desktop-child-123",
            "host_task_name": delegated["spawn_request"]["task_name"], "host_model": delegated["spawn_request"]["model"],
        })
        self.assertEqual(confirmed["state"]["attempts"][-1]["status"], "running")
        self.assertEqual(confirmed["host_spawn"]["agent_id"], "desktop-child-123")

    def test_host_can_finalize_passed_before_coordinator_links_report_evidence(self):
        state = self.init(task_id="finalize-before-evidence", complexity="C2")["state"]
        delegation = self.delegate(state, "finalize-before-evidence", "discover", "explorer", task_kind="discovery", risk="low")
        report = self.report("finalize-before-evidence", delegation["attempt_id"])
        finalized = control.finalize_attempt({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"], "status": "passed",
        })
        pending = control.record_gate({
                "task_id": "finalize-before-evidence", "principal": "thread-a",
                "expected_revision": finalized["state"]["revision"], "gate": "discover", "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_evidence")
        evidence = control.record_evidence({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"], "gate": "discover",
            "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"],
            "summary": "worker report reviewed",
        })
        advanced = control.record_gate({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed",
        })
        self.assertEqual(advanced["state"]["current_gates"], ["plan"])

    def test_recoverable_model_sequence_is_corrected_without_contract_errors(self):
        state = self.init(task_id="recoverable-sequence", complexity="C2")["state"]
        observed = control.status({"task_id": "recoverable-sequence", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "plan", "agent": "explorer", "task_kind": "discovery", "risk": "low",
            "objective": "plan", "ownership": "", "allowed_paths": [],
            "acceptance_criteria": [], "verification": [],
        })
        self.assertEqual(delegated["gate_correction"], {"requested": "plan", "used": "discover"})
        package = self.task_document(control.db_task_artifact_path(self.ledger, "recoverable-sequence"), f"dispatch:{delegated['attempt_id']}")
        self.assertIn("Own bounded repository discovery", package["ownership"])
        self.assertEqual(package["allowed_paths"], ["."])
        self.assertTrue(package["acceptance_criteria"])
        self.assertTrue(package["verification"])
        premature = control.record_gate({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "gate": "plan", "outcome": "passed",
        })
        self.assertFalse(premature["recorded"])
        self.assertEqual(premature["next_action"], "record_evidence")
        self.assertEqual(premature["gate_correction"], {"requested": "plan", "used": "discover"})
        confirmed = control.confirm_host_spawn({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "luna-medium-worker", "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
        })
        report = self.report("recoverable-sequence", delegated["attempt_id"])
        inferred = control.record_evidence({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": confirmed["state"]["revision"], "gate": "discover",
            "summary": "report reviewed",
        })
        self.assertEqual(inferred["evidence"]["attempt_id"], delegated["attempt_id"])
        self.assertEqual(inferred["evidence"]["report_receipt"], report["receipt"]["receipt_id"])
        self.assertEqual(inferred["inferred"], {"gate": False, "attempt_id": True, "report_receipt": True})

    def test_delegation_infers_missing_gate_profile_kind_and_risk(self):
        state = self.init(task_id="inferred-delegation", complexity="C2")["state"]
        delegated = control.record_delegation({
            "task_id": "inferred-delegation", "principal": "thread-a",
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["gate"], "discover")
        self.assertEqual(delegated["spawn_request"]["profile"], "explorer")
        self.assertEqual(delegated["state"]["attempts"][-1]["task_kind"], "discovery")
        self.assertEqual(delegated["state"]["attempts"][-1]["risk"], "low")
        self.assertEqual(delegated["agent_correction"], {"requested": None, "used": "explorer"})
        self.assertEqual(delegated["task_kind_correction"], {"requested": None, "used": "discovery"})
        self.assertEqual(delegated["risk_correction"], {"requested": None, "used": "low"})

    def test_rework_releases_retry_budget_for_invalidated_attempts(self):
        state = self.init(task_id="retry-rework") ["state"]
        first = self.delegate(state, "retry-rework", "discover", "explorer")
        failed = control.finalize_attempt({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": first["state"]["revision"],
            "attempt_id": first["attempt_id"], "status": "failed", "reason": "first failed",
        })
        second = self.delegate(failed["state"], "retry-rework", "discover", "explorer")
        failed = control.finalize_attempt({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": second["state"]["revision"],
            "attempt_id": second["attempt_id"], "status": "failed", "reason": "second failed",
        })
        observed = control.status({"task_id": "retry-rework", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "same_strategy_limit reached"):
            control.record_delegation({
                "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
                "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
                "task_kind": "discover", "risk": "low", "objective": "third try", "ownership": "Read-only discovery",
                "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
            })
        reworked = control.update_pipeline({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
            "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True, "reason": "new evidence",
        })
        observed = control.status({"task_id": "retry-rework", "principal": "thread-a"})
        resumed = control.record_delegation({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": reworked["state"]["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "reworked try", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(resumed["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_worker_question_bus_is_scoped_idempotent_and_resumable(self):
        state = self.init(task_id="questions")["state"]
        first = self.delegate(state, "questions", "discover", "general", parallel=True)
        second = self.delegate(first["state"], "questions", "discover", "explorer", parallel=True)
        publish_args = {
            "task_id": "questions",
            "principal": "thread-a",
            "attempt_id": first["attempt_id"],
            "submission_id": "need-decision",
            "question": "Which compatibility mode should I preserve?",
            "context": {"choices": ["strict", "legacy"]},
            "blocking": True,
        }
        published = control.publish_worker_question(publish_args)
        replay = control.publish_worker_question(publish_args)
        self.assertFalse(published["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["question"]["question_id"], published["question"]["question_id"])
        with self.assertRaisesRegex(ValueError, "different content"):
            control.publish_worker_question({**publish_args, "question": "A different question"})
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.list_worker_questions({"task_id": "questions", "principal": "intruder"})

        open_questions = control.list_worker_questions({"task_id": "questions", "principal": "thread-a", "status": "open"})
        self.assertEqual([item["question_id"] for item in open_questions["questions"]], [published["question"]["question_id"]])
        self.assertEqual(
            control.get_worker_question_updates({"task_id": "questions", "principal": "thread-a", "attempt_id": second["attempt_id"]})["updates"],
            [],
        )

        answer_args = {
            "task_id": "questions",
            "principal": "thread-a",
            "question_id": published["question"]["question_id"],
            "submission_id": "decision-1",
            "answer": "Preserve strict mode.",
            "resume_context": {"instruction": "Continue with strict compatibility", "source": "coordinator"},
        }
        answered = control.answer_worker_question(answer_args)
        answer_replay = control.answer_worker_question(answer_args)
        self.assertFalse(answered["idempotent"])
        self.assertTrue(answer_replay["idempotent"])
        updates = control.get_worker_question_updates({
            "task_id": "questions",
            "principal": "thread-a",
            "attempt_id": first["attempt_id"],
            "after_sequence": published["cursor"],
        })
        self.assertEqual([item["kind"] for item in updates["updates"]], ["question_answered"])
        self.assertEqual(updates["updates"][0]["resume_context"], answer_args["resume_context"])
        with self.assertRaisesRegex(ValueError, "different content"):
            control.answer_worker_question({**answer_args, "answer": "Use legacy mode."})

    def test_cortex_question_routes_workers_to_main_chat_with_flexible_answers(self):
        state = self.init(task_id="question-ui")["state"]
        delegated = self.delegate(state, "question-ui", "discover", "explorer")
        pending = control.cortex_question({
            "task_id": "question-ui", "principal": "thread-a", "attempt_id": delegated["attempt_id"],
            "submission_id": "explorer-question-1", "question": "Which paths should be changed?",
            "options": [
                {"option_id": "source_paths", "label": "src", "description": "Update source files"},
                {"option_id": "test_paths", "label": "tests", "description": "Update tests"},
            ],
            "multiple": True, "custom_label": "Additional direction", "context": {"reason": "scope"},
        })
        self.assertEqual(pending["status"], "pending_user_input")
        self.assertEqual(pending["ui"]["custom_label"], "Additional direction")
        question_id = pending["question_id"]
        listed = control.list_worker_questions({"task_id": "question-ui", "principal": "thread-a", "status": "open"})
        question = listed["questions"][0]
        self.assertEqual(question["options"][0]["label"], "src")
        self.assertTrue(question["multiple"])
        with mock.patch.object(control, "_request_mcp_elicitation", return_value=("accept", {"selections": ["source_paths", "test_paths"], "custom_response": {"image": {"path": "/tmp/shot.png"}}}, "elicitation-1")):
            answered = control.cortex_question({"task_id": "question-ui", "principal": "thread-a", "question_id": question_id})
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answer"]["option_ids"], ["source_paths", "test_paths"])
        self.assertEqual(answered["answer"]["custom_response"]["image"]["path"], "/tmp/shot.png")
        updates = control.get_worker_question_updates({"task_id": "question-ui", "principal": "thread-a", "attempt_id": delegated["attempt_id"]})
        self.assertEqual(updates["updates"][-1]["kind"], "question_answered")
        self.assertEqual(updates["updates"][-1]["answer_option_ids"], ["source_paths", "test_paths"])

    def test_cortex_question_form_always_puts_custom_field_last(self):
        single = control._question_form_schema(control._question_config({"header": "Pick one", "options": ["A", "B"]}))
        self.assertEqual(list(single["properties"]), ["selection", "custom_response"])
        multi = control._question_form_schema(control._question_config({"header": "Pick many", "options": ["A", "B"], "multiple": True}))
        self.assertEqual(multi["properties"]["selections"]["type"], "array")
        self.assertEqual(list(multi["properties"])[-1], "custom_response")

    def test_multiple_worker_questions_are_independent_and_ordered(self):
        state = self.init(task_id="question-concurrency")['state']
        first = self.delegate(state, "question-concurrency", "discover", "general", parallel=True)
        second = self.delegate(first["state"], "question-concurrency", "discover", "explorer", parallel=True)
        for attempt, number in ((first, "one"), (second, "two")):
            control.cortex_question({
                "task_id": "question-concurrency", "principal": "thread-a", "attempt_id": attempt["attempt_id"],
                "submission_id": f"question-{number}", "question": f"Choose for worker {number}",
                "options": ["keep", "change"],
            })
        listed = control.list_worker_questions({"task_id": "question-concurrency", "principal": "thread-a", "status": "open"})
        self.assertEqual(listed["open_count"], 2)
        self.assertEqual(listed["open_question_ids"], [item["question_id"] for item in listed["questions"]])
        self.assertNotEqual(listed["questions"][0]["attempt_id"], listed["questions"][1]["attempt_id"])
        for question in listed["questions"]:
            control.answer_worker_question({
                "task_id": "question-concurrency", "principal": "thread-a", "question_id": question["question_id"],
                "submission_id": f"answer-{question['question_id']}", "answer": {"selection": "keep"},
                "resume_context": {"source": "test"},
            })
        self.assertEqual(control.list_worker_questions({"task_id": "question-concurrency", "principal": "thread-a", "status": "open"})["open_count"], 0)

    def test_task_language_and_internal_english_worker_contract(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        created = control.init_task({"task_id": "language-contract", "objective": "Проверить язык", "classification_id": classified["classification_id"], "principal": "thread-a", "user_language": "ru"})
        self.assertEqual(created["state"]["user_language"], "ru")
        delegation = self.delegate(created["state"], "language-contract", "discover", "explorer")
        prompt = self.briefing_from_request(delegation["spawn_request"])
        self.assertIn("Internal worker protocol: English only", prompt)
        self.assertEqual(prompt.count("Emit English only in every message"), 1)
        self.assertIn("Treat non-English task text as input data", prompt)
        self.assertIn("Never address the user", prompt)
        self.assertIn("Do not repeat, translate, or mirror the user's language", prompt)
        self.assertNotIn("User-facing language:", prompt)
        self.assertNotIn("user_language", delegation)
        self.assertIn("Use only tools actually available in this worker context", prompt)
        self.assertIn("mcp__codebase_memory__list_projects", prompt)
        expected_project_key = control.codebase_memory_project_key_from_root(self.project)
        self.assertIn(f"use project key {expected_project_key!r} directly", prompt)
        self.assertIn("do not call `list_projects` before the first indexed query", prompt)
        self.assertIn("at most once and accept only an entry whose canonical root_path exactly matches", prompt)
        self.assertIn("prefer `get_architecture`, `search_graph`, `trace_path`, `detect_changes`", prompt)
        self.assertIn("Confirm consequential indexed claims in current source or tests", prompt)
        self.assertIn("you may call `index_repository` once", prompt)
        self.assertIn("do not loop on Codebase Memory setup", prompt)
        self.assertIn("REPORT_RECORDED report_ref=<report_id>", prompt)
        self.assertIn("do not paste or reproduce its JSON", prompt)

    def test_codebase_memory_project_key_matches_upstream_path_rule(self):
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/Users/dev/my-project"),
            "Users-dev-my-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root(r"C:\Users\dev\project"),
            "C-Users-dev-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/home///user/my project"),
            "home-user-my-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/Users/yunxin/Desktop/开发/后端/信租风控通后端"),
            "Users-yunxin-Desktop-e5bc80e58f91-e5908ee7abaf-"
            "e4bfa1e7a79fe9a38ee68ea7e9809ae5908ee7abaf",
        )
        long_key = control.codebase_memory_project_key_from_root("/Users/dev/" + "开" * 60 + "/alpha")
        self.assertEqual(len(long_key), 200)
        self.assertRegex(long_key, r"-[0-9a-f]{8}$")

    def test_composite_delegation_and_completion_fast_paths(self):
        self.init(task_id="composites")
        prepared = control.prepare_delegation({
            "task_id": "composites", "principal": "thread-a", "delegation": {
                "gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low",
                "objective": "Inspect the repository", "ownership": "Own discovery", "parallel": True,
                "allowed_paths": ["."], "acceptance_criteria": ["Publish findings"], "verification": ["Cite paths"],
            },
        })
        attempt_id = prepared["delegation"]["attempt_id"]
        completed = control.complete_attempt({
            "task_id": "composites", "principal": "thread-a", "attempt_id": attempt_id,
            "host_agent_id": "host-composite", "host_task_name": prepared["delegation"]["spawn_request"]["task_name"], "host_model": "gpt-5.6-luna",
            "host_reasoning_effort": "low", "status": "passed", "report": {
                "summary": "discovery complete", "findings": [], "questions": [], "changed_files": [],
                "tests": [], "evidence": ["source paths"], "uncertainty": [],
            },
        })
        self.assertTrue(completed["atomic"])
        self.assertEqual(completed["state"]["attempts"][-1]["status"], "passed")
        receipt = completed["report"]["receipt"]["receipt_id"]
        committed = control.commit_gate({
            "task_id": "composites", "principal": "thread-a", "gate": "discover", "mode": "verification",
            "attempt_id": attempt_id, "report_receipt": receipt, "summary": "verify discovery", "verification_id": "benign_success",
        })
        self.assertTrue(committed["recorded"])
        self.assertEqual(committed["state"]["current_gates"], ["implementation"])
        audited = control.close_audit({"task_id": "composites", "principal": "thread-a"})
        self.assertEqual(audited["report_count"], 1)

    def test_mutable_report_grants_are_not_part_of_the_runtime(self):
        self.assertNotIn("grant_report_context", control.TOOLS)
        state = self.init(task_id="no-report-grants", complexity="C2")["state"]
        delegated = self.delegate(state, "no-report-grants", "plan", "planner")
        report_root = self.ledger / "tasks/0001-no-report-grants/reports"
        self.assertFalse((report_root / "grants").exists())
        index = self.task_document(
            control.db_task_artifact_path(self.ledger, "no-report-grants"),
            f"report_delegation:{delegated['attempt_id']}",
        )
        self.assertNotIn("grant_ids", index)

    def test_commit_gate_retry_after_completed_transition_is_idempotent(self):
        self.init(task_id="commit-idempotent")
        delegated = self.delegate(control.status({"task_id": "commit-idempotent", "principal": "thread-a"})["state"], "commit-idempotent", "discover", "explorer")
        report = self.report("commit-idempotent", delegated["attempt_id"])
        first = control.commit_gate({
            "task_id": "commit-idempotent", "principal": "thread-a", "gate": "discover",
            "mode": "verification", "attempt_id": delegated["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"], "summary": "verify discovery",
            "verification_id": "benign_success",
        })
        retry = control.commit_gate({
            "task_id": "commit-idempotent", "principal": "thread-a", "gate": "discovery",
            "mode": "verification", "attempt_id": delegated["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"], "summary": "retry after timeout",
            "verification_id": "benign_success",
        })
        self.assertTrue(first["recorded"])
        self.assertTrue(retry["recorded"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["state"]["current_gates"], ["implementation"])

    def test_record_evidence_rejects_report_id_instead_of_owned_receipt(self):
        state = self.init(task_id="report-id-receipt", complexity="C2")["state"]
        delegated = self.delegate(state, "report-id-receipt", "plan", "planner")
        report = self.report("report-id-receipt", delegated["attempt_id"])
        with self.assertRaisesRegex(ValueError, "attempt-tied report receipt"):
            control.record_evidence({
                "task_id": "report-id-receipt",
                "principal": "thread-a",
                "expected_revision": delegated["state"]["revision"],
                "gate": "plan",
                "attempt_id": delegated["attempt_id"],
                "report_receipt": report["report"]["report_id"],
                "summary": "report-backed plan evidence",
            })

    def test_commit_gate_repeated_invalid_receipt_terminalizes_instead_of_hanging(self):
        state = self.init(task_id="receipt-circuit-breaker", complexity="C2")["state"]
        delegated = self.delegate(state, "receipt-circuit-breaker", "plan", "planner")
        results = []
        for _ in range(control.MAX_GATE_RECOVERY_FAILURES):
            results.append(control.commit_gate({
                "task_id": "receipt-circuit-breaker",
                "principal": "thread-a",
                "gate": "plan",
                "mode": "verification",
                "attempt_id": delegated["attempt_id"],
                "report_receipt": "not-a-real-receipt",
                "summary": "verify plan",
                "verification_id": "benign_success",
            }))
        self.assertEqual([item["recorded"] for item in results], [False] * control.MAX_GATE_RECOVERY_FAILURES)
        self.assertTrue(results[0]["recoverable"])
        self.assertTrue(results[1]["recoverable"])
        self.assertTrue(results[2]["terminal"])
        self.assertFalse(results[2]["recoverable"])
        self.assertEqual(results[2]["state"]["status"], "blocked")
        self.assertEqual(results[2]["next_action"], "create_handoff_and_resume_after_gate_repair")
        self.assertEqual(len(results[2]["state"]["recovery_events"]), control.MAX_GATE_RECOVERY_FAILURES)

    def test_complete_attempt_invalid_report_is_recoverable_and_can_be_corrected(self):
        self.init(task_id="attempt-recovery", complexity="C2")
        prepared = control.prepare_delegation({
            "task_id": "attempt-recovery", "principal": "thread-a", "delegation": {
                "gate": "plan", "agent": "planner", "task_kind": "planning", "risk": "moderate",
                "objective": "plan", "ownership": "Own plan", "allowed_paths": ["."],
                "acceptance_criteria": ["Publish a report"], "verification": ["Report evidence"],
            },
        })
        attempt_id = prepared["delegation"]["attempt_id"]
        bad = control.complete_attempt({
            "task_id": "attempt-recovery", "principal": "thread-a", "attempt_id": attempt_id,
            "host_agent_id": "host-attempt-recovery", "host_task_name": prepared["delegation"]["spawn_request"]["task_name"],
            "host_model": prepared["delegation"]["spawn_request"]["model"],
            "host_reasoning_effort": prepared["delegation"]["spawn_request"]["reasoning_effort"],
            "status": "passed", "report": {"summary": "missing required report fields"},
        })
        self.assertFalse(bad["recorded"])
        self.assertTrue(bad["recoverable"])
        self.assertEqual(bad["state"]["attempts"][0]["status"], "running")
        good = control.complete_attempt({
            "task_id": "attempt-recovery", "principal": "thread-a", "attempt_id": attempt_id,
            "host_agent_id": "host-attempt-recovery", "host_task_name": prepared["delegation"]["spawn_request"]["task_name"],
            "host_model": prepared["delegation"]["spawn_request"]["model"],
            "host_reasoning_effort": prepared["delegation"]["spawn_request"]["reasoning_effort"],
            "status": "passed", "submission_id": "corrected-report", "report": {
                "summary": "complete", "findings": [], "questions": [], "changed_files": [],
                "tests": [], "evidence": ["report"], "uncertainty": [],
            },
        })
        self.assertTrue(good["atomic"])
        self.assertEqual(good["state"]["attempts"][0]["status"], "passed")

    def test_prepare_delegations_rejects_mixed_gates(self):
        self.init(task_id="composite-batch")
        result = control.prepare_delegations({
            "task_id": "composite-batch", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "implementation", "agent": "general", "task_kind": "implementation", "risk": "moderate", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "batch_requires_one_gate")

    def test_prepare_delegations_returns_independent_spawn_requests(self):
        self.init(task_id="composite-success")
        result = control.prepare_delegations({
            "task_id": "composite-success", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        self.assertTrue(result["recorded"])
        self.assertEqual(len(result["spawn_requests"]), 2)
        self.assertEqual(len(set(result["attempts"])), 2)
        task_names = [item["task_name"] for item in result["spawn_requests"]]
        self.assertEqual(len(set(task_names)), 2)
        self.assertTrue(all(item.startswith("explorer_") for item in task_names))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9_]{1,80}", item) for item in task_names))
        self.assertTrue(all(item["profile"] == "explorer" for item in result["spawn_requests"]))

    def test_parallel_gate_wave_accepts_multiple_independent_gates(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1",
            "requirements": ["independent discovery and implementation checks"],
            "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover", "implementation"], ["review"]],
            "principal": "thread-a",
        })
        created = control.init_task({
            "task_id": "parallel-wave", "objective": "run independent gates concurrently",
            "classification_id": classified["classification_id"], "principal": "thread-a",
        })
        self.assertEqual(created["state"]["current_gates"], ["discover", "implementation"])
        prepared = control.prepare_delegations({
            "task_id": "parallel-wave", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "discover", "ownership": "discover", "allowed_paths": ["."], "acceptance_criteria": ["discover"], "verification": ["discover"]},
                {"gate": "implementation", "agent": "general", "task_kind": "implementation", "risk": "moderate", "parallel": True, "objective": "implement", "ownership": "implement", "allowed_paths": ["."], "acceptance_criteria": ["implement"], "verification": ["implement"]},
            ],
        })
        self.assertTrue(prepared["recorded"])
        self.assertEqual(prepared["gates"], ["discover", "implementation"])
        self.assertEqual({item["gate"] for item in prepared["state"]["attempts"]}, {"discover", "implementation"})

        implementation_evidence = control.record_evidence({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": prepared["state"]["revision"],
            "gate": "implementation", "summary": "implementation wave evidence",
        })
        implementation_passed = control.record_gate({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": implementation_evidence["state"]["revision"],
            "gate": "implementation", "outcome": "passed",
        })
        self.assertEqual(implementation_passed["state"]["current_gates"], ["discover"])

        discover_evidence = control.record_evidence({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": implementation_passed["state"]["revision"],
            "gate": "discover", "summary": "discovery wave evidence",
        })
        discover_passed = control.record_gate({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": discover_evidence["state"]["revision"],
            "gate": "discover", "outcome": "passed",
        })
        self.assertEqual(discover_passed["state"]["current_gates"], ["review"])
        self.assertEqual(discover_passed["state"]["current_gates"], ["review"])

    def test_reassessment_can_split_parallel_wave(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1", "requirements": ["independent checks"],
            "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover", "implementation"], ["review"]],
            "principal": "thread-a",
        })
        created = control.init_task({"task_id": "split-wave", "objective": "reassess waves", "classification_id": classified["classification_id"], "principal": "thread-a"})
        revised = control.reassess_pipeline({
            "task_id": "split-wave", "principal": "thread-a", "expected_revision": created["state"]["revision"],
            "signals": ["implementation now depends on discovery"], "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover"], ["implementation"], ["review"]],
            "intent": "resequence", "decision": "updated", "reason": "new dependency discovered", "apply": True,
        })
        self.assertTrue(revised["applied"])
        self.assertEqual(revised["state"]["parallel_groups"], [["discover"], ["implementation"], ["review"], ["documentation"], ["close"]])
        self.assertEqual(revised["state"]["current_gates"], ["discover"])

    def test_prepare_delegations_rolls_back_on_mid_batch_failure(self):
        self.init(task_id="composite-rollback")
        result = control.prepare_delegations({
            "task_id": "composite-rollback", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"], "context_report_ids": ["report-does-not-exist"]},
            ],
        })
        self.assertFalse(result["recorded"])
        self.assertTrue(result["atomic"])
        self.assertEqual(result["prepared"], [])
        state = control.status({"task_id": "composite-rollback", "principal": "thread-a"})["state"]
        self.assertEqual(state["attempts"], [])

    def test_v3_minimal_start_defaults_to_c2_and_hides_durable_ids(self):
        started = self.v3_start("minimal relative orchestration")
        self.assertTrue(started["ok"])
        self.assertEqual(started["schema"], control.PUBLIC_ORCHESTRATION_SCHEMA)
        self.assertEqual(started["step"], 1)
        self.assertEqual(len(started["dispatches"]), 1)
        self.assertEqual(set(started["dispatches"][0]), {
            "worker", "phase", "profile", "display_name", "capability", "sandbox",
            "selection_reason", "call", "arguments", "dispatch_ref", "briefing_path",
            "briefing_digest",
        })
        self.assertEqual(started["dispatches"][0]["phase"], "discover")
        self.assertEqual(started["dispatches"][0]["profile"], "explorer")
        self.assertEqual(started["dispatches"][0]["sandbox"], "read-only")
        self.assertIn("canonical automatic owner", started["dispatches"][0]["selection_reason"])
        self.assertIn("COORDINATOR LOCK", started["next_action"])
        self.assertIn("remain idle", started["next_action"])
        self.assertIn("All project operations belong to workers", started["next_action"])
        self.assertEqual(started["dispatches"][0]["arguments"].get("model"), None)
        self.assertEqual(started["dispatches"][0]["arguments"]["reasoning_effort"], "medium")
        self.assertNotIn("task_id", started)
        self.assertNotIn("wave_id", started)
        tasks = list((self.ledger / "tasks").iterdir())
        definition = self.task_definition(tasks[0])
        self.assertEqual(definition["complexity"], "C2")
        self.assertEqual(definition["plan_approval"], "required")

    def test_fresh_orchestration_uses_only_canonical_ledger_artifacts(self):
        started = self.v3_start(
            "audit the generated ledger layout",
            waves=[
                {"workers": [{"phase": "discover"}]},
                {"workers": [{"phase": "plan"}]},
            ],
            complexity="C1",
            plan_approval="auto",
        )
        continued = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("canonical plan recorded")),
        })
        self.assertTrue(continued["ok"])

        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        loaded = control.db_load_task(self.ledger, state["task_id"])
        plan = loaded[2] if loaded is not None else None
        registry = control._operation_registry(self.ledger)
        self.assertEqual(state["schema"], "cortex/v8")
        self.assertNotIn("current_gate", state)
        self.assertIsInstance(plan, dict)
        self.assertEqual(plan["schema"], control.ORCHESTRATION_PLAN_SCHEMA)
        self.assertEqual(registry["schema"], "cortex/orchestration/v5")
        self.assertEqual(
            [item["version"] for item in control.db_migration_history(self.ledger)],
            list(range(1, control.DATABASE_SCHEMA_VERSION + 1)),
        )
        self.assertFalse((self.ledger / "v3-operations.json").exists())
        self.assertFalse((task_dir / "status-receipts").exists())
        self.assertFalse(any(task_dir.rglob("*-snapshot.json")))
        self.assertFalse(any((task_dir / "handoffs").glob("*-manifest.json")))

        allowed = [
            re.compile(r"^cortex\.db(?:-(?:wal|shm))?$"),
            re.compile(r"^\.state\.lock$"),
            re.compile(r"^tasks/[^/]+/journal\.md$"),
            re.compile(r"^tasks/[^/]+/evidence/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/delegations/[^/]+\.briefing\.md$"),
            re.compile(r"^tasks/[^/]+/reports/(records|receipts|consumptions)/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/reports/markdown/[^/]+\.md$"),
            re.compile(r"^tasks/[^/]+/planning/revisions/[^/]+/(manifest\.json|overview\.md|packages/[^/]+\.json)$"),
            re.compile(r"^tasks/[^/]+/handoffs/(?:manifests/)?[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/(?:\.lifecycle-events\.lock|lifecycle-events-meta\.json|lifecycle-events\.jsonl)$"),
            re.compile(r"^lanes/[^/]+/journal\.md$"),
        ]
        files = [path.relative_to(self.ledger).as_posix() for path in self.ledger.rglob("*") if path.is_file()]
        self.assertFalse(any("v3" in path.lower() or "v7" in path.lower() for path in files))
        unknown = [path for path in files if not any(pattern.fullmatch(path) for pattern in allowed)]
        self.assertEqual(unknown, [])

    def test_v3_automatic_prompt_uses_gate_briefing_and_task_context(self):
        started = self.v3_start(
            "add a durable worker prompt contract",
            requirements=["Preserve the public facade"],
            acceptance_criteria=["Every agent receives the overall outcome"],
            scope=["plugins/cortex"],
            verification=["Run prompt contract tests"],
            budget="No external writes",
            pause_conditions=["A public schema change becomes necessary"],
        )
        prompt = self.briefing_from_response(started)
        self.assertIn("## Role playbook", prompt)
        self.assertIn("## Assignment data", prompt)
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["user_request"], "add a durable worker prompt contract")
        self.assertEqual(assignment["requirements"], ["Preserve the public facade"])
        self.assertEqual(assignment["scope"], ["plugins/cortex"])
        self.assertEqual(assignment["task_acceptance_criteria"], ["Every agent receives the overall outcome"])
        self.assertIn("Identify entry points", assignment["gate_acceptance_criteria"][0])
        self.assertEqual(assignment["task_verification"], ["Run prompt contract tests"])
        self.assertIn("Judge only this gate; unfinished downstream task outcomes are not blockers", prompt)
        self.assertIn("Optional `gate_result`: pass findings=[]", prompt)
        self.assertIn("no info entries or `closure` except review/close", prompt)
        self.assertEqual(assignment["pause_conditions"], ["A public schema change becomes necessary"])
        self.assertEqual(assignment["budget"], "No external writes")
        self.assertNotIn("Complete and report the discover gate", prompt)
        self.assertNotIn("## Canonical Cortex team", prompt)

    def test_scheduler_contract_is_complete_in_the_immutable_worker_briefing(self):
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "contract.md").write_text("# Verified contract\n", encoding="utf-8")
        started = self.v3_start(
            "transport every scheduler field into the worker briefing",
            complexity="C1",
            requirements=["Preserve the public facade."],
            acceptance_criteria=["The worker receives the complete assignment."],
            verification=["Inspect the immutable dispatch briefing."],
            waves=[{"workers": [{
                "phase": "discover",
                "profile": "explorer",
                "objective": "Trace the constructor-to-worker data path.",
                "paths": ["plugins/cortex/scripts"],
                "context_files": ["docs/contract.md"],
                "acceptance": ["Every scheduler-owned assignment field is present."],
                "verification": ["Compare the package and rendered briefing."],
                "model": "luna",
                "effort": "high",
            }]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        attempt = state["attempts"][0]
        package = self.task_document(task_dir, f"dispatch:{attempt['attempt_id']}")
        prompt = self.briefing_from_response(started)

        self.assertEqual(package["gate"], "discover")
        self.assertEqual(package["agent"], "explorer")
        self.assertEqual(package["objective"], "Trace the constructor-to-worker data path.")
        self.assertEqual(package["allowed_paths"], ["plugins/cortex/scripts"])
        self.assertEqual(package["context_files"], ["docs/contract.md"])
        self.assertEqual(package["acceptance_criteria"], ["Every scheduler-owned assignment field is present."])
        self.assertEqual(package["verification"], ["Compare the package and rendered briefing."])
        self.assertEqual(package["depends_on_phases"], [])
        self.assertIn("selection_reason", package)
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["phase"], "discover")
        self.assertEqual(assignment["profile"], "explorer")
        self.assertEqual(assignment["mission"], "Trace the constructor-to-worker data path.")
        self.assertEqual(assignment["allowed_paths"], ["plugins/cortex/scripts"])
        self.assertEqual(assignment["context_files"], ["docs/contract.md"])
        self.assertEqual(assignment["gate_acceptance_criteria"], ["Every scheduler-owned assignment field is present."])
        self.assertEqual(assignment["gate_verification"], ["Compare the package and rendered briefing."])
        self.assertEqual(assignment["phase_dependencies"], [])
        self.assertTrue(assignment["selection_rationale"])
        self.assertNotIn("Model route and reasoning effort:", prompt)
        bootstrap = started["dispatches"][0]["arguments"]["message"]
        self.assertIn(started["dispatches"][0]["dispatch_ref"], bootstrap)
        self.assertIn(started["dispatches"][0]["briefing_digest"], bootstrap)
        self.assertNotIn("Trace the constructor-to-worker data path.", bootstrap)

    def test_implementation_router_prefers_narrow_specialists_and_conservative_fallback(self):
        cases = {
            "Build a React frontend and an API backend for this workflow": "fullstack_dev",
            "Implement an Android screen in Jetpack Compose": "mobile_dev",
            "Update Docker and GitHub Actions deployment": "devops_engineer",
            "Implement an ETL backfill into the data warehouse": "data_engineer",
            "Build a data pipeline for warehouse imports": "data_engineer",
            "Reproduce the failing test, prove root cause, and fix it": "debugger",
            "Refactor the module without changing behavior": "refactorer",
            "Implement a browser component and CSS states": "frontend_dev",
            "Add a server API endpoint and business logic": "backend_dev",
            "Исправь почему сервис падает и найди корневую причину": "debugger",
            "Implement the bounded requested change": "general",
        }
        for objective, expected in cases.items():
            with self.subTest(objective=objective):
                selected = control.select_implementation_profile({"objective": objective})
                self.assertEqual(selected["profile"], expected)
                self.assertTrue(selected["reason"])
                self.assertIn(selected["source"], {"bounded_task_signals", "conservative_fallback"})

    def test_automatic_waves_embed_specialist_implementation_rationale(self):
        waves = control._v3_auto_waves({
            "objective": "Add a browser UI backed by a server API",
            "requirements": [],
            "complexity": "C2",
        })
        implementation = next(
            spec
            for wave in waves
            for spec in wave["delegations"]
            if spec["gate"] == "implementation"
        )
        self.assertEqual(implementation["agent"], "fullstack_dev")
        self.assertIn("both browser-facing and server-facing", implementation["selection_reason"])

    def test_automatic_pipeline_reads_the_full_task_and_multilingual_specialist_signals(self):
        cases = {
            "Audit authorization security before changing the API": "security",
            "Проверь доступность интерфейса с клавиатуры": "accessibility",
            "Оптимизируй производительность и задержку сервиса": "performance",
            "Спроектируй миграцию схемы базы данных": "database_architecture",
        }
        for objective, expected_gate in cases.items():
            with self.subTest(objective=objective):
                waves = control._v3_auto_waves({
                    "objective": objective,
                    "requirements": [],
                    "complexity": "C2",
                })
                gates = {spec["gate"] for wave in waves for spec in wave["delegations"]}
                self.assertIn(expected_gate, gates)

    def test_v3_harvest_routes_use_the_complete_census_pipeline(self):
        expected = ["scope", "discover", "architecture", "plan", "documentation", "review", "close"]
        for objective in (
            "Harvest exhaustive repository knowledge documentation",
            "Run harvest-refresh with a complete feature census",
        ):
            with self.subTest(objective=objective):
                waves = control._v3_auto_waves({
                    "objective": objective,
                    "requirements": [],
                    "complexity": "C2",
                })
                self.assertEqual(
                    [spec["gate"] for wave in waves for spec in wave["delegations"]],
                    expected,
                )
                started = self.v3_start(objective)
                self.assertTrue(started["ok"])
                for dispatch in started["dispatches"]:
                    self.assertEqual(dispatch["call"], "spawn_agent")
                    self.assertRegex(dispatch["arguments"]["task_name"], r"^[a-z0-9_]{1,80}$")

    def test_v2_base_pipelines_and_specialist_placement_are_evidence_first(self):
        self.assertEqual(
            control.BASE_PIPELINES,
            {
                "C1": ["discover", "implementation", "review", "close"],
                "C2": ["discover", "plan", "implementation", "qa", "review", "documentation", "close"],
                "C3": ["scope", "discover", "plan", "implementation", "qa", "review", "documentation", "close"],
            },
        )
        classified = control.classify({
            "complexity": "C3",
            "requirements": [
                "architecture and database schema design",
                "UX interaction design",
                "security audit, performance profiling, and accessibility compliance",
            ],
        })
        pipeline = classified["pipeline"]
        self.assertLess(pipeline.index("scope"), pipeline.index("discover"))
        for gate in ("architecture", "database_architecture", "ux"):
            self.assertLess(pipeline.index("discover"), pipeline.index(gate))
            self.assertLess(pipeline.index(gate), pipeline.index("plan"))
        for gate in ("security", "performance", "accessibility"):
            self.assertLess(pipeline.index("implementation"), pipeline.index(gate))
            self.assertLess(pipeline.index(gate), pipeline.index("review"))

    def test_v3_harvest_never_requires_post_plan_user_approval(self):
        started = self.v3_start("$cortex:orchestrator harvest", complexity="C3")
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertEqual(task["plan_approval"], "auto")

    def test_v3_profile_schema_exposes_exact_roster_and_rejects_wrong_gate_owner(self):
        self.assertEqual(set(control.V3_WORKER_SCHEMA["properties"]["profile"]["enum"]), control.AGENTS)
        rejected = self.v3_start(
            "invalid planner owner",
            waves=[{"workers": [{"phase": "plan", "profile": "backend_dev"}]}],
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("cannot own phase", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_requires_an_observable_task_result_contract_before_writing_ledger(self):
        rejected = control.start_orchestration({
            "project_root": str(self.project),
            "task": {"user_request": "implement the requested behavior", "complexity": "C1"},
            "waves": [{"workers": [{"phase": "implementation"}]}],
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "start_validation_failed")
        self.assertIn("task.acceptance_criteria", rejected["diagnostics"][0]["message"])
        self.assertIn("task.verification", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_incompatible_registry_blocks_start_without_selecting_an_existing_task(self):
        existing = self.v3_start("existing task must remain isolated")
        self.assertTrue(existing["ok"])

        unscoped = control.manage_orchestration({
            "project_root": str(self.project), "intent": "inspect",
        })
        self.assertFalse(unscoped["ok"])
        self.assertEqual(unscoped["code"], "task_ref_required")
        self.assertNotIn("task_ref", unscoped)
        self.assertIn("Do not inspect, list, infer, or select another task", unscoped["next_action"])

        control.db_put_global(
            self.ledger,
            "operation_registry",
            {"schema": "cortex/orchestration/v0", "starts": {}, "tasks": {}},
        )
        blocked = self.v3_start("new task must never attach to the existing task")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["outcome"], "blocked")
        self.assertEqual(blocked["code"], "start_state_incompatible")
        self.assertFalse(blocked["retryable"])
        self.assertFalse(blocked["task_created"])
        self.assertNotIn("task_ref", blocked)
        self.assertIn("Cortex did not create a task", blocked["next_action"])
        self.assertIn("Do not call manage_orchestration", blocked["next_action"])

        recovery = control.manage_orchestration({
            "project_root": str(self.project), "intent": "inspect",
        })
        self.assertFalse(recovery["ok"])
        self.assertEqual(recovery["code"], "task_ref_required")
        self.assertNotIn("task_ref", recovery)

    def test_planner_rejects_microtasks_without_acceptance_and_verification(self):
        started = self.v3_start("plan a bounded change", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        planning = self.v3_planning()
        planning["work_packages"][0]["microtasks"][0]["verification"] = []
        rejected = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("planner omitted microtask verification")
            ),
            "planning": planning,
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("requires non-empty acceptance_criteria and verification", rejected["diagnostics"][0]["message"])

    def test_public_result_validation_reconciles_implementation_claims_with_attempt_delta(self):
        started = self.v3_start(
            "implement one observable file change",
            complexity="C1",
            waves=[{"workers": [{"phase": "implementation"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
        }
        no_change = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(
                attempt, self.v3_report("claimed success without an artifact")
            ),
        })
        self.assertFalse(no_change["ok"])
        self.assertIn("requires at least one real changed file", no_change["diagnostics"][0]["message"])

        unsupported_report = self.v3_report("claimed a file that was not changed")
        unsupported_report["changed_files"] = ["implemented.txt"]
        unsupported_report = self._report_with_briefing(attempt, unsupported_report)
        unsupported = control.publish_worker_report({**identity, "report": unsupported_report})
        self.assertFalse(unsupported["ok"])
        self.assertIn("not changed relative to this worker attempt baseline", unsupported["diagnostics"][0]["message"])

        changed_path = self.project / "implemented.txt"
        changed_path.write_text("observable result\n", encoding="utf-8")
        extra_path = self.project / "also-implemented.txt"
        extra_path.write_text("second observable result\n", encoding="utf-8")
        incomplete_report = self.v3_report("omitted one observed file change")
        incomplete_report["changed_files"] = ["implemented.txt"]
        incomplete_report = self._report_with_briefing(attempt, incomplete_report)
        incomplete = control.publish_worker_report({**identity, "report": incomplete_report})
        self.assertFalse(incomplete["ok"])
        self.assertIn("omit observed changes", incomplete["diagnostics"][0]["message"])

        accepted_report = self.v3_report("implemented every observed file change")
        accepted_report["changed_files"] = ["implemented.txt", "also-implemented.txt"]
        accepted_report = self._report_with_briefing(attempt, accepted_report)
        accepted = control.publish_worker_report({**identity, "report": accepted_report})
        self.assertTrue(accepted["ok"])
        self.reconcile_projections(worker_id="result-validation-test")
        record = json.loads(
            (task_dir / "reports/records" / f"{accepted['report_ref']}.json").read_text(encoding="utf-8")
        )
        validation = record["result_validation"]
        self.assertEqual(validation["schema"], control.RESULT_VALIDATION_SCHEMA)
        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["artifacts"]["reported_change_count"], 2)

    def test_public_result_validation_rejects_unstructured_checks_and_claimed_read_only_writes_but_tolerates_concurrency(self):
        review = self.v3_start(
            "review the current behavior",
            complexity="C1",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("review used an unstructured check claim")
        report["tests"] = ["tests passed"]
        report = self._report_with_briefing(attempt, report)
        rejected_check = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"], "report": report,
        })
        self.assertFalse(rejected_check["ok"])
        self.assertIn("must contain exactly command, cwd, exit_code, and evidence", rejected_check["diagnostics"][0]["message"])

        changed_path = self.project / "review-write.txt"
        changed_path.write_text("unexpected review write\n", encoding="utf-8")
        stealth_report = self.v3_report("review concealed a forbidden write")
        stealth_report = self._report_with_briefing(attempt, stealth_report)
        concurrent_change = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"], "report": stealth_report,
        })
        self.assertTrue(concurrent_change["ok"], concurrent_change)
        concurrent_record, _ = control.read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{concurrent_change['report_ref']}.json",
            kinds={"worker_report"},
        )
        self.assertEqual(concurrent_record["result_validation"]["artifacts"]["concurrent_change_count"], 1)

        write_report = self.v3_report("review reported a forbidden write")
        write_report["changed_files"] = ["review-write.txt"]
        write_report = self._report_with_briefing(attempt, write_report)
        rejected_write = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"], "report": write_report,
        })
        self.assertFalse(rejected_write["ok"])
        self.assertIn("read-only result gate", rejected_write["diagnostics"][0]["message"])

    def test_gate_rejects_a_report_without_server_result_validation_receipt(self):
        started = self.v3_start("inspect the bounded surface", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        raw = control.record_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "attempt_id": attempt["attempt_id"],
            "report": self.v3_report("legacy report without result validation"),
        })
        rejected = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": raw["report"]["report_id"]}],
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("server-validated result contract", rejected["diagnostics"][0]["message"])

    def test_v3_explicit_worker_contract_overrides_gate_defaults_only(self):
        started = self.v3_start(
            "explicit contract",
            waves=[{"workers": [{
                "phase": "plan", "objective": "Plan the exact adapter change",
                "acceptance": ["Adapter plan is decision complete"],
                "verification": ["Cite adapter tests"],
            }]}],
            acceptance_criteria=["Public behavior remains compatible"],
        )
        prompt = self.briefing_from_response(started)
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["mission"], "Plan the exact adapter change")
        self.assertEqual(assignment["gate_acceptance_criteria"], ["Adapter plan is decision complete"])
        self.assertEqual(assignment["gate_verification"], ["Cite adapter tests"])
        self.assertEqual(assignment["task_acceptance_criteria"], ["Public behavior remains compatible"])
        self.assertNotIn("Produce a decision-complete implementation plan", prompt)

    def test_v3_repeated_exact_start_is_idempotent_but_changed_work_creates_a_new_task(self):
        first = self.v3_start("stable objective", waves=[{"workers": [{"phase": "discover"}]}])
        replay = self.v3_start("stable objective", waves=[{"workers": [{"phase": "discover"}]}])
        changed = self.v3_start("stable objective complete end to end", waves=[{"workers": [{"phase": "discover"}]}])
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["task_ref"], first["task_ref"])
        self.assertTrue(changed["ok"])
        self.assertNotEqual(changed["task_ref"], first["task_ref"])
        self.assertEqual(len(list((self.ledger / "tasks").iterdir())), 2)
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertIn("Do not invoke or repeat any worker dispatch", replay["next_action"])

    def test_v1_task_without_pipeline_contract_version_resumes_without_migration_or_duplicate_dispatch(self):
        started = self.v3_start(
            "resume a persisted v1 task",
            complexity="C1",
            waves=[
                {"workers": [{"phase": "discover"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        original_pipeline = list(state["current_pipeline"])
        original_attempt_id = state["attempts"][0]["attempt_id"]
        state.pop("pipeline_contract_version", None)
        self.write_task_state(state)

        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["dispatches"][0]["arguments"]["task_name"], started["dispatches"][0]["arguments"]["task_name"])
        after = self.task_state(task_dir)
        self.assertNotIn("pipeline_contract_version", after)
        self.assertEqual(after["current_pipeline"], original_pipeline)
        self.assertEqual([item["attempt_id"] for item in after["attempts"]], [original_attempt_id])

        replay = self.v3_start(
            "resume a persisted v1 task",
            complexity="C1",
            waves=[
                {"workers": [{"phase": "discover"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])

    def test_v3_same_user_request_cannot_duplicate_active_task_when_coordinator_metadata_changes(self):
        request = "$cortex:orchestrator harvest"
        first = control.start_orchestration({
            "project_root": str(self.project),
            "task": {"user_request": request, "user_language": "ru", "complexity": "C3"},
            "waves": [{"workers": [{"phase": "plan"}]}],
        })
        replay = control.start_orchestration({
            "project_root": str(self.project),
            "task": {"user_request": request, "language": "English", "complexity": "C2"},
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertEqual(replay["task_ref"], first["task_ref"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertEqual(len(list((self.ledger / "tasks").iterdir())), 1)

    def test_v3_worker_question_pauses_report_and_continue_then_resumes_same_attempt(self):
        started = self.v3_start("underspecified product request", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }
        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Which product outcome should this landing page optimize for?",
            "header": "Primary goal",
            "options": ["Sales", "Lead generation"],
            "context": {"reason": "The repository cannot establish desired user intent."},
        })
        self.assertEqual(asked["outcome"], "question_recorded")
        question_ref = asked["question_ref"]
        rejected_report = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(attempt, self.v3_report("premature")),
        })
        self.assertFalse(rejected_report["ok"])
        self.assertEqual(rejected_report["code"], "blocking_question_open")
        blocked = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"report_ref": "report-not-recorded"}],
        })
        self.assertFalse(blocked["ok"])
        self.assertIn(question_ref, blocked["diagnostics"][0]["message"])
        answered = control.answer_worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "question_id": question_ref,
            "submission_id": "user-answer-primary-goal",
            "answer": "Lead generation",
            "resume_context": {"source": "main_chat", "same_attempt": attempt["attempt_id"]},
        })
        self.assertEqual(answered["question"]["status"], "answered")
        polled = control.worker_question({**identity, "action": "poll", "question_ref": question_ref})
        self.assertEqual(polled["outcome"], "question_answered")
        self.assertEqual(polled["answer_text"], "Lead generation")
        after = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(after["attempts"][0]["attempt_id"], attempt["attempt_id"])
        published = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(attempt, self.v3_report("planned after answer")),
            "planning": self.v3_planning(),
        })
        self.assertTrue(published["ok"])

    def test_v3_worker_outputs_must_be_english_while_main_question_projection_can_be_localized(self):
        started = self.v3_start(
            "Проверь локализацию внутренних сообщений",
            user_language="ru",
            waves=[{"workers": [{"phase": "plan"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }
        rejected_report = control.publish_worker_report({
            **identity,
            "report": self.v3_report("Отчёт worker не должен быть на русском"),
        })
        self.assertFalse(rejected_report["ok"])
        self.assertEqual(rejected_report["code"], "worker_output_language_violation")
        rejected_question = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Какой результат нужен пользователю?",
        })
        self.assertFalse(rejected_question["ok"])
        self.assertEqual(rejected_question["outcome"], "needs_correction")
        self.assertTrue(rejected_question["retryable"])
        self.assertFalse(rejected_question["attempt_budget_consumed"])
        self.assertIn("worker question must be English-only", rejected_question["diagnostics"][0]["message"])

        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Which result should the user receive?",
            "header": "Desired result",
            "options": ["Summary", "Detailed report"],
        })
        with mock.patch.object(control, "_request_mcp_elicitation") as forbidden_english_ui:
            missing_localization = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["question_ref"]},
            })
        self.assertFalse(missing_localization["ok"])
        self.assertIn("non-English user questions require localized_question", missing_localization["diagnostics"][0]["message"])
        forbidden_english_ui.assert_not_called()
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"selection": "Краткая сводка", "custom_response": ""}, "localized-question-1"),
        ) as elicitation:
            managed = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {
                    "question_ref": asked["question_ref"],
                    "localized_question": "Какой результат должен получить пользователь?",
                    "localized_header": "Нужный результат",
                    "localized_options": ["Краткая сводка", "Подробный отчёт"],
                    "localized_custom_label": "Свой вариант",
                },
            })
        self.assertTrue(managed["ok"])
        self.assertEqual(managed["outcome"], "question_answered")
        self.assertEqual(elicitation.call_args.args[0], "Какой результат должен получить пользователь?")
        durable = control.list_worker_questions({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "thread_id": state["thread_id"],
        })["questions"][0]
        self.assertEqual(durable["question"], "Which result should the user receive?")
        self.assertEqual(durable["header"], "Desired result")

    def test_localized_question_translation_uses_public_answer_en_without_reopening_ui(self):
        started = self.v3_start(
            "Проверь перевод ответа на вопрос",
            user_language="ru",
            waves=[{"workers": [{"phase": "plan"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }
        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Which constraint should guide the implementation?",
        })
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"custom_response": "Нужно сохранить обратную совместимость."}, "translation-question-1"),
        ) as elicitation:
            awaiting = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {
                    "question_ref": asked["question_ref"],
                    "localized_question": "Какое ограничение должно направлять реализацию?",
                },
            })
            self.assertEqual(awaiting["outcome"], "awaiting_translation")
            request = awaiting["translation_request"]
            self.assertEqual(request["intent"], "question")
            self.assertEqual(request["payload"]["question_ref"], asked["question_ref"])
            self.assertEqual(request["payload"]["answer"]["custom_response"], "Нужно сохранить обратную совместимость.")
            completed = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": request["intent"],
                "payload": {
                    **request["payload"],
                    "answer_en": "Preserve backwards compatibility.",
                },
            })
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(completed["outcome"], "question_answered")
        self.assertEqual(elicitation.call_count, 1)
        self.assertIn("plugin source/cache", awaiting["next_action"])
        answer = control.worker_question({
            **identity,
            "action": "poll",
            "question_ref": asked["question_ref"],
        })
        self.assertEqual(answer["answer_text"], "Preserve backwards compatibility.")

    def test_russian_plan_approval_uses_russian_native_question_copy(self):
        started = self.v3_start(
            "Утверди план перед реализацией",
            user_language="ru",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        with mock.patch.object(control, "_request_mcp_elicitation") as elicitation:
            cancelled = control.manage_orchestration({
                "project_root": str(self.project), "task_ref": started["task_ref"],
                "intent": "plan_approval", "payload": {"decision": "prompt"},
            })
        self.assertEqual(cancelled["outcome"], "awaiting_plan_approval")
        self.assertFalse(elicitation.called)
        interaction = cancelled["plan_approval_interaction"]
        self.assertEqual(interaction["prompt"], "Утвердить завершённый план?")
        self.assertEqual(interaction["title"], "Проверка плана")
        self.assertEqual([item["label"] for item in interaction["actions"]], ["Утвердить", "Отмена"])

    def test_v3_question_ref_opens_native_ui_once_without_coordinator_identity(self):
        started = self.v3_start("underspecified product request", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }
        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Which outcome should the landing page prioritize?",
            "header": "Primary goal",
            "options": ["Lead generation", "Direct sales"],
        })
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"selection": "Lead generation", "custom_response": ""}, "native-question-1"),
        ) as elicitation:
            managed = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["question_ref"]},
            })
        self.assertTrue(managed["ok"])
        self.assertEqual(managed["outcome"], "question_answered")
        self.assertEqual(managed["result"]["status"], "answered")
        self.assertIn("followup_task", managed["next_action"])
        elicitation.assert_called_once()

        # A duplicate coordinator call is an idempotent receipt and must not
        # open a second native question UI.
        with mock.patch.object(control, "_request_mcp_elicitation") as repeated_ui:
            replay = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["question_ref"]},
            })
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["result"]["status"], "answered")
        repeated_ui.assert_not_called()
        polled = control.worker_question({**identity, "action": "poll", "question_ref": asked["question_ref"]})
        self.assertIn("Lead generation", polled["answer_text"])

    def test_v3_question_management_rejects_guessed_identity_and_plain_text_fallback(self):
        started = self.v3_start("underspecified product request", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        asked = control.worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "action": "ask",
            "question": "Keep the current product or replace it?",
        })
        rejected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {"question_ref": asked["question_ref"], "principal": "guessed-root"},
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("owns lifecycle identity", rejected["diagnostics"][0]["message"])

        with mock.patch.object(control, "_request_mcp_elicitation", side_effect=RuntimeError("host has no elicitation")):
            unavailable = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["question_ref"]},
            })
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["code"], "host_question_unavailable")
        self.assertIn("must not collect or fabricate the answer", unavailable["next_action"])
        question = self.task_document(task_dir, f"question:{asked['question_ref']}")
        self.assertEqual(question["status"], "open")

        # A temporary host-capability failure is retryable rather than a
        # committed replay.  The same compact coordinator call must try the
        # native UI again when elicitation becomes available.
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"selection": "Keep it", "custom_response": ""}, "native-question-2"),
        ) as retried_ui:
            retried = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["question_ref"]},
            })
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["outcome"], "question_answered")
        retried_ui.assert_called_once()

    def test_v3_exact_user_request_rejects_coordinator_expansion_before_ledger_write(self):
        rejected = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "создай лендинг",
                "objective": "Create a polished responsive sales landing page with an accessible CTA.",
                "complexity": "C2",
            },
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "start_validation_failed")
        self.assertIn("must exactly match", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_underspecified_product_surface_requires_answered_question_before_plan_report(self):
        started = self.v3_start("создай лендинг", waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"])
        briefing = self.briefing_from_response(started)
        self.assertIn("Cortex intent preflight: BLOCKING", briefing)
        self.assertIn("## Assignment data", briefing)
        assignment = json.loads(briefing.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["user_request"], "создай лендинг")
        self.assertIn("idle and resumable", briefing)
        self.assertIn("followup_task", briefing)
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(task["user_request"], "создай лендинг")
        self.assertEqual(task["objective"], "создай лендинг")
        self.assertTrue(task["intent_clarification_required"])
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": "planner",
        }

        escaped = self.v3_report("planner tried to assume the product intent")
        escaped["questions"] = ["Which audience and outcome should the landing page target?"]
        escaped = self._report_with_briefing(attempt, escaped)
        rejected_questions = control.publish_worker_report({**identity, "report": escaped})
        self.assertFalse(rejected_questions["ok"])
        self.assertEqual(rejected_questions["code"], "unresolved_report_questions")

        rejected_empty = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(attempt, self.v3_report("empty escape")),
        })
        self.assertFalse(rejected_empty["ok"])
        self.assertEqual(rejected_empty["code"], "intent_clarification_required")
        rejected_inline = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"report": self.v3_report("legacy inline escape")}],
        })
        self.assertFalse(rejected_inline["ok"])
        self.assertIn("unsupported result fields: report", rejected_inline["diagnostics"][0]["message"])

        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "What outcome and audience should this landing page prioritize?",
            "options": ["Lead generation", "Direct sales", "Product explanation"],
        })
        control.answer_worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "question_id": asked["question_ref"],
            "submission_id": "answer-landing-intent",
            "answer": "Lead generation for B2B sales teams; preserve the existing brand.",
            "resume_context": {"source": "main_chat", "same_attempt": attempt["attempt_id"]},
        })
        polled = control.worker_question({**identity, "action": "poll", "question_ref": asked["question_ref"]})
        self.assertEqual(polled["outcome"], "question_answered")
        accepted = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(attempt, self.v3_report("intent answered")),
            "planning": self.v3_planning(),
        })
        self.assertTrue(accepted["ok"])
        persisted = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": accepted["report_ref"],
        })
        self.assertEqual(len(persisted["resolved_user_decisions"]), 1)
        self.assertEqual(
            persisted["resolved_user_decisions"][0]["question_en"],
            "What outcome and audience should this landing page prioritize?",
        )
        self.assertIn("Lead generation for B2B sales teams", persisted["resolved_user_decisions"][0]["answer_en"])

    def test_v3_desktop_skill_link_is_canonicalized_before_task_persistence_and_labeling(self):
        request = (
            "[$cortex:orchestrator](/opt/cortex-test/.codex/plugins/cache/cortex/cortex/4.0.0/skills/"
            "orchestrator/SKILL.md) создай лендинг"
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertEqual(task["user_request"], "$cortex:orchestrator создай лендинг")
        self.assertEqual(task["objective"], "$cortex:orchestrator создай лендинг")
        self.assertRegex(task_dir.name, r"^0001-task-[0-9a-f]{8}$")
        self.assertNotIn("home", task_dir.name)
        self.assertNotIn("plugins", task_dir.name)
        self.assertNotIn("SKILL.md", json.dumps(task))
        briefing = self.briefing_from_response(started)
        self.assertNotIn("plugins/cache", briefing)
        self.assertTrue(task["intent_clarification_required"])
        self.assertIn("Cortex intent preflight: BLOCKING", briefing)

    def test_v3_desktop_skill_link_cache_version_does_not_split_task_identity(self):
        first = self.v3_start(
            "[$cortex:orchestrator](/opt/cortex-test/.codex/plugins/cache/cortex/cortex/4.0.0/skills/"
            "orchestrator/SKILL.md) harvest",
            waves=[{"workers": [{"phase": "plan"}]}],
        )
        replay = self.v3_start(
            "[$cortex:orchestrator](/opt/cortex-test/.codex/plugins/cache/cortex/cortex/4.4.1/skills/"
            "orchestrator/SKILL.md) harvest",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        self.assertTrue(first["ok"], first)
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["task_ref"], first["task_ref"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        task_dir = next((self.ledger / "tasks").iterdir())
        self.assertRegex(task_dir.name, r"^0001-harvest-[0-9a-f]{8}$")
        self.assertNotIn("plugins", task_dir.name)

    def test_follow_up_canonicalizes_desktop_skill_link_before_persistence(self):
        payload = control._v3_follow_up_payload({
            "user_request": (
                "[$cortex:orchestrator](/opt/cortex-test/.codex/plugins/cache/cortex/cortex/4.4.1/skills/"
                "orchestrator/SKILL.md) correct the report"
            ),
        })
        self.assertEqual(payload["task"]["user_request"], "$cortex:orchestrator correct the report")

    def test_v3_detailed_product_surface_request_does_not_force_a_preflight_question(self):
        request = (
            "Create a landing page for B2B sales teams with lead generation as the primary goal; preserve the "
            "existing brand and copy, and use the current design system."
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertFalse(task["intent_clarification_required"])
        self.assertEqual(control._intent_clarification_preflight("Implement mapping retry logic"), (False, None))
        self.assertEqual(control._intent_clarification_preflight("Fix the application crash"), (False, None))

    def test_v3_non_planner_worker_question_is_answered_through_coordinator_management(self):
        started = self.v3_start("backend behavior needs product choice", waves=[{
            "workers": [{"phase": "implementation", "profile": "backend_dev"}],
        }])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": "backend_dev",
        }
        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Should the conflicting update fail or use last-write-wins semantics?",
            "options": ["Fail on conflict", "Last write wins"],
        })
        managed = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {
                "command": "answer",
                "question_ref": asked["question_ref"],
                "answer": "Fail on conflict",
                "resume_context": {"source": "main_chat", "same_attempt": attempt["attempt_id"]},
            },
        })
        self.assertTrue(managed["ok"])
        self.assertEqual(managed["result"]["question"]["status"], "answered")
        polled = control.worker_question({**identity, "action": "poll", "question_ref": asked["question_ref"]})
        self.assertEqual(polled["outcome"], "question_answered")
        self.assertEqual(polled["answer_text"], "Fail on conflict")

    def test_v3_prune_removes_only_confirmed_stale_task_state_and_is_idempotent(self):
        old = self.v3_start("abandoned old task", waves=[{"workers": [{"phase": "discover"}]}])
        recent = self.v3_start("recent active task", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        old_task_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == old["task_ref"])
        old_dir = self.ledger / "tasks" / index[old_task_id]["directory"]
        old_state = self.task_state(old_dir)
        old_state["status"] = "completed"
        old_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        self.write_task_state(old_state)
        old_task = control.load_task_definition(old_dir)
        self.assertIsNotNone(control.db_get_classification(self.ledger, old_task["classification_id"]))
        control.db_put_global(self.ledger, "resource_claims", {
            "old": {"scope_kind": "task", "scope_id": old_task_id},
            "lane": {"scope_kind": "lane", "scope_id": "shared-lane"},
        })
        lane_dir = self.ledger / "lanes" / "shared-lane"
        lane_dir.mkdir(parents=True)
        lane_definition = {"schema": control.SCHEMA, "lane_id": "shared-lane"}
        lane_state = {
            "schema": control.SCHEMA,
            "lane_id": "shared-lane",
            "bound_tasks": [old_task_id, next(task_id for task_id in index if task_id != old_task_id)],
        }
        control.db_put_lane(self.ledger, lane_definition, lane_state)
        keep = self.project / "keep.txt"
        keep.write_text("project data", encoding="utf-8")

        rejected = control.manage_orchestration({
            "project_root": str(self.project), "intent": "prune", "payload": {"older_than_days": 7},
        })
        self.assertFalse(rejected["ok"])
        self.assertTrue(old_dir.exists())
        pruned = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertTrue(pruned["ok"])
        self.assertEqual(pruned["pruned_task_refs"], [old["task_ref"]])
        self.assertFalse(old_dir.exists())
        self.assertTrue(keep.is_file())
        remaining_index = control.read_task_index(self.ledger)
        self.assertEqual(len(remaining_index), 1)
        recent_task_id = next(iter(remaining_index))
        self.assertEqual(control._v3_task_ref(recent_task_id), recent["task_ref"])
        registry = control._operation_registry(self.ledger)
        self.assertNotIn(old_task_id, registry["tasks"])
        self.assertTrue(all(item.get("task_id") != old_task_id for item in registry["starts"].values()))
        self.assertIsNone(control.db_get_classification(self.ledger, old_task["classification_id"]))
        claims = control.db_get_global(self.ledger, "resource_claims", {})
        self.assertEqual(set(claims), {"lane"})
        _, lane_state = control.db_get_lane(self.ledger, "shared-lane")
        self.assertEqual(lane_state["bound_tasks"], [recent_task_id])
        self.assertFalse((self.ledger / "active-tasks.json").exists())
        activations = control.db_get_global(self.ledger, "activations", {})
        self.assertTrue(all(item.get("task_id") != old_task_id for item in activations.values()))
        replay = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertEqual(replay["pruned_count"], 0)

    def test_v3_prune_failure_preserves_canonical_metadata_until_retry(self):
        started = self.v3_start("prune retry must retain canonical state", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        task_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == started["task_ref"])
        task_dir = self.ledger / "tasks" / index[task_id]["directory"]
        state = self.task_state(task_dir)
        state["status"] = "completed"
        state["updated_at"] = "2000-01-01T00:00:00+00:00"
        self.write_task_state(state)
        task = control.load_task_definition(task_dir)
        control.db_put_global(self.ledger, "resource_claims", {
            "task-claim": {"scope_kind": "task", "scope_id": task_id},
        })
        before_registry = control._operation_registry(self.ledger)

        with mock.patch.object(control, "_remove_prune_directory", side_effect=OSError("simulated filesystem outage")):
            failed = control.manage_orchestration({
                "project_root": str(self.project),
                "intent": "prune",
                "payload": {"confirmation": "PRUNE", "older_than_days": 7},
            })
        self.assertFalse(failed["ok"])
        self.assertTrue(task_dir.exists())
        self.assertIn(task_id, control.read_task_index(self.ledger))
        self.assertEqual(control._operation_registry(self.ledger), before_registry)
        self.assertEqual(control.db_get_global(self.ledger, "resource_claims", {}), {
            "task-claim": {"scope_kind": "task", "scope_id": task_id},
        })
        self.assertIsNotNone(control.db_get_classification(self.ledger, task["classification_id"]))
        tombstones = control.db_list_prune_tombstones(self.ledger, task_id=task_id)
        self.assertEqual([row["status"] for row in tombstones], ["failed"])

        retried = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertTrue(retried["ok"])
        self.assertFalse(task_dir.exists())
        self.assertNotIn(task_id, control.read_task_index(self.ledger))
        self.assertNotIn(task_id, control._operation_registry(self.ledger)["tasks"])
        self.assertEqual(control.db_get_global(self.ledger, "resource_claims", {}), {})
        self.assertIsNone(control.db_get_classification(self.ledger, task["classification_id"]))
        self.assertEqual(
            [row["status"] for row in control.db_list_prune_tombstones(self.ledger, task_id=task_id)],
            ["finalized"],
        )

    def test_v3_prune_retains_old_active_task_and_shared_classification_receipt(self):
        active = self.v3_start("long-running task must survive prune", waves=[{"workers": [{"phase": "discover"}]}])
        completed = self.v3_start("completed task can be pruned", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        active_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == active["task_ref"])
        completed_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == completed["task_ref"])
        active_dir = self.ledger / "tasks" / index[active_id]["directory"]
        completed_dir = self.ledger / "tasks" / index[completed_id]["directory"]
        active_state = self.task_state(active_dir)
        completed_state = self.task_state(completed_dir)
        completed_task = control.load_task_definition(completed_dir)
        active_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        completed_state["status"] = "completed"
        completed_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        # Simulate an old shared receipt reference: pruning one task must not
        # delete evidence still owned by the retained active contract.
        active_state["classification_receipt"] = completed_task["classification_id"]
        self.write_task_state(active_state)
        self.write_task_state(completed_state)
        self.assertIsNotNone(control.db_get_classification(self.ledger, completed_task["classification_id"]))

        pruned = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })

        self.assertTrue(pruned["ok"])
        self.assertEqual(pruned["pruned_task_refs"], [completed["task_ref"]])
        self.assertTrue(active_dir.exists())
        self.assertFalse(completed_dir.exists())
        self.assertGreaterEqual(pruned["retained_nonterminal_count"], 1)
        self.assertIsNotNone(control.db_get_classification(self.ledger, completed_task["classification_id"]))

    def test_v3_multiple_same_project_tasks_are_isolated_by_task_ref(self):
        starts = [
            self.v3_start(f"independent session task {index}", waves=[{"workers": [{"phase": "discover"}]}])
            for index in range(1, 4)
        ]
        self.assertTrue(all(item["ok"] for item in starts))
        self.assertEqual(len({item["task_ref"] for item in starts}), 3)
        registry = control._operation_registry(self.ledger)
        self.assertEqual(len(registry["starts"]), 3)
        self.assertEqual(len(registry["tasks"]), 3)
        ambiguous = control.continue_orchestration({
            "project_root": str(self.project),
            "step": starts[0]["step"],
            "results": [{"report_ref": "report-not-selected"}],
        })
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(ambiguous["code"], "task_ref_required")
        advanced = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": starts[0]["task_ref"],
            "step": starts[0]["step"],
            "results": self.v3_results(starts[0], self.v3_report("first session only")),
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["task_ref"], starts[0]["task_ref"])

    def test_v3_validation_errors_preserve_the_selected_task_ref(self):
        started = self.v3_start("preserve task ref", waves=[{"workers": [{"phase": "discover"}]}])
        continued = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [],
        })
        self.assertFalse(continued["ok"])
        self.assertEqual(continued["task_ref"], started["task_ref"])
        managed = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "unsupported recovery intent",
        })
        self.assertFalse(managed["ok"])
        self.assertEqual(managed["task_ref"], started["task_ref"])

    def test_v3_concurrent_process_starts_preserve_one_registry(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        barrier = threading.Barrier(3)
        responses = []
        errors = []

        def start(index):
            try:
                request = {
                    "jsonrpc": "2.0", "id": index, "method": "tools/call",
                    "params": {"name": "start_orchestration", "arguments": {
                        "project_root": str(self.project),
                        "task": {
                            "user_request": f"concurrent task {index}", "complexity": "C1",
                            "acceptance_criteria": ["The requested outcome is observed."],
                            "verification": ["Run an authoritative outcome check."],
                        },
                        "waves": [{"workers": [{"phase": "discover"}]}],
                    }},
                }
                barrier.wait()
                completed = subprocess.run(
                    [sys.executable, str(script)],
                    input=json.dumps(request) + "\n",
                    text=True,
                    capture_output=True,
                    check=True,
                )
                responses.append(json.loads(completed.stdout)["result"]["structuredContent"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=start, args=(index,)) for index in range(1, 4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertTrue(all(item["ok"] for item in responses))
        self.assertEqual(len({item["task_ref"] for item in responses}), 3)
        registry = control._operation_registry(self.ledger)
        self.assertEqual(len(registry["tasks"]), 3)

    def test_v3_concurrent_identical_starts_share_the_pending_reservation(self):
        task = {
            "user_request": "one concurrent idempotent task", "complexity": "C1",
            "acceptance_criteria": ["The requested outcome is observed."],
            "verification": ["Run an authoritative outcome check."],
        }
        params = {
            "project_root": str(self.project),
            "task": task,
            "waves": [{"workers": [{"phase": "discover"}]}],
        }
        barrier = threading.Barrier(3)
        responses = []
        errors = []

        def start():
            try:
                barrier.wait()
                responses.append(control.start_orchestration(params))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertTrue(all(item["ok"] for item in responses))
        self.assertEqual(len({item["task_ref"] for item in responses}), 1)
        registry = control._operation_registry(self.ledger)
        self.assertEqual(len(registry["starts"]), 1)
        self.assertEqual(len(registry["tasks"]), 1)

    def test_v3_normalizes_human_language_aliases_before_ledger_creation(self):
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "language alias", "language": "English", "user_language": "English",
                "acceptance_criteria": ["The requested outcome is observed."],
                "verification": ["Run an authoritative outcome check."],
                "plan_approval": "auto",
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertEqual(task["user_language"], "en")

    def test_v3_compact_aliases_and_defaults_match_internal_delegation_contract(self):
        started = self.v3_start("compact aliases", waves=[{"workers": [{
            "phase": "research", "profile": "exploration", "objective": "Inspect",
            "paths": ["plugins/cortex"], "acceptance": ["Facts collected"],
            "verification": ["Cite files"], "model": "luna", "effort": "high",
        }]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual((attempt["gate"], attempt["agent"]), ("discover", "explorer"))
        self.assertEqual(attempt["allowed_paths"], ["plugins/cortex"])
        self.assertEqual(attempt["acceptance_criteria"], ["Facts collected"])
        self.assertEqual(attempt["verification"], ["Cite files"])
        self.assertEqual(attempt["expected_model"], "gpt-5.6-luna")

    def test_v3_planner_dispatch_stays_below_host_output_truncation_budget(self):
        request = (
            "$cortex:orchestrator harvest\nRun a source-backed full knowledge harvest for this small repository as a "
            "C1 task. Use the normal harvest pipeline and do not request plan approval because this is a harvest "
            "command. Acceptance: every feature-bearing surface is mapped or explicitly excluded; the authentication "
            "feature documentation explains actors, entry points, main and negative scenarios, state/data, interfaces, "
            "configuration, failure/recovery, verification, and exact source evidence; zero unexplained unmapped "
            "surfaces remain. Verification: run authoritative tests, validate links and source paths, and independently "
            "review completeness before closing."
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        prompt = self.briefing_from_response(started)
        bootstrap = started["dispatches"][0]["arguments"]["message"]
        serialized = json.dumps(started, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(prompt.encode("utf-8")), 16_000)
        self.assertLess(len(bootstrap.encode("utf-8")), 1_500)
        self.assertLess(len(serialized.encode("utf-8")), 8_000)
        self.assertLess(serialized.index("NEXT REQUIRED ACTION"), serialized.index("You are the internal Cortex worker"))
        self.assertIn("Never claim it was sent or call wait without the returned child target", started["next_action"])
        self.assertIn("NEXT REQUIRED ACTION: FIRST", started["next_action"])
        self.assertIn("exact failed result Cortex already accepted", started["next_action"])
        self.assertIn("use list_agents defensively", started["next_action"])
        self.assertIn("THEN call every dispatch.call", started["next_action"])
        self.assertIn("close that exact completed native child with close_agent", started["next_action"])
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["user_request"], request)
        self.assertEqual(prompt.count(request), 0)
        self.assertNotIn(request, serialized)
        self.assertIn("only direct-read exception under .codex/cortex", bootstrap)
        self.assertIn("Dispatch briefing reviewed:", bootstrap)
        self.assertRegex(started["dispatches"][0]["dispatch_ref"], r"^dispatch-[0-9a-f]{24}$")
        self.assertRegex(started["dispatches"][0]["briefing_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(started["dispatches"][0]["display_name"], "Planner Authentication")
        self.assertRegex(started["dispatches"][0]["arguments"]["task_name"], r"^planner_authentication_01_[0-9a-f]{8}$")
        self.assertIn("Canonical Cortex team", prompt)
        self.assertIn("security_auditor", prompt)

    def test_v3_dispatch_uses_scoped_immutable_briefing_and_requires_review_marker(self):
        started = self.v3_start(
            "Audit the authentication feature.",
            waves=[{"workers": [{"phase": "plan"}]}],
        )
        dispatch = started["dispatches"][0]
        briefing_path = Path(dispatch["briefing_path"])
        briefing = briefing_path.read_text(encoding="utf-8")
        self.assertEqual(briefing_path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(hashlib.sha256(briefing.encode("utf-8")).hexdigest(), dispatch["briefing_digest"])
        self.assertIn(str(briefing_path), dispatch["arguments"]["message"])
        self.assertIn(dispatch["briefing_digest"], dispatch["arguments"]["message"])
        self.assertIn(dispatch["dispatch_ref"], dispatch["arguments"]["message"])

        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        fallback = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": dispatch["briefing_digest"],
        })
        self.assertTrue(fallback["ok"], fallback)
        self.assertEqual(fallback["briefing"], briefing)
        self.assertEqual(
            fallback["review_marker"],
            control.dispatch_briefing_review_marker(dispatch["briefing_digest"]),
        )
        oversized = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": dispatch["briefing_digest"],
            "max_bytes": control.MAX_BRIEFING_BYTES,
        })
        self.assertTrue(oversized["ok"], oversized)
        self.assertTrue(oversized["max_bytes_normalized"])
        self.assertEqual(oversized["requested_max_bytes"], control.MAX_BRIEFING_BYTES)
        self.assertEqual(oversized["effective_max_bytes"], control.ARTIFACT_TRANSPORT_MAX_BYTES)
        invalid_size = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": dispatch["briefing_digest"],
            "max_bytes": 0,
        })
        self.assertFalse(invalid_size["ok"])
        self.assertEqual(invalid_size["outcome"], "needs_correction")
        self.assertEqual(invalid_size["code"], "dispatch_briefing_request_invalid")
        self.assertEqual(invalid_size["diagnostics"][0]["path"], "max_bytes")
        self.assertTrue(invalid_size["retryable"])
        self.assertFalse(invalid_size["attempt_budget_consumed"])
        self.assertIn("never justify ending the worker", invalid_size["next_action"])
        denied = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": "0" * 64,
        })
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["outcome"], "needs_correction")
        self.assertEqual(denied["code"], "dispatch_briefing_request_invalid")
        self.assertTrue(denied["retryable"])
        briefing_path.chmod(0o600)
        blocked = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": dispatch["briefing_digest"],
        })
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["outcome"], "blocked")
        self.assertFalse(blocked["retryable"])
        briefing_path.chmod(0o400)
        package = self.task_document(task_dir, f"dispatch:{attempt['attempt_id']}")
        self.assertNotIn("## Specialist playbook", json.dumps(self.task_state(task_dir)))
        self.assertNotIn("## Specialist playbook", json.dumps(package))

        missing = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self.v3_report("briefing marker omitted"),
            "planning": self.v3_planning(),
        })
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["code"], "report_evidence_incomplete")
        self.assertIn("Dispatch briefing reviewed:", missing["diagnostics"][0]["message"])

    def test_v3_record_report_rejects_tampered_immutable_briefing(self):
        started = self.v3_start(
            "Inspect the authentication feature.",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        dispatch = started["dispatches"][0]
        briefing_path = Path(dispatch["briefing_path"])
        briefing_path.chmod(0o600)
        briefing_path.write_text(briefing_path.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
        briefing_path.chmod(0o400)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("tampered briefing")
        report["evidence"].append(control.dispatch_briefing_review_marker(attempt["briefing_digest"]))
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "dispatch_briefing_invalid")
        self.assertEqual(rejected["outcome"], "blocked")

    def test_v3_unknown_phase_and_profile_are_recoverable_without_task_writes(self):
        invalid_phase = self.v3_start("bad phase", waves=[{"workers": [{"phase": "discvoery"}]}])
        invalid_profile = self.v3_start("bad profile", waves=[{"workers": [{"phase": "discover", "profile": "explroer"}]}])
        self.assertFalse(invalid_phase["ok"])
        self.assertFalse(invalid_profile["ok"])
        self.assertIn("try", invalid_phase["diagnostics"][0]["message"])
        self.assertIn("try", invalid_profile["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_single_and_parallel_worker_slots_are_relative_and_atomic(self):
        sequential = self.v3_start("single slot", waves=[{"workers": [{"phase": "discover"}]}])
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": sequential["task_ref"], "step": sequential["step"],
            "results": self.v3_results(sequential),
        })
        self.assertTrue(advanced["ok"])
        while advanced["outcome"] != "completed":
            advanced = control.continue_orchestration({
                "project_root": str(self.project), "task_ref": advanced["task_ref"], "step": advanced["step"],
                "results": self.v3_results(advanced),
            })
            self.assertTrue(advanced["ok"])

        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "parallel slots", "complexity": "small",
                "acceptance_criteria": ["The requested outcome is observed."],
                "verification": ["Run an authoritative outcome check."],
            },
            "waves": [{"workers": [{"phase": "discover"}, {"phase": "discover", "profile": "explorer"}]}],
        })
        task_dir = max((self.ledger / "tasks").iterdir())
        before = json.dumps(self.task_state(task_dir), sort_keys=True)
        rejected = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [
                {"worker": 1, "report_ref": "report-one"},
                {"worker": 1, "report_ref": "report-duplicate"},
            ],
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(json.dumps(self.task_state(task_dir), sort_keys=True), before)
        self.assertFalse("inflight_continue" in control._operation_registry(self.ledger))
        accepted_results = self.v3_results(started, [self.v3_report("one"), self.v3_report("two")])
        accepted = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [accepted_results[1], accepted_results[0]],
        })
        self.assertTrue(accepted["ok"])

    def test_v3_continue_replays_exact_retry_and_requires_a_new_report_ref_on_next_step(self):
        started = self.v3_start("relative retry", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "qa"}]},
        ])
        payload = {
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": self.v3_results(started, self.v3_report("same report")),
        }
        first = control.continue_orchestration(payload)
        replay = control.continue_orchestration(payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertNotEqual(replay, first)
        self.assertIn("Do not invoke or repeat a worker dispatch", replay["next_action"])
        registry = control._operation_registry(self.ledger)
        task_record = next(iter(registry["tasks"].values()))
        self.assertEqual(task_record["last_continue"]["response"]["dispatches"], [])
        self.assertTrue(task_record["last_continue"]["response"]["replayed"])
        self.assertEqual(first["step"], 2)
        second = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": first["step"],
            "results": self.v3_results(first, self.v3_report("same report")),
        })
        self.assertTrue(second["ok"])
        state = self.task_state(next((self.ledger / "tasks").iterdir()))
        reports = control.list_task_reports({"task_id": state["task_id"], "principal": state["principal"]})["reports"]
        self.assertEqual(len(reports), 2)
        completed_attempts = [item for item in state["attempts"] if item["status"] == "passed"]
        self.assertEqual(completed_attempts[0]["dispatch_correlation"], "worker_report_received")
        self.assertNotIn("host_spawn", completed_attempts[0])

    def test_v3_worker_records_report_returns_compact_ref_and_next_worker_receives_context(self):
        started = self.v3_start("tool-backed reports", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        self.assertEqual(started["pipeline"]["authority"], "coordinator")
        self.assertEqual(started["dispatches"][0]["arguments"]["fork_turns"], "none")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("repository-grounded discovery handoff")
        report["findings"] = ["Use the discovered service boundary."]
        report = self._report_with_briefing(attempt, report)
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertTrue(published["ok"])
        self.assertEqual(published["outcome"], "report_recorded")
        self.assertNotIn("report", published)
        self.assertNotIn("state", published)

        read = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
        })
        self.assertEqual(read["report"], report)
        self.assertEqual(read["result_validation"]["status"], "passed")
        self.assertEqual(read["result_validation"]["schema"], control.RESULT_VALIDATION_SCHEMA)
        report_markdown = Path(read["report_markdown_path"])
        self.assertEqual(report_markdown, task_dir / "reports/markdown/report-0001.md")
        self.assertTrue(report_markdown.is_file())
        self.assertEqual(
            read["report_markdown_link"],
            f"[Report discover — report-0001](<{report_markdown}>)",
        )
        self.assertIn("Publish report_markdown_link verbatim", read["next_action"])
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["dispatches"][0]["phase"], "implementation")
        prompt = self.briefing_from_response(advanced)
        self.assertNotIn("repository-grounded discovery handoff", prompt)
        self.assertNotIn("Use the discovered service boundary.", prompt)
        self.assertIn("read every ref with the public read_worker_report tool", prompt)
        self.assertIn(f"task_ref={started['task_ref']!r}", prompt)
        self.assertIn("Predecessor review requirement", prompt)
        self.assertIn(f"Predecessor review: {published['report_ref']}", prompt)
        self.assertIn(f"attempt_id='implementation-02'", prompt)
        self.assertIn("profile='general'", prompt)
        self.assertNotIn("Attempt baseline ref:", prompt)
        state = control.load_task_state_for_artifact(task_dir)
        successor = next(item for item in state["attempts"] if item["gate"] == "implementation")
        worker_read = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
            "attempt_id": successor["attempt_id"],
            "profile": successor["profile"],
        })
        self.assertTrue(worker_read["ok"], worker_read)
        self.assertNotIn("report_markdown_link", worker_read)
        self.assertIn("evidence context", worker_read["next_action"])
        ungranted = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": "report-9999",
            "attempt_id": successor["attempt_id"],
            "profile": successor["profile"],
        })
        self.assertFalse(ungranted["ok"])
        self.assertIn("only predecessor report refs", ungranted["diagnostics"][0]["message"])
        self.assertIn("mcp__codebase_memory__list_projects", prompt)
        self.assertIn("If no exact usable index exists, do not create or refresh one in this gate.", prompt)
        self.assertFalse((task_dir / "reports/records").exists())
        report_artifacts, _ = control.db_list_artifacts(
            self.ledger, state["task_id"], kind="worker_report", offset=0, page_size=10,
        )
        self.assertEqual(len(report_artifacts), 1)
        state = control.load_task_state_for_artifact(task_dir)
        completed = next(item for item in state["attempts"] if item["gate"] == "discover")
        self.assertEqual(completed["report_ids"], [published["report_ref"]])

    def test_read_only_profile_contract_overrides_a_normally_writable_gate(self):
        started = self.v3_start(
            "independently verify the quality checks",
            waves=[{"workers": [{"phase": "qa", "profile": "build_verification"}]}],
        )
        prompt = self.briefing_from_response(started)
        self.assertIn("This is a read-only result gate.", prompt)
        self.assertIn("report.changed_files must be exactly []", prompt)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", prompt)
        self.assertIn("cross-language test/build/cache residue", prompt)
        self.assertIn("arbitrary gitignored artifacts", prompt)
        self.assertIn("No rm, git clean, or cleanup scripts", prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        (self.project / "unexpected-verifier-write.txt").write_text("must be rejected\n", encoding="utf-8")
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("verifier must remain read-only")
            ),
        })
        self.assertTrue(rejected["ok"], rejected)
        record, _ = control.read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{rejected['report_ref']}.json",
            kinds={"worker_report"},
        )
        self.assertEqual(record["result_validation"]["artifacts"]["concurrent_change_count"], 1)

    def test_completion_report_rejects_every_nonzero_executed_check(self):
        started = self.v3_start(
            "reject a false-positive review completion",
            waves=[{"workers": [{"phase": "review", "profile": "code_reviewer"}]}],
        )
        self.assertIn(
            "integer exit_code 0",
            self.briefing_from_response(started),
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("review found an unresolved defect")
        report["tests"].append({
            "command": "python3 verify_links.py",
            "cwd": ".",
            "exit_code": 1,
            "evidence": "The link verifier found one unresolved local fragment target.",
        })
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, report),
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["outcome"], "failed")
        self.assertEqual(rejected["code"], "worker_verification_failed")
        self.assertIn("report.tests index(es): 2", rejected["diagnostics"][0]["message"])
        self.assertIn("Do not omit, disguise, or relabel", rejected["next_action"])
        self.assertEqual(list((task_dir / "reports/records").glob("report-*.json")), [])

    def test_completion_report_rejects_placeholder_test_commands(self):
        started = self.v3_start(
            "reject unreproducible completion evidence",
            waves=[{"workers": [{"phase": "architecture", "profile": "architect"}]}],
        )
        self.assertIn(
            "exact command (no `...`)",
            self.briefing_from_response(started),
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("review evidence must be reproducible")
        report["tests"][0]["command"] = "python3 - <<'PY' ... assertions ... PY"
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, report),
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_validation_failed")
        self.assertIn("exact reproducible invocation", rejected["diagnostics"][0]["message"])
        self.assertEqual(list((task_dir / "reports/records").glob("report-*.json")), [])

    def test_close_markers_ignore_command_and_cwd_text_but_reject_semantic_blockers(self):
        created = self.init(task_id="blocked-path-task")
        task_dir = self.ledger / "tasks" / created["task_directory"]
        task = self.task_definition(task_dir)
        task["acceptance_criteria"] = ["The task is complete."]
        task["verification"] = ["The close check passes."]
        control.db_update_task_definition(self.ledger, task)
        state = {"require_handoff": True, "task_id": "blocked-path-task"}
        attempt = {"gate": "close"}
        report = self.v3_report("close verification completed")
        report["tests"][0]["command"] = "python3 verify_blocked_resume.py"
        report["tests"][0]["cwd"] = str(self.base / "blocked-resume-fixture")
        control._validate_close_report(task_dir, state, attempt, report)

        report["summary"] = "Close remains blocked on an unresolved dependency."
        with self.assertRaisesRegex(ValueError, "unresolved completion markers: blocked"):
            control._validate_close_report(task_dir, state, attempt, report)

    def test_closure_finding_is_canonical_across_review_close_and_resolved_rework(self):
        started = self.v3_start(
            "canonical closure finding rework",
            waves=[
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        review = state["attempts"][0]
        finding = {
            "fingerprint": "docs-link-001", "severity": "P2", "status": "open", "blocking": True,
            "summary": "Documentation link is stale",
            "details": {"affected_paths": ["docs/features/example/index.md"]},
        }
        closure = {
            "decision": "pass", "findings": [finding],
            "verification": {"executed": ["focused closure regression"], "not_executed": [], "required_missing": [], "limitations": []},
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        review_ref = self._publish_closure_report(task_dir, state, review, closure)
        rework = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": review_ref}],
        })
        self.assertTrue(rework["ok"], rework)
        self.assertEqual(rework["outcome"], "ready_to_spawn")
        self.assertEqual(rework["dispatches"][0]["phase"], "documentation")
        findings = control.db_list_task_findings(self.ledger, state["task_id"], include_resolved=False)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["source_evidence"], [{"report_id": "report-0001", "attempt_id": review["attempt_id"]}])
        after_rework = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(after_rework["status"], "active")
        self.assertEqual(after_rework["closure_rework"]["review"]["target_gate"], "documentation")
        self.assertEqual(after_rework["closure_rework"]["review"]["rerun_gates"], ["review", "close"])
        self.assertTrue(next(item for item in after_rework["attempts"] if item["attempt_id"] == review["attempt_id"])["invalidated"])

        rerun_review = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": rework["step"],
            "results": self.v3_results(rework, self.v3_report("documentation correction completed")),
        })
        self.assertTrue(rerun_review["ok"], rerun_review)
        self.assertEqual(rerun_review["dispatches"][0]["phase"], "review")

        current = control.load_task_state_for_artifact(task_dir)
        replacement_review = next(
            item for item in current["attempts"]
            if item["gate"] == "review" and not item.get("invalidated")
        )
        resolved = dict(finding, status="resolved", blocking=False, resolved_at="2026-08-17T12:00:00Z")
        resolved_closure = dict(closure, findings=[resolved])
        resolved_report, _ = self._report_for_attempt(
            task_dir,
            replacement_review,
            self.v3_report("review confirmed the documentation correction"),
        )
        resolved_ref = self._publish_closure_report(
            task_dir,
            current,
            replacement_review,
            resolved_closure,
            report=resolved_report,
        )
        close = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": rerun_review["step"],
            "results": [{"report_ref": resolved_ref}],
        })
        self.assertTrue(close["ok"], close)
        self.assertEqual(close["dispatches"][0]["phase"], "close")
        self.assertEqual(control.db_list_task_findings(self.ledger, state["task_id"])[0]["status"], "resolved")

        completed = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": close["step"],
            "results": self.v3_results(close, self.v3_report("fresh close passed after the corrective review")),
        })
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(completed["outcome"], "completed")

    def test_open_canonical_p2_blocks_a_textually_passing_close_and_reopens_rework(self):
        started = self.v3_start(
            "close cannot override canonical closure debt",
            waves=[
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
        )
        reviewed = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": self.v3_results(started, self.v3_report("review textually passed")),
        })
        documented = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": reviewed["step"],
            "results": self.v3_results(reviewed, self.v3_report("documentation textually passed")),
        })
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        close_attempt = next(item for item in state["attempts"] if item["gate"] == "close" and not item.get("invalidated"))
        control.db_upsert_task_finding(
            self.ledger,
            state["task_id"],
            {
                "fingerprint": "late-p2-001", "severity": "P2", "status": "open", "blocking": False,
                "summary": "A canonical P2 remains open",
                "details": {"affected_paths": ["docs/features/example/index.md"]},
            },
            source={"report_id": "report-0001", "attempt_id": state["attempts"][0]["attempt_id"]},
        )
        rework = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": documented["step"],
            "results": self.v3_results(documented, self.v3_report("close textually passed despite P2")),
        })
        self.assertTrue(rework["ok"], rework)
        self.assertEqual(rework["outcome"], "ready_to_spawn")
        self.assertEqual(rework["dispatches"][0]["phase"], "documentation")
        after = control.load_task_state_for_artifact(task_dir)
        self.assertTrue(next(item for item in after["attempts"] if item["attempt_id"] == close_attempt["attempt_id"])["invalidated"])
        self.assertNotEqual(after["status"], "completed")

    def test_required_verification_missing_blocks_review_and_dispatches_rework(self):
        started = self.v3_start(
            "required verification cannot be overridden",
            waves=[
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        review = state["attempts"][0]
        closure = {
            "decision": "pass", "findings": [],
            "verification": {
                "executed": [], "not_executed": [],
                "required_missing": ["Run the required package verification."],
                "limitations": [],
            },
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        review_ref = self._publish_closure_report(task_dir, state, review, closure)
        rework = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": review_ref}],
        })
        self.assertTrue(rework["ok"], rework)
        self.assertEqual(rework["dispatches"][0]["phase"], "documentation")
        finding = next(
            item for item in control.db_list_task_findings(self.ledger, state["task_id"])
            if item["fingerprint"] == "verification-required-missing"
        )
        self.assertEqual(finding["severity"], "P1")
        self.assertEqual(finding["status"], "open")

    def test_review_report_requires_top_level_closure(self):
        started = self.v3_start("closure is mandatory", waves=[{"workers": [{"phase": "review"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        rejected = control.publish_worker_report({
                "project_root": str(self.project), "task_id": state["task_id"], "attempt_id": attempt["attempt_id"],
                "profile": attempt["profile"], "report": self._report_with_briefing(attempt, self.v3_report("missing closure")),
            })
        self.assertFalse(rejected["ok"])
        self.assertIn("review and close reports require a top-level closure sibling", rejected["diagnostics"][0]["message"])

    def test_waived_p2_with_auditable_metadata_does_not_block_close(self):
        started = self.v3_start("waived closure finding", waves=[{"workers": [{"phase": "review"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        closure = {
            "decision": "pass", "findings": [{
                "fingerprint": "accepted-risk-002", "severity": "P2", "status": "waived", "blocking": True,
                "summary": "Known documentation gap", "waiver_reason": "Tracked for next release",
                "waived_by": "release-owner", "waived_at": "2026-08-17T12:00:00Z",
            }],
            "verification": {"executed": ["focused closure regression"], "not_executed": [], "required_missing": [], "limitations": []},
            "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
        }
        report_ref = self._publish_closure_report(task_dir, state, attempt, closure)
        close_step = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": report_ref}],
        })
        self.assertEqual(close_step["outcome"], "ready_to_spawn")
        self.assertEqual(control.db_list_task_findings(self.ledger, state["task_id"])[0]["status"], "waived")

    def test_read_only_result_tolerates_conventional_ephemeral_artifacts_from_multiple_stacks(self):
        (self.project / ".gitignore").write_text("coverage.tmp\n", encoding="utf-8")
        started = self.v3_start(
            "independently verify multiple language test suites",
            waves=[{"workers": [{"phase": "review", "profile": "code_reviewer"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        artifacts = {
            "python/__pycache__/module.cpython-312.pyc": b"python cache",
            "javascript/.nyc_output/result.json": b"javascript coverage cache",
            "jvm/.gradle/test-cache.bin": b"jvm cache",
            "dotnet/TestResults/result.trx": b"dotnet test result",
            "rust/target/debug/test-binary": b"rust build output",
            "dart/.dart_tool/test-cache.json": b"dart cache",
        }
        for relative, content in artifacts.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.project / "coverage.tmp").write_text("recognized coverage output\n", encoding="utf-8")
        recorded = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("read-only verification completed across language stacks")
            ),
            "closure": {
                "decision": "pass", "findings": [],
                "verification": {"executed": ["focused regression"], "not_executed": [], "required_missing": [], "limitations": []},
                "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
            },
        })
        self.assertTrue(recorded["ok"], recorded)
        record, _ = control.read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{recorded['report_ref']}.json",
            kinds={"worker_report"},
        )
        artifacts_receipt = record["result_validation"]["artifacts"]
        self.assertEqual(artifacts_receipt["ephemeral_artifact_count"], 7)
        self.assertRegex(artifacts_receipt["ephemeral_artifacts_digest"], r"^[0-9a-f]{64}$")

    def test_read_only_result_rejects_arbitrary_gitignored_artifacts(self):
        (self.project / ".gitignore").write_text("untrusted-report.tmp\n", encoding="utf-8")
        started = self.v3_start(
            "independently verify without creating project outputs",
            waves=[{"workers": [{"phase": "review", "profile": "code_reviewer"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        (self.project / "untrusted-report.tmp").write_text("unknown ignored side effect\n", encoding="utf-8")
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("read-only verification left an unrecognized artifact")
            ),
            "closure": {
                "decision": "pass", "findings": [],
                "verification": {"executed": ["focused regression"], "not_executed": [], "required_missing": [], "limitations": []},
                "workspace": {"modified": [], "untracked": [], "staged": [], "committed": "not_required"},
            },
        })
        self.assertFalse(rejected["ok"])
        self.assertIn(
            "generated or ignored project artifacts changed during read-only result gate",
            rejected["diagnostics"][0]["message"],
        )
        self.assertIn("untrusted-report.tmp", rejected["diagnostics"][0]["message"])

    def test_v3_plan_approval_holds_successor_wave_until_user_approves(self):
        started = self.v3_start(
            "review the plan before implementation",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("planner proposed an ordered implementation plan")),
        })
        self.assertTrue(held["ok"])
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        self.assertEqual(held["dispatches"], [])
        self.assertEqual(held["plan_review"]["summary"], "planner proposed an ordered implementation plan")
        self.assertEqual(held["plan_review"]["pipeline_contract_version"], 2)
        self.assertEqual(held["plan_review"]["plan_report_ref"], "report-0001")
        self.assertRegex(held["plan_review"]["plan_revision"], r"^plan-report-0001$")
        self.assertRegex(held["plan_review"]["verified_predecessor_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(held["plan_review"]["semantic_future_pipeline_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(held["plan_review"]["semantic_pipeline_version"], 1)
        task_dir = next((self.ledger / "tasks").iterdir())
        self.assertEqual(
            Path(held["plan_review"]["report_markdown_path"]),
            task_dir / "reports/markdown/report-0001.md",
        )
        self.assertEqual(
            held["plan_review"]["report_markdown_link"],
            f"[Report plan — report-0001](<{task_dir / 'reports/markdown/report-0001.md'}>)",
        )
        self.assertIn("manage_orchestration", held["next_action"])
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["plan_approval"]["status"], "awaiting_user")
        self.assertEqual([item["gate"] for item in state["attempts"]], ["plan"])

        blocked = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": held["step"],
            "results": [{"report_ref": "report-plan-not-approved"}],
        })
        self.assertFalse(blocked["ok"])
        self.assertIn("awaiting explicit user approval", blocked["diagnostics"][0]["message"])

        with mock.patch.object(control, "_request_mcp_elicitation") as elicitation:
            prompt = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
        self.assertTrue(prompt["ok"])
        interaction = prompt["plan_approval_interaction"]
        self.assertEqual(interaction["schema"], "cortex/plan-approval/v1")
        self.assertEqual([action["id"] for action in interaction["actions"]], ["approve", "cancel"])
        self.assertEqual(
            [action["label"] for action in interaction["actions"]],
            ["Approve", "Cancel"],
        )
        self.assertFalse(elicitation.called)
        approved = control.manage_orchestration(interaction["actions"][0]["arguments"])
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["outcome"], "ready_to_spawn")
        self.assertEqual(approved["dispatches"][0]["phase"], "implementation")
        self.assertEqual(approved["approval_message"], "Plan approved.")
        self.assertIn("Tell the user", approved["next_action"])
        self.assertEqual(
            approved["result"]["decision"],
            "approved",
        )

    def test_plan_approval_rejects_a_stale_basis_before_post_plan_dispatch(self):
        started = self.v3_start(
            "block stale plan approval",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        plan = control._load_orchestrate_plan(task_dir, state)
        plan["semantic_pipeline_version"] = 2
        orchestration_engine._write_orchestrate_plan(task_dir, plan)
        prompt = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval", "payload": {"decision": "prompt"},
        })
        approve_args = prompt["plan_approval_interaction"]["actions"][0]["arguments"]
        blocked = control.manage_orchestration(approve_args)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "plan_reapproval_required")
        self.assertTrue(blocked["recoverable"])
        self.assertEqual(self.task_state(task_dir)["plan_approval"]["status"], "awaiting_user")

    def test_plan_approval_rejects_invalid_and_replayed_button_requests(self):
        started = self.v3_start(
            "reject invalid plan approval button requests",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        prompt = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval", "payload": {"decision": "prompt"},
        })
        approve_args = prompt["plan_approval_interaction"]["actions"][0]["arguments"]
        invalid = {
            **approve_args,
            "payload": {"decision": "approve", "request_id": "plan-approval-invalid"},
        }
        rejected = control.manage_orchestration(invalid)
        self.assertFalse(rejected["ok"])
        self.assertIn("request_id", rejected["diagnostics"][0]["message"])
        self.assertEqual(
            self.task_state(next((self.ledger / "tasks").iterdir()))["plan_approval"]["status"],
            "awaiting_user",
        )

        approved = control.manage_orchestration(approve_args)
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["outcome"], "ready_to_spawn")
        replay = control.manage_orchestration(approve_args)
        self.assertFalse(replay["ok"])
        self.assertIn("no pending plan approval", replay["diagnostics"][0]["message"])

    def test_plan_approval_cancel_then_revise_requeues_planner(self):
        started = self.v3_start(
            "revise the plan after cancelling approval",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        prompt = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval", "payload": {"decision": "prompt"},
        })
        cancelled = control.manage_orchestration(prompt["plan_approval_interaction"]["actions"][1]["arguments"])
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["outcome"], "awaiting_plan_approval")
        self.assertEqual(cancelled["result"]["decision"], "cancelled")

        revised = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval",
            "payload": {"decision": "revise", "feedback": "Add an explicit rollback scenario."},
        })
        self.assertTrue(revised["ok"])
        self.assertEqual(revised["outcome"], "ready_to_spawn")
        self.assertEqual(revised["dispatches"][0]["phase"], "plan")
        task_dir = next((self.ledger / "tasks").iterdir())
        self.assertEqual(self.task_state(task_dir)["plan_approval"]["status"], "pending_plan")

    def test_material_future_change_preserves_approval_history_and_requires_a_replacement_plan(self):
        started = self.v3_start(
            "reapprove a materially changed future pipeline",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "review"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        prompt = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval", "payload": {"decision": "prompt"},
        })
        approved = control.manage_orchestration(prompt["plan_approval_interaction"]["actions"][0]["arguments"])
        self.assertEqual(approved["dispatches"][0]["phase"], "implementation")
        implementation_results = self.v3_results(approved)

        missing_rework = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": approved["step"], "results": implementation_results,
            "future_waves": [{"workers": [{"phase": "review", "objective": "Review the changed delivery contract."}]}],
            "reason": "implementation evidence materially changed the review contract",
        })
        self.assertFalse(missing_rework["ok"])
        self.assertEqual(missing_rework["code"], "plan_reapproval_required")

        replacement = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": approved["step"], "results": implementation_results,
            "future_waves": [
                {"workers": [{"phase": "plan", "objective": "Reconcile the materially changed review contract."}]},
                {"workers": [{"phase": "review", "objective": "Review the changed delivery contract."}]},
            ],
            "rework": True,
            "reason": "implementation evidence materially changed the review contract",
        })
        self.assertTrue(replacement["ok"], replacement)
        self.assertEqual(replacement["dispatches"][0]["phase"], "plan")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        self.assertEqual(state["plan_approval"]["status"], "pending_plan")
        self.assertTrue(any(item.get("event") == "approved" for item in state["plan_approval"]["history"]))
        self.assertTrue(any(item.get("event") == "material_pipeline_change" for item in state["plan_approval"]["history"]))
        plan = control._load_orchestrate_plan(task_dir, state)
        self.assertEqual(plan["semantic_pipeline_version"], 2)
        self.assertTrue(plan["history"][-1].get("approval"))

        held_again = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": replacement["step"], "results": self.v3_results(replacement),
        })
        self.assertEqual(held_again["outcome"], "awaiting_plan_approval")
        self.assertNotEqual(
            held_again["plan_review"]["plan_revision"],
            held["plan_review"]["plan_revision"],
        )

    def test_transport_only_future_change_does_not_invalidate_approval(self):
        started = self.v3_start(
            "keep approval across a transport-only future change",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
                {"workers": [{"phase": "review"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"], "results": self.v3_results(started),
        })
        prompt = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "intent": "plan_approval", "payload": {"decision": "prompt"},
        })
        approved = control.manage_orchestration(prompt["plan_approval_interaction"]["actions"][0]["arguments"])
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": approved["step"], "results": self.v3_results(approved),
            "future_waves": [{"workers": [{
                "phase": "review", "model": "luna", "effort": "medium", "visible": True,
            }]}],
            "reason": "use a visible transport for the unchanged review contract",
        })
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["dispatches"][0]["phase"], "review")
        self.assertEqual(advanced["dispatches"][0]["call"], "create_thread")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        self.assertEqual(state["plan_approval"]["status"], "approved")
        plan = control._load_orchestrate_plan(task_dir, state)
        self.assertEqual(plan["semantic_pipeline_version"], 1)
        self.assertFalse(any(item.get("event") == "material_pipeline_change" for item in state["plan_approval"]["history"]))

    def test_v3_plan_approval_cancel_is_silent_and_keeps_the_plan_pending(self):
        started = self.v3_start(
            "cancel plan approval and wait for a user message",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("plan awaiting a decision")),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")

        with mock.patch.object(control, "_request_mcp_elicitation") as elicitation:
            prompt = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
        self.assertFalse(elicitation.called)
        cancelled = control.manage_orchestration(prompt["plan_approval_interaction"]["actions"][1]["arguments"])
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["outcome"], "awaiting_plan_approval")
        self.assertEqual(cancelled["dispatches"], [])
        self.assertEqual(cancelled["result"]["decision"], "cancelled")
        self.assertEqual(cancelled["output_policy"], "silent")
        self.assertEqual(cancelled["allowed_visible_events"], ["user_message"])
        self.assertIn("Stop now and wait", cancelled["next_action"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["plan_approval"]["status"], "awaiting_user")
        self.assertEqual([item["gate"] for item in state["attempts"]], ["plan"])

    def test_v3_plan_approval_uses_native_mcp_controls_when_stdio_is_initialized(self):
        started = self.v3_start(
            "use native plan approval controls",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("native approval is pending")),
        })
        with mock.patch.object(control, "MCP_INTERACTIVE", True), mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"decision": "approve"}, "native-plan-1"),
        ) as elicitation:
            approved = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(approved["outcome"], "ready_to_spawn")
        self.assertEqual(approved["dispatches"][0]["phase"], "implementation")
        elicitation.assert_called_once()
        requested_schema = elicitation.call_args.args[1]
        self.assertEqual(requested_schema["properties"]["decision"]["oneOf"], [
            {"const": "approve", "title": "Approve"},
            {"const": "cancel", "title": "Cancel"},
        ])
        self.assertEqual(
            elicitation.call_args.kwargs["meta"]["schema"],
            "cortex/plan-approval/v1",
        )
        self.assertEqual(
            elicitation.call_args.kwargs["meta"]["request_id"],
            self.task_state(next((self.ledger / "tasks").iterdir()))["plan_approval"]["request_id"],
        )

    def test_v3_plan_approval_native_cancel_stays_pending_and_silent(self):
        started = self.v3_start(
            "cancel native plan approval",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("native cancellation is pending")),
        })
        with mock.patch.object(control, "MCP_INTERACTIVE", True), mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("cancel", None, "native-plan-cancel"),
        ):
            cancelled = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["outcome"], "awaiting_plan_approval")
        self.assertEqual(cancelled["dispatches"], [])
        self.assertEqual(cancelled["result"]["decision"], "cancelled")
        self.assertEqual(cancelled["output_policy"], "silent")
        task_dir = next((self.ledger / "tasks").iterdir())
        self.assertEqual(self.task_state(task_dir)["plan_approval"]["status"], "awaiting_user")

    def test_mcp_process_renders_native_plan_approval_and_stays_pending_after_cancel(self):
        started = self.v3_start(
            "nested native plan approval cancellation",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("nested native cancellation is pending")),
        })
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            initialized = call({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {"extensions": {"openai/form": {}}}},
            })
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "cortex")
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "manage_orchestration", "arguments": {
                    "project_root": str(self.project), "task_ref": started["task_ref"],
                    "intent": "plan_approval", "payload": {"decision": "prompt"},
                }},
            }) + "\n")
            proc.stdin.flush()
            elicitation = json.loads(proc.stdout.readline())
            self.assertEqual(elicitation["method"], "elicitation/create")
            self.assertEqual(elicitation["params"]["_meta"]["cortex"]["schema"], "cortex/plan-approval/v1")
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "cancel"},
            }) + "\n")
            proc.stdin.flush()
            completed = json.loads(proc.stdout.readline())
            self.assertEqual(completed["id"], 2)
            structured = completed["result"]["structuredContent"]
            self.assertEqual(structured["outcome"], "awaiting_plan_approval")
            self.assertEqual(structured["dispatches"], [])
            self.assertEqual(structured["result"]["decision"], "cancelled")
            self.assertEqual(structured["output_policy"], "silent")
            task_dir = next((self.ledger / "tasks").iterdir())
            self.assertEqual(self.task_state(task_dir)["plan_approval"]["status"], "awaiting_user")
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_v3_follow_up_creates_a_linked_corrective_task_without_mutating_completed_source(self):
        source = self.v3_start(
            "Заверши исходную задачу до корректирующего запроса",
            complexity="C1",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        source_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(source_dir)
        attempt = state["attempts"][0]
        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("source evidence for a later corrective task")
            ),
        })
        self.assertTrue(published["ok"])
        completed = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": source["task_ref"], "step": source["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        while completed.get("outcome") != "completed":
            dispatches = completed.get("dispatches") or []
            self.assertTrue(dispatches)
            results = self.v3_results(
                completed,
                [self.v3_report(f"complete corrective source phase {completed['step']} worker {slot}")
                 for slot in range(1, len(dispatches) + 1)],
            )
            completed = control.continue_orchestration({
                "project_root": str(self.project), "task_ref": source["task_ref"], "step": completed["step"],
                "results": results,
            })
        self.assertEqual(completed["outcome"], "completed")
        state = control.load_task_state_for_artifact(source_dir)
        created_handoff = control.handoff({
            "project_root": str(self.project), "task_id": state["task_id"], "principal": state["principal"],
            "expected_revision": state["revision"], "completed": ["Source task closed."],
            "files": [], "next_action": "Use this source only as corrective-task context.",
        })
        source_task_before = json.dumps(self.task_definition(source_dir), sort_keys=True)
        source_state_before = json.dumps(self.task_state(source_dir), sort_keys=True)

        follow_up = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": source["task_ref"], "intent": "follow_up",
            "payload": {
                "user_request": "Correct the behavior that was wrong in the completed source task.",
                "complexity": "C1", "report_refs": [published["report_ref"]],
                "acceptance_criteria": ["The incorrect behavior is corrected."],
                "verification": ["Run a focused regression check for the correction."],
            },
        })
        self.assertTrue(follow_up["ok"])
        self.assertEqual(follow_up["outcome"], "ready_to_spawn")
        self.assertNotEqual(follow_up["task_ref"], source["task_ref"])
        self.assertEqual(follow_up["follow_up"]["source_task_ref"], source["task_ref"])
        self.assertEqual(
            follow_up["follow_up"]["source_handoff_path"],
            created_handoff["handoff_file"],
        )
        self.assertTrue(Path(follow_up["follow_up"]["source_report_markdown_paths"][0]).is_file())
        self.assertEqual(json.dumps(self.task_definition(source_dir), sort_keys=True), source_task_before)
        self.assertEqual(json.dumps(self.task_state(source_dir), sort_keys=True), source_state_before)

        task_dirs = sorted((self.ledger / "tasks").iterdir())
        corrective_dir = next(path for path in task_dirs if path != source_dir)
        corrective_task = control.load_task_definition(corrective_dir)
        corrective_state = control.load_task_state_for_artifact(corrective_dir)
        self.assertEqual(corrective_task["pipeline_contract_version"], 2)
        self.assertEqual(corrective_state["pipeline_contract_version"], 2)
        self.assertEqual(corrective_task["follow_up"]["source_task_ref"], source["task_ref"])
        self.assertEqual(corrective_task["follow_up"]["source_report_refs"], [published["report_ref"]])
        self.assertEqual(corrective_task["user_language"], "ru")
        prompt = self.briefing_from_response(follow_up)
        self.assertIn("Follow-up context: this corrective task is linked", prompt)
        self.assertIn(created_handoff["handoff_file"], prompt)

        deactivated = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": follow_up["task_ref"], "intent": "deactivate",
        })
        self.assertTrue(deactivated["ok"])
        corrective_state = control.load_task_state_for_artifact(corrective_dir)
        corrective_principal = corrective_state["principal"]
        self.assertFalse(control.activation_status({"project_root": str(self.project), "principal": corrective_principal})["active"])

        replay = control.manage_orchestration({
            "project_root": str(self.project), "intent": "follow_up",
            "payload": {
                "task_ref": source["task_ref"],
                "user_request": "Correct the behavior that was wrong in the completed source task.",
                "complexity": "C1", "report_refs": [published["report_ref"]],
                "acceptance_criteria": ["The incorrect behavior is corrected."],
                "verification": ["Run a focused regression check for the correction."],
            },
        })
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["task_ref"], follow_up["task_ref"])
        self.assertEqual(replay["dispatches"], [])
        self.assertTrue(replay["replayed"])
        self.assertIn("idempotent replay", replay["next_action"])
        self.assertNotIn("/cortex", replay["next_action"])
        self.assertTrue(control.activation_status({"project_root": str(self.project), "principal": corrective_principal})["active"])
        self.assertEqual(json.dumps(self.task_definition(source_dir), sort_keys=True), source_task_before)
        self.assertEqual(json.dumps(self.task_state(source_dir), sort_keys=True), source_state_before)

    def test_v3_follow_up_rejects_an_active_source_task(self):
        source = self.v3_start("active source task", waves=[{"workers": [{"phase": "discover"}]}])
        rejected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": source["task_ref"], "intent": "follow_up",
            "payload": {"user_request": "Correct an active task instead of reopening it."},
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("requires a completed source task", rejected["diagnostics"][0]["message"])

    def test_planner_materializes_task_local_work_packages_and_exposes_them_for_review(self):
        started = self.v3_start(
            "produce a durable work breakdown",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("planner produced a package graph")
        planning = {
            "overview": "Deliver the API and UI as separately owned packages after the API dependency is ready.",
            "work_packages": [
                {
                    "id": "api", "title": "API", "objective": "Add the service contract.",
                    "allowed_paths": ["src/api"],
                    "microtasks": [{
                        "id": "contract", "title": "Define contract", "objective": "Create the public contract.",
                        "profile": "backend_dev", "allowed_paths": ["src/api"],
                        "acceptance_criteria": ["Contract is documented."], "verification": ["Run API tests."],
                    }],
                },
                {
                    "id": "ui", "title": "UI", "objective": "Consume the service contract.",
                    "depends_on": ["api"], "allowed_paths": ["src/ui"],
                    "microtasks": [{
                        "id": "screen", "title": "Build screen", "objective": "Render the new UI.",
                        "profile": "frontend_dev", "allowed_paths": ["src/ui"],
                        "acceptance_criteria": ["Screen renders the contract data."], "verification": ["Run UI tests."],
                    }],
                },
            ],
        }
        report = self._report_with_briefing(attempt, report)
        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": report, "planning": planning,
        })
        self.assertTrue(published["ok"])
        manifest = control.current_planning_manifest(task_dir)
        self.assertEqual(manifest["schema"], control.PLANNING_SCHEMA)
        self.assertEqual(manifest["source_report_ref"], published["report_ref"])
        self.assertEqual([package["id"] for package in manifest["work_packages"]], ["api", "ui"])
        self.assertFalse((task_dir / "planning").exists())
        from cortex_runtime.projection_service import reconcile as reconcile_projections
        reconcile_projections(self.ledger, worker_id="planning-test")
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/overview.md").is_file())
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/packages/api.json").is_file())
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/packages/ui.json").is_file())

        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        artifacts = held["plan_review"]["planning_artifacts"]
        self.assertEqual(artifacts["manifest_ref"], "sqlite:task_documents/planning_current")
        self.assertEqual(artifacts["overview_path"], "planning/revisions/plan-report-0001/overview.md")
        self.assertEqual([package["id"] for package in artifacts["work_packages"]], ["api", "ui"])

        prompt = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "plan_approval",
            "payload": {"decision": "prompt"},
        })
        approved = control.manage_orchestration(prompt["plan_approval_interaction"]["actions"][0]["arguments"])
        self.assertEqual(approved["outcome"], "ready_to_spawn")
        completed = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": approved["step"],
            "results": self.v3_results(approved, self.v3_report("implementation completed")),
        })
        for _ in range(4):
            if completed["outcome"] != "ready_to_spawn":
                break
            completed = control.continue_orchestration({
                "project_root": str(self.project), "task_ref": started["task_ref"],
                "step": completed["step"],
                "results": self.v3_results(completed, self.v3_report("terminal gate completed")),
            })
        self.assertEqual(completed["outcome"], "completed")
        terminal_manifest = control.current_planning_manifest(task_dir)
        self.assertEqual(terminal_manifest, manifest)
        self.assertTrue(all(
            control.db_get_artifact_for_export_path(
                self.ledger, state["task_id"], package["artifact_path"],
            ) is not None
            for package in terminal_manifest["work_packages"]
        ))

    def test_planner_work_packages_reject_cycles_and_missing_artifact(self):
        started = self.v3_start("validate work breakdown artifacts", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        cyclic = self.v3_planning()
        cyclic["work_packages"][0]["depends_on"] = ["core"]
        rejected = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("invalid plan")),
            "planning": cyclic,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_validation_failed")
        self.assertIn("cannot depend on itself", rejected["diagnostics"][0]["message"])

        missing = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("missing plan artifact")),
        })
        self.assertFalse(missing["ok"])
        self.assertIn("planner reports require", missing["diagnostics"][0]["message"])
        self.assertTrue(started["ok"])

    def test_planner_microtasks_allow_cross_package_dependencies_but_keep_one_global_dag(self):
        planning = self.v3_planning()
        planning["work_packages"].append({
            "id": "integration",
            "title": "Integration",
            "objective": "Verify the core delivery through the public boundary.",
            "depends_on": ["core"],
            "allowed_paths": ["tests"],
            "microtasks": [{
                "id": "integration_test",
                "title": "Exercise the integration",
                "objective": "Verify the core microtask from another package.",
                "profile": "qa_engineer",
                "allowed_paths": ["tests"],
                "depends_on": ["core_change"],
                "acceptance_criteria": ["The integration observes the core behavior."],
                "verification": ["Run the focused integration test."],
            }],
        })
        sanitized = control.sanitize_planning_payload(planning)
        self.assertEqual(
            sanitized["work_packages"][1]["microtasks"][0]["depends_on"],
            ["core_change"],
        )

        sanitized["work_packages"][0]["microtasks"][0]["depends_on"] = ["integration_test"]
        with self.assertRaisesRegex(ValueError, "planning microtask dependencies must be acyclic"):
            control.sanitize_planning_payload({
                "overview": sanitized["overview"],
                "work_packages": sanitized["work_packages"],
            }, persisted=True)

        planning["work_packages"][1]["microtasks"][0]["depends_on"] = ["missing_microtask"]
        with self.assertRaisesRegex(ValueError, "depends on unknown item"):
            control.sanitize_planning_payload(planning)

    def test_gate_report_accepts_concise_observed_check_output(self):
        started = self.v3_start(
            "accept concise deterministic QA evidence",
            waves=[{"workers": [{"phase": "qa", "profile": "qa_engineer"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self._report_with_briefing(attempt, self.v3_report("QA completed"))
        report["tests"][0]["evidence"] = "No whitespace errors reported."
        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": report,
        })
        self.assertTrue(published["ok"], published)

    def test_gate_report_still_rejects_empty_check_output(self):
        started = self.v3_start(
            "reject missing QA evidence",
            waves=[{"workers": [{"phase": "qa", "profile": "qa_engineer"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self._report_with_briefing(attempt, self.v3_report("QA completed"))
        report["tests"][0]["evidence"] = ""
        rejected = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("concrete observed output summary", rejected["diagnostics"][0]["message"])

    def test_planner_scope_owns_the_strict_scoping_artifact(self):
        started = self.v3_start(
            "scope the evidence domains before discovery",
            complexity="C3",
            plan_approval="auto",
            waves=[{"workers": [{"phase": "scope"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        attempt = state["attempts"][0]
        report = self._report_with_briefing(attempt, self.v3_report("scoping complete"))

        missing = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(missing["ok"])
        self.assertIn("scope reports require", missing["diagnostics"][0]["message"])

        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": report, "scoping": self.v3_scoping(),
        })
        self.assertTrue(published["ok"], published)
        read = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
        })
        self.assertEqual(read["scoping"]["overview"], self.v3_scoping()["overview"])

        discover = self.v3_start(
            "reject scoping from discovery",
            complexity="C1",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        registry = control._operation_registry(self.ledger)
        discover_task_id = next(
            task_id for task_id, item in registry["tasks"].items()
            if item.get("start", {}).get("task_ref") == discover["task_ref"]
        )
        discover_dir, discover_state, _ = control._v3_task_state(self.ledger, discover_task_id)
        discover_attempt = discover_state["attempts"][0]
        rejected = control.publish_worker_report({
            "project_root": str(self.project), "task_id": discover_state["task_id"],
            "attempt_id": discover_attempt["attempt_id"], "profile": discover_attempt["profile"],
            "report": self._report_with_briefing(discover_attempt, self.v3_report("wrong owner")),
            "scoping": self.v3_scoping(),
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("only by the active planner scope attempt", rejected["diagnostics"][0]["message"])

    def test_scoping_domains_reject_duplicates_cycles_overflow_and_incomplete_criteria(self):
        base = self.v3_scoping()
        self.assertEqual(control.sanitize_scoping_payload(base)["schema"], control.SCOPING_SCHEMA)

        duplicate = dict(base)
        duplicate["discovery_domains"] = [dict(base["discovery_domains"][0]), dict(base["discovery_domains"][0])]
        with self.assertRaisesRegex(ValueError, "unique"):
            control.sanitize_scoping_payload(duplicate)

        overflow = dict(base)
        overflow["discovery_domains"] = [
            {**dict(base["discovery_domains"][0]), "id": f"domain_{index}", "title": f"Domain {index}"}
            for index in range(control.MAX_DISCOVERY_DOMAINS + 1)
        ]
        with self.assertRaisesRegex(ValueError, "1..8"):
            control.sanitize_scoping_payload(overflow)

        cycle = dict(base)
        first = {**dict(base["discovery_domains"][0]), "id": "first", "depends_on": ["second"]}
        second = {**dict(base["discovery_domains"][0]), "id": "second", "title": "Second", "depends_on": ["first"]}
        cycle["discovery_domains"] = [first, second]
        with self.assertRaisesRegex(ValueError, "acyclic"):
            control.sanitize_scoping_payload(cycle)

        incomplete = json.loads(json.dumps(base))
        incomplete["discovery_domains"][0]["verification"] = []
        with self.assertRaisesRegex(ValueError, "verification"):
            control.sanitize_scoping_payload(incomplete)

    def test_v3_plan_approval_revision_restarts_planner_with_user_feedback(self):
        started = self.v3_start(
            "revise the plan before implementation",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("first plan")),
        })
        revised = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "plan_approval",
            "payload": {"decision": "revise", "feedback": "Keep the public API unchanged and add rollback coverage."},
        })
        self.assertTrue(revised["ok"])
        self.assertEqual(revised["outcome"], "ready_to_spawn")
        self.assertEqual(revised["dispatches"][0]["phase"], "plan")
        self.assertIn("Keep the public API unchanged", self.briefing_from_response(revised))
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["plan_approval"]["status"], "pending_plan")
        self.assertEqual(len([item for item in state["attempts"] if item["gate"] == "plan"]), 2)

        held_again = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": revised["step"],
            "results": self.v3_results(revised, self.v3_report("revised plan")),
        })
        self.assertTrue(held_again["ok"])
        self.assertEqual(held_again["outcome"], "awaiting_plan_approval")

    def test_v3_report_read_accepts_bounded_large_task_state_without_embedding_reports(self):
        started = self.v3_start("large harvest state", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        attempt = state["attempts"][0]
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("large-state report remains readable")
            ),
        })
        state = self.task_state(task_dir)
        state["bounded_large_state_fixture"] = "x" * (control.MAX_REPORT_BYTES * 5)
        self.write_task_state(state)
        read = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
        })
        self.assertTrue(read["ok"])
        self.assertEqual(read["report"]["summary"], "large-state report remains readable")

    def test_v3_artifact_management_pages_metadata_and_streams_large_markdown(self):
        started = self.v3_start("stream task artifacts", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        content = "# Evidence\n\n" + ("source-backed behavior\n" * 5000)
        artifact = control.store_immutable_artifact(
            task_dir,
            state["task_id"],
            kind="report_markdown",
            title="reports/markdown/large.md",
            mime_type="text/markdown",
            content=content,
            export_path="reports/markdown/large.md",
        )
        page = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "list", "kind": "report_markdown", "page_size": 1},
        })
        self.assertTrue(page["ok"])
        self.assertEqual(page["artifacts"], [artifact])
        metadata = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "metadata", "artifact_ref": artifact["artifact_ref"]},
        })
        first = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "read", "artifact_ref": artifact["artifact_ref"], "cursor": metadata["read_cursor"], "max_bytes": 4096},
        })
        self.assertTrue(first["ok"])
        self.assertFalse(first["complete"])
        self.assertLessEqual(first["returned_bytes"], 4096)
        self.assertTrue(first["content_part"].startswith("# Evidence"))
        oversized = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {
                "action": "read", "artifact_ref": artifact["artifact_ref"],
                "cursor": metadata["read_cursor"], "max_bytes": control.MAX_BRIEFING_BYTES,
            },
        })
        self.assertTrue(oversized["ok"], oversized)
        self.assertTrue(oversized["max_bytes_normalized"])
        self.assertEqual(oversized["requested_max_bytes"], control.MAX_BRIEFING_BYTES)
        self.assertEqual(oversized["effective_max_bytes"], control.ARTIFACT_TRANSPORT_MAX_BYTES)
        denied = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "read", "artifact_ref": artifact["artifact_ref"], "cursor": first["next_cursor"] + "x"},
        })
        self.assertFalse(denied["ok"])
        self.assertIn("cursor", denied["diagnostics"][0]["message"])

    def test_v3_scoped_large_report_read_uses_cursor_not_an_inline_body(self):
        started = self.v3_start("page a large report", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        attempt = state["attempts"][0]
        record = {
            "schema": control.REPORT_SCHEMA, "report_id": "report-0099", "task_id": state["task_id"],
            "gate": "discover", "attempt_id": attempt["attempt_id"], "submission_id": "large-report",
            "producer": {"profile": attempt["profile"], "model": attempt["selected_model"], "reasoning_effort": attempt["selected_reasoning_effort"]},
            "report": {"summary": "large", "findings": ["x" * 50000], "questions": [], "changed_files": [], "tests": [], "evidence": ["bounded"], "uncertainty": []},
            "planning": None, "result_validation": None, "content_digest": "test", "created_at": control.now(),
        }
        artifact = control.store_immutable_artifact(
            task_dir, state["task_id"], kind="worker_report", title="reports/records/report-0099.json",
            mime_type="application/json", content=json.dumps(record), export_path="reports/records/report-0099.json",
        )
        control.store_immutable_artifact(
            task_dir, state["task_id"], kind="report_markdown", title="reports/markdown/report-0099.md",
            mime_type="text/markdown", content="# Large report\n", export_path="reports/markdown/report-0099.md",
        )
        first = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"], "report_ref": "report-0099", "max_bytes": 4096,
        })
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["report_artifact"]["artifact_ref"], artifact["artifact_ref"])
        self.assertNotIn("report", first)
        self.assertFalse(first["complete"])
        self.assertLessEqual(first["returned_bytes"], 4096)
        oversized = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "report_ref": "report-0099", "max_bytes": control.MAX_BRIEFING_BYTES,
        })
        self.assertTrue(oversized["ok"], oversized)
        self.assertTrue(oversized["max_bytes_normalized"])
        self.assertEqual(oversized["effective_max_bytes"], control.ARTIFACT_TRANSPORT_MAX_BYTES)
        second = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"], "report_ref": "report-0099", "cursor": first["next_cursor"], "max_bytes": 4096,
        })
        self.assertTrue(second["ok"])
        self.assertGreater(second["byte_offset"], first["byte_offset"])

    def test_v3_report_read_returns_recoverable_result_for_missing_identity_or_record(self):
        missing_root = control.read_worker_report({"report_ref": "report-0001"})
        self.assertFalse(missing_root["ok"])
        self.assertEqual(missing_root["code"], "report_read_request_invalid")
        self.assertTrue(missing_root["retryable"])
        self.assertFalse(missing_root["attempt_budget_consumed"])
        started = self.v3_start("missing report", waves=[{"workers": [{"phase": "discover"}]}])
        missing_record = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"], "report_ref": "report-9999",
        })
        self.assertFalse(missing_record["ok"])
        self.assertEqual(missing_record["code"], "report_read_request_invalid")
        self.assertTrue(missing_record["retryable"])
        self.assertFalse(missing_record["attempt_budget_consumed"])

    def test_large_baseline_manifest_is_readable_during_handoff_and_reconciliation(self):
        started = self.v3_start("large baseline handoff", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        baseline = control.load_manifest_snapshot(
            task_dir, state["initial_manifest_ref"], "test task baseline"
        )
        baseline["policy"]["test_padding"] = "x" * (control.MAX_REPORT_BYTES * 5)
        padded_ref = control.manifest_snapshot_ref(baseline)
        control.db_put_manifest_snapshot(self.ledger, padded_ref, baseline["digest"], baseline)
        state["initial_manifest_ref"] = padded_ref
        self.write_task_state(state)

        receipt, _ = control.reconcile_manifest(task_dir, state, [])
        self.assertEqual(receipt["baseline_digest"], baseline["digest"])

        with mock.patch.object(control, "handoff", return_value={"recorded": True}) as handoff:
            result = control._auto_handoff(
                {"project_root": str(self.project), "principal": "thread-a"},
                task_dir,
                state,
                "Close the Cortex task.",
            )
        self.assertTrue(result["recorded"])
        handoff.assert_called_once()

    def test_json_write_budget_rejects_before_replacing_existing_file(self):
        path = self.base / "bounded.json"
        path.write_text("sentinel\n", encoding="utf-8")
        original_limit = control.MAX_JSON_BYTES
        try:
            control.MAX_JSON_BYTES = 128
            with self.assertRaisesRegex(ValueError, r"JSON document 'bounded\.json' is oversized"):
                control.write_json(path, {"padding": "x" * 512})
        finally:
            control.MAX_JSON_BYTES = original_limit
        self.assertEqual(path.read_text(encoding="utf-8"), "sentinel\n")

    def test_oversized_baseline_is_rejected_before_task_directory_creation(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        baseline = {
            "schema": control.TRACKER_POLICY["schema"],
            "project_root": str(self.project),
            "policy": {},
            "entries": {},
            "entry_count": 0,
            "digest": "digest",
            "captured_at": control.now(),
            "padding": "x" * 512,
        }
        original_limit = control.MAX_MANIFEST_BYTES
        try:
            control.MAX_MANIFEST_BYTES = 128
            with mock.patch.object(control, "capture_project_manifest", return_value=baseline):
                with self.assertRaisesRegex(ValueError, "baseline manifest is oversized"):
                    control.init_task({
                        "task_id": "oversized-baseline",
                        "objective": "reject oversized baseline",
                        "complexity": "C1",
                        "classification_id": classified["classification_id"],
                        "principal": "thread-a",
                    })
        finally:
            control.MAX_MANIFEST_BYTES = original_limit
        self.assertEqual(list((self.ledger / "tasks").iterdir()), [])

    def test_public_worker_report_requires_predecessor_review_acknowledgement(self):
        started = self.v3_start("enforced handoff review", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation", "depends_on": ["discover"]}]},
        ])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        first_attempt = state["attempts"][0]
        first = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": first_attempt["attempt_id"],
            "profile": first_attempt["profile"],
            "report": self._report_with_briefing(
                first_attempt, self.v3_report("first verified handoff")
            ),
        })
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"report_ref": first["report_ref"]}],
        })
        self.assertTrue(advanced["ok"])
        state = control.load_task_state_for_artifact(task_dir)
        second_attempt = next(item for item in state["attempts"] if item["gate"] == "implementation")
        changed_path = self.project / "implemented.txt"
        changed_path.write_text("implemented\n", encoding="utf-8")
        missing_ack_report = self.v3_report("missing review acknowledgement")
        missing_ack_report["changed_files"] = ["implemented.txt"]
        missing_ack_report = self._report_with_briefing(second_attempt, missing_ack_report)
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": second_attempt["attempt_id"],
            "profile": second_attempt["profile"],
            "report": missing_ack_report,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_evidence_incomplete")
        self.assertIn("Predecessor review: report-0001", rejected["diagnostics"][0]["message"])
        accepted_report = self.v3_report("review acknowledged")
        accepted_report["changed_files"] = ["implemented.txt"]
        accepted_report["evidence"].append(f"Predecessor review: {first['report_ref']}")
        accepted_report = self._report_with_briefing(second_attempt, accepted_report)
        accepted = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": second_attempt["attempt_id"],
            "profile": second_attempt["profile"],
            "report": accepted_report,
        })
        self.assertTrue(accepted["ok"])

    def test_public_worker_report_returns_structured_identity_and_path_corrections(self):
        started = self.v3_start("structured worker report corrections", waves=[
            {"workers": [{"phase": "discover"}]},
        ])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]

        missing_identity = control.publish_worker_report({
            "project_root": str(self.project),
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self.v3_report("missing task identity"),
        })
        self.assertFalse(missing_identity["ok"])
        self.assertEqual(missing_identity["code"], "report_identity_invalid")
        self.assertIn("exact project_root, task_id, attempt_id, and profile", missing_identity["next_action"])

        invalid_paths = self.v3_report("invalid changed files")
        invalid_paths["changed_files"] = [str(self.project / "README.md")]
        rejected_path = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": invalid_paths,
        })
        self.assertFalse(rejected_path["ok"])
        self.assertEqual(rejected_path["code"], "report_changed_files_invalid")
        self.assertIn("project-relative", rejected_path["next_action"])

        unsupported = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self.v3_report("unsupported field"),
            "principal": "do-not-guess",
        })
        self.assertFalse(unsupported["ok"])
        self.assertEqual(unsupported["code"], "report_validation_failed")
        self.assertIn("unsupported record_report fields", unsupported["diagnostics"][0]["message"])

        with mock.patch.object(control, "record_report", side_effect=ValueError("report index is unreadable")):
            with self.assertRaisesRegex(ValueError, "report index is unreadable"):
                control.publish_worker_report({
                    "project_root": str(self.project),
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": attempt["profile"],
                    "report": self.v3_report("server corruption remains observable"),
                })

    def test_v3_workers_consume_project_knowledge_indexes_and_acknowledge_review(self):
        project_docs = self.project / "docs/project"
        feature_docs = self.project / "docs/features"
        project_docs.mkdir(parents=True)
        feature_docs.mkdir(parents=True)
        (feature_docs / "trading").mkdir()
        (project_docs / "index.md").write_text("# Project knowledge\n", encoding="utf-8")
        (feature_docs / "index.md").write_text("# Feature catalog\n", encoding="utf-8")
        (feature_docs / "trading/index.md").write_text("# Trading\n", encoding="utf-8")
        started = self.v3_start("use existing repository knowledge", waves=[
            {"workers": [{
                "phase": "plan",
                "context_files": ["docs/features/trading/index.md"],
            }]},
        ])
        self.assertTrue(started["ok"])
        prompt = self.briefing_from_response(started)
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(
            assignment["context_files"],
            ["docs/project/index.md", "docs/features/index.md", "docs/features/trading/index.md"],
        )
        self.assertIn("Before broad source search, design, or edits", prompt)
        self.assertIn("docs/features/index.md as the capability/coverage catalog", prompt)
        self.assertIn("Every changed_files item must be a safe project-relative path", prompt)
        self.assertIn("Required report evidence acknowledgements for this exact attempt", prompt)
        self.assertIn("Knowledge reviewed: docs/project/index.md, docs/features/index.md", prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(
            attempt["knowledge_index_files"],
            ["docs/project/index.md", "docs/features/index.md"],
        )
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("knowledge was not acknowledged")
            ),
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_evidence_incomplete")
        self.assertIn("Knowledge reviewed: docs/features/index.md, docs/project/index.md", rejected["diagnostics"][0]["message"])
        report = self.v3_report("knowledge-guided plan complete")
        report["evidence"].append(
            "Knowledge reviewed: docs/project/index.md, docs/features/index.md, docs/features/trading/index.md"
        )
        report = self._report_with_briefing(attempt, report)
        accepted = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
            "planning": self.v3_planning(),
        })
        self.assertTrue(accepted["ok"])

    def test_harvest_documentation_cannot_publish_with_a_shallow_feature_index(self):
        project_docs = self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        feature_index = feature_docs / "index.md"
        feature_index.write_text("# Features\n\n- [Trading](trading/index.md)\n", encoding="utf-8")
        started = self.v3_start("harvest exhaustive knowledge", waves=[
            {"workers": [{"phase": "documentation"}]},
        ])
        self.assertTrue(started["ok"])
        writer_prompt = self.briefing_from_response(started)
        self.assertIn(
            "`Feature`, `Runtime owner`, `Entry points`, `Source evidence`, `Documentation`, `Verification`, `Status`",
            writer_prompt,
        )
        self.assertIn(
            "`Coverage matrix`, `Inventory totals`, `Unmapped surfaces`, `Exclusions`, `Known unknowns`",
            writer_prompt,
        )
        self.assertIn("extra columns may follow only after them", writer_prompt)
        self.assertIn("status is exactly `covered`, `documented`, `verified`, or `excluded`", writer_prompt)
        self.assertIn("docs/project/conventions.md", writer_prompt)
        self.assertIn("docs/features/<feature>/index.md", writer_prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self.v3_report("documentation written")
        report["evidence"].append(
            "Knowledge reviewed: docs/project/index.md, docs/features/index.md"
        )
        report = self._report_with_briefing(attempt, report)
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "harvest_manifest_invalid")
        self.assertIn("shallow or incomplete", rejected["diagnostics"][0]["message"])
        self.assertIn(
            "matrix columns (expected exact header prefix: Feature | Runtime owner | Entry points",
            rejected["diagnostics"][0]["message"],
        )
        feature_index.write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n"
            "## Coverage matrix\n\n"
            "The required labels all appear, but an extra leading column violates the exact prefix.\n\n"
            "| Surface | Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| service.py | Trading | engine | command | service.py | trading.md | test.py | documented |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        report["changed_files"] = ["docs/features/index.md"]
        rejected_prose_only = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected_prose_only["ok"])
        self.assertEqual(rejected_prose_only["code"], "harvest_manifest_invalid")
        self.assertIn("expected exact header prefix", rejected_prose_only["diagnostics"][0]["message"])
        feature_index.write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n"
            "## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | [trading](trading/index.md) | test.py | documented |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        trading_page = feature_docs / "trading/index.md"
        trading_page.parent.mkdir()
        trading_page.write_text(
            "# Trading\n\n## Runtime owner\n\nThe runtime owns trading.\n\n"
            "## Behavior and workflow\n\nThe workflow handles an order scenario.\n\n"
            "## State and data\n\nState is persisted as order data.\n\n"
            "## Interfaces\n\nThe API route is the entry point.\n\n"
            "## Failure and recovery\n\nAn error triggers recovery.\n\n"
            "## Verification\n\nTests verify the behavior.\n",
            encoding="utf-8",
        )
        report["changed_files"] = ["docs/features/index.md", "docs/features/trading/index.md"]
        accepted = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertTrue(accepted["ok"])

    def test_harvest_coverage_matrix_requires_complete_structured_rows(self):
        self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        (feature_docs / "index.md").write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n"
            "## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | no canonical link | test.py | complete-ish |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "status must be covered, documented, verified, or excluded.*Documentation must link",
        ):
            control._validate_harvest_coverage_manifest(
                self.project,
                {"objective": "harvest exhaustive repository knowledge"},
                "documentation",
            )

    def test_harvest_requires_all_canonical_project_documents_and_index_links(self):
        project_docs = self.project / "docs/project"
        project_docs.mkdir(parents=True)
        (project_docs / "index.md").write_text(
            "# Project knowledge\n\nThis sufficiently detailed index describes verified project behavior and evidence boundaries for maintainers.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "harvest canonical project documentation is incomplete.*conventions.md.*verification.md.*decisions.md.*gotchas.md",
        ):
            control._validate_harvest_coverage_manifest(
                self.project,
                {"objective": "harvest exhaustive repository knowledge"},
                "documentation",
            )

        started = self.v3_start("harvest exhaustive repository knowledge", waves=[
            {"workers": [{"phase": "documentation"}]},
        ])
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self._report_with_briefing(attempt, self.v3_report("project documentation written"))
        report["evidence"].append("Knowledge reviewed: docs/project/index.md")
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "harvest_manifest_invalid")
        self.assertIn("harvest canonical project documentation is incomplete", rejected["diagnostics"][0]["message"])

        self.write_canonical_harvest_project_docs()
        (project_docs / "index.md").write_text(
            "# Project knowledge\n\n"
            "This sufficiently detailed index describes verified project behavior and evidence boundaries for maintainers without navigation links.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "harvest project index must link every canonical project document.*conventions.md.*verification.md.*decisions.md.*gotchas.md",
        ):
            control._validate_harvest_coverage_manifest(
                self.project,
                {"objective": "harvest exhaustive repository knowledge"},
                "documentation",
            )

    def test_harvest_rejects_flat_feature_page_as_canonical_entry_point(self):
        self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        (feature_docs / "index.md").write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n"
            "## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | [Trading](trading.md) | test.py | documented |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        (feature_docs / "trading.md").write_text(
            "# Trading\n\n## Runtime owner\n\nThe runtime owner controls trading behavior.\n\n"
            "## Behavior and workflow\n\nThe workflow accepts and processes an order scenario.\n\n"
            "## State and data\n\nOrder state is stored and retrieved.\n\n"
            "## Interfaces\n\nA command starts the operation.\n\n"
            "## Failure and recovery\n\nErrors retain state and permit recovery.\n\n"
            "## Verification\n\nFocused tests verify successful and failing behavior.\n",
            encoding="utf-8",
        )
        started = self.v3_start("harvest exhaustive repository knowledge", waves=[
            {"workers": [{"phase": "documentation"}]},
        ])
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        report = self._report_with_briefing(attempt, self.v3_report("flat feature page written"))
        report["evidence"].append(
            "Knowledge reviewed: docs/project/index.md, docs/features/index.md"
        )
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": report,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "harvest_manifest_invalid")
        self.assertIn(
            "Documentation must include a canonical docs/features/<feature>/index.md link",
            rejected["diagnostics"][0]["message"],
        )

    def test_v3_context_files_reject_escape_missing_and_symlink_paths(self):
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "relevant.md").write_text("# Relevant\n", encoding="utf-8")
        outside = self.base / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (docs / "linked.md").symlink_to(outside)
        for context_file in ("../outside.md", str(outside), "docs/missing.md", "docs/linked.md"):
            with self.subTest(context_file=context_file):
                rejected = self.v3_start(
                    f"reject unsafe context {context_file}",
                    waves=[{"workers": [{"phase": "discover", "context_files": [context_file]}]}],
                )
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["code"], "start_validation_failed")
                self.assertIn("context file", rejected["diagnostics"][0]["message"].replace("_", " "))
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_depends_on_selects_exact_predecessor_phases(self):
        started = self.v3_start("semantic handoff dependencies", plan_approval="auto", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "architecture", "depends_on": ["discover"]}]},
            {"workers": [{"phase": "plan"}]},
            {"workers": [{"phase": "implementation", "depends_on": ["plan"]}]},
        ])
        current = started
        reports = []
        for summary in ("discovery handoff", "architecture handoff", "plan handoff"):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": current["step"],
                "results": self.v3_results(current, self.v3_report(summary)),
            })
            self.assertTrue(current["ok"], current)
            reports.append(summary)
            prompt = self.briefing_from_response(current)
            if current["dispatches"][0]["phase"] == "architecture":
                self.assertIn("Verified predecessor handoff refs: report-0001", prompt)
                self.assertNotIn("report-0002", prompt)
            if current["dispatches"][0]["phase"] == "plan":
                self.assertIn("Verified predecessor handoff refs: report-0002", prompt)
                self.assertNotIn("report-0001", prompt)
            if current["dispatches"][0]["phase"] == "implementation":
                self.assertIn("Verified predecessor handoff refs: report-0003", prompt)
                self.assertNotIn("report-0001", prompt)
                self.assertNotIn("report-0002", prompt)

    def test_v3_final_planner_accepts_full_twelve_report_predecessor_basis(self):
        self.assertEqual(control.MAX_CONTEXT_REPORTS, 8)
        started = self.v3_start("full bounded planner evidence", plan_approval="auto", waves=[
            {"workers": [{"phase": "scope"}]},
            {"workers": [
                {"phase": "discover", "objective": f"Inspect discovery domain {index}."}
                for index in range(1, control.MAX_DISCOVERY_DOMAINS + 1)
            ]},
            {"workers": [
                {"phase": "architecture"},
                {"phase": "database_architecture"},
                {"phase": "ux"},
            ]},
            {"workers": [{"phase": "plan"}]},
        ])
        current = started
        for summary in ("scope", "discovery", "design"):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": current["step"],
                "results": self.v3_results(current, self.v3_report(f"{summary} evidence")),
            })
            self.assertTrue(current["ok"], current)

        self.assertEqual(current["dispatches"][0]["phase"], "plan")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        plan_attempt = next(
            attempt for attempt in state["attempts"]
            if attempt["gate"] == "plan" and attempt["status"] == control.AWAITING_HOST_SPAWN
        )
        package = self.task_document(task_dir, f"dispatch:{plan_attempt['attempt_id']}")
        self.assertEqual(package["context_report_ids"], ["report-0010", "report-0011", "report-0012"])
        basis, _ = orchestration_engine._verified_plan_predecessor_basis(task_dir, state)
        self.assertEqual([item["report_ref"] for item in basis], [f"report-{index:04d}" for index in range(1, 13)])
        prompt = self.briefing_from_response(current)
        self.assertNotIn("report-0001", prompt)
        self.assertIn("Verified predecessor handoff refs: report-0010", prompt)
        self.assertIn("report-0012", prompt)

    def test_v3_predecessor_dispatch_has_no_separate_report_count_limit(self):
        started = self.v3_start("all bounded task evidence reaches planner", plan_approval="auto", waves=[
            {"workers": [{"phase": "scope"}]},
            {"workers": [
                {"phase": "discover", "objective": f"Inspect source partition {index}."}
                for index in range(1, 33)
            ]},
            {"workers": [{"phase": "plan"}]},
        ])
        discovery = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("scope evidence")),
        })
        self.assertTrue(discovery["ok"], discovery)
        planner = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": discovery["step"],
            "results": self.v3_results(discovery, self.v3_report("partition evidence")),
        })
        self.assertTrue(planner["ok"], planner)
        self.assertEqual(planner["dispatches"][0]["phase"], "plan")

        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        plan_attempt = next(
            attempt for attempt in state["attempts"]
            if attempt["gate"] == "plan" and attempt["status"] == control.AWAITING_HOST_SPAWN
        )
        package = self.task_document(task_dir, f"dispatch:{plan_attempt['attempt_id']}")
        self.assertEqual(len(package["context_report_ids"]), 32)
        self.assertEqual(package["context_report_ids"][0], "report-0002")
        self.assertEqual(package["context_report_ids"][-1], "report-0033")

    def test_transitive_predecessor_frontier_scales_past_one_thousand_reports(self):
        attempts = []
        report_ids = []
        for index in range(1, 1002):
            report_id = f"report-{index:04d}"
            report_ids.append(report_id)
            attempts.append({
                "attempt_id": f"discover-{index:04d}",
                "gate": "discover",
                "status": "passed",
                "report_ids": [report_id],
                "context_report_ids": ([report_ids[-2]] if index > 1 else []),
            })
        frontier = orchestration_engine._transitive_context_frontier(
            {"attempts": attempts}, report_ids,
        )
        self.assertEqual(frontier, ["report-1001"])

    def test_v3_inspect_recovers_report_when_native_worker_ack_is_interrupted(self):
        started = self.v3_start("recover persisted report", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        published = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("persisted before native acknowledgement interruption")
            ),
        })
        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"])
        self.assertEqual(inspected["step"], started["step"])
        self.assertEqual(
            inspected["result"]["available_reports"],
            [{
                "report_ref": published["report_ref"],
                "phase": "discover",
                "profile": "explorer",
                "summary": "persisted before native acknowledgement interruption",
                "report_markdown_path": str(task_dir / "reports/markdown/report-0001.md"),
                "report_markdown_link": f"[Report discover — report-0001](<{task_dir / 'reports/markdown/report-0001.md'}>)",
            }],
        )
        self.assertEqual(inspected["context_handoff"]["schema"], "cortex/context-handoff/v1")
        self.assertEqual(inspected["context_handoff"]["task_ref"], started["task_ref"])
        self.assertEqual(inspected["context_handoff"]["goal"], "recover persisted report")
        self.assertEqual(inspected["context_handoff"]["reports"][0]["report_ref"], published["report_ref"])
        self.assertIn("fork_turns=none", inspected["context_handoff"]["protocol"]["hidden_dispatch"])
        self.assertIn("report_markdown_link", inspected["context_handoff"]["protocol"]["report_publication"])
        self.assertIn("context_handoff", inspected["next_action"])
        self.assertIn("manage_orchestration", inspected["context_handoff"]["next_action"])
        advanced = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": inspected["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["dispatches"][0]["phase"], "implementation")

    def test_v3_inspect_mixed_stops_preserves_reports_and_failed_receipts(self):
        response = mcp_api.v3_response(
            {
                "ok": True,
                "state": "waiting_workers",
                "wave_id": "wave-1",
                "operation": "inspect",
                "spawn_requests": [],
                "result": {
                    "context_handoff": {
                        "active_workers": [],
                        "stopped_workers": [
                            {"report_refs": ["report-0001"]},
                            {
                                "failure_status": "failed",
                                "failure_reason": "native_worker_stopped_without_report",
                                "dispatch_ref": "dispatch-0002",
                            },
                        ],
                    },
                },
            },
            "task-ref",
            native_arguments=lambda request: {},
            public_schema="cortex/orchestration/v5",
            coordinator_lock="LOCK",
        )
        self.assertIn("report-0001", response["next_action"])
        self.assertIn("dispatch-0002", response["next_action"])
        self.assertIn("status='failed'", response["next_action"])
        self.assertIn("Read and publish", response["next_action"])

        mixed_response = mcp_api.v3_response(
            {
                "ok": True,
                "state": "waiting_workers",
                "wave_id": "wave-1",
                "operation": "inspect",
                "spawn_requests": [],
                "result": {
                    "context_handoff": {
                        "active_workers": [{"host_agent_id": "native-live-01"}],
                        "stopped_workers": [{
                            "failure_status": "failed",
                            "failure_reason": "native_worker_stopped_without_report",
                            "dispatch_ref": "dispatch-stopped-01",
                        }],
                    },
                },
            },
            "task-ref",
            native_arguments=lambda request: {},
            public_schema="cortex/orchestration/v5",
            coordinator_lock="LOCK",
        )
        self.assertIn("native-live-01", mixed_response["next_action"])
        self.assertIn("dispatch-stopped-01", mixed_response["next_action"])
        self.assertIn("Include exactly one failed result", mixed_response["next_action"])

    def test_v3_public_schema_never_advertises_inline_worker_reports(self):
        result_schema = control.CONTINUE_ORCHESTRATION_SCHEMA["properties"]["results"]["items"]
        self.assertNotIn("report", result_schema["properties"])
        self.assertIn("report_ref", result_schema["properties"])
        self.assertIn("dispatch_ref", result_schema["properties"])
        self.assertIn("non-success", result_schema["properties"]["dispatch_ref"]["description"])
        self.assertIn("never an inline report body", result_schema["properties"]["report_ref"]["description"])
        self.assertIn("depends_on", control.V3_WORKER_SCHEMA["properties"])
        self.assertIn("context_files", control.V3_WORKER_SCHEMA["properties"])

    def test_v3_phase_aliases_accept_common_labels_and_reject_cross_wave_duplicates(self):
        task = {"objective": "phase aliases", "complexity": "C2"}
        compact = control._v3_compact_waves([
            {"workers": [{"phase": "implement", "profile": "backend_dev"}]},
            {"workers": [{"phase": "build_verification", "profile": "build_verification"}]},
        ], task)
        self.assertEqual(
            [wave["delegations"][0]["gate"] for wave in compact],
            ["implementation", "close"],
        )
        with self.assertRaisesRegex(ValueError, "repeat canonical phase 'qa'"):
            control._v3_compact_waves([
                {"workers": [{"phase": "qa"}]},
                {"workers": [{"phase": "verification"}]},
            ], task)
        parallel_qa = control._v3_compact_waves([
            {"workers": [
                {"phase": "qa", "profile": "qa_engineer"},
                {"phase": "verification", "profile": "build_verification"},
            ]},
        ], task)
        self.assertEqual(len(parallel_qa[0]["delegations"]), 2)
        human_labels = control._v3_compact_waves([
            {"workers": [{"phase": "analysis", "profile": "discovery"}]},
            {"workers": [{"phase": "implement", "profile": "implementer"}]},
        ], {
            "objective": "Implement the backend API endpoint",
            "requirements": ["server-side service logic"],
            "complexity": "C2",
        })
        self.assertEqual(human_labels[0]["delegations"][0]["gate"], "discover")
        self.assertEqual(human_labels[0]["delegations"][0]["agent"], "explorer")
        self.assertEqual(human_labels[1]["delegations"][0]["gate"], "implementation")
        self.assertEqual(human_labels[1]["delegations"][0]["agent"], "backend_dev")
        self.assertIn("generic implementation worker", human_labels[1]["delegations"][0]["selection_reason"])

    def test_v3_failed_worker_is_retired_before_a_successful_relative_retry(self):
        started = self.v3_start("worker retry", waves=[{"workers": [{"phase": "discover"}]}])
        failed = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "transient worker failure",
                "dispatch_ref": started["dispatches"][0]["dispatch_ref"],
            }],
        })
        self.assertTrue(failed["ok"])
        self.assertEqual(failed["step"], started["step"])
        self.assertEqual(len(failed["dispatches"]), 1)
        retried = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": failed["task_ref"], "step": failed["step"],
            "results": self.v3_results(failed, self.v3_report("retry succeeded")),
        })
        self.assertTrue(retried["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        failed_attempts = [item for item in state["attempts"] if item["status"] == "failed"]
        self.assertEqual(len(failed_attempts), 1)
        self.assertTrue(failed_attempts[0]["invalidated"])
        self.assertEqual(failed_attempts[0]["invalidation_reason"], "retry_after_failure")

    def test_v3_automatic_gate_rework_is_bounded_and_resume_resets_its_budget(self):
        current = self.v3_start("bounded worker retry", waves=[{"workers": [{"phase": "discover"}]}])
        for failure_number in range(1, control.MAX_ORCHESTRATE_GATE_FAILURES + 1):
            result = {
                "status": "failed",
                "reason": f"worker failure {failure_number}",
                "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
            }
            if failure_number == control.MAX_SAME_STRATEGY_FAILURES:
                result["next_strategy"] = "use an alternate repository evidence path"
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "step": current["step"],
                "results": [result],
            })
            self.assertTrue(current["ok"], current)
            if failure_number < control.MAX_ORCHESTRATE_GATE_FAILURES:
                self.assertEqual(current["outcome"], "ready_to_spawn")
                self.assertEqual(len(current["dispatches"]), 1)
        self.assertEqual(current["outcome"], "blocked")
        self.assertEqual(current["dispatches"], [])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["orchestrate_gate_failure_counts"]["discover"], 3)
        self.assertIn("rework budget exhausted", state["blocked_reason"])

        resumed = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "intent": "resume",
            "reason": "the worker failure cause was repaired",
        })
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["outcome"], "ready_to_spawn")
        state = control.load_task_state_for_artifact(task_dir)
        self.assertNotIn("discover", state["orchestrate_gate_failure_counts"])

    def test_v3_failed_result_is_bound_to_the_dispatched_attempt(self):
        started = self.v3_start("identical failed retries", waves=[{"workers": [{"phase": "discover"}]}])
        first_payload = {
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_report",
                "dispatch_ref": started["dispatches"][0]["dispatch_ref"],
            }],
        }
        first = control.continue_orchestration(first_payload)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["outcome"], "ready_to_spawn")
        first_replay = control.continue_orchestration(first_payload)
        self.assertTrue(first_replay["replayed"])
        self.assertEqual(first_replay["dispatches"], [])

        unchanged_strategy_payload = {
            **first_payload,
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_report",
                "dispatch_ref": first["dispatches"][0]["dispatch_ref"],
            }],
        }
        unchanged_strategy = control.continue_orchestration(unchanged_strategy_payload)
        self.assertFalse(unchanged_strategy["ok"], unchanged_strategy)
        self.assertEqual(unchanged_strategy["code"], "orchestrate_validation_failed")
        self.assertIn("same_strategy_limit reached", unchanged_strategy["diagnostics"][0]["message"])

        second_payload = {
            **unchanged_strategy_payload,
            "results": [{
                **unchanged_strategy_payload["results"][0],
                "next_strategy": "inspect an alternate repository evidence path",
            }],
        }
        second = control.continue_orchestration(second_payload)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["outcome"], "ready_to_spawn")
        self.assertEqual(len(second["dispatches"]), 1)
        retry_assignment = json.loads(
            self.briefing_from_response(second).split("```json\n", 1)[1].split("\n```", 1)[0]
        )
        self.assertEqual(retry_assignment["strategy"], "inspect an alternate repository evidence path")
        replay = control.continue_orchestration(second_payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])

    def test_v3_final_continue_retry_replays_after_task_completion(self):
        started = self.v3_start("final replay", waves=[{"workers": [{"phase": "close"}]}], complexity="tiny")
        current = started
        payload = None
        while current["outcome"] != "completed":
            payload = {
                "project_root": str(self.project), "task_ref": current["task_ref"], "step": current["step"],
                "results": self.v3_results(current, self.v3_report(f"completed step {current['step']}")),
            }
            current = control.continue_orchestration(payload)
            self.assertTrue(current["ok"])
        completed = current
        self.assertEqual(completed["outcome"], "completed")
        self.assertTrue(completed["result"]["close_verified"])
        self.assertIn("handoff_ready", completed["result"])
        replay = control.continue_orchestration(payload)
        self.assertEqual(replay["outcome"], "completed")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertEqual(replay["result"], completed["result"])

    def test_v3_management_never_implicitly_selects_an_active_task(self):
        started = self.v3_start("active task requires an opaque reference", waves=[{"workers": [{"phase": "discover"}]}])
        unscoped = control.manage_orchestration({"project_root": str(self.project)})
        self.assertFalse(unscoped["ok"])
        self.assertEqual(unscoped["code"], "task_ref_required")
        self.assertNotIn("candidates", unscoped)
        selected = control.manage_orchestration({
            "project_root": str(self.project), "intent": "inspect",
            "task_ref": started["task_ref"],
        })
        self.assertTrue(selected["ok"])
        self.assertNotIn("task_id", selected)

    def test_v3_task_scoped_public_calls_require_task_ref(self):
        started = self.v3_start("every public task call is explicitly scoped")
        empty_continuation = control.continue_orchestration({
            "project_root": str(self.project),
        })
        self.assertFalse(empty_continuation["ok"])
        self.assertEqual(empty_continuation["code"], "task_ref_required")
        continuation = control.continue_orchestration({
            "project_root": str(self.project),
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "exercise task_ref guard",
                "dispatch_ref": started["dispatches"][0]["dispatch_ref"],
            }],
        })
        self.assertFalse(continuation["ok"])
        self.assertEqual(continuation["code"], "task_ref_required")
        report_read = control.read_worker_report({
            "project_root": str(self.project), "report_ref": "report-0001",
        })
        self.assertFalse(report_read["ok"])
        self.assertEqual(report_read["code"], "task_ref_required")

    def test_public_api_ignores_private_task_without_canonical_plan(self):
        created = self.init(task_id="unplanned-private-task", complexity="C1")
        self.delegate(created["state"], "unplanned-private-task", "discover", "explorer")
        inspected = control.manage_orchestration({"project_root": str(self.project), "intent": "inspect"})
        self.assertFalse(inspected["ok"])
        self.assertEqual(inspected["code"], "task_ref_required")

    def test_v3_future_wave_rework_requires_explicit_opt_in(self):
        started = self.v3_start("v3 future rework", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        common = {
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": self.v3_results(started, self.v3_report("discovery complete")),
            "future_waves": [{"workers": [{"phase": "discover"}, {"phase": "implementation"}]}],
            "reason": "new evidence requires discovery rework",
        }
        denied = control.continue_orchestration(common)
        self.assertFalse(denied["ok"])
        self.assertIn("allow_rework=true", denied["diagnostics"][0]["message"])
        missing_pipeline = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "step": started["step"],
            "results": common["results"],
            "rework": True,
            "reason": "new evidence requires rework",
        })
        self.assertFalse(missing_pipeline["ok"])
        self.assertIn("requires explicit future_waves", missing_pipeline["diagnostics"][0]["message"])
        allowed = control.continue_orchestration({**common, "rework": True, "reason": "new evidence"})
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["step"], 1)

    def test_v3_final_close_rework_reopens_completed_pipeline(self):
        current = self.v3_start("rework defects found at final close", waves=[
            {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
            {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
            {"workers": [{"phase": "close", "profile": "build_verification"}]},
        ])
        task_ref = current["task_ref"]
        for expected_next in ("review", "close"):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": task_ref,
                "step": current["step"],
                "results": self.v3_results(current, self.v3_report("phase completed before final review")),
            })
            self.assertTrue(current["ok"], current)
            self.assertEqual(current["dispatches"][0]["phase"], expected_next)

        reworked = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": task_ref,
            "step": current["step"],
            "results": self.v3_results(current, self.v3_report("close evidence requires bounded documentation rework")),
            "future_waves": [
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
            "rework": True,
            "reason": "final close evidence identified a documentation defect that must be corrected and reverified",
        })
        self.assertTrue(reworked["ok"], reworked)
        self.assertEqual(reworked["outcome"], "ready_to_spawn")
        self.assertEqual(reworked["dispatches"][0]["phase"], "documentation")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["completed_gates"], [])
        prior_close = [item for item in state["attempts"] if item["gate"] == "close"][0]
        self.assertTrue(prior_close["invalidated"])
        active_documentation = [
            item for item in state["attempts"]
            if item["gate"] == "documentation" and not item.get("invalidated")
        ]
        self.assertEqual(len(active_documentation), 1)
        self.assertNotEqual(active_documentation[0]["attempt_id"], "documentation-01")

    def test_v3_noop_future_wave_reassessment_advances_with_monotonic_steps(self):
        started = self.v3_start("v3 no-op future", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": self.v3_results(started, self.v3_report("discovery complete")),
            "future_waves": [{"workers": [{"phase": "implementation"}]}],
            "reason": "confirm the coordinator-selected implementation route",
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["step"], 2)
        self.assertEqual(len(advanced["dispatches"]), 1)
        task_dir = next((self.ledger / "tasks").iterdir())
        plan = control.db_load_task(self.ledger, self.task_state(task_dir)["task_id"])[2]
        wave_ids = [wave["wave_id"] for wave in plan["waves"]]
        self.assertEqual(wave_ids, sorted(set(wave_ids)))

    def test_v3_final_planner_automatically_receives_verified_scope_basis(self):
        started = self.v3_start("planner keeps every verified scope report", waves=[
            {"workers": [{"phase": "scope", "profile": "planner"}]},
        ])
        scope_results = self.v3_results(started, self.v3_report("scope evidence"))
        advanced = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": scope_results,
            "future_waves": [
                {"workers": [{"phase": "plan", "profile": "planner"}]},
                {"workers": [{"phase": "architecture", "profile": "architect"}]},
                {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
                {"workers": [{"phase": "qa", "profile": "qa_engineer"}]},
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
            "reason": "scope evidence requires a complete final planning basis",
        })
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["dispatches"][0]["phase"], "plan")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        planner = next(item for item in state["attempts"] if item["gate"] == "plan" and not item.get("invalidated"))
        self.assertIn(scope_results[0]["report_ref"], planner["context_report_ids"])

    def test_v3_corrected_future_retry_resumes_a_failed_gates_recorded_transaction(self):
        started = self.v3_start("correct a future wave without a stale lock", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        payload = {
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("discovery evidence")),
            "future_waves": [{"workers": [{"phase": "implementation"}]}],
            "reason": "initial future-wave contract",
        }
        with mock.patch.object(
            orchestration_engine,
            "_prepare_orchestrate_wave",
            side_effect=ValueError("future-wave contract needs correction"),
        ):
            rejected = control.continue_orchestration(payload)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "orchestrate_validation_failed")
        self.assertEqual(rejected["diagnostics"][0]["phase"], "gates_recorded")

        corrected = control.continue_orchestration({
            **payload,
            "reason": "corrected future-wave contract after the returned diagnostic",
        })
        self.assertTrue(corrected["ok"], corrected)
        self.assertEqual(corrected["outcome"], "ready_to_spawn")
        self.assertEqual(corrected["dispatches"][0]["phase"], "implementation")
        registry = control._operation_registry(self.ledger)
        task_id = next(iter(registry["tasks"]))
        self.assertNotIn("inflight_continue", registry["tasks"][task_id])

    def test_orchestrate_start_replays_and_advance_returns_parallel_then_dependent_wave(self):
        waves = [
            {"wave_id": "discovery", "delegations": [{"gate": "discover", "agent": "explorer"}]},
            {"wave_id": "architecture", "delegations": [{"gate": "architecture", "agent": "architect"}]},
            {"wave_id": "plan", "delegations": [{"gate": "plan", "agent": "planner"}]},
            {"wave_id": "implementation", "delegations": [{"gate": "implementation", "agent": "general"}]},
            {"wave_id": "review", "delegations": [{"gate": "review", "agent": "code_reviewer"}]},
        ]
        started = self.facade_start("facade-waves", waves, complexity="C2")
        replayed = self.facade_start("facade-waves", waves, complexity="C2")
        self.assertTrue(started["ok"])
        self.assertFalse(started["idempotent"])
        self.assertTrue(replayed["idempotent"])
        self.assertEqual(replayed["transaction_id"], started["transaction_id"])
        self.assertEqual(len(started["spawn_requests"]), 1)
        briefing = self.briefing_from_request(started["spawn_requests"][0])
        self.assertIn("call the public `get_report_template` tool", briefing)
        self.assertIn("returns draft_path plus draft_ref", briefing)
        self.assertIn("small JSON Merge Patch", briefing)
        self.assertIn("call `record_report` with this identity and draft_ref", briefing)
        self.assertNotIn("validate_report_draft", briefing)
        self.assertNotIn("validation_digest", briefing)
        self.assertIn("consume no worker attempt", briefing)
        self.assertIn("do not paste or reproduce that JSON", briefing)

        discovery = control.orchestrate({
            "operation": "advance", "submission_id": "facade-waves-advance-discovery",
            "task_id": "facade-waves", "wave_id": started["wave_id"],
            "principal": "thread-a", "thread_id": "thread-a",
            "completions": [self.facade_completion(started["spawn_requests"][0])],
        })
        self.assertEqual(discovery["wave_id"], "architecture")
        self.assertEqual(len(discovery["spawn_requests"]), 1)
        active_attempt_ids = {
            item["attempt_id"] for item in discovery["state_summary"]["attempts"]
            if item["status"] == control.AWAITING_HOST_SPAWN
        }
        self.assertEqual({item["attempt_id"] for item in discovery["spawn_requests"]}, active_attempt_ids)

        planning = control.orchestrate({
            "operation": "advance", "submission_id": "facade-waves-advance-architecture",
            "task_id": "facade-waves", "wave_id": discovery["wave_id"],
            "principal": "thread-a", "thread_id": "thread-a",
            "completions": [self.facade_completion(item) for item in discovery["spawn_requests"]],
        })
        self.assertEqual(planning["wave_id"], "plan")
        self.assertEqual(len(planning["spawn_requests"]), 1)

        implementation = control.orchestrate({
            "operation": "advance", "submission_id": "facade-waves-advance-plan",
            "task_id": "facade-waves", "wave_id": planning["wave_id"],
            "principal": "thread-a", "thread_id": "thread-a",
            "completions": [self.facade_completion(item) for item in planning["spawn_requests"]],
        })
        self.assertEqual(implementation["wave_id"], "implementation")
        self.assertEqual(len(implementation["spawn_requests"]), 1)

    def test_orchestrate_allows_multiple_parallel_documentation_slots(self):
        waves = [{"wave_id": "documentation", "delegations": [
            {"gate": "documentation", "agent": "technical_writer", "objective": "Write the project index."},
            {"gate": "documentation", "agent": "technical_writer", "objective": "Write the trading feature pages."},
            {"gate": "documentation", "agent": "technical_writer", "objective": "Write the operations feature pages."},
        ]}]
        started = self.facade_start("facade-parallel-documentation", waves, complexity="C2")
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["state"], "ready_to_spawn")
        self.assertEqual(len(started["spawn_requests"]), 3)
        self.assertEqual(len({item["attempt_id"] for item in started["spawn_requests"]}), 3)
        self.assertEqual(len({item["briefing_digest"] for item in started["spawn_requests"]}), 3)

    def test_orchestrate_rejects_changed_idempotency_payload_without_duplicate_attempt(self):
        waves = [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}]
        started = self.facade_start("facade-idempotency", waves, submission_id="same-submission")
        changed = control.orchestrate({
            "operation": "start", "submission_id": "same-submission",
            "principal": "thread-a", "thread_id": "thread-a",
            "task": {"task_id": "facade-idempotency", "objective": "different payload", "complexity": "C1"},
            "waves": waves,
            "host_capabilities": {"spawn_agent_models": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"], "create_thread_models": ["gpt-5.6-luna"]},
        })
        self.assertFalse(changed["ok"])
        self.assertIn("different content", changed["diagnostics"][0]["message"])
        inspected = control.orchestrate({"operation": "inspect", "task_id": "facade-idempotency", "principal": "thread-a"})
        self.assertEqual(len(inspected["state_summary"]["attempts"]), 1)
        self.assertEqual(inspected["spawn_requests"][0]["attempt_id"], started["spawn_requests"][0]["attempt_id"])

    def test_orchestrate_start_recovers_after_every_transaction_phase(self):
        waves = [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}]
        original_checkpoint = orchestration_engine._checkpoint_orchestrate_transaction
        for phase in ("activated", "classified", "initialized", "plan_recorded", "wave_prepared"):
            with self.subTest(phase=phase):
                task_id = f"start-crash-{phase.replace('_', '-')}"
                fired = False

                def crash_after_checkpoint(path, receipt, current_phase, **context):
                    nonlocal fired
                    original_checkpoint(path, receipt, current_phase, **context)
                    if current_phase == phase and not fired:
                        fired = True
                        raise RuntimeError(f"simulated crash after {phase}")

                with mock.patch.object(orchestration_engine, "_checkpoint_orchestrate_transaction", side_effect=crash_after_checkpoint):
                    interrupted = self.facade_start(task_id, waves)
                self.assertFalse(interrupted["ok"])
                recovered = self.facade_start(task_id, waves)
                self.assertTrue(recovered["ok"])
                self.assertEqual(len(recovered["spawn_requests"]), 1)
                state = control.orchestrate({"operation": "inspect", "task_id": task_id, "principal": "thread-a"})
                self.assertEqual(len(state["state_summary"]["attempts"]), 1)
                receipt = control.db_get_operation(self.ledger, f"{task_id}-start")
                self.assertIsNotNone(receipt)
                self.assertEqual(receipt["status"], "committed")

    def test_orchestrate_advance_recovers_after_every_transaction_phase(self):
        waves = [
            {"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]},
            {"wave_id": "implementation", "delegations": [{"gate": "implementation", "agent": "general"}]},
            {"wave_id": "review", "delegations": [{"gate": "review", "agent": "code_reviewer"}]},
        ]
        original_checkpoint = orchestration_engine._checkpoint_orchestrate_transaction
        for phase in ("attempts_completed", "gates_recorded", "next_wave_prepared"):
            with self.subTest(phase=phase):
                task_id = f"advance-crash-{phase.replace('_', '-')}"
                started = self.facade_start(task_id, waves)
                arguments = {
                    "operation": "advance", "submission_id": f"{task_id}-advance",
                    "task_id": task_id, "wave_id": started["wave_id"], "principal": "thread-a",
                    "completions": [self.facade_completion(started["spawn_requests"][0])],
                }
                fired = False

                def crash_after_checkpoint(path, receipt, current_phase, **context):
                    nonlocal fired
                    original_checkpoint(path, receipt, current_phase, **context)
                    if current_phase == phase and not fired:
                        fired = True
                        raise RuntimeError(f"simulated crash after {phase}")

                with mock.patch.object(orchestration_engine, "_checkpoint_orchestrate_transaction", side_effect=crash_after_checkpoint):
                    interrupted = control.orchestrate(arguments)
                self.assertFalse(interrupted["ok"])
                recovered = control.orchestrate(arguments)
                self.assertTrue(recovered["ok"])
                self.assertEqual(recovered["wave_id"], "implementation")
                self.assertEqual(len(recovered["spawn_requests"]), 1)
                inspected = control.orchestrate({"operation": "inspect", "task_id": task_id, "principal": "thread-a"})
                self.assertEqual(len(inspected["state_summary"]["attempts"]), 2)
                reports = control.list_task_reports({"task_id": task_id, "principal": "thread-a"})["reports"]
                self.assertEqual(len(reports), 1)
                receipt = control.db_get_operation(self.ledger, f"{task_id}-advance")
                self.assertIsNotNone(receipt)
                self.assertEqual(receipt["status"], "committed")

    def test_orchestrate_malformed_report_and_host_mismatch_are_recoverable(self):
        waves = [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}]
        malformed_start = self.facade_start("facade-malformed", waves)
        request = malformed_start["spawn_requests"][0]
        task_dir, state, _ = control._v3_task_state(self.ledger, "facade-malformed")
        attempt = control._attempt(state, request["attempt_id"])
        malformed = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": {"summary": "missing fields"},
        })
        self.assertFalse(malformed["ok"])
        self.assertEqual(malformed["outcome"], "needs_correction")
        malformed_state = control.orchestrate({"operation": "inspect", "task_id": "facade-malformed", "principal": "thread-a"})
        self.assertEqual(malformed_state["state_summary"]["attempts"][0]["status"], control.AWAITING_HOST_SPAWN)

        mismatch_start = self.facade_start("facade-host-mismatch", waves)
        mismatch_request = mismatch_start["spawn_requests"][0]
        mismatch = control.orchestrate({
            "operation": "advance", "submission_id": "facade-host-mismatch-advance",
            "task_id": "facade-host-mismatch", "wave_id": mismatch_start["wave_id"], "principal": "thread-a",
            "completions": [self.facade_completion(mismatch_request, host_model="gpt-5.6-sol")],
        })
        self.assertFalse(mismatch["ok"])
        self.assertIn("model", mismatch["diagnostics"][0]["message"].lower())

    def test_report_draft_pipeline_allows_unbounded_read_only_corrections_then_one_atomic_record(self):
        waves = [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}]
        started = self.facade_start("facade-report-corrections", waves)
        request = started["spawn_requests"][0]
        task_dir, state, _ = control._v3_task_state(self.ledger, "facade-report-corrections")
        attempt = control._attempt(state, request["attempt_id"])
        identity = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }

        invalid_template = control.get_report_template({**identity, "profile": "planner"})
        self.assertFalse(invalid_template["ok"])
        self.assertEqual(invalid_template["outcome"], "needs_correction")
        self.assertTrue(invalid_template["retryable"])
        self.assertFalse(invalid_template["attempt_budget_consumed"])
        self.assertEqual(invalid_template["diagnostics"][0]["path"], "profile")

        template = control.get_report_template(identity)
        self.assertTrue(template["ok"], template)
        self.assertEqual(template["outcome"], "report_template_ready")
        self.assertNotIn("template", template)
        self.assertRegex(template["draft_ref"], r"^draft-[0-9a-f]{32}$")
        draft_path = Path(template["draft_path"])
        self.assertTrue(draft_path.is_file())
        self.assertEqual(draft_path.stat().st_mode & 0o777, 0o600)
        template_envelope = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(template_envelope["report"]["questions"], [])
        self.assertIn("Dispatch briefing reviewed:", template_envelope["report"]["evidence"][0])
        self.assertFalse(template["persisted"])
        self.assertTrue(template["draft_persisted"])

        unfilled = control.publish_worker_report({
            **identity, "draft_ref": template["draft_ref"],
        })
        self.assertFalse(unfilled["ok"])
        self.assertEqual(unfilled["diagnostics"][0]["code"], "report_placeholder_unresolved")
        self.assertEqual(unfilled["diagnostics"][0]["path"], "report.summary")
        self.assertTrue(draft_path.exists())

        semantic_report = self._report_with_briefing(
            attempt, self.v3_report("semantic validation stays inside record_report")
        )
        semantic_report["tests"] = [{
            "command": "git status --short", "cwd": ".", "exit_code": "0",
            "evidence": "The command completed successfully.",
        }]
        semantic_rejection = control.publish_worker_report({
            **identity, "draft_ref": template["draft_ref"], "report": semantic_report,
        })
        self.assertFalse(semantic_rejection["ok"])
        self.assertEqual(semantic_rejection["outcome"], "report_draft_invalid")
        self.assertTrue(semantic_rejection["draft_persisted"])
        self.assertTrue(draft_path.exists())

        hidden_mode = control.publish_worker_report({
            **identity,
            "report": self._report_with_briefing(attempt, self.v3_report("hidden mode must stay private")),
            "_validate_only": True,
        })
        self.assertFalse(hidden_mode["ok"])
        self.assertEqual(hidden_mode["code"], "report_validation_failed")
        self.assertIn("unsupported record_report fields", hidden_mode["diagnostics"][0]["message"])

        invalid_reports = []
        for index in range(4):
            report = self._report_with_briefing(
                attempt,
                self.v3_report(f"caller-correctable report validation {index + 1}"),
            )
            report["next_action"] = f"unsupported report field {index + 1}"
            invalid_reports.append(report)

        for index, report in enumerate(invalid_reports):
            request_payload = (
                {**identity, "draft_ref": template["draft_ref"], "report": report}
                if index == 0
                else {
                    **identity,
                    "draft_ref": template["draft_ref"],
                    "patch": {"report": {"next_action": report["next_action"]}},
                }
            )
            rejected = control.publish_worker_report(request_payload)
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["outcome"], "report_draft_invalid")
            self.assertEqual(rejected["code"], "report_validation_failed")
            self.assertTrue(rejected["retryable"])
            self.assertFalse(rejected["attempt_budget_consumed"])
            self.assertEqual(rejected["draft_ref"], template["draft_ref"])
            self.assertEqual(Path(rejected["draft_path"]), draft_path)
            self.assertTrue(draft_path.exists())
            self.assertEqual(rejected["diagnostics"][0]["path"], "report.next_action")
            self.assertTrue(rejected["diagnostics"][0]["fix"])

        current = control.orchestrate({
            "operation": "inspect", "task_id": "facade-report-corrections", "principal": "thread-a",
        })
        self.assertEqual(len(current["state_summary"]["attempts"]), 1)
        self.assertEqual(current["state_summary"]["attempts"][0]["attempt_id"], attempt["attempt_id"])
        self.assertEqual(
            control.list_task_reports({
                "project_root": str(self.project), "task_id": state["task_id"], "principal": "thread-a",
            })["reports"],
            [],
        )

        valid_report = self._report_with_briefing(
            attempt, self.v3_report("report accepted after four draft corrections")
        )
        accepted = control.publish_worker_report({
            **identity,
            "draft_ref": template["draft_ref"],
            "patch": {"report": {**valid_report, "next_action": None}},
        })
        self.assertTrue(accepted["ok"], accepted)
        self.assertRegex(accepted["report_ref"], r"^report-[0-9]{4}$")
        draft_key = f"report_draft:{attempt['attempt_id']}:{template['draft_ref']}"
        self.assertIsNone(control.db_get_task_document(self.ledger, state["task_id"], draft_key))
        self.assertFalse(draft_path.exists())
        reports = control.list_task_reports({
            "project_root": str(self.project), "task_id": state["task_id"], "principal": "thread-a",
        })["reports"]
        self.assertEqual([item["report_id"] for item in reports], [accepted["report_ref"]])

    def test_orchestrate_future_wave_rework_requires_opt_in_and_restarts_gate(self):
        waves = [
            {"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]},
            {"wave_id": "implementation", "delegations": [{"gate": "implementation", "agent": "general"}]},
            {"wave_id": "review", "delegations": [{"gate": "review", "agent": "code_reviewer"}]},
        ]
        denied_start = self.facade_start("facade-rework-denied", waves)
        denied = control.orchestrate({
            "operation": "advance", "submission_id": "facade-rework-denied-advance",
            "task_id": "facade-rework-denied", "wave_id": denied_start["wave_id"], "principal": "thread-a",
            "completions": [self.facade_completion(denied_start["spawn_requests"][0])],
            "future_waves": waves,
        })
        self.assertFalse(denied["ok"])
        self.assertIn("allow_rework=true", denied["diagnostics"][0]["message"])
        denied_state = control.orchestrate({"operation": "inspect", "task_id": "facade-rework-denied", "principal": "thread-a"})
        self.assertEqual(denied_state["state_summary"]["completed_gates"], [])

        allowed_start = self.facade_start("facade-rework-allowed", waves)
        allowed = control.orchestrate({
            "operation": "advance", "submission_id": "facade-rework-allowed-advance",
            "task_id": "facade-rework-allowed", "wave_id": allowed_start["wave_id"], "principal": "thread-a",
            "completions": [self.facade_completion(allowed_start["spawn_requests"][0])],
            "future_waves": waves, "allow_rework": True,
        })
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["wave_id"], "discover")
        self.assertEqual(allowed["state_summary"]["completed_gates"], [])
        self.assertNotEqual(allowed["spawn_requests"][0]["attempt_id"], allowed_start["spawn_requests"][0]["attempt_id"])

    def test_orchestrate_blocked_wave_resumes_with_a_fresh_attempt(self):
        started = self.facade_start("facade-resume", [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}])
        blocked = control.orchestrate({
            "operation": "advance", "submission_id": "facade-resume-blocked",
            "task_id": "facade-resume", "wave_id": started["wave_id"], "principal": "thread-a",
            "completions": [self.facade_completion(started["spawn_requests"][0], status="blocked", report=None, reason="dependency unavailable")],
        })
        self.assertEqual(blocked["state"], "blocked")
        resumed = control.orchestrate({
            "operation": "resume", "submission_id": "facade-resume-retry",
            "task_id": "facade-resume", "principal": "thread-a", "reason": "dependency restored",
        })
        self.assertEqual(resumed["state"], "ready_to_spawn")
        self.assertNotEqual(resumed["spawn_requests"][0]["attempt_id"], started["spawn_requests"][0]["attempt_id"])

    def test_orchestrate_rejects_task_without_canonical_plan(self):
        created = self.init(task_id="facade-v7-compatibility", complexity="C1")
        delegated = self.delegate(created["state"], "facade-v7-compatibility", "discover", "explorer")
        task_dir = next((self.ledger / "tasks").iterdir())
        self.assertIsNone(control.db_load_task(self.ledger, "facade-v7-compatibility")[2])
        inspected = control.orchestrate({
            "operation": "inspect", "task_id": "facade-v7-compatibility", "principal": "thread-a",
        })
        self.assertFalse(inspected["ok"])
        self.assertIn("canonical orchestration plan is missing", inspected["diagnostics"][0]["message"])
        self.assertIsNone(control.db_load_task(self.ledger, "facade-v7-compatibility")[2])
        spawn_request = {**delegated["spawn_request"], "attempt_id": delegated["attempt_id"]}
        advanced = control.orchestrate({
            "operation": "advance", "submission_id": "facade-v7-compatibility-advance",
            "task_id": "facade-v7-compatibility", "wave_id": inspected["wave_id"], "principal": "thread-a",
            "completions": [self.facade_completion(
                spawn_request,
                status="failed",
                report=None,
                reason="canonical plan is missing",
                host_agent_id=delegated["host_spawn"]["agent_id"],
            )],
        })
        self.assertFalse(advanced["ok"])
        self.assertIsNone(control.db_load_task(self.ledger, "facade-v7-compatibility")[2])

    def test_orchestrate_lane_and_resource_modes_keep_rare_capabilities(self):
        started = self.facade_start("facade-resource", [{"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]}])
        lane = control.orchestrate({
            "operation": "lane", "submission_id": "facade-lane-create", "principal": "thread-a",
            "payload": {"command": "create", "lane_id": "facade-lane", "purpose": "facade test"},
        })
        self.assertTrue(lane["ok"])
        inspected_lane = control.orchestrate({
            "operation": "lane", "principal": "thread-a", "payload": {"command": "inspect", "lane_id": "facade-lane"},
        })
        self.assertEqual(inspected_lane["result"]["state"]["lane_id"], "facade-lane")

        claimed = control.orchestrate({
            "operation": "resource", "submission_id": "facade-resource-claim",
            "task_id": "facade-resource", "principal": "thread-a",
            "payload": {"command": "claim", "path": "port:4321", "owner": "thread-a", "expires_at": "2999-01-01T00:00:00+00:00"},
        })
        self.assertTrue(claimed["ok"])
        self.assertIn(control.lock_key("port:4321"), claimed["result"]["state"]["locks"])
        self.assertEqual(started["state"], "ready_to_spawn")

    def test_mcp_elicitation_nested_exchange_is_json_rpc_safe(self):
        original_stdin, original_stdout = control.sys.stdin, control.sys.stdout
        try:
            control.sys.stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": "cortex-question-smoke", "result": {"action": "accept", "content": {"custom_response": "yes"}}}) + "\n")
            control.sys.stdout = io.StringIO()
            with mock.patch.object(control.secrets, "token_hex", return_value="smoke"):
                action, content, request_id = control._request_mcp_elicitation("Choose", {"type": "object", "properties": {"custom_response": {"type": "string"}}})
            self.assertEqual((action, content, request_id), ("accept", {"custom_response": "yes"}, "cortex-question-smoke"))
            outbound = json.loads(control.sys.stdout.getvalue())
            self.assertEqual(outbound["method"], "elicitation/create")
        finally:
            control.sys.stdin, control.sys.stdout = original_stdin, original_stdout

    def test_mcp_resource_discovery_returns_an_empty_catalogue(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        requests = "\n".join((
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list", "params": {}}),
        )) + "\n"
        result = subprocess.run(
            [sys.executable, str(script)], input=requests, text=True,
            capture_output=True, check=True,
        )
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["capabilities"]["resources"], {"subscribe": False, "listChanged": False})
        instructions = responses[0]["result"]["instructions"]
        self.assertLessEqual(len(instructions), 512)
        self.assertIn("publishes every read_worker_report report_markdown_link", instructions)
        self.assertIn("Internal workers emit English only", instructions)
        self.assertIn("After resume, clear, or compaction", instructions)
        self.assertEqual(responses[1]["result"], {"resources": []})
        self.assertEqual(responses[2]["result"], {"resourceTemplates": []})

    def test_openai_form_extension_is_used_when_host_advertises_it(self):
        original_stdin, original_stdout = control.sys.stdin, control.sys.stdout
        try:
            control.sys.stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": "cortex-question-extension", "result": {"action": "cancel"}}) + "\n")
            control.sys.stdout = io.StringIO()
            with mock.patch.object(control.secrets, "token_hex", return_value="extension"), mock.patch.object(control, "MCP_OPENAI_FORM", True):
                action, _, _ = control._request_mcp_elicitation("Choose", {"type": "object", "properties": {"custom_response": {"type": "string"}}})
            self.assertEqual(action, "cancel")
            outbound = json.loads(control.sys.stdout.getvalue())
            self.assertEqual(outbound["params"]["mode"], "openai/form")
        finally:
            control.sys.stdin, control.sys.stdout = original_stdin, original_stdout

    def test_mcp_process_completes_facade_question_after_host_response(self):
        started = self.v3_start("nested question", waves=[{"workers": [{"phase": "discover"}]}])
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            initialized = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {"extensions": {"openai/form": {}}}}})
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "cortex")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "manage_orchestration", "arguments": {"intent": "question", "project_root": str(self.project), "task_ref": started["task_ref"], "payload": {"command": "ask", "question": "Continue?"}}}}) + "\n")
            proc.stdin.flush()
            elicitation = json.loads(proc.stdout.readline())
            self.assertEqual(elicitation["method"], "elicitation/create")
            self.assertEqual(elicitation["params"]["mode"], "openai/form")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "accept", "content": {"custom_response": "yes"}}}) + "\n")
            proc.stdin.flush()
            completed = json.loads(proc.stdout.readline())
            self.assertEqual(completed["id"], 2)
            self.assertEqual(completed["result"]["structuredContent"]["result"]["status"], "answered")
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_mcp_process_renders_native_plan_approval_and_advances_after_approve(self):
        started = self.v3_start(
            "nested native plan approval",
            complexity="C1",
            plan_approval="required",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "implementation"}]},
            ],
        )
        held = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": self.v3_results(started, self.v3_report("nested native approval is pending")),
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            initialized = call({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {"extensions": {"openai/form": {}}}},
            })
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "cortex")
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "manage_orchestration", "arguments": {
                    "project_root": str(self.project), "task_ref": started["task_ref"],
                    "intent": "plan_approval", "payload": {"decision": "prompt"},
                }},
            }) + "\n")
            proc.stdin.flush()
            elicitation = json.loads(proc.stdout.readline())
            self.assertEqual(elicitation["method"], "elicitation/create")
            self.assertEqual(elicitation["params"]["mode"], "openai/form")
            self.assertEqual(elicitation["params"]["requestedSchema"]["required"], ["decision"])
            self.assertEqual(
                elicitation["params"]["_meta"]["cortex"]["schema"],
                "cortex/plan-approval/v1",
            )
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": elicitation["id"],
                "result": {"action": "accept", "content": {"decision": "approve"}},
            }) + "\n")
            proc.stdin.flush()
            completed = json.loads(proc.stdout.readline())
            self.assertEqual(completed["id"], 2)
            structured = completed["result"]["structuredContent"]
            self.assertEqual(structured["outcome"], "ready_to_spawn")
            self.assertEqual(structured["dispatches"][0]["phase"], "implementation")
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_blocked_task_can_resume(self):
        result = self.init()
        state = result["state"]
        blocked = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked", "summary": "dependency unavailable"})
        self.assertEqual(blocked["state"]["status"], "blocked")
        resumed = control.resume_task({"task_id": "demo", "principal": "thread-a", "expected_revision": blocked["state"]["revision"], "reason": "dependency restored"})
        self.assertEqual(resumed["state"]["status"], "active")

    def test_rework_resets_completed_gate(self):
        result = self.init()
        state = result["state"]
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "summary": "done"})
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        changed = control.update_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": closed["state"]["revision"], "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True, "reason": "rework"})
        self.assertIn("discover", changed["state"]["current_pipeline"])
        self.assertNotIn("discover", changed["state"]["completed_gates"])

    def test_principal_binding_and_replan_limit(self):
        result = self.init()
        state = result["state"]
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.reassess_pipeline({"task_id": "demo", "principal": "other", "expected_revision": state["revision"], "signals": ["security"], "apply": False})
        first = control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "signals": ["security"], "apply": True})
        second = control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": first["state"]["revision"], "signals": ["performance"], "apply": True})
        with self.assertRaisesRegex(ValueError, "replan limit"):
            control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": second["state"]["revision"], "signals": ["docs"], "apply": True})

    def test_c2_requires_linked_attempt_and_handoff(self):
        result = self.init(task_id="c2", complexity="C2")
        state = result["state"]
        pending = control.record_evidence({"task_id": "c2", "principal": "thread-a", "expected_revision": state["revision"], "gate": "plan", "summary": "unlinked"})
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_delegation")
        delegation = self.delegate(state, "c2", "plan", "planner")
        report = self.report("c2", delegation["attempt_id"])
        evidence = control.record_evidence({"task_id": "c2", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "plan complete"})
        control.record_gate({"task_id": "c2", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        self.assertFalse(control.status({"task_id": "c2", "principal": "thread-a"})["state"]["handoff_created"])

    def test_report_bus_scoping_receipts_reconciliation_and_router(self):
        state = self.init(task_id="reports", complexity="C2")["state"]
        delegation = self.delegate(state, "reports", "plan", "planner", risk="low", requested_model="gpt-5.6-terra", requested_reasoning_effort="none")
        package = self.task_document(control.db_task_artifact_path(self.ledger, "reports"), f"dispatch:{delegation['attempt_id']}")
        self.assertEqual(package["requested_model"], "gpt-5.6-terra")
        self.assertEqual(package["selected_model"], "gpt-5.6-terra")
        self.assertIsNone(package["fallback_reason"])
        self.assertEqual(package["selected_reasoning_effort"], "high")
        self.assertEqual(package["model_choice_reason"], "coordinator_selected_terra")
        report = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": []}})
        replay = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": []}})
        self.assertTrue(replay["idempotent"])
        task_dir = self.ledger / "tasks" / "0001-reports"
        canonical = control.db_get_artifact_for_export_path(
            self.ledger, "reports", "reports/records/report-0001.json",
        )
        self.assertIsNotNone(canonical)
        self.assertFalse((task_dir / "reports").exists())
        from cortex_runtime.ledger_db import list_projection_jobs
        scheduled = list_projection_jobs(self.ledger, task_id="reports", limit=10)
        report_jobs = [job for job in scheduled if str(job.get("export_path") or "").startswith("reports/")]
        self.assertEqual({job["status"] for job in report_jobs}, {"pending"})
        self.reconcile_projections(worker_id="report-projection-test")
        artifacts = "\n".join(path.read_text(encoding="utf-8") for path in (task_dir / "reports").rglob("*") if path.is_file())
        self.assertNotIn("canary", artifacts)
        self.assertIn("&lt;script&gt;", (task_dir / "reports/markdown/report-0001.md").read_text(encoding="utf-8"))
        evidence = control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "report-backed evidence"})
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "receipt replay"})
        (task_dir / "reports/markdown/report-0001.md").unlink()
        repaired = self.reconcile_projections(worker_id="report-projection-repair-test")
        self.assertTrue(repaired)
        self.assertTrue((task_dir / "reports/markdown/report-0001.md").exists())

    def test_report_context_is_explicit_and_report_shape_is_strict(self):
        state = self.init(task_id="context", complexity="C2")["state"]
        first = self.delegate(state, "context", "plan", "planner")
        report = self.report("context", first["attempt_id"])
        with self.assertRaisesRegex(ValueError, "exactly"):
            control.record_report({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "submission_id": "bad", "report": {**report["report"]["report"], "unknown": True}})
        bodies = control.get_delegation_reports({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "report_ids": [report["report"]["report_id"]]})
        self.assertEqual(len(bodies["reports"]), 1)
        self.assertEqual(bodies["reports"][0]["receipt"]["receipt_id"], report["receipt"]["receipt_id"])
        with self.assertRaisesRegex(ValueError, "not granted"):
            control.get_delegation_reports({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "report_ids": ["report-9999"]})

    def test_delegated_predecessor_context_does_not_transfer_another_attempts_receipt(self):
        state = self.init(task_id="receipt-scope", complexity="C2")["state"]
        producer = self.delegate(state, "receipt-scope", "plan", "planner")
        report = self.report("receipt-scope", producer["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "receipt-scope", "principal": "thread-a",
            "expected_revision": producer["state"]["revision"], "gate": "plan",
            "attempt_id": producer["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "producer evidence",
        })
        advanced = control.record_gate({
            "task_id": "receipt-scope", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed",
        })
        consumer = self.delegate(
            advanced["state"], "receipt-scope", "discover", "explorer",
            context_report_ids=[report["report"]["report_id"]],
        )
        bodies = control.get_delegation_reports({
            "task_id": "receipt-scope", "principal": "thread-a", "attempt_id": consumer["attempt_id"],
            "report_ids": [report["report"]["report_id"]],
        })
        self.assertNotIn("receipt", bodies["reports"][0])

    def test_concurrent_report_publishers_are_serialized(self):
        state = self.init(task_id="publishers", complexity="C2")["state"]
        delegation = self.delegate(state, "publishers", "plan", "planner")
        results, failures = [], []

        def publish(index):
            try:
                results.append(control.record_report({"task_id": "publishers", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": f"publisher-{index}", "report": {"summary": f"publisher {index}", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": [f"evidence {index}"], "uncertainty": []}}))
            except Exception as exc:  # pragma: no cover - failure is asserted below.
                failures.append(exc)

        threads = [threading.Thread(target=publish, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(failures)
        self.assertEqual(len({item["report"]["report_id"] for item in results}), 8)
        self.assertEqual(len(control.list_task_reports({"task_id": "publishers", "principal": "thread-a"})["reports"]), 8)

    def test_stranded_report_recovery_never_overwrites_authoritative_record(self):
        state = self.init(task_id="crash", complexity="C2")["state"]
        delegation = self.delegate(state, "crash", "plan", "planner")
        first = self.report("crash", delegation["attempt_id"], submission_id="first")
        task_dir = self.ledger / "tasks/0001-crash/reports"
        self.reconcile_projections(worker_id="stranded-report-test")
        original = (task_dir / "records/report-0001.json").read_bytes()
        (task_dir / "records/report-0001.json").unlink()
        (task_dir / "markdown/report-0001.md").unlink()
        (task_dir / "receipts/report-receipt-report-0001.json").unlink()
        replay = self.report("crash", delegation["attempt_id"], submission_id="first")
        self.assertTrue(replay["idempotent"])
        second = self.report("crash", delegation["attempt_id"], submission_id="second")
        self.assertEqual(second["report"]["report_id"], "report-0002")
        self.reconcile_projections(worker_id="stranded-report-repair-test")
        self.assertEqual((task_dir / "records/report-0001.json").read_bytes(), original)
        self.assertEqual(len(control.list_task_reports({"task_id": "crash", "principal": "thread-a"})["reports"]), 2)

    def test_revised_planner_reports_use_unique_overview_artifacts(self):
        self.v3_start(
            "preserve every revised planning artifact",
            waves=[{"workers": [{"phase": "plan", "profile": "planner"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        first = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("first plan revision")),
            "planning": self.v3_planning(),
        })
        revised = self.v3_planning()
        revised["overview"] = "Revise the plan while preserving the first immutable revision."
        second = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report_with_briefing(attempt, self.v3_report("second plan revision")),
            "planning": revised,
        })

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["report_ref"], "report-0001")
        self.assertEqual(second["report_ref"], "report-0002")
        current = control.current_planning_manifest(task_dir)
        self.assertEqual(current["source_report_ref"], "report-0002")
        self.assertEqual(
            current["overview_artifact_path"],
            "planning/revisions/plan-report-0002/overview.md",
        )
        overview_artifacts, _ = control.db_list_artifacts(
            self.ledger,
            state["task_id"],
            kind="planning_overview",
            offset=0,
            page_size=10,
        )
        self.assertEqual(
            {item["export_path"] for item in overview_artifacts},
            {
                "planning/revisions/plan-report-0001/overview.md",
                "planning/revisions/plan-report-0002/overview.md",
            },
        )
        self.reconcile_projections(worker_id="revised-plan-artifacts-test")
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/overview.md").is_file())
        self.assertTrue((task_dir / "planning/revisions/plan-report-0002/overview.md").is_file())
        self.assertFalse((task_dir / "planning/overview.md").exists())

    def test_reconcile_ignores_manual_receipt_projection_edits(self):
        state = self.init(task_id="receipt-boundary", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-boundary", "plan", "planner")
        report = self.report("receipt-boundary", delegation["attempt_id"])
        self.reconcile_projections(worker_id="receipt-boundary-test")
        receipt_path = self.ledger / "tasks/0001-receipt-boundary/reports/receipts/report-receipt-report-0001.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["consumed_at"] = control.now()
        receipt["consumed_by_evidence_id"] = "evidence-0001"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        reconciled = self.reconcile_projections(worker_id="receipt-boundary-repair-test")
        self.assertIn("failed", {item["status"] for item in reconciled})
        canonical_receipt, _ = control.read_immutable_json_artifact(
            self.ledger / "tasks/0001-receipt-boundary", "receipt-boundary",
            "reports/receipts/report-receipt-report-0001.json", kinds={"report_receipt"},
        )
        self.assertIsNone(canonical_receipt["consumed_at"])
        self.assertIsNone(canonical_receipt["consumed_by_evidence_id"])
        evidence = control.record_evidence({"task_id": "receipt-boundary", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "recovered"})
        self.assertEqual(evidence["evidence"]["report_id"], "report-0001")

    def test_consumed_report_replay_reconstructs_consumed_receipt(self):
        state = self.init(task_id="receipt-replay", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-replay", "plan", "planner")
        report = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        evidence = control.record_evidence({"task_id": "receipt-replay", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "consumed"})
        self.reconcile_projections(worker_id="receipt-replay-test")
        receipt_path = self.ledger / "tasks/0001-receipt-replay/reports/receipts/report-receipt-report-0001.json"
        receipt_path.unlink()
        replay = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        self.assertTrue(replay["idempotent"])
        self.assertIsNotNone(replay["receipt"]["consumed_at"])
        self.assertEqual(replay["receipt"]["consumed_by_evidence_id"], "evidence-0001")
        self.reconcile_projections(worker_id="receipt-replay-repair-test")
        rebuilt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertIsNone(rebuilt["consumed_by_evidence_id"])
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "receipt-replay", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "replay"})

    def test_report_bus_rejects_symlinked_child_directories(self):
        for child in ("records", "markdown", "receipts", "consumptions", "delegations"):
            with self.subTest(child=child):
                task_id = f"symlink-{child}"
                created = self.init(task_id=task_id, complexity="C2")
                task_dir = self.ledger / "tasks" / created["task_directory"]
                target = self.base / f"sentinel-{child}"
                target.mkdir()
                bus_child = task_dir / "reports" / child
                bus_child.mkdir(parents=True)
                bus_child.rmdir()
                bus_child.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink|real directory"):
                    control.report_bus_paths(task_dir)
                self.assertEqual(list(target.iterdir()), [])

    def test_report_crash_points_recover_deterministically(self):
        original_report_index = runtime_reports._write_report_index
        original_delegation_report_index = runtime_reports._write_delegation_report_index
        phases = ("index", "delegation")
        for phase_index, phase in enumerate(phases, 1):
            with self.subTest(phase=phase):
                task_id = f"crash-{phase}"
                state = self.init(task_id=task_id, complexity="C2")["state"]
                delegation = self.delegate(state, task_id, "plan", "planner")
                fired = {"value": False}

                def write_report_index(*args, **kwargs):
                    if phase == "index" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash while updating report index")
                    return original_report_index(*args, **kwargs)

                def write_delegation_report_index(*args, **kwargs):
                    if phase == "delegation" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash while updating delegation report index")
                    return original_delegation_report_index(*args, **kwargs)

                runtime_reports._write_report_index = write_report_index
                runtime_reports._write_delegation_report_index = write_delegation_report_index
                try:
                    with self.assertRaises(OSError):
                        self.report(task_id, delegation["attempt_id"], submission_id="stable")
                finally:
                    runtime_reports._write_report_index = original_report_index
                    runtime_reports._write_delegation_report_index = original_delegation_report_index
                recovered = self.report(task_id, delegation["attempt_id"], submission_id="stable")
                self.assertFalse(recovered["idempotent"])
                self.assertEqual(recovered["report"]["report_id"], "report-0001")
                self.assertEqual(control.reconcile_report_bus({"task_id": task_id, "principal": "thread-a"})["report_count"], 1)

    def test_report_reconciliation_rejects_planning_for_non_planner_attempt(self):
        task_id = "reconcile-non-planner-planning"
        state = self.init(task_id=task_id, complexity="C2")["state"]
        delegation = self.delegate(state, task_id, "discover", "explorer")
        recorded = self.report(task_id, delegation["attempt_id"])
        report_id = recorded["report"]["report_id"]
        task_dir = self.ledger / "tasks" / f"0001-{task_id}"
        record, metadata = control.read_immutable_json_artifact(
            task_dir,
            task_id,
            f"reports/records/{report_id}.json",
            kinds={"worker_report", "report_record"},
        )
        invalid_record = {**record, "planning": self.v3_planning()}
        with mock.patch.object(
            control,
            "read_immutable_json_artifact",
            return_value=(invalid_record, metadata),
        ):
            with self.assertRaisesRegex(
                ValueError,
                rf"report record '{report_id}'.*planning is allowed only for planner plan reports",
            ):
                control.reconcile_report_bus({"task_id": task_id, "principal": "thread-a"})

    def test_report_allocation_ignores_orphaned_markdown_projections(self):
        state = self.init(task_id="orphan-markdown", complexity="C2")["state"]
        delegation = self.delegate(state, "orphan-markdown", "plan", "planner")
        task_dir = self.ledger / "tasks/0001-orphan-markdown"
        orphan = task_dir / "reports/markdown/report-0001.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("orphan\n", encoding="utf-8")
        recorded = self.report("orphan-markdown", delegation["attempt_id"])
        self.assertEqual(recorded["report"]["report_id"], "report-0001")

    def test_per_attempt_report_quota_and_terminal_attempt_are_rejected(self):
        state = self.init(task_id="quotas", complexity="C2")["state"]
        delegation = self.delegate(state, "quotas", "plan", "planner")
        original_attempt = control.MAX_REPORTS_PER_ATTEMPT
        try:
            control.MAX_REPORTS_PER_ATTEMPT = 1
            self.report("quotas", delegation["attempt_id"], submission_id="one")
            with self.assertRaisesRegex(ValueError, "quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="two")
        finally:
            control.MAX_REPORTS_PER_ATTEMPT = original_attempt
        report = control.record_report({"task_id": "quotas", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "one", "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": []}})
        evidence = control.record_evidence({"task_id": "quotas", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "done"})
        control.record_gate({"task_id": "quotas", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        with self.assertRaisesRegex(ValueError, "terminal"):
            self.report("quotas", delegation["attempt_id"], submission_id="late")

    def test_report_indexes_do_not_impose_a_task_wide_count_quota(self):
        state = self.init(task_id="long-history", complexity="C2")["state"]
        task_dir = self.ledger / "tasks/0001-long-history"
        report_ids = [f"report-{index:04d}" for index in range(1, 1002)]
        control.db_put_task_document(self.ledger, state["task_id"], "report_index", {
            "schema": control.REPORT_SCHEMA,
            "task_id": state["task_id"],
            "reports": [{"report_id": report_id} for report_id in report_ids],
            "submissions": {f"attempt-{index}:submission": report_id for index, report_id in enumerate(report_ids, 1)},
            "updated_at": control.now(),
        })
        loaded = control._report_index(control.report_bus_paths(task_dir), state["task_id"])
        self.assertEqual(len(loaded["reports"]), 1001)

        attempt_id = "synthetic-attempt"
        control.db_put_task_document(self.ledger, state["task_id"], f"report_delegation:{attempt_id}", {
            "schema": control.REPORT_SCHEMA,
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "owned_report_ids": [],
            "context_report_ids": report_ids,
            "updated_at": control.now(),
        })
        _, delegation_index = control._delegation_report_index(
            control.report_bus_paths(task_dir), state["task_id"], attempt_id,
        )
        self.assertEqual(len(delegation_index["context_report_ids"]), 1001)

    def test_journal_symlinks_cannot_modify_task_or_lane_state(self):
        state = self.init(task_id="journal", complexity="C1")["state"]
        state = control.record_gate({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked"})["state"]
        task_dir = self.ledger / "tasks/0001-journal"
        sentinel = self.base / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "journal.md").symlink_to(sentinel)
        resumed = control.resume_task({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"]})
        self.assertEqual(resumed["state"]["status"], "active")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        lane = control.create_lane({"lane_id": "journal-lane", "principal": "thread-a"})
        lane_dir = self.ledger / "lanes/journal-lane"
        lane_dir.mkdir(parents=True, exist_ok=True)
        (lane_dir / "journal.md").symlink_to(sentinel)
        claimed = control.claim_lane({"lane_id": "journal-lane", "principal": "thread-a", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertIsNotNone(claimed["state"]["lease"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_bearer_and_uri_redaction(self):
        text = control.redact("Authorization: Bearer abc123 trailing-canary\n continuation-canary\nSafe: visible https://user:pass@example.test API_KEY='quoted-secret'")
        self.assertNotIn("abc123", text)
        self.assertNotIn("trailing-canary", text)
        self.assertNotIn("continuation-canary", text)
        self.assertNotIn("user:pass", text)
        self.assertNotIn("quoted-secret", text)
        self.assertIn("Safe: visible", text)

    def test_rework_gate_reassessment_applies(self):
        result = self.init(task_id="rework", complexity="C2")
        state = result["state"]
        delegation = self.delegate(state, "rework", "plan", "planner")
        report = self.report("rework", delegation["attempt_id"])
        evidence = control.record_evidence({"task_id": "rework", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "done"})
        closed = control.record_gate({"task_id": "rework", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        changed = control.reassess_pipeline({"task_id": "rework", "principal": "thread-a", "expected_revision": closed["state"]["revision"], "signals": [], "intent": "rework_gate", "gate": "plan", "decision": "updated", "reason": "new evidence", "apply": True})
        self.assertIn("plan", changed["state"]["current_pipeline"])
        self.assertNotIn("plan", changed["state"]["completed_gates"])

    def test_completion_unbinds_task_but_keeps_activation_until_normal(self):
        self.activate(principal="thread-finish")
        classified = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        result = control.init_task({"task_id": "finish", "objective": "finish", "complexity": "C1", "classification_id": classified["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        state = result["state"]
        for gate in ["discover", "implementation", "review", "close"]:
            evidence = control.record_evidence({"task_id": "finish", "principal": "thread-finish", "expected_revision": state["revision"], "gate": gate, "summary": gate})
            state = control.record_gate({"task_id": "finish", "principal": "thread-finish", "expected_revision": evidence["state"]["revision"], "gate": gate, "outcome": "passed"})["state"]
        self.assertFalse((self.ledger / "active-tasks.json").exists())
        self.assertEqual(state["status"], "completed")
        activation = control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(activation["active"])
        self.assertIsNone(activation["activation"]["task_id"])
        self.assertNotIn("initialized_at", activation["activation"])

        next_classification = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        next_task = control.init_task({"task_id": "next", "objective": "next", "complexity": "C1", "classification_id": next_classification["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(next_task["created"])
        self.assertFalse((self.ledger / "active-tasks.json").exists())

        control.deactivate_orchestration({"user_command": "/normal", "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertFalse(control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})["active"])

    def test_resource_claim_expiry(self):
        result = self.init(task_id="resources", complexity="C1")
        state = result["state"]
        claim = control.claim_resource({"task_id": "resources", "principal": "thread-a", "expected_revision": state["revision"], "path": "port:4000", "owner": "worker", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertFalse(claim["state"]["locks"][control.lock_key("port:4000")]["advisory"])
        self.assertEqual(control.status({"task_id": "resources", "principal": "thread-a"})["task"]["task_id"], "resources")

    def test_completion_bypasses_are_rejected(self):
        result = self.init(task_id="skip", complexity="C2")
        state = result["state"]
        with self.assertRaisesRegex(ValueError, "skip_reason"):
            control.record_gate({"task_id": "skip", "principal": "thread-a", "expected_revision": state["revision"], "gate": "plan", "outcome": "skipped"})
        with self.assertRaisesRegex(ValueError, "close gate"):
            control.update_pipeline({"task_id": "skip", "principal": "thread-a", "expected_revision": state["revision"], "pipeline": ["plan", "discover"]})

    def test_nonzero_command_cannot_pass(self):
        result = self.init(task_id="nonzero", complexity="C1")
        state = result["state"]
        evidence = control.record_evidence({"task_id": "nonzero", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "kind": "command", "command": "false", "exit_code": 1, "summary": "expected failure"})
        with self.assertRaisesRegex(ValueError, "self-attested"):
            control.record_gate({"task_id": "nonzero", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})

    def test_rework_invalidates_downstream_gates(self):
        result = self.init(task_id="downstream", complexity="C1")
        state = result["state"]
        for gate in ["discover", "implementation"]:
            evidence = control.record_evidence({"task_id": "downstream", "principal": "thread-a", "expected_revision": state["revision"], "gate": gate, "summary": gate})
            state = control.record_gate({"task_id": "downstream", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": gate, "outcome": "passed"})["state"]
        changed = control.update_pipeline({"task_id": "downstream", "principal": "thread-a", "expected_revision": state["revision"], "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True})
        self.assertNotIn("discover", changed["state"]["completed_gates"])
        self.assertNotIn("implementation", changed["state"]["completed_gates"])

    def test_handoff_is_structured_and_gate_summary_is_redacted(self):
        result = self.init(task_id="handoff", complexity="C1")
        state = result["state"]
        handoff = control.handoff({"task_id": "handoff", "principal": "thread-a", "expected_revision": state["revision"], "completed": ["work"], "next_action": "none"})
        self.assertTrue(handoff["state"]["handoff_created"])
        evidence = control.record_evidence({"task_id": "handoff", "principal": "thread-a", "expected_revision": handoff["state"]["revision"], "gate": "discover", "summary": "done"})
        closed = control.record_gate({"task_id": "handoff", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed", "summary": "client_secret: hidden"})
        self.assertNotIn("hidden", closed["state"]["gates"]["discover"]["summary"])

    def test_optional_lane_lifecycle_and_task_binding(self):
        self.activate()
        created = control.create_lane({"lane_id": "feature-lane", "principal": "thread-a", "mode": "persistent", "purpose": "long-lived workstream", "declarations": [{"repo": "app", "branch": "feature/x", "worktree": "/tmp/app-x"}]})
        self.assertEqual(created["state"]["mode"], "persistent")
        claimed = control.claim_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-1", "expires_at": "2999-01-01T00:00:00+00:00"})
        with self.assertRaisesRegex(ValueError, "live lease"):
            control.claim_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-2", "expires_at": "2999-01-01T00:00:00+00:00"})
        task = self.init(task_id="lane-task", complexity="C1")
        bound = control.bind_task_lane({"task_id": "lane-task", "lane_id": "feature-lane", "principal": "thread-a", "expected_revision": task["state"]["revision"]})
        resource = control.claim_lane_resource({"lane_id": "feature-lane", "principal": "thread-a", "path": "port:4311", "owner": "run-1", "kind": "port", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertEqual(bound["state"]["lane_id"], "feature-lane")
        self.assertFalse(resource["advisory"])
        control.release_lane_resource({"lane_id": "feature-lane", "principal": "thread-a", "path": "port:4311", "owner": "run-1"})
        control.release_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-1"})
        retired = control.retire_lane({"lane_id": "feature-lane", "principal": "thread-a", "clean": True})
        self.assertEqual(retired["state"]["status"], "retired")

    def test_lane_expired_lease_requires_explicit_reclaim(self):
        self.activate()
        control.create_lane({"lane_id": "recover-lane", "principal": "thread-a"})
        definition, state = control.db_get_lane(self.ledger, "recover-lane")
        state["lease"] = {"owner": "thread-a", "run_id": "old", "expires_at": "2000-01-01T00:00:00+00:00"}
        control.db_put_lane(self.ledger, definition, state)
        with self.assertRaisesRegex(ValueError, "reclaim=true"):
            control.claim_lane({"lane_id": "recover-lane", "principal": "thread-a", "run_id": "new", "expires_at": "2999-01-01T00:00:00+00:00"})
        recovered = control.claim_lane({"lane_id": "recover-lane", "principal": "thread-a", "run_id": "new", "expires_at": "2999-01-01T00:00:00+00:00", "reclaim": True})
        self.assertTrue(recovered["reclaimed"])

    def test_lane_materialize_reconcile_and_clean_retirement(self):
        self.activate()
        repo = self.base / "source"
        worktree = self.base / "worktree"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "codex@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Codex Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("fixture\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
        created = control.create_lane({"lane_id": "materialized", "principal": "thread-a", "declarations": [{"repo_path": str(repo), "worktree_path": str(worktree), "branch": "feature/lane", "sync_from": "HEAD"}]})
        claimed = control.claim_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1", "expires_at": "2999-01-01T00:00:00+00:00"})
        materialized = control.materialize_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1", "confirm": True})
        self.assertTrue(worktree.exists())
        self.assertEqual(materialized["materializations"][0]["status"], "created")
        reconciled = control.reconcile_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1"})
        self.assertEqual(reconciled["results"][0]["status"], "ok")
        control.release_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1"})
        retired = control.retire_lane({"lane_id": "materialized", "principal": "thread-a", "clean": True, "confirm": True})
        self.assertEqual(retired["state"]["status"], "retired")
        self.assertFalse(worktree.exists())

    def test_concurrent_mutations_are_serialized(self):
        result = self.init()
        errors = []

        def add_evidence(index):
            try:
                control.record_evidence({"task_id": "demo", "principal": "thread-a", "gate": "discover", "summary": f"worker {index}"})
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=add_evidence, args=(index,)) for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        state = control.status({"task_id": "demo", "principal": "thread-a"})["state"]
        self.assertEqual(len(state["evidence"]), 8)
        self.assertEqual(state["revision"], result["state"]["revision"] + 8)

    def test_symlink_root_and_invalid_id_are_rejected(self):
        self.activate()
        with self.assertRaisesRegex(ValueError, "identifier"):
            control.init_task({"task_id": "../escape", "objective": "bad", "complexity": "C1"})
        linked = self.base / "linked"
        linked.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
            control.ledger_root({"project_root": str(linked)})
        os.environ["CORTEX_ROOT"] = str(self.base / "external-ledger")
        try:
            with self.assertRaisesRegex(ValueError, "CORTEX_ROOT is not supported"):
                control.ledger_root({"project_root": str(self.project)})
        finally:
            os.environ.pop("CORTEX_ROOT", None)

    def test_mcp_smoke_exposes_v3_lifecycle_and_scoped_report_tools(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.run([sys.executable, str(script)], input='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n', text=True, capture_output=True, check=True)
        tools = json.loads(proc.stdout)["result"]["tools"]
        names = {item["name"] for item in tools}
        self.assertEqual(names, {"start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "get_report_template", "record_report", "read_dispatch_briefing", "read_worker_report"})
        self.assertNotIn("orchestrate", names)
        self.assertEqual(len(tools), 8)
        self.assertTrue(all("project_root" in item["inputSchema"]["properties"] for item in tools))
        by_name = {item["name"]: item for item in tools}
        self.assertEqual(by_name["start_orchestration"]["inputSchema"]["required"], ["project_root", "task"])
        self.assertEqual(by_name["start_orchestration"]["inputSchema"]["properties"]["task"]["required"], ["user_request"])
        self.assertEqual(by_name["continue_orchestration"]["inputSchema"]["required"], ["project_root", "step", "results"])
        self.assertEqual(by_name["worker_question"]["inputSchema"]["required"], ["project_root", "task_id", "attempt_id", "profile", "action"])
        self.assertEqual(by_name["get_report_template"]["inputSchema"]["required"], ["project_root", "task_id", "attempt_id", "profile"])
        self.assertEqual(
            by_name["record_report"]["inputSchema"]["required"],
            ["project_root", "task_id", "attempt_id", "profile"],
        )
        self.assertEqual(
            by_name["record_report"]["inputSchema"]["anyOf"],
            [{"required": ["draft_ref"]}, {"required": ["report"]}],
        )
        self.assertIn("draft_ref", by_name["record_report"]["inputSchema"]["properties"])
        self.assertIn("patch", by_name["record_report"]["inputSchema"]["properties"])
        self.assertNotIn("validation_digest", by_name["record_report"]["inputSchema"]["properties"])
        forbidden = {"operation", "submission_id", "task_id", "wave_id", "attempt_id", "host_tool", "host_model", "host_reasoning_effort"}
        for name in ("start_orchestration", "continue_orchestration"):
            self.assertFalse(forbidden & set(by_name[name]["inputSchema"]["properties"]))

    def test_mcp_logs_invalid_tool_input_with_session_and_call_ids(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        with tempfile.TemporaryDirectory() as home:
            environment = os.environ.copy()
            environment["HOME"] = home
            environment.pop("CORTEX_ROOT", None)
            request = {
                "jsonrpc": "2.0",
                "id": "call-17",
                "method": "tools/call",
                "params": {
                    "name": "get_task_status",
                    "arguments": {
                        "task_id": "missing-log-task",
                        "attempt_id": "attempt-9",
                        "thread_id": "chat-session-42",
                        "principal": "owner",
                        "api_key": "do-not-persist",
                        "project_root": str(self.project),
                    },
                },
            }
            completed = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=True,
            )
            response = json.loads(completed.stdout)
            self.assertIn("error", response)
            log_path = Path(home) / ".codex" / "logs" / "cortex-tool-errors.jsonl"
            self.assertTrue(log_path.is_file())
            record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["event"], "tool_error")
            self.assertEqual(record["tool"], "get_task_status")
            self.assertEqual(record["chat_session_id"], "chat-session-42")
            self.assertEqual(record["request_id"], "call-17")
            self.assertEqual(record["ids"]["task_id"], "missing-log-task")
            self.assertEqual(record["ids"]["attempt_id"], "attempt-9")
            self.assertEqual(record["input"]["api_key"], "<REDACTED>")
            self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)

    def test_tool_error_log_keeps_complete_newest_lines_within_ten_megabyte_cap(self):
        self.assertEqual(control.MAX_TOOL_ERROR_LOG_BYTES, 10 * 1024 * 1024)
        log_path = self.base / "private-logs" / "cortex-tool-errors.jsonl"
        with (
            mock.patch.object(control, "_tool_error_log_path", return_value=log_path),
            mock.patch.object(control, "MAX_TOOL_ERROR_LOG_BYTES", 4096),
        ):
            for index in range(40):
                control.log_tool_error(
                    {
                        "method": "tools/call",
                        "params": {
                            "name": "bounded-log-test",
                            "arguments": {"task_id": f"task-{index}", "payload": "x" * 240},
                        },
                    },
                    f"call-{index}",
                    "",
                    RuntimeError(f"bounded failure {index}"),
                )
        content = log_path.read_bytes()
        self.assertLessEqual(len(content), 4096)
        self.assertTrue(content.endswith(b"\n"))
        records = [json.loads(line) for line in content.decode("utf-8").splitlines()]
        self.assertGreater(len(records), 1)
        self.assertEqual(records[-1]["request_id"], "call-39")
        self.assertNotEqual(records[0]["request_id"], "call-0")
        self.assertTrue(all(record["event"] == "tool_error" for record in records))

    def test_mcp_does_not_log_structured_facade_validation_results(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        with tempfile.TemporaryDirectory() as home:
            environment = os.environ.copy()
            environment["HOME"] = home
            environment.pop("CORTEX_ROOT", None)
            request = {
                "jsonrpc": "2.0",
                "id": "call-18",
                "method": "tools/call",
                "params": {
                    "name": "start_orchestration",
                    "arguments": {
                        "task": {
                            "user_request": "invalid compact wave",
                            "acceptance_criteria": ["The requested outcome is observed."],
                            "verification": ["Run an authoritative outcome check."],
                        },
                        "waves": [{"workers": [{"phase": "discvoery"}]}],
                        "project_root": str(self.project),
                    },
                },
            }
            completed = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=True,
            )
            response = json.loads(completed.stdout)
            structured = response["result"]["structuredContent"]
            self.assertEqual(structured["ok"], False)
            self.assertEqual(structured["code"], "start_validation_failed")
            self.assertIn("unknown worker phase", structured["diagnostics"][0]["message"])
            self.assertIn("COORDINATOR LOCK", structured["next_action"])
            log_path = Path(home) / ".codex" / "logs" / "cortex-tool-errors.jsonl"
            self.assertFalse(log_path.exists())

    def test_mcp_does_not_log_structured_worker_report_validation_results(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        with tempfile.TemporaryDirectory() as home:
            environment = os.environ.copy()
            environment["HOME"] = home
            environment.pop("CORTEX_ROOT", None)
            request = {
                "jsonrpc": "2.0",
                "id": "call-report-validation",
                "method": "tools/call",
                "params": {
                    "name": "record_report",
                    "arguments": {
                        "project_root": str(self.project),
                        "attempt_id": "discover-01",
                        "profile": "explorer",
                        "report": self.v3_report("missing task id stays recoverable"),
                    },
                },
            }
            completed = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=True,
            )
            response = json.loads(completed.stdout)
            structured = response["result"]["structuredContent"]
            self.assertFalse(structured["ok"])
            self.assertEqual(structured["code"], "report_identity_invalid")
            self.assertIn("task_id", structured["diagnostics"][0]["message"])
            log_path = Path(home) / ".codex" / "logs" / "cortex-tool-errors.jsonl"
            self.assertFalse(log_path.exists())

    def test_facade_aggregates_all_start_contract_errors_before_writing_ledger(self):
        malformed = control.orchestrate({
            "operation": "start",
            "project_root": str(self.project),
            "principal": "thread-a",
            "thread_id": "thread-a",
            "submission_id": "aggregate-start",
            "task": {"objective": "missing task id", "complexity": "C3"},
            "waves": [{
                "id": "plan",
                "gates": [{"id": "plan", "owner": "coordinator"}],
            }],
            "host_capabilities": {"available_models": ["gpt-5.6-terra"]},
        })
        self.assertFalse(malformed["ok"])
        paths = {item["path"] for item in malformed["diagnostics"]}
        self.assertIn("task.task_id", paths)
        self.assertIn("waves[0].wave_id", paths)
        self.assertIn("waves[0].gates", paths)
        self.assertIn("waves[0].delegations", paths)
        self.assertIn("host_capabilities.spawn_agent_models", paths)
        self.assertFalse((self.ledger / "tasks").exists())

    def test_mcp_rejects_root_fallbacks_and_starts_in_the_canonical_ledger(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"

        def call(arguments, environment=None):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "start_orchestration", "arguments": arguments}}
            completed = subprocess.run([sys.executable, str(script)], input=json.dumps(request) + "\n", text=True, capture_output=True, env=environment, check=True)
            return json.loads(completed.stdout)

        common = {"task": {
            "user_request": "root test", "complexity": "C1",
            "acceptance_criteria": ["The requested outcome is observed."],
            "verification": ["Run an authoritative outcome check."],
        }}
        missing_root = call(common)["result"]["structuredContent"]
        self.assertFalse(missing_root["ok"])
        self.assertIn("project_root is required", missing_root["diagnostics"][0]["message"])
        relative_root = call({**common, "project_root": "relative"})["result"]["structuredContent"]
        self.assertFalse(relative_root["ok"])
        self.assertIn("absolute path", relative_root["diagnostics"][0]["message"])
        external = self.base / "external-ledger"
        environment = os.environ.copy()
        environment["CORTEX_ROOT"] = str(external)
        rejected = call({**common, "project_root": str(self.project)}, environment)
        rejected_result = rejected["result"]["structuredContent"]
        self.assertFalse(rejected_result["ok"])
        self.assertIn("CORTEX_ROOT is not supported", rejected_result["diagnostics"][0]["message"])
        self.assertFalse(external.exists())
        accepted = call({**common, "project_root": str(self.project)})["result"]["structuredContent"]
        self.assertTrue(accepted["ok"])
        self.assertNotIn("task_id", accepted)
        self.assertTrue((self.ledger / "tasks").is_dir())

    def test_mcp_process_supports_multiple_project_roots(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        other = self.base / "other-project"
        other.mkdir()
        def start(root, task_id, submission_id):
            return {"jsonrpc": "2.0", "id": submission_id, "method": "tools/call", "params": {"name": "start_orchestration", "arguments": {"project_root": str(root), "task": {"user_request": task_id, "complexity": "C1", "acceptance_criteria": ["The requested outcome is observed."], "verification": ["Run an authoritative outcome check."]}}}}
        requests = [start(self.project, "first-root", "first-root-start"), start(other, "second-root", "second-root-start")]
        completed = subprocess.run([sys.executable, str(script)], input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True, check=True)
        first, second = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(first["result"]["structuredContent"]["ok"])
        self.assertTrue(second["result"]["structuredContent"]["ok"])
        self.assertTrue((self.project / ".codex/cortex").is_dir())
        self.assertTrue((other / ".codex/cortex").is_dir())

    def test_mcp_profile_cache_survives_source_directory_rename(self):
        source = Path(__file__).parents[1] / "plugins/cortex"
        cached = self.base / "cached-cortex"
        renamed = self.base / "retired-cache-entry"
        shutil.copytree(source, cached, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        script = cached / "scripts/cortex.py"
        proc = subprocess.Popen(
            [sys.executable, str(script)], cwd=self.project,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    self.fail(proc.stderr.read())
                return json.loads(line)

            initialized = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(initialized["result"]["serverInfo"]["version"].split("+", 1)[0], "9.2.0")
            cached.rename(renamed)
            request = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "start_orchestration", "arguments": {
                    "project_root": str(self.project),
                    "task": {
                        "user_request": "use in-memory profiles", "complexity": "C1",
                        "acceptance_criteria": ["The requested outcome is observed."],
                        "verification": ["Run an authoritative outcome check."],
                    },
                    "waves": [{"workers": [{"phase": "discover", "profile": "explorer"}]}],
                }},
            }
            started = call(request)["result"]["structuredContent"]
            self.assertTrue(started["ok"])
            briefing = self.briefing_from_response(started)
            self.assertIn("Role and mission: You are the read-only repository explorer", briefing)
            self.assertNotIn("Select this profile", briefing)
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
