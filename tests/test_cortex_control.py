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
        return control.record_report({"task_id": task_id, "principal": principal, "attempt_id": attempt_id, "submission_id": submission_id, "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": [], "next_action": "advance the gate"}})

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
        package = json.loads((task_dir / "delegations" / f"{attempt['attempt_id']}.json").read_text(encoding="utf-8"))
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
        if attempt["gate"] == "plan":
            payload["planning"] = self.v3_planning()
        published = control.publish_worker_report(payload)
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
            "next_action": "advance",
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

    def v3_start(self, objective="v3 task", waves=None, **task_overrides):
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
            state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            bindings = json.loads((self.ledger / "host-sessions.json").read_text(encoding="utf-8"))
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
            bindings = json.loads((self.ledger / "host-sessions.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        bindings = json.loads((self.ledger / "host-sessions.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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

    def test_subagent_stop_without_report_is_durably_failed_and_not_waitable(self):
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        attempt = state["attempts"][0]
        self.assertEqual(attempt["status"], "failed")
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
        self.assertIn("durably failed without a report", wait_context)
        self.assertIn("Do not call followup_task", wait_context)
        self.assertIn(started["task_ref"], wait_context)
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertEqual(inspected["context_handoff"]["stopped_workers"][0]["host_agent_id"], "native.Stop:01")
        self.assertIn("status=failed", inspected["next_action"])
        self.assertNotIn("Wait only on", inspected["next_action"])

    def test_post_wait_stop_context_ignores_passed_and_running_attempts(self):
        event = {"hook_event_name": "PostToolUse", "tool_name": "wait"}
        failed = {"attempts": [{
            "attempt_id": "close-01",
            "status": "failed",
            "host_stop_outcome": "native_worker_stopped_without_report",
        }]}
        context = cortex_hook.stopped_worker_after_wait_context(event, failed, "task-test")
        self.assertIn("Do not call followup_task", context)
        self.assertIn("manage_orchestration(intent='inspect'", context)

        for status, outcome in (("passed", "report_recorded"), ("running", None)):
            state = {"attempts": [{
                "attempt_id": "close-02",
                "status": status,
                "host_stop_outcome": outcome,
            }]}
            self.assertIsNone(cortex_hook.stopped_worker_after_wait_context(event, state, "task-test"))

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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

            index = json.loads((self.ledger / "task-index.json").read_text(encoding="utf-8"))
            bindings = json.loads((self.ledger / "host-sessions.json").read_text(encoding="utf-8"))
            task_ids = sorted(task_id for task_id, session in bindings["tasks"].items() if session == "shared-host")
            self.assertEqual(len(task_ids), 3)
            for task_id in task_ids[:2]:
                state_path = self.ledger / "tasks" / index[task_id]["directory"] / "current.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["status"] = "completed"
                state_path.write_text(json.dumps(state), encoding="utf-8")
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
                self.assertTrue((root / ".codex/cortex/tasks" / created["task_directory"] / "task.json").is_file())
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
        self.assertTrue((root / "tasks" / "0001-first-task" / "task.json").exists())
        self.assertTrue((root / "tasks" / "0002-second-task" / "task.json").exists())
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
        task = json.loads((self.ledger / "tasks" / created["task_directory"] / "task.json").read_text(encoding="utf-8"))
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
        task = json.loads((self.ledger / "tasks" / created["task_directory"] / "task.json").read_text(encoding="utf-8"))
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
        receipt_path = self.ledger / "classification-receipts" / f"{classified['classification_id']}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del receipt["requirements"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
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
        attempt = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))["attempts"][0]
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
        delegation_file = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
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
        created = self.init(task_id="documentation-legacy", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate legacy documentation receipt",
        })
        delegation = self.delegate(narrowed["state"], "documentation-legacy", "documentation", "technical_writer")
        report = self.report("documentation-legacy", delegation["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": report["state"]["revision"],
            "gate": "documentation",
            "attempt_id": delegation["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "kind": "documentation_sync",
            "decision": "updated",
            "summary": "legacy documentation evidence",
        })
        task_dir = self.ledger / "tasks" / "0001-documentation-legacy"
        current_path = task_dir / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        current["evidence"][-1]["kind"] = "documentation_sync"
        current["documentation_receipt"] = None
        current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        evidence_path = task_dir / "evidence" / f"{evidence['evidence']['evidence_id']}.json"
        legacy_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        legacy_evidence["kind"] = "documentation_sync"
        evidence_path.write_text(json.dumps(legacy_evidence, indent=2) + "\n", encoding="utf-8")
        status = control.status({"task_id": "documentation-legacy", "principal": "thread-a"})
        passed = control.record_gate({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": status["state"]["revision"],
            "gate": "documentation",
            "outcome": "passed",
        })
        self.assertFalse(passed["recorded"])
        self.assertEqual(passed["reason"], "documentation_evidence_required")
        self.assertNotIn("documentation", passed["state"]["completed_gates"])

    def test_gate_rejects_evidence_file_that_no_longer_matches_state(self):
        created = self.init(task_id="evidence-reconciliation", complexity="C1")
        state = created["state"]
        evidence = control.record_evidence({
            "task_id": "evidence-reconciliation", "principal": "thread-a",
            "expected_revision": state["revision"], "gate": "discover", "summary": "Observed repository evidence.",
        })
        path = self.ledger / "tasks/0001-evidence-reconciliation/evidence/evidence-0001.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["summary"] = "tampered"
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evidence record failed reconciliation"):
            control.record_gate({
                "task_id": "evidence-reconciliation", "principal": "thread-a",
                "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed",
            })

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
        abandoned = self.delegate(state, "terminal-attempt", "plan", "planner")
        finalized = control.finalize_attempt({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": abandoned["state"]["revision"],
            "attempt_id": abandoned["attempt_id"],
            "status": "cancelled",
            "reason": "host worker timed out",
        })
        replacement = self.delegate(finalized["state"], "terminal-attempt", "plan", "planner")
        report = self.report("terminal-attempt", replacement["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": replacement["state"]["revision"],
            "gate": "plan",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the gate",
        })
        closed = control.record_gate({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "plan",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[abandoned["attempt_id"]], "cancelled")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gates"], ["discover"])

    def test_failed_terminal_attempt_without_evidence_does_not_block_gate_completion(self):
        state = self.init(task_id="failed-only-attempt", complexity="C2")["state"]
        failed = self.delegate(state, "failed-only-attempt", "plan", "planner")
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
            "gate": "plan",
            "outcome": "passed",
        })
        self.assertEqual(closed["state"]["attempts"][0]["status"], "failed")
        self.assertEqual(closed["state"]["current_gates"], ["discover"])

    def test_active_running_attempt_without_evidence_still_blocks_gate(self):
        state = self.init(task_id="active-attempt", complexity="C2")["state"]
        delegation = self.delegate(state, "active-attempt", "plan", "planner")
        pending = control.record_gate({
                "task_id": "active-attempt",
                "principal": "thread-a",
                "expected_revision": delegation["state"]["revision"],
                "gate": "plan",
                "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["reason"], "evidence_required")

    def test_invalidated_running_attempt_can_be_superseded_and_no_longer_blocks_gate(self):
        state = self.init(task_id="invalidated-attempt", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-attempt", "plan", "planner")
        reworked = control.update_pipeline({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": original["state"]["revision"],
            "operations": [{"op": "rework", "gate": "plan"}],
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

        replacement = self.delegate(superseded["state"], "invalidated-attempt", "plan", "planner")
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
            "gate": "plan",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the reworked gate",
        })
        closed = control.record_gate({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "plan",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[original["attempt_id"]], "superseded")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gates"], ["discover"])

    def test_invalidated_terminal_attempt_remains_idempotent(self):
        state = self.init(task_id="invalidated-terminal", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-terminal", "plan", "planner")
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
            "operations": [{"op": "rework", "gate": "plan"}],
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
        package = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual({key: delegation["spawn_request"][key] for key in expected}, expected)
        self.assertRegex(
            delegation["spawn_request"]["task_name"],
            r"^general_objective_01_[0-9a-f]{8}$",
        )
        self.assertNotEqual(delegation["spawn_request"]["task_name"], delegation["spawn_request"]["display_name"])
        self.assertEqual({key: package["spawn_request"][key] for key in expected}, expected)
        self.assertEqual({key: delegation["state"]["attempts"][-1]["spawn_request"][key] for key in expected}, expected)
        self.assertIn("internal Cortex worker with profile `general`", self.briefing_from_request(delegation["spawn_request"]))
        self.assertEqual(delegation["state"]["attempts"][-1]["dispatch_correlation"], "coordinator_recorded_host_spawn")

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
        self.assertIn("exactly these eight keys", briefing)
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
            "changed_files": [], "tests": [], "evidence": ["evidence"], "uncertainty": [], "next_action": "advance"},
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
            "uncertainty": [], "next_action": "advance"},
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
            "uncertainty": [], "next_action": "advance"},
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
            "uncertainty": [], "next_action": "advance"},
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
        delegation = self.delegate(state, "finalize-before-evidence", "plan", "planner", task_kind="planning", risk="moderate")
        report = self.report("finalize-before-evidence", delegation["attempt_id"])
        finalized = control.finalize_attempt({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"], "status": "passed",
        })
        pending = control.record_gate({
                "task_id": "finalize-before-evidence", "principal": "thread-a",
                "expected_revision": finalized["state"]["revision"], "gate": "plan", "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_evidence")
        evidence = control.record_evidence({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"], "gate": "plan",
            "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"],
            "summary": "worker report reviewed",
        })
        advanced = control.record_gate({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed",
        })
        self.assertEqual(advanced["state"]["current_gates"], ["discover"])

    def test_recoverable_model_sequence_is_corrected_without_contract_errors(self):
        state = self.init(task_id="recoverable-sequence", complexity="C2")["state"]
        observed = control.status({"task_id": "recoverable-sequence", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "planner", "task_kind": "planning", "risk": "moderate",
            "objective": "plan", "ownership": "", "allowed_paths": [],
            "acceptance_criteria": [], "verification": [],
        })
        self.assertEqual(delegated["gate_correction"], {"requested": "discover", "used": "plan"})
        package = json.loads(Path(delegated["delegation_file"]).read_text(encoding="utf-8"))
        self.assertIn("Own planning and requirement closure", package["ownership"])
        self.assertEqual(package["allowed_paths"], ["."])
        self.assertTrue(package["acceptance_criteria"])
        self.assertTrue(package["verification"])
        premature = control.record_gate({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "gate": "discover", "outcome": "passed",
        })
        self.assertFalse(premature["recorded"])
        self.assertEqual(premature["next_action"], "record_evidence")
        self.assertEqual(premature["gate_correction"], {"requested": "discover", "used": "plan"})
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
        self.assertEqual(inferred["inferred"], {"gate": True, "attempt_id": True, "report_receipt": True})

    def test_delegation_infers_missing_gate_profile_kind_and_risk(self):
        state = self.init(task_id="inferred-delegation", complexity="C2")["state"]
        delegated = control.record_delegation({
            "task_id": "inferred-delegation", "principal": "thread-a",
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["gate"], "plan")
        self.assertEqual(delegated["spawn_request"]["profile"], "planner")
        self.assertEqual(delegated["state"]["attempts"][-1]["task_kind"], "planning")
        self.assertEqual(delegated["state"]["attempts"][-1]["risk"], "low")
        self.assertEqual(delegated["agent_correction"], {"requested": None, "used": "planner"})
        self.assertEqual(delegated["task_kind_correction"], {"requested": None, "used": "planning"})
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
        with self.assertRaisesRegex(ValueError, "retry budget exhausted"):
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
            "options": [{"label": "src", "description": "Update source files"}, {"label": "tests", "description": "Update tests"}],
            "multiple": True, "custom_label": "Additional direction", "context": {"reason": "scope"},
        })
        self.assertEqual(pending["status"], "pending_user_input")
        self.assertEqual(pending["ui"]["custom_label"], "Additional direction")
        question_id = pending["question_id"]
        listed = control.list_worker_questions({"task_id": "question-ui", "principal": "thread-a", "status": "open"})
        question = listed["questions"][0]
        self.assertEqual(question["options"][0]["label"], "src")
        self.assertTrue(question["multiple"])
        with mock.patch.object(control, "_request_mcp_elicitation", return_value=("accept", {"selections": ["src", "tests"], "custom_response": {"image": {"path": "/tmp/shot.png"}}}, "elicitation-1")):
            answered = control.cortex_question({"task_id": "question-ui", "principal": "thread-a", "question_id": question_id})
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answer"]["selections"], ["src", "tests"])
        self.assertEqual(answered["answer"]["custom_response"]["image"]["path"], "/tmp/shot.png")
        updates = control.get_worker_question_updates({"task_id": "question-ui", "principal": "thread-a", "attempt_id": delegated["attempt_id"]})
        self.assertEqual(updates["updates"][-1]["kind"], "question_answered")
        self.assertEqual(updates["updates"][-1]["answer"]["selections"], ["src", "tests"])

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
        self.assertIn(f"matching the exact root_path {str(self.project)!r}", prompt)
        self.assertIn("prefer `get_architecture`, `search_graph`, `trace_path`, `detect_changes`", prompt)
        self.assertIn("Confirm consequential indexed claims in current source or tests", prompt)
        self.assertIn("you may call `index_repository` once", prompt)
        self.assertIn("do not loop on Codebase Memory setup", prompt)
        self.assertIn("REPORT_RECORDED report_ref=<report_id>", prompt)
        self.assertIn("do not paste or reproduce its JSON", prompt)

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
                "tests": [], "evidence": ["source paths"], "uncertainty": [], "next_action": "advance",
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
        index = json.loads((report_root / "delegations" / delegated["attempt_id"] / "index.json").read_text(encoding="utf-8"))
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
                "tests": [], "evidence": ["report"], "uncertainty": [], "next_action": "advance",
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
                {"gate": "discover", "agent": "not-a-profile", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
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
        self.assertEqual(started["dispatches"][0]["phase"], "plan")
        self.assertEqual(started["dispatches"][0]["profile"], "planner")
        self.assertEqual(started["dispatches"][0]["sandbox"], "read-only")
        self.assertIn("canonical automatic owner", started["dispatches"][0]["selection_reason"])
        self.assertIn("COORDINATOR LOCK", started["next_action"])
        self.assertIn("remain idle", started["next_action"])
        self.assertIn("All project operations belong to workers", started["next_action"])
        self.assertEqual(started["dispatches"][0]["arguments"]["model"], "gpt-5.6-terra")
        self.assertEqual(started["dispatches"][0]["arguments"]["reasoning_effort"], "high")
        self.assertNotIn("task_id", started)
        self.assertNotIn("wave_id", started)
        tasks = list((self.ledger / "tasks").iterdir())
        definition = json.loads((tasks[0] / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(definition["complexity"], "C2")
        self.assertEqual(definition["plan_approval"], "required")

    def test_fresh_orchestration_uses_only_canonical_ledger_artifacts(self):
        started = self.v3_start(
            "audit the generated ledger layout",
            waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "discover"}]},
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        plan = json.loads((task_dir / "orchestration.json").read_text(encoding="utf-8"))
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        transaction = json.loads(next((self.ledger / "operations").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], "cortex/v8")
        self.assertNotIn("current_gate", state)
        self.assertEqual(plan["schema"], control.ORCHESTRATION_PLAN_SCHEMA)
        self.assertEqual(registry["schema"], "cortex/orchestration/v4")
        self.assertEqual(transaction["schema"], control.ORCHESTRATION_TRANSACTION_SCHEMA)
        self.assertFalse((self.ledger / "v3-operations.json").exists())
        self.assertFalse((task_dir / "status-receipts").exists())
        self.assertFalse(any(task_dir.rglob("*-snapshot.json")))
        self.assertFalse(any((task_dir / "handoffs").glob("*-manifest.json")))

        allowed = [
            re.compile(r"^\.state\.lock$"),
            re.compile(r"^(activations|host-sessions|task-index|orchestration-operations|resource-claims)\.json$"),
            re.compile(r"^(classification-receipts|operations)/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/(baseline-manifest|current|orchestration|task)\.json$"),
            re.compile(r"^tasks/[^/]+/journal\.md$"),
            re.compile(r"^tasks/[^/]+/(delegations|evidence)/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/delegations/[^/]+\.briefing\.md$"),
            re.compile(r"^tasks/[^/]+/reports/index\.json$"),
            re.compile(r"^tasks/[^/]+/reports/(records|receipts|consumptions)/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/reports/markdown/[^/]+\.md$"),
            re.compile(r"^tasks/[^/]+/reports/delegations/[^/]+/index\.json$"),
            re.compile(r"^tasks/[^/]+/questions/records/[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/planning/(manifest\.json|overview\.md)$"),
            re.compile(r"^tasks/[^/]+/planning/revisions/[^/]+/(manifest\.json|packages/[^/]+\.json)$"),
            re.compile(r"^tasks/[^/]+/handoffs/(?:manifests/)?[^/]+\.json$"),
            re.compile(r"^tasks/[^/]+/(?:\.lifecycle-events\.lock|lifecycle-events-meta\.json|lifecycle-events\.jsonl)$"),
            re.compile(r"^lanes/[^/]+/(?:current\.json|journal\.md)$"),
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
        self.assertIn("## Specialist playbook", prompt)
        self.assertIn("## Assignment", prompt)
        self.assertIn("Overall task outcome: satisfy the exact user-authored request above", prompt)
        self.assertIn("Current mission: Produce a decision-complete implementation plan for: the exact user-authored request above", prompt)
        self.assertIn("Task requirements: Preserve the public facade", prompt)
        self.assertIn("Task scope: plugins/cortex", prompt)
        self.assertIn("Task-level success criteria: Every agent receives the overall outcome", prompt)
        self.assertIn("Gate success criteria: Separate repository-discoverable facts", prompt)
        self.assertIn("Task-level validation: Run prompt contract tests", prompt)
        self.assertIn("Pause conditions: A public schema change becomes necessary", prompt)
        self.assertIn("Budget or operating limit: No external writes", prompt)
        self.assertNotIn("Complete and report the discover gate", prompt)
        self.assertIn("## Canonical Cortex team", prompt)
        for profile in control.AGENTS:
            self.assertIn(f"- {profile} [", prompt)

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
        expected = ["plan", "discover", "architecture", "documentation", "review", "close"]
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

    def test_v3_harvest_never_requires_post_plan_user_approval(self):
        started = self.v3_start("$cortex:orchestrator harvest", complexity="C3")
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
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

    def test_planner_rejects_microtasks_without_acceptance_and_verification(self):
        started = self.v3_start("plan a bounded change", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        record = json.loads(
            (task_dir / "reports/records" / f"{accepted['report_ref']}.json").read_text(encoding="utf-8")
        )
        validation = record["result_validation"]
        self.assertEqual(validation["schema"], control.RESULT_VALIDATION_SCHEMA)
        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["artifacts"]["reported_change_count"], 2)

    def test_public_result_validation_rejects_unstructured_checks_and_read_only_writes(self):
        review = self.v3_start(
            "review the current behavior",
            complexity="C1",
            waves=[{"workers": [{"phase": "review"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        rejected_stealth_write = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"], "report": stealth_report,
        })
        self.assertFalse(rejected_stealth_write["ok"])
        self.assertIn("project files changed during read-only result gate", rejected_stealth_write["diagnostics"][0]["message"])

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertIn("Current mission: Plan the exact adapter change", prompt)
        self.assertIn("Gate success criteria: Adapter plan is decision complete", prompt)
        self.assertIn("Required gate verification: Cite adapter tests", prompt)
        self.assertIn("Task-level success criteria: Public behavior remains compatible", prompt)
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        after = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        with self.assertRaisesRegex(ValueError, "worker question must be English-only"):
            control.worker_question({
                **identity,
                "action": "ask",
                "question": "Какой результат нужен пользователю?",
            })

        asked = control.worker_question({
            **identity,
            "action": "ask",
            "question": "Which result should the user receive?",
            "header": "Desired result",
            "options": ["Summary", "Detailed report"],
        })
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

    def test_v3_question_ref_opens_native_ui_once_without_coordinator_identity(self):
        started = self.v3_start("underspecified product request", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertIn("Do not ask it through commentary", unavailable["next_action"])
        question = json.loads((task_dir / "questions/records" / f"{asked['question_ref']}.json").read_text(encoding="utf-8"))
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
        self.assertIn("Exact user-authored request", briefing)
        self.assertIn("idle and resumable", briefing)
        self.assertIn("followup_task", briefing)
        task_dir = next((self.ledger / "tasks").iterdir())
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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

    def test_v3_desktop_skill_link_is_canonicalized_before_task_persistence_and_labeling(self):
        request = (
            "[$cortex:orchestrator](/opt/cortex-test/.codex/plugins/cache/cortex/cortex/4.0.0/skills/"
            "orchestrator/SKILL.md) создай лендинг"
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["user_request"], "$cortex:orchestrator создай лендинг")
        self.assertEqual(task["objective"], "$cortex:orchestrator создай лендинг")
        self.assertRegex(task_dir.name, r"^0001-task-[0-9a-f]{8}$")
        self.assertNotIn("home", task_dir.name)
        self.assertNotIn("plugins", task_dir.name)
        self.assertNotIn("SKILL.md", (task_dir / "task.json").read_text(encoding="utf-8"))
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
        self.assertTrue(first["ok"])
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
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertFalse(task["intent_clarification_required"])
        self.assertEqual(control._intent_clarification_preflight("Implement mapping retry logic"), (False, None))
        self.assertEqual(control._intent_clarification_preflight("Fix the application crash"), (False, None))

    def test_v3_non_planner_worker_question_is_answered_through_coordinator_management(self):
        started = self.v3_start("backend behavior needs product choice", waves=[{
            "workers": [{"phase": "implementation", "profile": "backend_dev"}],
        }])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        index = json.loads((self.ledger / "task-index.json").read_text(encoding="utf-8"))
        old_task_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == old["task_ref"])
        old_dir = self.ledger / "tasks" / index[old_task_id]["directory"]
        old_state_path = old_dir / "current.json"
        old_state = json.loads(old_state_path.read_text(encoding="utf-8"))
        old_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        old_state_path.write_text(json.dumps(old_state), encoding="utf-8")
        old_task = json.loads((old_dir / "task.json").read_text(encoding="utf-8"))
        classification_path = self.ledger / "classification-receipts" / f"{old_task['classification_id']}.json"
        self.assertTrue(classification_path.is_file())
        claims_path = self.ledger / "resource-claims.json"
        claims_path.write_text(json.dumps({
            "old": {"scope_kind": "task", "scope_id": old_task_id},
            "lane": {"scope_kind": "lane", "scope_id": "shared-lane"},
        }), encoding="utf-8")
        lane_dir = self.ledger / "lanes" / "shared-lane"
        lane_dir.mkdir(parents=True)
        lane_state_path = lane_dir / "current.json"
        lane_state_path.write_text(json.dumps({
            "schema": control.SCHEMA,
            "lane_id": "shared-lane",
            "bound_tasks": [old_task_id, next(task_id for task_id in index if task_id != old_task_id)],
        }), encoding="utf-8")
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
        remaining_index = json.loads((self.ledger / "task-index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(remaining_index), 1)
        recent_task_id = next(iter(remaining_index))
        self.assertEqual(control._v3_task_ref(recent_task_id), recent["task_ref"])
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        self.assertNotIn(old_task_id, registry["tasks"])
        self.assertTrue(all(item.get("task_id") != old_task_id for item in registry["starts"].values()))
        self.assertFalse(classification_path.exists())
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        self.assertEqual(set(claims), {"lane"})
        lane_state = json.loads(lane_state_path.read_text(encoding="utf-8"))
        self.assertEqual(lane_state["bound_tasks"], [recent_task_id])
        self.assertFalse((self.ledger / "active-tasks.json").exists())
        if (self.ledger / "activations.json").exists():
            activations = json.loads((self.ledger / "activations.json").read_text(encoding="utf-8"))
            self.assertTrue(all(item.get("task_id") != old_task_id for item in activations.values()))
        if (self.ledger / "operations").exists():
            operation_task_ids = {
                json.loads(path.read_text(encoding="utf-8")).get("task_id")
                for path in (self.ledger / "operations").glob("*.json")
            }
            self.assertNotIn(old_task_id, operation_task_ids)
        replay = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertEqual(replay["pruned_count"], 0)

    def test_v3_multiple_same_project_tasks_are_isolated_by_task_ref(self):
        starts = [
            self.v3_start(f"independent session task {index}", waves=[{"workers": [{"phase": "discover"}]}])
            for index in range(1, 4)
        ]
        self.assertTrue(all(item["ok"] for item in starts))
        self.assertEqual(len({item["task_ref"] for item in starts}), 3)
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        self.assertEqual(len(registry["starts"]), 3)
        self.assertEqual(len(registry["tasks"]), 3)
        ambiguous = control.continue_orchestration({
            "project_root": str(self.project),
            "step": starts[0]["step"],
            "results": [{"report_ref": "report-not-selected"}],
        })
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(ambiguous["code"], "task_selection_required")
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
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
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
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        self.assertEqual(len(registry["starts"]), 1)
        self.assertEqual(len(registry["tasks"]), 1)

    def test_v3_normalizes_human_language_aliases_before_ledger_creation(self):
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "language alias", "language": "English", "user_language": "English",
                "acceptance_criteria": ["The requested outcome is observed."],
                "verification": ["Run an authoritative outcome check."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["user_language"], "en")

    def test_v3_compact_aliases_and_defaults_match_internal_delegation_contract(self):
        started = self.v3_start("compact aliases", waves=[{"workers": [{
            "phase": "research", "profile": "exploration", "objective": "Inspect",
            "paths": ["plugins/cortex"], "acceptance": ["Facts collected"],
            "verification": ["Cite files"], "model": "luna", "effort": "high",
        }]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertLess(len(prompt.encode("utf-8")), 11_500)
        self.assertLess(len(bootstrap.encode("utf-8")), 1_500)
        self.assertLess(len(serialized.encode("utf-8")), 8_000)
        self.assertLess(serialized.index("NEXT REQUIRED ACTION"), serialized.index("You are the internal Cortex worker"))
        self.assertIn("Never claim it was sent or call wait without the returned child target", started["next_action"])
        self.assertEqual(prompt.count(request), 1)
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        denied = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": dispatch["dispatch_ref"],
            "briefing_digest": "0" * 64,
        })
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["code"], "dispatch_briefing_unavailable")
        package = (task_dir / "delegations" / f"{attempt['attempt_id']}.json").read_text(encoding="utf-8")
        self.assertNotIn("## Specialist playbook", (task_dir / "current.json").read_text(encoding="utf-8"))
        self.assertNotIn("## Specialist playbook", package)

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            "project_root": str(self.project), "step": sequential["step"],
            "results": self.v3_results(sequential),
        })
        self.assertTrue(advanced["ok"])
        while advanced["outcome"] != "completed":
            advanced = control.continue_orchestration({
                "project_root": str(self.project), "step": advanced["step"],
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
            "waves": [{"workers": [{"phase": "discover"}, {"phase": "architecture"}]}],
        })
        task_dir = max((self.ledger / "tasks").iterdir())
        before = (task_dir / "current.json").read_bytes()
        rejected = control.continue_orchestration({
            "project_root": str(self.project), "step": started["step"],
            "results": [
                {"worker": 1, "report_ref": "report-one"},
                {"worker": 1, "report_ref": "report-duplicate"},
            ],
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual((task_dir / "current.json").read_bytes(), before)
        self.assertFalse("inflight_continue" in (self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        accepted_results = self.v3_results(started, [self.v3_report("one"), self.v3_report("two")])
        accepted = control.continue_orchestration({
            "project_root": str(self.project), "step": started["step"],
            "results": [accepted_results[1], accepted_results[0]],
        })
        self.assertTrue(accepted["ok"])

    def test_v3_continue_replays_exact_retry_and_requires_a_new_report_ref_on_next_step(self):
        started = self.v3_start("relative retry", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "qa"}]},
        ])
        payload = {
            "project_root": str(self.project), "step": started["step"],
            "results": self.v3_results(started, self.v3_report("same report")),
        }
        first = control.continue_orchestration(payload)
        replay = control.continue_orchestration(payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertNotEqual(replay, first)
        self.assertIn("Do not invoke or repeat a worker dispatch", replay["next_action"])
        registry = json.loads((self.ledger / "orchestration-operations.json").read_text(encoding="utf-8"))
        task_record = next(iter(registry["tasks"].values()))
        self.assertEqual(task_record["last_continue"]["response"]["dispatches"], [])
        self.assertTrue(task_record["last_continue"]["response"]["replayed"])
        self.assertEqual(first["step"], 2)
        second = control.continue_orchestration({
            "project_root": str(self.project),
            "step": first["step"],
            "results": self.v3_results(first, self.v3_report("same report")),
        })
        self.assertTrue(second["ok"])
        reports = list((next((self.ledger / "tasks").iterdir()) / "reports/records").glob("*.json"))
        self.assertEqual(len(reports), 2)
        state = json.loads((next((self.ledger / "tasks").iterdir()) / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            "project_root": str(self.project),
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
        self.assertIn("Attempt result baseline:", prompt)
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        records = list((task_dir / "reports/records").glob("*.json"))
        self.assertEqual(len(records), 1)
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertIn("No rm, git clean, or cleanup scripts", prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "report_validation_failed")
        self.assertIn("project files changed during read-only", rejected["diagnostics"][0]["message"])

    def test_read_only_result_rejects_new_generated_or_gitignored_artifacts(self):
        (self.project / ".gitignore").write_text("coverage.tmp\n", encoding="utf-8")
        started = self.v3_start(
            "independently verify without writing caches",
            waves=[{"workers": [{"phase": "review", "profile": "code_reviewer"}]}],
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        attempt = state["attempts"][0]
        cache = self.project / "src" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "module.cpython-312.pyc").write_bytes(b"cache")
        (self.project / "coverage.tmp").write_text("ignored side effect\n", encoding="utf-8")
        rejected = control.publish_worker_report({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "report": self._report_with_briefing(
                attempt, self.v3_report("read-only verification left generated artifacts")
            ),
        })
        self.assertFalse(rejected["ok"])
        self.assertIn(
            "generated or ignored project artifacts changed during read-only result gate",
            rejected["diagnostics"][0]["message"],
        )
        self.assertIn("src/__pycache__", rejected["diagnostics"][0]["message"])
        self.assertIn("coverage.tmp", rejected["diagnostics"][0]["message"])

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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

        approved = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "plan_approval",
            "payload": {"decision": "approve"},
        })
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["outcome"], "ready_to_spawn")
        self.assertEqual(approved["dispatches"][0]["phase"], "implementation")

    def test_v3_follow_up_creates_a_linked_corrective_task_without_mutating_completed_source(self):
        source = self.v3_start(
            "Заверши исходную задачу до корректирующего запроса",
            complexity="C1",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        source_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((source_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((source_dir / "current.json").read_text(encoding="utf-8"))
        created_handoff = control.handoff({
            "project_root": str(self.project), "task_id": state["task_id"], "principal": state["principal"],
            "expected_revision": state["revision"], "completed": ["Source task closed."],
            "files": [], "next_action": "Use this source only as corrective-task context.",
        })
        source_task_before = (source_dir / "task.json").read_text(encoding="utf-8")
        source_state_before = (source_dir / "current.json").read_text(encoding="utf-8")

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
        self.assertEqual((source_dir / "task.json").read_text(encoding="utf-8"), source_task_before)
        self.assertEqual((source_dir / "current.json").read_text(encoding="utf-8"), source_state_before)

        task_dirs = sorted((self.ledger / "tasks").iterdir())
        corrective_dir = next(path for path in task_dirs if path != source_dir)
        corrective_task = json.loads((corrective_dir / "task.json").read_text(encoding="utf-8"))
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
        corrective_state = json.loads((corrective_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertEqual((source_dir / "task.json").read_text(encoding="utf-8"), source_task_before)
        self.assertEqual((source_dir / "current.json").read_text(encoding="utf-8"), source_state_before)

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        manifest = json.loads((task_dir / "planning/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], control.PLANNING_SCHEMA)
        self.assertEqual(manifest["source_report_ref"], published["report_ref"])
        self.assertEqual([package["id"] for package in manifest["work_packages"]], ["api", "ui"])
        self.assertTrue((task_dir / "planning/overview.md").is_file())
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/packages/api.json").is_file())
        self.assertTrue((task_dir / "planning/revisions/plan-report-0001/packages/ui.json").is_file())

        held = control.continue_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "step": started["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        self.assertEqual(held["outcome"], "awaiting_plan_approval")
        artifacts = held["plan_review"]["planning_artifacts"]
        self.assertEqual(artifacts["manifest_path"], "planning/manifest.json")
        self.assertEqual([package["id"] for package in artifacts["work_packages"]], ["api", "ui"])

    def test_planner_work_packages_reject_cycles_and_missing_artifact(self):
        started = self.v3_start("validate work breakdown artifacts", waves=[{"workers": [{"phase": "plan"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state_path = task_dir / "current.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
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
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["bounded_large_state_fixture"] = "x" * (control.MAX_REPORT_BYTES * 5)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        read = control.read_worker_report({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
        })
        self.assertTrue(read["ok"])
        self.assertEqual(read["report"]["summary"], "large-state report remains readable")

    def test_v3_report_read_returns_recoverable_result_for_missing_identity_or_record(self):
        missing_root = control.read_worker_report({"report_ref": "report-0001"})
        self.assertFalse(missing_root["ok"])
        self.assertEqual(missing_root["code"], "report_unavailable")
        started = self.v3_start("missing report", waves=[{"workers": [{"phase": "discover"}]}])
        missing_record = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"], "report_ref": "report-9999",
        })
        self.assertFalse(missing_record["ok"])
        self.assertEqual(missing_record["code"], "report_unavailable")

    def test_large_baseline_manifest_is_readable_during_handoff_and_reconciliation(self):
        started = self.v3_start("large baseline handoff", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        baseline_path = task_dir / "baseline-manifest.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["test_padding"] = "x" * (control.MAX_REPORT_BYTES * 5)
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))

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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"report_ref": first["report_ref"]}],
        })
        self.assertTrue(advanced["ok"])
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        self.assertIn(
            "Context files: docs/project/index.md; docs/features/index.md; docs/features/trading/index.md",
            prompt,
        )
        self.assertIn("Before broad source search, design, or edits", prompt)
        self.assertIn("docs/features/index.md as the capability/coverage catalog", prompt)
        self.assertIn("Every changed_files item must be a safe project-relative path", prompt)
        self.assertIn("Required report evidence acknowledgements for this exact attempt", prompt)
        self.assertIn("Knowledge reviewed: docs/project/index.md, docs/features/index.md", prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        project_docs = self.project / "docs/project"
        feature_docs = self.project / "docs/features"
        project_docs.mkdir(parents=True)
        feature_docs.mkdir(parents=True)
        (project_docs / "index.md").write_text("# Project knowledge\n", encoding="utf-8")
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
        self.assertIn("extra columns may follow only after them", writer_prompt)
        self.assertIn("status is exactly `covered`, `documented`, `verified`, or `excluded`", writer_prompt)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            {"workers": [{"phase": "plan"}]},
            {"workers": [{"phase": "discover", "depends_on": ["plan"]}]},
            {"workers": [{"phase": "architecture", "depends_on": ["plan"]}]},
            {"workers": [{"phase": "implementation", "depends_on": ["architecture"]}]},
        ])
        current = started
        reports = []
        for summary in ("plan handoff", "discovery handoff", "architecture handoff"):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": current["step"],
                "results": self.v3_results(current, self.v3_report(summary)),
            })
            self.assertTrue(current["ok"])
            reports.append(summary)
            prompt = self.briefing_from_response(current)
            if current["dispatches"][0]["phase"] == "architecture":
                self.assertIn("Verified predecessor handoff refs: report-0001", prompt)
                self.assertNotIn("report-0002", prompt)
            if current["dispatches"][0]["phase"] == "implementation":
                self.assertIn("Verified predecessor handoff refs: report-0003", prompt)
                self.assertNotIn("report-0001", prompt)
                self.assertNotIn("report-0002", prompt)

    def test_v3_reference_handoffs_scale_without_dropping_old_reports(self):
        with mock.patch.object(control, "MAX_CONTEXT_REPORTS", 1):
            started = self.v3_start("bounded handoff overflow", plan_approval="auto", waves=[
                {"workers": [{"phase": "plan"}]},
                {"workers": [{"phase": "discover"}]},
                {"workers": [{"phase": "architecture"}]},
            ])
            second = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": started["step"],
                "results": self.v3_results(started, self.v3_report("plan report")),
            })
            self.assertTrue(second["ok"])
            advanced = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "step": second["step"],
                "results": self.v3_results(second, self.v3_report("discover report")),
            })
            self.assertTrue(advanced["ok"])
            prompt = self.briefing_from_response(advanced)
            self.assertIn("report-0001", prompt)
            self.assertIn("report-0002", prompt)

    def test_v3_inspect_recovers_report_when_native_worker_ack_is_interrupted(self):
        started = self.v3_start("recover persisted report", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
            "step": inspected["step"],
            "results": [{"report_ref": published["report_ref"]}],
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["dispatches"][0]["phase"], "implementation")

    def test_v3_public_schema_never_advertises_inline_worker_reports(self):
        result_schema = control.CONTINUE_ORCHESTRATION_SCHEMA["properties"]["results"]["items"]
        self.assertNotIn("report", result_schema["properties"])
        self.assertIn("report_ref", result_schema["properties"])
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
            "project_root": str(self.project), "step": started["step"],
            "results": [{"status": "failed", "reason": "transient worker failure"}],
        })
        self.assertTrue(failed["ok"])
        self.assertEqual(failed["step"], started["step"])
        self.assertEqual(len(failed["dispatches"]), 1)
        retried = control.continue_orchestration({
            "project_root": str(self.project), "step": failed["step"],
            "results": self.v3_results(failed, self.v3_report("retry succeeded")),
        })
        self.assertTrue(retried["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        failed_attempts = [item for item in state["attempts"] if item["status"] == "failed"]
        self.assertEqual(len(failed_attempts), 1)
        self.assertTrue(failed_attempts[0]["invalidated"])
        self.assertEqual(failed_attempts[0]["invalidation_reason"], "retry_after_failure")

    def test_v3_automatic_gate_rework_is_bounded_and_resume_resets_its_budget(self):
        current = self.v3_start("bounded worker retry", waves=[{"workers": [{"phase": "discover"}]}])
        for failure_number in range(1, control.MAX_ORCHESTRATE_GATE_FAILURES + 1):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "step": current["step"],
                "results": [{"status": "failed", "reason": f"worker failure {failure_number}"}],
            })
            self.assertTrue(current["ok"], current)
            if failure_number < control.MAX_ORCHESTRATE_GATE_FAILURES:
                self.assertEqual(current["outcome"], "ready_to_spawn")
                self.assertEqual(len(current["dispatches"]), 1)
        self.assertEqual(current["outcome"], "blocked")
        self.assertEqual(current["dispatches"], [])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        self.assertNotIn("discover", state["orchestrate_gate_failure_counts"])

    def test_v3_final_continue_retry_replays_after_task_completion(self):
        started = self.v3_start("final replay", waves=[{"workers": [{"phase": "close"}]}], complexity="tiny")
        current = started
        payload = None
        while current["outcome"] != "completed":
            payload = {
                "project_root": str(self.project), "step": current["step"],
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

    def test_v3_ambiguous_active_tasks_require_an_opaque_task_ref(self):
        for objective in ("first active", "second active"):
            self.v3_start(objective, waves=[{"workers": [{"phase": "discover"}]}])
        ambiguous = control.manage_orchestration({"project_root": str(self.project)})
        self.assertEqual(ambiguous["outcome"], "needs_selection")
        self.assertEqual({item["objective"] for item in ambiguous["candidates"]}, {"first active", "second active"})
        selected = control.manage_orchestration({
            "project_root": str(self.project), "intent": "inspect",
            "task_ref": ambiguous["candidates"][0]["task_ref"],
        })
        self.assertTrue(selected["ok"])
        self.assertNotIn("task_id", selected)

    def test_public_api_ignores_private_task_without_canonical_plan(self):
        created = self.init(task_id="unplanned-private-task", complexity="C1")
        self.delegate(created["state"], "unplanned-private-task", "discover", "explorer")
        inspected = control.manage_orchestration({"project_root": str(self.project), "intent": "inspect"})
        self.assertFalse(inspected["ok"])
        self.assertEqual(inspected["code"], "no_active_task")

    def test_v3_future_wave_rework_requires_explicit_opt_in(self):
        started = self.v3_start("v3 future rework", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        common = {
            "project_root": str(self.project), "step": started["step"],
            "results": self.v3_results(started, self.v3_report("discovery complete")),
            "future_waves": [{"workers": [{"phase": "discover"}, {"phase": "implementation"}]}],
            "reason": "new evidence requires discovery rework",
        }
        denied = control.continue_orchestration(common)
        self.assertFalse(denied["ok"])
        self.assertIn("allow_rework=true", denied["diagnostics"][0]["message"])
        allowed = control.continue_orchestration({**common, "rework": True, "reason": "new evidence"})
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["step"], 1)

    def test_v3_noop_future_wave_reassessment_advances_with_monotonic_steps(self):
        started = self.v3_start("v3 no-op future", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        advanced = control.continue_orchestration({
            "project_root": str(self.project), "step": started["step"],
            "results": self.v3_results(started, self.v3_report("discovery complete")),
            "future_waves": [{"workers": [{"phase": "implementation"}]}],
            "reason": "confirm the coordinator-selected implementation route",
        })
        self.assertTrue(advanced["ok"])
        self.assertEqual(advanced["step"], 2)
        self.assertEqual(len(advanced["dispatches"]), 1)
        task_dir = next((self.ledger / "tasks").iterdir())
        plan = json.loads((task_dir / "orchestration.json").read_text(encoding="utf-8"))
        wave_ids = [wave["wave_id"] for wave in plan["waves"]]
        self.assertEqual(wave_ids, sorted(set(wave_ids)))

    def test_orchestrate_start_replays_and_advance_returns_parallel_then_dependent_wave(self):
        waves = [
            {"wave_id": "plan", "delegations": [{"gate": "plan", "agent": "planner"}]},
            {"wave_id": "discovery", "delegations": [
                {"gate": "discover", "agent": "explorer"},
                {"gate": "architecture", "agent": "architect"},
            ]},
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
        self.assertIn("call the public `record_report` tool exactly once", briefing)
        self.assertIn("do not paste or reproduce that JSON", briefing)

        discovery = control.orchestrate({
            "operation": "advance", "submission_id": "facade-waves-advance-plan",
            "task_id": "facade-waves", "wave_id": started["wave_id"],
            "principal": "thread-a", "thread_id": "thread-a",
            "completions": [self.facade_completion(started["spawn_requests"][0])],
        })
        self.assertEqual(discovery["wave_id"], "discovery")
        self.assertEqual(len(discovery["spawn_requests"]), 2)
        active_attempt_ids = {
            item["attempt_id"] for item in discovery["state_summary"]["attempts"]
            if item["status"] == control.AWAITING_HOST_SPAWN
        }
        self.assertEqual({item["attempt_id"] for item in discovery["spawn_requests"]}, active_attempt_ids)

        implementation = control.orchestrate({
            "operation": "advance", "submission_id": "facade-waves-advance-discovery",
            "task_id": "facade-waves", "wave_id": discovery["wave_id"],
            "principal": "thread-a", "thread_id": "thread-a",
            "completions": [self.facade_completion(item) for item in discovery["spawn_requests"]],
        })
        self.assertEqual(implementation["wave_id"], "implementation")
        self.assertEqual(len(implementation["spawn_requests"]), 1)

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
        original_checkpoint = control._checkpoint_orchestrate_transaction
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

                with mock.patch.object(control, "_checkpoint_orchestrate_transaction", side_effect=crash_after_checkpoint):
                    interrupted = self.facade_start(task_id, waves)
                self.assertFalse(interrupted["ok"])
                recovered = self.facade_start(task_id, waves)
                self.assertTrue(recovered["ok"])
                self.assertEqual(len(recovered["spawn_requests"]), 1)
                state = control.orchestrate({"operation": "inspect", "task_id": task_id, "principal": "thread-a"})
                self.assertEqual(len(state["state_summary"]["attempts"]), 1)
                receipt = json.loads((self.ledger / "operations" / f"{task_id}-start.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "committed")

    def test_orchestrate_advance_recovers_after_every_transaction_phase(self):
        waves = [
            {"wave_id": "discover", "delegations": [{"gate": "discover", "agent": "explorer"}]},
            {"wave_id": "implementation", "delegations": [{"gate": "implementation", "agent": "general"}]},
            {"wave_id": "review", "delegations": [{"gate": "review", "agent": "code_reviewer"}]},
        ]
        original_checkpoint = control._checkpoint_orchestrate_transaction
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

                with mock.patch.object(control, "_checkpoint_orchestrate_transaction", side_effect=crash_after_checkpoint):
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
                receipt = json.loads((self.ledger / "operations" / f"{task_id}-advance.json").read_text(encoding="utf-8"))
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
        plan_path = task_dir / "orchestration.json"
        self.assertFalse(plan_path.exists())
        inspected = control.orchestrate({
            "operation": "inspect", "task_id": "facade-v7-compatibility", "principal": "thread-a",
        })
        self.assertFalse(inspected["ok"])
        self.assertIn("canonical orchestration plan is missing", inspected["diagnostics"][0]["message"])
        self.assertFalse(plan_path.exists())
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
        self.assertFalse(plan_path.exists())

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
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "manage_orchestration", "arguments": {"intent": "question", "project_root": str(self.project), "payload": {"command": "ask", "question": "Continue?"}}}}) + "\n")
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
        package = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual(package["requested_model"], "gpt-5.6-terra")
        self.assertEqual(package["selected_model"], "gpt-5.6-terra")
        self.assertIsNone(package["fallback_reason"])
        self.assertEqual(package["selected_reasoning_effort"], "high")
        self.assertEqual(package["model_choice_reason"], "coordinator_selected_terra")
        report = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": [], "next_action": "advance"}})
        replay = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": [], "next_action": "advance"}})
        self.assertTrue(replay["idempotent"])
        task_dir = self.ledger / "tasks" / "0001-reports"
        artifacts = "\n".join(path.read_text(encoding="utf-8") for path in (task_dir / "reports").rglob("*") if path.is_file())
        self.assertNotIn("canary", artifacts)
        self.assertIn("&lt;script&gt;", (task_dir / "reports/markdown/report-0001.md").read_text(encoding="utf-8"))
        evidence = control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "report-backed evidence"})
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "receipt replay"})
        (task_dir / "reports/markdown/report-0001.md").unlink()
        reconciled = control.reconcile_report_bus({"task_id": "reports", "principal": "thread-a"})
        self.assertEqual(reconciled["report_count"], 1)
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
                results.append(control.record_report({"task_id": "publishers", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": f"publisher-{index}", "report": {"summary": f"publisher {index}", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": [f"evidence {index}"], "uncertainty": [], "next_action": "merge"}}))
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
        original = (task_dir / "records/report-0001.json").read_bytes()
        (task_dir / "index.json").write_text(json.dumps({"schema": control.REPORT_SCHEMA, "task_id": "crash", "reports": [], "submissions": {}, "updated_at": control.now()}), encoding="utf-8")
        (task_dir / "markdown/report-0001.md").unlink()
        (task_dir / "receipts/report-receipt-report-0001.json").unlink()
        replay = self.report("crash", delegation["attempt_id"], submission_id="first")
        self.assertTrue(replay["idempotent"])
        second = self.report("crash", delegation["attempt_id"], submission_id="second")
        self.assertEqual(second["report"]["report_id"], "report-0002")
        self.assertEqual((task_dir / "records/report-0001.json").read_bytes(), original)
        reconciled = control.reconcile_report_bus({"task_id": "crash", "principal": "thread-a"})
        self.assertEqual(reconciled["report_count"], 2)

    def test_reconcile_preserves_orphan_receipt_consumption_as_tombstone(self):
        state = self.init(task_id="receipt-boundary", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-boundary", "plan", "planner")
        report = self.report("receipt-boundary", delegation["attempt_id"])
        receipt_path = self.ledger / "tasks/0001-receipt-boundary/reports/receipts/report-receipt-report-0001.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["consumed_at"] = control.now()
        receipt["consumed_by_evidence_id"] = "evidence-0001"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        reconciled = control.reconcile_report_bus({"task_id": "receipt-boundary", "principal": "thread-a"})
        repaired = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertNotIn("receipts/report-receipt-report-0001.json", reconciled["repaired"])
        self.assertIsNotNone(repaired["consumed_at"])
        self.assertEqual(repaired["consumed_by_evidence_id"], "evidence-0001")
        tombstone = receipt_path.parents[1] / "consumptions" / "report-receipt-report-0001.json"
        self.assertTrue(tombstone.is_file())
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "receipt-boundary", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "recovered"})

    def test_consumed_report_replay_reconstructs_consumed_receipt(self):
        state = self.init(task_id="receipt-replay", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-replay", "plan", "planner")
        report = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        evidence = control.record_evidence({"task_id": "receipt-replay", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "consumed"})
        receipt_path = self.ledger / "tasks/0001-receipt-replay/reports/receipts/report-receipt-report-0001.json"
        receipt_path.unlink()
        replay = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        self.assertTrue(replay["idempotent"])
        self.assertIsNotNone(replay["receipt"]["consumed_at"])
        self.assertEqual(replay["receipt"]["consumed_by_evidence_id"], "evidence-0001")
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
                bus_child.rmdir()
                bus_child.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink|real directory"):
                    control.report_bus_paths(task_dir)
                self.assertEqual(list(target.iterdir()), [])

    def test_report_crash_points_recover_deterministically(self):
        original_exclusive_json = control.write_json_exclusive
        original_exclusive_text = control.write_text_exclusive
        original_json = control.write_json
        phases = ("markdown", "receipt", "index", "delegation")
        for phase_index, phase in enumerate(phases, 1):
            with self.subTest(phase=phase):
                task_id = f"crash-{phase}"
                state = self.init(task_id=task_id, complexity="C2")["state"]
                delegation = self.delegate(state, task_id, "plan", "planner")
                fired = {"value": False}

                def exclusive_text(path, text):
                    if phase == "markdown" and path.parent.name == "markdown" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after record")
                    return original_exclusive_text(path, text)

                def exclusive_json(path, value):
                    if phase == "receipt" and path.parent.name == "receipts" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after markdown")
                    return original_exclusive_json(path, value)

                def write_json(path, value):
                    target = (phase == "index" and path.name == "index.json" and path.parent.name == "reports") or (phase == "delegation" and path.name == "index.json" and path.parent.parent.name == "delegations")
                    if target and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after authoritative artifact")
                    return original_json(path, value)

                control.write_text_exclusive, control.write_json_exclusive, control.write_json = exclusive_text, exclusive_json, write_json
                try:
                    with self.assertRaises(OSError):
                        self.report(task_id, delegation["attempt_id"], submission_id="stable")
                finally:
                    control.write_text_exclusive, control.write_json_exclusive, control.write_json = original_exclusive_text, original_exclusive_json, original_json
                recovered = self.report(task_id, delegation["attempt_id"], submission_id="stable")
                self.assertTrue(recovered["idempotent"])
                self.assertEqual(recovered["report"]["report_id"], "report-0001")
                self.assertEqual(control.reconcile_report_bus({"task_id": task_id, "principal": "thread-a"})["report_count"], 1)

    def test_report_allocation_skips_orphaned_markdown_artifacts(self):
        state = self.init(task_id="orphan-markdown", complexity="C2")["state"]
        delegation = self.delegate(state, "orphan-markdown", "plan", "planner")
        task_dir = self.ledger / "tasks/0001-orphan-markdown"
        (task_dir / "reports/markdown/report-0001.md").write_text("orphan\n", encoding="utf-8")
        recorded = self.report("orphan-markdown", delegation["attempt_id"])
        self.assertEqual(recorded["report"]["report_id"], "report-0002")

    def test_report_quotas_and_terminal_attempt_are_rejected(self):
        state = self.init(task_id="quotas", complexity="C2")["state"]
        delegation = self.delegate(state, "quotas", "plan", "planner")
        original_attempt, original_task, original_bytes = control.MAX_REPORTS_PER_ATTEMPT, control.MAX_REPORTS_PER_TASK, control.MAX_REPORT_AGGREGATE_BYTES
        try:
            control.MAX_REPORTS_PER_ATTEMPT = 1
            self.report("quotas", delegation["attempt_id"], submission_id="one")
            with self.assertRaisesRegex(ValueError, "quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="two")
            control.MAX_REPORTS_PER_ATTEMPT = 10
            control.MAX_REPORTS_PER_TASK = 1
            with self.assertRaisesRegex(ValueError, "quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="task-limit")
            control.MAX_REPORTS_PER_TASK = original_task
            control.MAX_REPORT_AGGREGATE_BYTES = 1
            with self.assertRaisesRegex(ValueError, "byte quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="byte-limit")
        finally:
            control.MAX_REPORTS_PER_ATTEMPT, control.MAX_REPORTS_PER_TASK, control.MAX_REPORT_AGGREGATE_BYTES = original_attempt, original_task, original_bytes
        report = control.record_report({"task_id": "quotas", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "one", "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": [], "next_action": "advance the gate"}})
        evidence = control.record_evidence({"task_id": "quotas", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "done"})
        control.record_gate({"task_id": "quotas", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        with self.assertRaisesRegex(ValueError, "terminal"):
            self.report("quotas", delegation["attempt_id"], submission_id="late")

    def test_journal_symlinks_cannot_modify_task_or_lane_state(self):
        state = self.init(task_id="journal", complexity="C1")["state"]
        state = control.record_gate({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked"})["state"]
        task_dir = self.ledger / "tasks/0001-journal"
        sentinel = self.base / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        (task_dir / "journal.md").unlink()
        (task_dir / "journal.md").symlink_to(sentinel)
        resumed = control.resume_task({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"]})
        self.assertEqual(resumed["state"]["status"], "active")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        lane = control.create_lane({"lane_id": "journal-lane", "principal": "thread-a"})
        lane_dir = self.ledger / "lanes/journal-lane"
        (lane_dir / "journal.md").unlink()
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
        root = self.ledger
        lane_state = root / "lanes" / "recover-lane" / "current.json"
        state = json.loads(lane_state.read_text())
        state["lease"] = {"owner": "thread-a", "run_id": "old", "expires_at": "2000-01-01T00:00:00+00:00"}
        lane_state.write_text(json.dumps(state))
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
        self.assertEqual(names, {"start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "record_report", "read_dispatch_briefing", "read_worker_report"})
        self.assertNotIn("orchestrate", names)
        self.assertEqual(len(tools), 7)
        self.assertTrue(all("project_root" in item["inputSchema"]["properties"] for item in tools))
        by_name = {item["name"]: item for item in tools}
        self.assertEqual(by_name["start_orchestration"]["inputSchema"]["required"], ["project_root", "task"])
        self.assertEqual(by_name["start_orchestration"]["inputSchema"]["properties"]["task"]["required"], ["user_request"])
        self.assertEqual(by_name["continue_orchestration"]["inputSchema"]["required"], ["project_root", "step", "results"])
        self.assertEqual(by_name["worker_question"]["inputSchema"]["required"], ["project_root", "task_id", "attempt_id", "profile", "action"])
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
            self.assertEqual(initialized["result"]["serverInfo"]["version"].split("+", 1)[0], "6.1.0")
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
            self.assertIn("Select this profile", self.briefing_from_response(started))
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
