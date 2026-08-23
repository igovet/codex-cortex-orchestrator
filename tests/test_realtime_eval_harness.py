"""Deterministic acceptance tests for the source-mode realtime evaluator."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cortex-luna-high-eval.py"
FAILURE_FIXTURE_API_KEY = "TEST_ONLY_FAILURE_METADATA_API_KEY"


def load_harness():
    spec = importlib.util.spec_from_file_location("cortex_luna_high_eval", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_CHILD = r'''
import json
from pathlib import Path
import subprocess
import sys
import time

mode, marker = sys.argv[1], Path(sys.argv[2])
if mode == "emit":
    print(json.dumps({
        "type": "item",
        "item": {
            "type": "mcp_tool_call",
            "tool": "mcp__cortex__complete_attempt",
            "status": "completed",
            "arguments": {"prompt": "SECRET_PROMPT", "result": "SECRET_RESULT"},
            "result": {"structured_content": {"ok": True, "token": "SECRET_TOKEN"}},
        },
    }), flush=True)
    time.sleep(0.45)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "silence":
    time.sleep(1.3)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "secrets":
    print(json.dumps({
        "type": "item",
        "item": {"type": "agent_message", "text": "SECRET_PROMPT SECRET_RESULT"},
    }), flush=True)
    print(json.dumps({
        "type": "item",
        "item": {
            "type": "mcp_tool_call",
            "tool": "mcp__cortex__start_orchestration",
            "status": "completed",
            "arguments": {"prompt": "SECRET_PROMPT"},
            "result": {"structured_content": {"ok": False, "result": "SECRET_RESULT"}},
        },
    }), flush=True)
    print("SECRET_STDERR", file=sys.stderr, flush=True)
    marker.write_text("child-finished", encoding="utf-8")
elif mode == "group":
    grandchild_code = (
        "from pathlib import Path; import time; "
        f"Path({str(marker)!r}).write_text('grandchild-alive'); time.sleep(60)"
    )
    grandchild = subprocess.Popen([sys.executable, "-c", grandchild_code])
    marker.with_suffix(".pid").write_text(str(grandchild.pid), encoding="utf-8")
    time.sleep(60)
elif mode == "exited_parent":
    grandchild_code = (
        "from pathlib import Path; import time; "
        f"Path({str(marker)!r}).write_text('grandchild-alive'); time.sleep(60)"
    )
    grandchild = subprocess.Popen([sys.executable, "-c", grandchild_code])
    marker.with_suffix(".pid").write_text(str(grandchild.pid), encoding="utf-8")
    # The direct parent exits successfully while its inherited pipe holder remains.
    raise SystemExit(0)
elif mode == "gates_recorded_failure":
    print(json.dumps({
        "type": "item",
        "item": {
            "type": "mcp_tool_call",
            "tool": "mcp__cortex__continue_orchestration",
            "status": "completed",
            "arguments": {"step": 3, "future_waves": "SECRET_FUTURE_WAVES"},
            "result": {"structuredContent": {
                "ok": False,
                "code": "orchestrate_validation_failed",
                "phase": "gates_recorded",
                "diagnostics": [{"message": "SECRET_DIAGNOSTIC"}],
            }},
        },
    }), flush=True)
    time.sleep(1.3)
    marker.write_text("child-continued-after-failure", encoding="utf-8")
'''


WRAPPER = r'''
import importlib.util
import json
from pathlib import Path
import sys

script, child, project, mode, marker, heartbeat, timeout = sys.argv[1:]
spec = importlib.util.spec_from_file_location("cortex_luna_high_eval_wrapper", script)
if spec is None or spec.loader is None:
    raise SystemExit("unable to load harness")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
harness.HEARTBEAT_SECONDS = float(heartbeat)
result = harness.run_live_command(
    [sys.executable, child, mode, marker],
    Path(project),
    "fixture",
    timeout_seconds=float(timeout),
)
print(json.dumps({"final": result}, sort_keys=True), flush=True)
'''


class RealtimeEvalHarnessTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()

    def setUp(self) -> None:
        self.set_up_host_private_control_store()
        self.tempdir = tempfile.TemporaryDirectory(prefix="cortex-realtime-test-")
        self.root = Path(self.tempdir.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.child = self.root / "fake_child.py"
        self.child.write_text(FAKE_CHILD, encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        self.tear_down_host_private_control_store()

    def run_wrapper(
        self,
        mode: str,
        *,
        heartbeat: float = 15,
        timeout: float = 5,
        send_sigterm: bool = False,
    ) -> tuple[subprocess.Popen[str], Path, str]:
        marker = self.root / f"{mode}.marker"
        command = [
            sys.executable,
            "-c",
            WRAPPER,
            str(SCRIPT),
            str(self.child),
            str(self.project),
            mode,
            str(marker),
            str(heartbeat),
            str(timeout),
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if send_sigterm:
            return process, marker, ""
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        return process, marker, output

    def assert_pid_stopped(self, pid_path: Path) -> None:
        self.assertTrue(pid_path.exists(), f"fake child did not record grandchild PID: {pid_path}")
        pid = int(pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            # A killed descendant can briefly remain as a zombie while init reaps it.
            stat_path = Path("/proc") / str(pid) / "stat"
            if stat_path.exists():
                state = stat_path.read_text(encoding="utf-8", errors="replace").split()[2:3]
                if state == ["Z"]:
                    return
            time.sleep(0.05)
        self.fail(f"grandchild process {pid} survived process-group termination")

    def parse_lines(self, output: str) -> list[dict[str, object]]:
        lines = output.splitlines()
        self.assertTrue(lines)
        parsed = [json.loads(line) for line in lines]
        self.assertTrue(all(isinstance(item, dict) for item in parsed))
        return parsed

    def test_child_output_is_streamed_before_child_exit(self) -> None:
        marker = self.root / "emit.marker"
        command = [
            sys.executable,
            "-c",
            WRAPPER,
            str(SCRIPT),
            str(self.child),
            str(self.project),
            "emit",
            str(marker),
            "15",
            "5",
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        first = json.loads(process.stdout.readline())
        second = json.loads(process.stdout.readline())
        self.assertEqual(first["event"], "parent_started")
        self.assertEqual(second["event"], "cortex_mcp_call")
        self.assertFalse(marker.exists(), "child output was delayed until after child exit")
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(json.loads(output.splitlines()[-1])["final"]["returncode"], 0)

    def test_heartbeat_records_last_activity_during_child_silence(self) -> None:
        _process, _marker, output = self.run_wrapper("silence", heartbeat=0.05)
        events = self.parse_lines(output)
        heartbeats = [item for item in events if item.get("event") == "heartbeat"]
        self.assertTrue(heartbeats)
        heartbeat = heartbeats[0]
        self.assertIn("last_activity", heartbeat)
        self.assertIn("last_activity_seconds_ago", heartbeat)
        self.assertIsInstance(heartbeat["last_activity"], str)
        self.assertIsInstance(heartbeat["last_activity_seconds_ago"], int)
        self.assertTrue(heartbeat["process_running"])

    def test_stream_metadata_is_sanitized_and_bounded(self) -> None:
        _process, _marker, output = self.run_wrapper("secrets", heartbeat=0.05)
        self.assertNotIn("SECRET_PROMPT", output)
        self.assertNotIn("SECRET_RESULT", output)
        self.assertNotIn("SECRET_TOKEN", output)
        self.assertNotIn("SECRET_STDERR", output)
        events = self.parse_lines(output)
        for event in events:
            self.assertLessEqual(len(json.dumps(event, sort_keys=True)), 1000)
            self.assertNotIn("arguments", event)
            self.assertNotIn("result", event)
        calls = [item for item in events if item.get("event") == "cortex_mcp_call"]
        self.assertEqual(calls[0]["tool"], "start_orchestration")
        self.assertEqual(calls[0]["ok"], False)

    def test_native_collaboration_events_expose_only_safe_tool_status(self) -> None:
        line = json.dumps({
            "type": "item",
            "item": {
                "type": "collab_tool_call",
                "tool": "spawn_agent",
                "status": "completed",
                "prompt": "SECRET_PROMPT",
                "agents_states": {"child": {"message": "SECRET_RESULT"}},
            },
        })
        event = self.harness.sanitize_codex_stream_line(line)
        self.assertEqual(event, {
            "event": "native_tool_call",
            "tool": "spawn_agent",
            "status": "completed",
            "outcome": "other_terminal_message",
            "agent_statuses": {"unknown": 1},
        })
        self.assertNotIn("SECRET_PROMPT", json.dumps(event))
        self.assertNotIn("SECRET_RESULT", json.dumps(event))

        closed = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {"type": "collab_tool_call", "tool": "close_agent", "status": "completed"},
        }))
        self.assertEqual(closed["tool"], "close_agent")

    def test_native_followup_transport_is_reported_without_arguments(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "collab_tool_call",
                "tool": "followup_task",
                "status": "completed",
                "arguments": {"target": "SECRET_CHILD"},
            },
        }))
        self.assertEqual(event, {
            "event": "native_tool_call",
            "tool": "followup_task",
            "status": "completed",
        })
        self.assertNotIn("SECRET_CHILD", json.dumps(event))

    def test_question_management_telemetry_retains_only_safe_single_resume_contract(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__manage_orchestration",
                "status": "completed",
                "arguments": {
                    "intent": "question",
                    "task_ref": "SECRET_TASK",
                    "payload": {"question_ref": "SECRET_QUESTION", "answer": "SECRET_ANSWER"},
                },
                "result": {"structuredContent": {
                    "ok": True,
                    "outcome": "question_answered",
                    "next_action": "Current server wording may change without changing the resume contract.",
                    "resume_contract": {
                        "question_ref": "SECRET_QUESTION",
                        "attempt_id": "SECRET_ATTEMPT",
                        "profile": "general",
                        "poll_action": "poll",
                    },
                    "result": {"question_ref": "SECRET_QUESTION", "answer": "SECRET_ANSWER"},
                }},
            },
        }))
        self.assertEqual(event, {
            "event": "cortex_mcp_call",
            "tool": "manage_orchestration",
            "status": "completed",
            "ok": True,
            "management_intent": "question",
            "outcome": "question_answered",
            "resume_contract": True,
        })
        rendered = json.dumps(event, sort_keys=True)
        self.assertNotIn("SECRET_TASK", rendered)
        self.assertNotIn("SECRET_QUESTION", rendered)
        self.assertNotIn("SECRET_ATTEMPT", rendered)
        self.assertNotIn("SECRET_ANSWER", rendered)

    def test_question_management_telemetry_accepts_batch_resume_contract_without_retaining_it(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__manage_orchestration",
                "status": "completed",
                "arguments": {"intent": "question", "payload": {"question_ref": "SECRET_BATCH"}},
                "result": {"structuredContent": {
                    "ok": True,
                    "outcome": "question_answered",
                    "resume_contract": {
                        "batch_ref": "SECRET_BATCH",
                        "attempt_id": "SECRET_ATTEMPT",
                        "profile": "general",
                        "poll_action": "poll_batch",
                    },
                }},
            },
        }))
        self.assertEqual(event, {
            "event": "cortex_mcp_call",
            "tool": "manage_orchestration",
            "status": "completed",
            "ok": True,
            "management_intent": "question",
            "outcome": "question_answered",
            "resume_contract": True,
        })
        self.assertNotIn("SECRET_BATCH", json.dumps(event, sort_keys=True))
        self.assertNotIn("SECRET_ATTEMPT", json.dumps(event, sort_keys=True))

    def test_question_management_telemetry_rejects_malformed_resume_contract(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__manage_orchestration",
                "status": "completed",
                "arguments": {"intent": "question", "payload": {"question_ref": "SECRET_QUESTION"}},
                "result": {"structuredContent": {
                    "ok": True,
                    "outcome": "question_answered",
                    "resume_contract": {
                        "question_ref": "SECRET_QUESTION",
                        "batch_ref": "SECRET_BATCH",
                        "attempt_id": "SECRET_ATTEMPT",
                        "profile": "general",
                        "poll_action": "poll",
                    },
                }},
            },
        }))
        self.assertEqual(event["resume_contract"], False)
        self.assertNotIn("SECRET_QUESTION", json.dumps(event, sort_keys=True))
        self.assertNotIn("SECRET_BATCH", json.dumps(event, sort_keys=True))
        self.assertNotIn("SECRET_ATTEMPT", json.dumps(event, sort_keys=True))

    def test_question_management_telemetry_keeps_awaiting_user_without_resume_contract(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__manage_orchestration",
                "status": "completed",
                "arguments": {"intent": "question", "payload": {"question_ref": "SECRET_QUESTION"}},
                "result": {"structuredContent": {
                    "ok": True,
                    "outcome": "awaiting_user",
                    "resume_contract": {"question_ref": "SECRET_QUESTION"},
                }},
            },
        }))
        self.assertEqual(event, {
            "event": "cortex_mcp_call",
            "tool": "manage_orchestration",
            "status": "completed",
            "ok": True,
            "management_intent": "question",
            "outcome": "awaiting_user",
        })
        self.assertNotIn("SECRET_QUESTION", json.dumps(event, sort_keys=True))

    def test_question_resume_lifecycle_rejects_followup_before_durable_answer(self) -> None:
        answer = {
            "event": "cortex_mcp_call",
            "tool": "manage_orchestration",
            "status": "completed",
            "ok": True,
            "management_intent": "question",
            "outcome": "question_answered",
            "resume_contract": True,
        }
        lifecycle = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "awaiting_user",
            },
            answer,
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_result_recorded"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(self.harness.observed_question_resume_lifecycle(lifecycle))
        self.assertFalse(self.harness.observed_question_resume_lifecycle([
            lifecycle[0], lifecycle[1], lifecycle[4], answer, *lifecycle[5:],
        ]))
        self.assertFalse(self.harness.observed_question_resume_lifecycle([
            lifecycle[0], lifecycle[1], lifecycle[2], answer, lifecycle[4], lifecycle[5], answer, *lifecycle[6:],
        ]))
        self.assertFalse(self.harness.observed_question_resume_lifecycle([
            *lifecycle[:7], lifecycle[8],
        ]))

    def test_question_resolution_audit_requires_durable_answer_and_decision_before_result(self) -> None:
        state = {
            "task_id": "safe-task",
            "attempts": [{"attempt_id": "safe-attempt", "invalidated": False}],
        }
        result_records = [{"attempt_id": "safe-attempt"}]
        resolved_events = [
            {"event_type": "briefing_acknowledged"},
            {"event_type": "question_created"},
            {"event_type": "question_answered"},
            {"event_type": "decision_resolved"},
            {"event_type": "work_completed"},
            {"event_type": "completed"},
        ]
        with mock.patch.object(
            self.harness.cortex.attempt_protocol,
            "list_attempt_events",
            return_value=resolved_events,
        ):
            audit = self.harness.safe_question_resolution_audit(
                self.root, state, result_records,
            )
        self.assertEqual(audit, {
            "question_attempt_count": 1,
            "question_created_count": 1,
            "question_answered_count": 1,
            "decision_resolved_count": 1,
            "resolved_before_result_count": 1,
            "all_question_attempts_resolved_before_result": True,
        })

        unresolved_events = [
            {"event_type": "question_created"},
            {"event_type": "work_completed"},
        ]
        with mock.patch.object(
            self.harness.cortex.attempt_protocol,
            "list_attempt_events",
            return_value=unresolved_events,
        ):
            audit = self.harness.safe_question_resolution_audit(
                self.root, state, result_records,
            )
        self.assertFalse(audit["all_question_attempts_resolved_before_result"])
        self.assertEqual(audit["resolved_before_result_count"], 0)

    def test_stream_classifies_native_result_and_known_lifecycle_failure(self) -> None:
        native = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "collab_tool_call",
                "tool": "wait",
                "status": "completed",
                "agents_states": {
                    "child": {
                        "status": "completed",
                        "message": "ATTEMPT_COMPLETED attempt_result_ref=SECRET_REF\nSECRET_SUMMARY",
                    },
                },
            },
        }))
        self.assertEqual(native["outcome"], "attempt_result_recorded")
        self.assertEqual(native["agent_statuses"], {"completed": 1})
        self.assertNotIn("SECRET_REF", json.dumps(native))
        validation = self.harness.classified_native_outcome({
                "child": {"message": "complete_attempt returned attempt_result_invalid for SECRET_PATH"},
        })
        self.assertEqual(validation, "attempt_result_invalid")
        lifecycle = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__continue_orchestration",
                "status": "completed",
                "result": {
                    "structuredContent": {
                        "ok": False,
                        "error": "passed completion requires attempt_result_ref from SECRET_RESULT",
                    },
                },
            },
        }))
        self.assertEqual(lifecycle["failure_class"], "resultless_success")
        self.assertNotIn("SECRET_RESULT", json.dumps(lifecycle))

        copied_dispatch_ref = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__continue_orchestration",
                "status": "completed",
                "result": {
                    "structuredContent": {
                        "ok": False,
                        "error": "successful results use attempt_result_ref only; do not supply dispatch_ref SECRET_REF",
                    },
                },
            },
        }))
        self.assertEqual(copied_dispatch_ref["failure_class"], "success_with_dispatch_ref")
        self.assertNotIn("SECRET_REF", json.dumps(copied_dispatch_ref))

    def test_native_terminal_audit_requires_read_then_server_continuation_before_close(self) -> None:
        events = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {
                "event": "native_tool_call", "tool": "wait", "status": "completed",
                "outcome": "attempt_result_recorded",
            },
            {
                "event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed",
                "ok": True,
            },
            {
                "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
                "ok": True,
            },
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        audit = self.harness.safe_native_terminal_audit(events)
        self.assertEqual(audit, {
            "spawned_worker_observations": 1,
            "terminal_wait_observations": 1,
            "canonical_result_reads": 1,
            "server_continuation_audits": 1,
            "terminal_closes": 1,
            "pending_canonical_reads": 0,
            "pending_server_continuation_audits": 0,
            "pending_terminal_closes": 0,
            "protocol_violations": 0,
            "ambiguous_native_observations": 0,
            "authorized_native_dispatches": 0,
            "dispatch_authorization_observed": False,
            "unmatched_native_spawns": 0,
            "all_observed_workers_terminally_audited": True,
        })

        missing_continuation = self.harness.safe_native_terminal_audit(events[:3] + events[4:])
        self.assertFalse(missing_continuation["all_observed_workers_terminally_audited"])
        self.assertEqual(missing_continuation["protocol_violations"], 1)

        reordered = self.harness.safe_native_terminal_audit(events[:3] + [events[4], events[3]])
        self.assertFalse(reordered["all_observed_workers_terminally_audited"])
        self.assertGreaterEqual(reordered["protocol_violations"], 1)

        ambiguous = self.harness.safe_native_terminal_audit([events[0], events[0], *events[1:]])
        self.assertFalse(ambiguous["all_observed_workers_terminally_audited"])
        self.assertEqual(ambiguous["ambiguous_native_observations"], 1)

    def test_native_terminal_audit_rejects_a_generic_spawn_as_a_cortex_substitute(self) -> None:
        """An extra host spawn has no durable Cortex attempt to consume."""
        events = [
            # Generic collaboration work is visible to the host but was not
            # returned in a Cortex dispatch response.  It must make the
            # aggregate terminal proof fail rather than be counted as a gate.
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {
                "event": "native_tool_call", "tool": "wait", "status": "completed",
                "outcome": "other_terminal_message",
            },
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {
                "event": "native_tool_call", "tool": "wait", "status": "completed",
                "outcome": "attempt_result_recorded",
            },
            {
                "event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed",
                "ok": True,
            },
            {
                "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
                "ok": True,
            },
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        audit = self.harness.safe_native_terminal_audit(events)
        self.assertFalse(audit["all_observed_workers_terminally_audited"])
        self.assertEqual(audit["spawned_worker_observations"], 2)
        self.assertEqual(audit["terminal_wait_observations"], 2)
        self.assertEqual(audit["pending_canonical_reads"], 1)

        # When source telemetry includes the preceding Cortex response, the
        # failure is explicit rather than merely an aggregate mismatch.
        authorized = [{
            "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
            "ok": True, "authorized_dispatch_count": 1,
        }, *events]
        bound_audit = self.harness.safe_native_terminal_audit(authorized)
        self.assertTrue(bound_audit["dispatch_authorization_observed"])
        self.assertEqual(bound_audit["authorized_native_dispatches"], 1)
        self.assertEqual(bound_audit["unmatched_native_spawns"], 1)
        self.assertFalse(bound_audit["all_observed_workers_terminally_audited"])

    def test_native_terminal_audit_correlates_provisional_waits_from_progress_events(self) -> None:
        """A host terminal message is not a result until Cortex confirms it.

        This mirrors the privacy-safe progress sequence from the live
        evaluator: every tool emits started/in-progress/completed events, but
        a completed native wait can retain only ``other_terminal_message``.
        The test deliberately uses five sequential workers so a regression
        cannot hide behind a single aggregate correlation.
        """
        events: list[dict[str, object]] = []
        for _worker in range(5):
            events.extend([
                {"event": "native_tool_call", "tool": "spawn_agent", "status": "started"},
                {"event": "native_tool_call", "tool": "spawn_agent", "status": "in_progress"},
                {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
                {"event": "native_tool_call", "tool": "wait", "status": "started"},
                {"event": "native_tool_call", "tool": "wait", "status": "in_progress"},
                {
                    "event": "native_tool_call", "tool": "wait", "status": "completed",
                    "outcome": "other_terminal_message",
                },
                {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "started"},
                {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "in_progress"},
                {
                    "event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed",
                    "ok": True,
                },
                {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "started"},
                {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "in_progress"},
                {
                    "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
                    "ok": True,
                },
                {"event": "native_tool_call", "tool": "close_agent", "status": "in_progress"},
                {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
            ])

        audit = self.harness.safe_native_terminal_audit(events)
        self.assertEqual(audit, {
            "spawned_worker_observations": 5,
            "terminal_wait_observations": 5,
            "canonical_result_reads": 5,
            "server_continuation_audits": 5,
            "terminal_closes": 5,
            "pending_canonical_reads": 0,
            "pending_server_continuation_audits": 0,
            "pending_terminal_closes": 0,
            "protocol_violations": 0,
            "ambiguous_native_observations": 0,
            "authorized_native_dispatches": 0,
            "dispatch_authorization_observed": False,
            "unmatched_native_spawns": 0,
            "all_observed_workers_terminally_audited": True,
        })

        # A provisional wait never bypasses the server-derived continuation.
        before_continuation = [
            event for event in events
            if not (
                event.get("event") == "cortex_mcp_call"
                and event.get("tool") == "continue_orchestration"
            )
        ]
        reordered = self.harness.safe_native_terminal_audit(before_continuation)
        self.assertFalse(reordered["all_observed_workers_terminally_audited"])
        self.assertEqual(reordered["terminal_closes"], 0)
        self.assertEqual(reordered["protocol_violations"], 5)

        # The provisional classification relaxes no ordering rule: a native
        # close still cannot precede Cortex's successful continuation audit.
        close_before_continuation = self.harness.safe_native_terminal_audit([
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {
                "event": "native_tool_call", "tool": "wait", "status": "completed",
                "outcome": "other_terminal_message",
            },
            {
                "event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed",
                "ok": True,
            },
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
            {
                "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
                "ok": True,
            },
        ])
        self.assertFalse(close_before_continuation["all_observed_workers_terminally_audited"])
        self.assertEqual(close_before_continuation["terminal_closes"], 0)
        self.assertEqual(close_before_continuation["protocol_violations"], 1)

    def test_native_terminal_audit_fails_closed_when_final_child_is_not_closed(self) -> None:
        """The final continuation does not make an explicit native close optional."""
        events: list[dict[str, object]] = []
        for _worker in range(5):
            events.extend([
                {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
                {
                    "event": "native_tool_call", "tool": "wait", "status": "completed",
                    "outcome": "attempt_result_recorded",
                },
                {
                    "event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed",
                    "ok": True,
                },
                {
                    "event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed",
                    "ok": True,
                },
                {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
            ])

        audit = self.harness.safe_native_terminal_audit(events[:-1])
        self.assertEqual(audit["spawned_worker_observations"], 5)
        self.assertEqual(audit["terminal_wait_observations"], 5)
        self.assertEqual(audit["canonical_result_reads"], 5)
        self.assertEqual(audit["server_continuation_audits"], 5)
        self.assertEqual(audit["terminal_closes"], 4)
        self.assertEqual(audit["pending_terminal_closes"], 1)
        self.assertEqual(audit["protocol_violations"], 0)
        self.assertFalse(audit["all_observed_workers_terminally_audited"])

    def test_terminal_result_audit_requires_one_finalized_result_per_accepted_attempt(self) -> None:
        state = {
            "attempts": [
                {"attempt_id": "safe-attempt-1", "status": "passed", "attempt_result_ref": "safe-ref-1"},
                {"attempt_id": "safe-attempt-2", "status": "passed", "attempt_result_ref": "safe-ref-2"},
                {
                    "attempt_id": "historical-invalidated", "status": "failed", "invalidated": True,
                    "attempt_result_ref": "historical-ref",
                },
            ],
        }
        records = [
            {
                "attempt_id": "safe-attempt-1", "attempt_result_ref": "safe-ref-1",
                "result": {"result_ref": "safe-ref-1", "status": "completed", "lifecycle_status": "COMPLETED"},
            },
            {
                "attempt_id": "safe-attempt-2", "attempt_result_ref": "safe-ref-2",
                "result": {"result_ref": "safe-ref-2", "status": "completed", "lifecycle_status": "COMPLETED"},
            },
            {
                "attempt_id": "historical-invalidated", "attempt_result_ref": "historical-ref",
                "result": {"result_ref": "historical-ref", "status": "completed", "lifecycle_status": "COMPLETED"},
            },
        ]
        audit = self.harness.safe_terminal_result_audit(state, records)
        self.assertEqual(audit, {
            "accepted_attempts": 2,
            "terminal_accepted_attempts": 2,
            "finalized_canonical_results": 2,
            "missing_canonical_results": 0,
            "duplicate_canonical_results": 0,
            "mismatched_canonical_results": 0,
            "foreign_result_records": 0,
            "invalidated_historical_results": 1,
            "malformed_accepted_attempts": 0,
            "all_accepted_attempts_have_terminal_canonical_results": True,
        })

        incomplete = self.harness.safe_terminal_result_audit(state, records[:1])
        self.assertFalse(incomplete["all_accepted_attempts_have_terminal_canonical_results"])
        self.assertEqual(incomplete["missing_canonical_results"], 1)

        mismatched_records = [*records[:2], {**records[1]}]
        mismatched_records[-1] = {
            **mismatched_records[-1],
            "attempt_result_ref": "different-safe-ref",
        }
        mismatched = self.harness.safe_terminal_result_audit(state, mismatched_records)
        self.assertFalse(mismatched["all_accepted_attempts_have_terminal_canonical_results"])
        self.assertEqual(mismatched["duplicate_canonical_results"], 1)
        self.assertEqual(mismatched["mismatched_canonical_results"], 1)

    def test_stream_retains_only_machine_safe_continue_failure_shape(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__continue_orchestration",
                "status": "completed",
                "arguments": {
                    "task_ref": "SECRET_TASK_REF",
                    "step": 4,
                    "results": [{"attempt_result_ref": "SECRET_RESULT_REF"}],
                },
                "result": {"structuredContent": {
                    "ok": False,
                    "code": "continue_validation_failed",
                    "diagnostics": [{
                        "code": "continue_validation_failed",
                        "message": "continue step must match the active relative step 3 SECRET_DIAGNOSTIC",
                    }],
                }},
            },
        }))
        self.assertEqual(event["failure"], {
            "error_code": "continue_validation_failed",
            "reason": "step_mismatch",
            "requested_step": 4,
            "expected_step": 3,
            "result_count": 1,
            "result_field_names": ["attempt_result_ref"],
            "canonical_ref_match": None,
        })
        serialized = json.dumps(event, sort_keys=True)
        for secret in ("SECRET_TASK_REF", "SECRET_RESULT_REF", "SECRET_DIAGNOSTIC"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("arguments", serialized)
        self.assertNotIn("diagnostics", serialized)

    def test_stream_marks_direct_canonical_ref_mismatch_without_retaining_ref(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__continue_orchestration",
                "status": "completed",
                "arguments": {"step": 3, "results": [{"attempt_result_ref": "SECRET_REF"}]},
                "result": {"structuredContent": {
                    "ok": False,
                    "code": "continue_validation_failed",
                    "diagnostics": [{"message": "successful result does not match the exact active attempt SECRET_REF"}],
                }},
            },
        }))
        self.assertEqual(event["failure"]["reason"], "ref_mismatch")
        self.assertIs(event["failure"]["canonical_ref_match"], False)
        self.assertNotIn("SECRET_REF", json.dumps(event, sort_keys=True))

    def test_safe_ledger_audit_record_projects_only_allowlisted_aggregates(self) -> None:
        record = self.harness.safe_ledger_audit_record(2, {
            "tasks": 1,
            "attempt_results": 3,
            "task_statuses": {"active": 1, "SECRET_STATUS": 9},
            "attempt_statuses": {"awaiting_host_spawn": 1, "SECRET_ATTEMPT": 9},
            "gates": {"documentation": 1, "SECRET_GATE": 9},
            "worker_sessions": {"completed": 1, "SECRET_SESSION": 9},
            "latest_ledger_event": "attempt_result",
            "raw_payload": "SECRET_PAYLOAD",
        })
        self.assertEqual(record, {
            "sequence": 2,
            "tasks": 1,
            "attempt_results": 3,
            "task_statuses": {"active": 1},
            "attempt_statuses": {"awaiting_host_spawn": 1},
            "gates": {"documentation": 1},
            "worker_sessions": {"completed": 1},
            "latest_ledger_event": "attempt_result",
        })
        self.assertNotIn("SECRET", json.dumps(record, sort_keys=True))

    def test_attempt_event_key_audit_scopes_idempotency_to_one_attempt(self) -> None:
        database_dir = self.harness.cortex.ledger_root_path(
            {"project_root": str(self.project)}, create=True,
        )
        database_dir.mkdir(mode=0o700)
        database = database_dir / "cortex.db"
        connection = sqlite3.connect(database)
        # This intentionally omits the production unique constraint so the
        # evaluator can prove its failure classification for a corrupt ledger.
        connection.execute(
            "CREATE TABLE attempt_events(task_id TEXT, attempt_id TEXT, event_key TEXT)"
        )
        connection.executemany(
            "INSERT INTO attempt_events VALUES (?, ?, ?)",
            [
                ("task-a", "attempt-1", "SECRET_PREDECESSOR_READ"),
                ("task-a", "attempt-2", "SECRET_PREDECESSOR_READ"),
            ],
        )
        connection.commit()
        connection.close()

        audit = self.harness.safe_attempt_event_key_audit(self.project)
        self.assertEqual(audit, {
            "status": "ok",
            "same_attempt_duplicate_groups": 0,
            "same_attempt_duplicate_rows": 0,
            "cross_attempt_reused_key_groups": 1,
            "cross_attempt_reused_key_rows": 2,
        })
        self.assertTrue(self.harness.attempt_event_key_audit_passed(audit))
        self.assertNotIn("SECRET", json.dumps(audit, sort_keys=True))

        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO attempt_events VALUES (?, ?, ?)",
            ("task-a", "attempt-1", "SECRET_PREDECESSOR_READ"),
        )
        connection.commit()
        connection.close()

        corrupt = self.harness.safe_attempt_event_key_audit(self.project)
        self.assertEqual(corrupt["same_attempt_duplicate_groups"], 1)
        self.assertEqual(corrupt["same_attempt_duplicate_rows"], 2)
        self.assertFalse(self.harness.attempt_event_key_audit_passed(corrupt))
        self.assertNotIn("SECRET", json.dumps(corrupt, sort_keys=True))

    def test_isolated_codex_runtime_uses_minimal_private_environment_and_cleans_up(self) -> None:
        base = self.root / "runtime-base"
        base.mkdir()
        source_home = self.root / "source-codex-home"
        source_home.mkdir(mode=0o700)
        source_auth = source_home / "auth.json"
        source_auth.write_text("fixture credential", encoding="utf-8")
        source_auth.chmod(0o600)
        environment = {
            "CODEX_HOME": str(source_home),
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "LANG": "C",
            "UNRELATED_HOST_SECRET": "must-not-reach-child",
        }
        with mock.patch.dict(self.harness.os.environ, environment, clear=True):
            with self.harness.isolated_codex_runtime(base) as child_environment:
                private_home = Path(child_environment["CODEX_HOME"])
                self.assertEqual(Path(child_environment["HOME"]), private_home)
                self.assertEqual(private_home.parent, base)
                self.assertEqual(stat.S_IMODE(private_home.stat().st_mode), 0o700)
                self.assertNotIn("UNRELATED_HOST_SECRET", child_environment)
                self.assertNotIn("CODEX_SESSION_ID", child_environment)
                self.assertEqual(child_environment["PATH"], environment["PATH"])
                self.assertEqual(child_environment["LANG"], "C")
                self.assertTrue((private_home / "auth.json").is_file())
                self.assertFalse((private_home / "auth.json").is_symlink())
                self.assertEqual(stat.S_IMODE((private_home / "auth.json").stat().st_mode), 0o600)
                self.assertFalse((private_home / "config.toml").exists())
            self.assertFalse(private_home.exists(), "private runtime home was not cleaned")

    def test_keep_mode_preserves_only_evaluator_owned_host_store_for_post_audit(self) -> None:
        base = self.root / "kept-host-store"
        base.mkdir()
        with self.harness.isolated_cortex_host_store(base, keep=True) as host_store:
            self.assertEqual(host_store.parent, base)
            self.assertEqual(stat.S_IMODE(host_store.stat().st_mode), 0o700)
        self.assertTrue(host_store.is_dir())
        ownership = self.harness._read_private_ownership_marker(host_store)
        self.harness.remove_private_runtime_home(host_store, base, ownership, allow_current_owner=True)
        self.assertFalse(host_store.exists())

    def test_owned_temp_cleanup_refuses_a_foreign_concurrent_run(self) -> None:
        base = self.root / "owned-concurrent"
        base.mkdir()
        first, first_owner = self.harness.create_owned_temp_directory(
            base, prefix="cortex-luna-high-first-", purpose="host_store",
        )
        second, second_owner = self.harness.create_owned_temp_directory(
            base, prefix="cortex-luna-high-second-", purpose="host_store",
        )
        sentinel = base / "foreign-sentinel"
        sentinel.write_text("must-survive", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "mismatched ownership"):
            self.harness.remove_private_runtime_home(second, base, first_owner)
        self.assertTrue(second.is_dir())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "must-survive")
        self.harness.remove_private_runtime_home(first, base, first_owner, allow_current_owner=True)
        self.harness.remove_private_runtime_home(second, base, second_owner, allow_current_owner=True)
        self.assertTrue(sentinel.exists())

    def test_owned_temp_cleanup_refuses_a_live_owner_by_default(self) -> None:
        base = self.root / "owned-live"
        base.mkdir()
        directory, ownership = self.harness.create_owned_temp_directory(
            base, prefix="cortex-luna-high-live-", purpose="codex_runtime",
        )
        marker = directory / self.harness.TEMP_OWNERSHIP_MARKER
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema"], self.harness.TEMP_OWNERSHIP_SCHEMA)
        self.assertEqual(metadata["purpose"], "codex_runtime")
        self.assertRegex(metadata["run_nonce"], r"^[0-9a-f]{64}$")
        self.assertEqual(metadata["owner_pid"], os.getpid())
        self.assertEqual(metadata["owner_pgid"], os.getpgrp())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        with self.assertRaisesRegex(RuntimeError, "live evaluator temporary directory"):
            self.harness.remove_private_runtime_home(directory, base, ownership)
        self.assertTrue(directory.is_dir())
        self.harness.remove_private_runtime_home(directory, base, ownership, allow_current_owner=True)

    def test_owned_temp_cleanup_allows_exact_exited_linux_owner(self) -> None:
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux /proc starttime verification is unavailable")
        owner_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.05)"])
        try:
            starttime = self.harness._linux_process_starttime(owner_process.pid)
            self.assertIsNotNone(starttime)
            pgid = os.getpgid(owner_process.pid)
        finally:
            owner_process.wait(timeout=5)
        base = self.root / "owned-exited"
        base.mkdir()
        directory = base / "stale-exact-owner"
        directory.mkdir(mode=0o700)
        ownership = self.harness.TempOwnership(
            run_nonce=self.harness.secrets.token_hex(32),
            owner_pid=owner_process.pid,
            owner_pgid=pgid,
            owner_starttime_source="linux_proc_stat",
            owner_starttime=str(starttime),
            purpose="failure_metadata",
        )
        self.harness._write_private_ownership_marker(directory, ownership)
        self.harness.remove_private_runtime_home(directory, base, ownership)
        self.assertFalse(directory.exists())

    def test_owned_temp_cleanup_refuses_tampered_marker_and_symlink(self) -> None:
        base = self.root / "owned-tamper"
        base.mkdir()
        directory, ownership = self.harness.create_owned_temp_directory(
            base, prefix="cortex-luna-high-tampered-", purpose="host_store",
        )
        marker = directory / self.harness.TEMP_OWNERSHIP_MARKER
        marker.write_text("{}", encoding="utf-8")
        marker.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "malformed evaluator temporary ownership marker"):
            self.harness.remove_private_runtime_home(directory, base, ownership, allow_current_owner=True)
        self.assertTrue(directory.is_dir())

        linked, linked_owner = self.harness.create_owned_temp_directory(
            base, prefix="cortex-luna-high-link-", purpose="host_store",
        )
        outside = self.root / "outside-owned-temp"
        outside.mkdir()
        outside_sentinel = outside / "sentinel"
        outside_sentinel.write_text("must-survive", encoding="utf-8")
        shutil.rmtree(linked)
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "non-directory or symlink"):
            self.harness.remove_private_runtime_home(linked, base, linked_owner, allow_current_owner=True)
        self.assertEqual(outside_sentinel.read_text(encoding="utf-8"), "must-survive")

    def test_record_attempt_event_is_allowlisted_without_retaining_arguments(self) -> None:
        event = self.harness.sanitize_codex_stream_line(json.dumps({
            "type": "item",
            "item": {
                "type": "mcp_tool_call",
                "tool": "mcp__cortex__record_attempt_event",
                "status": "completed",
                "arguments": {"payload": "SECRET_ATTEMPT_EVENT"},
                "result": {"structuredContent": {"ok": True}},
            },
        }))
        self.assertEqual(event, {
            "event": "cortex_mcp_call", "tool": "record_attempt_event", "status": "completed", "ok": True,
        })
        self.assertNotIn("SECRET_ATTEMPT_EVENT", json.dumps(event, sort_keys=True))

    def test_live_parent_fallback_reads_a_canonical_result_before_emitting_failure_slot(self) -> None:
        prompt = self.harness.live_prompt("automatic_governance", self.project)
        self.assertIn("first verify the absence of a canonical result", prompt)
        self.assertIn("call read_worker_result with that exact lookup token", prompt)
        self.assertIn("copy its step/results verbatim into continue_orchestration", prompt)
        self.assertIn("never synthesize a success from terminal text or its fields", prompt)
        self.assertIn("A canonical non-success AttemptResult is different", prompt)
        self.assertIn("submit one terminal receipt with status=blocked or failed", prompt)
        self.assertIn("omit attempt_result_ref", prompt)
        self.assertIn("Emit a status=failed slot only after Cortex has verified", prompt)
        self.assertLess(
            prompt.index("first verify the absence of a canonical result"),
            prompt.index("Emit a status=failed slot only after Cortex has verified"),
        )

    def test_auth_copy_failure_removes_partial_destination_and_preserves_error(self) -> None:
        source = self.root / "source-auth.json"
        source.write_text("fixture credential", encoding="utf-8")
        source.chmod(0o600)
        cases = (
            (
                "read",
                mock.patch.object(self.harness.os, "read", side_effect=OSError("fixture read failure")),
                OSError,
                "fixture read failure",
            ),
            (
                "write",
                mock.patch.object(self.harness.os, "write", return_value=0),
                OSError,
                "unable to write private Codex authentication file",
            ),
            (
                "oversize",
                mock.patch.object(
                    self.harness.os, "read", return_value=b"x" * (self.harness.MAX_AUTH_FILE_BYTES + 1),
                ),
                RuntimeError,
                "exceeds the evaluator size limit",
            ),
        )
        for label, replacement, error_type, error_text in cases:
            with self.subTest(label=label):
                destination = self.root / f"partial-{label}.json"
                with replacement, self.assertRaisesRegex(error_type, error_text):
                    self.harness.copy_private_regular_file(source, destination)
                self.assertFalse(destination.exists(), "partial private credential file survived")

    def test_normal_completion_closes_evaluator_pipe_handles(self) -> None:
        marker = self.root / "closed-normal.marker"
        captured: dict[str, subprocess.Popen[str]] = {}
        real_popen = subprocess.Popen

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = real_popen(*args, **kwargs)
            captured["process"] = process
            return process

        with mock.patch.object(self.harness.subprocess, "Popen", side_effect=capture_popen):
            result = self.harness.run_live_command(
                [sys.executable, str(self.child), "emit", str(marker)],
                self.project, "fixture", timeout_seconds=5,
            )
        self.assertEqual(result["returncode"], 0)
        process = captured["process"]
        self.assertTrue(process.stdout is not None and process.stdout.closed)
        self.assertTrue(process.stderr is not None and process.stderr.closed)

    def test_selector_setup_exception_closes_pipes_and_restores_handlers(self) -> None:
        marker = self.root / "closed-exception.marker"
        captured: dict[str, subprocess.Popen[str]] = {}
        real_popen = subprocess.Popen
        previous_term = signal.getsignal(signal.SIGTERM)
        previous_int = signal.getsignal(signal.SIGINT)

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = real_popen(*args, **kwargs)
            captured["process"] = process
            return process

        with (
            mock.patch.object(self.harness.subprocess, "Popen", side_effect=capture_popen),
            mock.patch.object(self.harness.selectors, "DefaultSelector", side_effect=OSError("selector setup failed")),
        ):
            with self.assertRaisesRegex(OSError, "selector setup failed"):
                self.harness.run_live_command(
                    [sys.executable, str(self.child), "silence", str(marker)],
                    self.project, "fixture", timeout_seconds=5,
                )
        process = captured["process"]
        self.assertTrue(process.stdout is not None and process.stdout.closed)
        self.assertTrue(process.stderr is not None and process.stderr.closed)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous_term)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous_int)

    def test_ledger_progress_exposes_only_bounded_lifecycle_metadata(self) -> None:
        database_dir = self.harness.cortex.ledger_root_path(
            {"project_root": str(self.project)}, create=True,
        )
        database_dir.mkdir(mode=0o700)
        database_dir.chmod(0o700)
        database = database_dir / "cortex.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE tasks(status TEXT, state_json TEXT);
            CREATE TABLE logical_artifacts(kind TEXT);
            CREATE TABLE worker_sessions(status TEXT);
            CREATE TABLE ledger_events(event_id INTEGER, event TEXT);
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?)",
            ("active", json.dumps({"attempts": [{"status": "running", "gate": "review", "result": "SECRET_RESULT"}]})),
        )
        connection.execute("INSERT INTO logical_artifacts VALUES ('attempt_result')")
        connection.execute("INSERT INTO worker_sessions VALUES ('running')")
        connection.execute("INSERT INTO ledger_events VALUES (1, 'attempt_result')")
        connection.commit()
        connection.close()
        database.chmod(0o600)

        progress = self.harness.safe_ledger_progress(self.project)
        self.assertIsNotNone(progress)
        assert progress is not None
        serialized = json.dumps(progress, sort_keys=True)
        self.assertNotIn("SECRET_RESULT", serialized)
        self.assertEqual(set(progress), {
            "tasks", "task_statuses", "attempt_statuses", "gates",
            "attempt_results", "worker_sessions", "latest_ledger_event",
        })
        self.assertEqual(progress["gates"], {"review": 1})
        self.assertEqual(progress["latest_ledger_event"], "attempt_result")

    def test_timeout_terminates_and_reaps_fake_process_group(self) -> None:
        _process, _marker, output = self.run_wrapper("group", heartbeat=0.05, timeout=0.2)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], "timeout")
        self.assertEqual(final["returncode"], -signal.SIGTERM)
        self.assert_pid_stopped(self.root / "group.pid")

    def test_gates_recorded_failure_stops_live_parent_without_speculative_recovery(self) -> None:
        _process, marker, output = self.run_wrapper(
            "gates_recorded_failure", heartbeat=0.05, timeout=5,
        )
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], "gates_recorded_public_failure")
        self.assertFalse(marker.exists(), "parent continued after a post-gate lifecycle failure")
        calls = [item for item in events if item.get("event") == "cortex_mcp_call"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["failure"], {
            "error_code": "orchestrate_validation_failed",
            "phase": "gates_recorded",
            "reason": "unclassified",
            "requested_step": 3,
            "expected_step": None,
            "result_count": None,
            "result_field_names": [],
            "canonical_ref_match": None,
        })
        serialized = json.dumps(events, sort_keys=True)
        self.assertNotIn("SECRET_FUTURE_WAVES", serialized)
        self.assertNotIn("SECRET_DIAGNOSTIC", serialized)

    def test_exited_parent_descendant_is_terminated_and_reaped(self) -> None:
        _process, _marker, output = self.run_wrapper("exited_parent", heartbeat=0.05, timeout=0.2)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], "timeout")
        self.assertEqual(final["returncode"], 0)
        self.assert_pid_stopped(self.root / "exited_parent.pid")

    def test_external_sigterm_terminates_and_reaps_fake_process_group(self) -> None:
        process, marker, _ = self.run_wrapper("group", timeout=5, send_sigterm=True)
        assert process.stdout is not None
        pid_path = marker.with_suffix(".pid")
        deadline = time.monotonic() + 3
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        events = self.parse_lines(output)
        final = events[-1]["final"]
        self.assertEqual(final["termination"], f"signal_{signal.SIGTERM}")
        self.assert_pid_stopped(pid_path)

    def test_final_json_is_parseable_and_source_mode_has_no_global_mutations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-realtime-home-") as home:
            home_path = Path(home)
            environment = os.environ.copy()
            environment.update({
                "HOME": str(home_path),
                "CODEX_HOME": str(home_path / ".codex"),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            before = sorted(path.relative_to(home_path).as_posix() for path in home_path.rglob("*"))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "PASS")
            after = sorted(path.relative_to(home_path).as_posix() for path in home_path.rglob("*"))
            self.assertEqual(before, after, "fixture mode mutated global Codex configuration")

    def test_live_command_uses_this_checkout_source_mcp_without_install_commands(self) -> None:
        expected_server = (ROOT / "plugins/cortex/scripts/cortex.py").resolve()
        self.assertEqual(self.harness.SERVER.resolve(), expected_server)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('mcp_servers.cortex.command="{sys.executable}"', source)
        self.assertIn('mcp_servers.cortex.args=["{SERVER}"]', source)
        self.assertNotIn('mcp_servers.cortex.args=["{SERVER}", "--mcp-audience=coordinator"]', source)
        self.assertNotRegex(source, r"codex\s+(?:plugin\s+)?(?:install|add|update|remove)\b")

    def fake_codex(self) -> Path:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "print(json.dumps({'type':'item','item':{'type':'mcp_tool_call',"
            "'tool':'mcp__cortex__complete_attempt','status':'completed',"
            "'arguments':{'prompt':'SECRET_PROMPT'},"
            "'result':{'structured_content':{'ok':False,'result':'SECRET_RESULT'}}}}), flush=True)\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return bin_dir

    def isolated_probe_codex(self, mode: str = "normal") -> Path:
        """Create a fake Codex that records only safe metadata outside its home."""
        bin_dir = self.root / "isolated-bin"
        bin_dir.mkdir()
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, stat, sys, time\n"
            "from pathlib import Path\n"
            f"MODE = {mode!r}\n"
            "args = sys.argv[1:]\n"
            "project = Path(args[args.index('-C') + 1])\n"
            "probe = project.parent / 'codex-probe.json'\n"
            "codex_home = Path(os.environ['CODEX_HOME'])\n"
            "record = {'argv': args, 'env': {key: os.environ.get(key) for key in ("
            "'HOME', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME')}, "
            "'codex_home_exists': codex_home.exists(), "
            "'codex_home_is_symlink': codex_home.is_symlink()}\n"
            "if codex_home.exists() and not codex_home.is_symlink():\n"
            "    record['codex_home_mode'] = stat.S_IMODE(codex_home.stat().st_mode)\n"
            "    auth = codex_home / 'auth.json'\n"
            "    fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
            "    with os.fdopen(fd, 'w', encoding='utf-8') as stream: stream.write('AUTH_SECRET_VALUE')\n"
            "    record['auth_mode'] = stat.S_IMODE(auth.stat().st_mode)\n"
            "    record['auth_path'] = str(auth)\n"
            "probe.write_text(json.dumps(record, sort_keys=True), encoding='utf-8')\n"
            "print(json.dumps({'type':'item','item':{'type':'mcp_tool_call',"
            "'tool':'mcp__cortex__complete_attempt','status':'completed',"
            "'arguments':{'prompt':'AUTH_SECRET_VALUE'},"
            "'result':{'structured_content':{'ok':False,'result':'AUTH_SECRET_VALUE'}}}}), flush=True)\n"
            "if MODE in {'timeout', 'sigterm'}: time.sleep(60)\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return bin_dir

    def isolated_parent_environment(self, bin_dir: Path) -> tuple[dict[str, str], Path, Path, Path]:
        global_root = self.root / "global-state"
        global_home = global_root / "home"
        global_home.mkdir(parents=True)
        global_codex_target = global_root / "codex-target"
        global_codex_target.mkdir()
        global_codex_link = global_root / "codex-link"
        global_codex_link.symlink_to(global_codex_target, target_is_directory=True)
        (global_home / ".codex-config.toml").write_text("global-sentinel", encoding="utf-8")
        (global_codex_target / "registry.json").write_text("global-registry-sentinel", encoding="utf-8")
        global_auth = global_codex_target / "auth.json"
        global_auth.write_text("GLOBAL_AUTH_SENTINEL", encoding="utf-8")
        global_auth.chmod(0o600)
        environment = os.environ.copy()
        environment.update({
            "PATH": f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}",
            "HOME": str(global_home),
            "CODEX_HOME": str(global_codex_link),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return environment, global_home, global_codex_target, global_codex_link

    def run_isolated_probe(self, *, mode: str = "normal") -> tuple[list[dict[str, object]], dict[str, object], Path, Path]:
        base = self.root / f"isolated-{mode}"
        base.mkdir()
        environment, global_home, global_codex_target, global_codex_link = self.isolated_parent_environment(
            self.isolated_probe_codex(mode)
        )
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            results = self.harness.live_eval(
                base, ("automatic_sequential",), timeout_seconds=0.2,
            )
        probe_path = base / "codex-probe.json"
        self.assertTrue(probe_path.is_file(), f"fake Codex did not write probe; output={output.getvalue()!r}")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertIsInstance(probe, dict)
        return results, probe, global_home, global_codex_target

    def assert_isolated_probe_clean(
        self,
        results: list[dict[str, object]],
        probe: dict[str, object],
        global_home: Path,
        global_codex_target: Path,
    ) -> None:
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.get("failure_metadata"), "not_retained")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("AUTH_SECRET_VALUE", serialized)
        self.assertTrue(probe["codex_home_exists"])
        self.assertFalse(probe["codex_home_is_symlink"])
        self.assertEqual(probe["codex_home_mode"], 0o700)
        self.assertEqual(probe["auth_mode"], 0o600)
        private_codex_home = Path(str(probe["env"]["CODEX_HOME"]))
        self.assertFalse(private_codex_home.exists(), "isolated Codex home survived evaluator cleanup")
        self.assertNotEqual(private_codex_home, global_codex_target)
        self.assertNotEqual(private_codex_home, global_home / ".codex")
        global_auth = global_codex_target / "auth.json"
        self.assertEqual(global_auth.read_text(encoding="utf-8"), "GLOBAL_AUTH_SENTINEL")
        self.assertEqual(os.stat(global_auth).st_mode & 0o777, 0o600)
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")
        self.assertEqual((global_codex_target / "registry.json").read_text(encoding="utf-8"), "global-registry-sentinel")
        command = " ".join(str(item) for item in probe["argv"])
        self.assertIn(f'mcp_servers.cortex.command="{sys.executable}"', command)
        self.assertIn(f'mcp_servers.cortex.args=["{self.harness.SERVER}"]', command)
        self.assertNotIn("--mcp-audience=coordinator", command)

    def test_live_eval_uses_private_codex_home_and_cleans_normal_run(self) -> None:
        results, probe, global_home, global_codex_target = self.run_isolated_probe()
        self.assert_isolated_probe_clean(results, probe, global_home, global_codex_target)
        for key in ("HOME", "CODEX_HOME"):
            self.assertNotEqual(probe["env"][key], str(global_home if key == "HOME" else global_codex_target))
            self.assertTrue(Path(str(probe["env"][key])).is_absolute())

    def test_live_eval_cleans_private_codex_home_after_timeout(self) -> None:
        results, probe, global_home, global_codex_target = self.run_isolated_probe(mode="timeout")
        self.assertEqual(results[0].get("termination"), "timeout")
        self.assert_isolated_probe_clean(results, probe, global_home, global_codex_target)

    def test_live_eval_rejects_symlinked_auth_without_retaining_runtime_home(self) -> None:
        base = self.root / "isolated-symlink-auth"
        base.mkdir()
        bin_dir = self.isolated_probe_codex("normal")
        environment, global_home, global_codex_target, _global_codex_link = self.isolated_parent_environment(bin_dir)
        global_auth = global_codex_target / "auth.json"
        outside_secret = self.root / "outside-auth.json"
        outside_secret.write_text("OUTSIDE_AUTH_SECRET", encoding="utf-8")
        outside_secret.chmod(0o600)
        global_auth.unlink()
        global_auth.symlink_to(outside_secret)
        with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, "regular non-symlink"):
                self.harness.live_eval(base, ("automatic_sequential",), timeout_seconds=10)
        self.assertNotIn("OUTSIDE_AUTH_SECRET", output.getvalue())
        self.assertTrue(global_auth.is_symlink())
        self.assertEqual(outside_secret.read_text(encoding="utf-8"), "OUTSIDE_AUTH_SECRET")
        self.assertEqual(list(base.glob("cortex-luna-high-codex-*")), [])
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")

    def test_live_eval_cleans_private_codex_home_after_external_sigterm(self) -> None:
        base = self.root / "isolated-sigterm"
        base.mkdir()
        bin_dir = self.isolated_probe_codex("sigterm")
        environment, global_home, global_codex_target, _global_codex_link = self.isolated_parent_environment(bin_dir)
        wrapper = (
            "import importlib.util, json, pathlib, sys\n"
            "script, base = sys.argv[1:]\n"
            "spec = importlib.util.spec_from_file_location('harness', script)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "assert spec.loader is not None\n"
            "spec.loader.exec_module(module)\n"
            "result = module.live_eval(pathlib.Path(base), ('automatic_sequential',), timeout_seconds=30)\n"
            "print(json.dumps(result, sort_keys=True), flush=True)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", wrapper, str(SCRIPT), str(base)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        probe_path = base / "codex-probe.json"
        deadline = time.monotonic() + 10
        while not probe_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(probe_path.exists(), "SIGTERM fixture did not reach fake Codex")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("AUTH_SECRET_VALUE", stdout)
        results = json.loads(stdout.splitlines()[-1])
        self.assertEqual(results[0].get("termination"), "signal_15")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertFalse(Path(str(probe["env"]["CODEX_HOME"])).exists())
        global_auth = global_codex_target / "auth.json"
        self.assertEqual(global_auth.read_text(encoding="utf-8"), "GLOBAL_AUTH_SENTINEL")
        self.assertEqual(os.stat(global_auth).st_mode & 0o777, 0o600)
        self.assertEqual((global_home / ".codex-config.toml").read_text(encoding="utf-8"), "global-sentinel")

    def run_failed_live_eval(self, *, retain: bool) -> list[dict[str, object]]:
        base = self.root / ("retained-eval" if retain else "default-eval")
        base.mkdir()
        bin_dir = self.fake_codex()
        environment = os.environ.copy()
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        # This fixture exercises failure-metadata handling with a fake Codex
        # binary, not evaluator authentication. Give the isolated runtime an
        # explicit test-only credential so a clean CI runner does not depend
        # on a developer's ~/.codex/auth.json.
        environment["CODEX_API_KEY"] = FAILURE_FIXTURE_API_KEY
        with mock.patch.dict(os.environ, environment, clear=True):
            return self.harness.live_eval(
                base,
                ("automatic_sequential",),
                timeout_seconds=10,
                retain_failure_metadata=retain,
            )

    def test_live_failure_metadata_is_not_retained_by_default(self) -> None:
        real_mkdtemp = tempfile.mkdtemp

        def reject_failure_retention(*, prefix: str = "", dir: str | None = None) -> str:
            if prefix == "cortex-luna-high-failure-":
                raise AssertionError("default failure path retained metadata")
            return real_mkdtemp(prefix=prefix, dir=dir)

        with mock.patch.object(
            self.harness.tempfile,
            "mkdtemp",
            side_effect=reject_failure_retention,
        ):
            results = self.run_failed_live_eval(retain=False)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["failure_metadata"], "not_retained")
        self.assertNotIn("failure_artifacts", result)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("SECRET_PROMPT", serialized)
        self.assertNotIn("SECRET_RESULT", serialized)
        self.assertNotIn(FAILURE_FIXTURE_API_KEY, serialized)

    def test_live_failure_metadata_requires_opt_in_and_is_sanitized(self) -> None:
        retention_dir = self.root / "retained-failure"
        real_mkdtemp = tempfile.mkdtemp

        def make_retention_dir(*, prefix: str = "", dir: str | None = None) -> str:
            if prefix != "cortex-luna-high-failure-":
                return real_mkdtemp(prefix=prefix, dir=dir)
            retention_dir.mkdir()
            return str(retention_dir)

        with mock.patch.object(self.harness.tempfile, "mkdtemp", side_effect=make_retention_dir):
            results = self.run_failed_live_eval(retain=True)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(Path(result["failure_artifacts"]).resolve(), retention_dir.resolve())
        progress_file = retention_dir / "progress.json"
        self.assertTrue(progress_file.is_file())
        progress = json.loads(progress_file.read_text(encoding="utf-8"))
        serialized = json.dumps(progress, sort_keys=True)
        self.assertNotIn("SECRET_PROMPT", serialized)
        self.assertNotIn("SECRET_RESULT", serialized)
        self.assertNotIn(FAILURE_FIXTURE_API_KEY, serialized)
        self.assertLessEqual(len(progress["events"]), 100)
        self.assertEqual(progress["events"][0]["tool"], "complete_attempt")
        self.assertEqual(progress["events"][0]["ok"], False)


if __name__ == "__main__":
    unittest.main()
