import json
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # initialize runtime bindings before facade imports
from cortex_runtime.dispatch_briefing import read_dispatch_briefing
from cortex_runtime import mcp_api
from cortex_runtime import attempt_protocol
from cortex_runtime import attempt_facade, ledger_db, questions
from cortex_runtime.questions import worker_question


class WorkerBoundaryValidationContractTests(unittest.TestCase):
    def test_nonretryable_worker_terminal_cleanup_preserves_forensics_without_result_or_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-private-store"
            host_store.mkdir(mode=0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Exercise a true nonretryable worker terminal.",
                        "acceptance_criteria": ["The exact worker is terminal and no result exists."],
                        "verification": ["Inspect durable task, session, event, and repair rows."],
                    },
                    "waves": [{"workers": [{
                        "phase": "implementation", "profile": "general",
                        "objective": "Record evidence before the terminal failure.",
                        "allowed_paths": ["result.txt"],
                    }]}],
                })
                dispatch = started["dispatches"][0]
                match = re.search(
                    r"read_dispatch_briefing\((\{[^\n]+?\})\)",
                    str(dispatch["arguments"]["message"]),
                )
                self.assertIsNotNone(match)
                assert match is not None
                pair = json.loads(match.group(1))
                self.assertTrue(read_dispatch_briefing(pair)["complete"])
                checkpoint = attempt_facade.record_attempt_event({
                    **pair, "event_type": "progress",
                    "payload": {"summary": "Durable evidence before terminal failure."},
                })
                self.assertTrue(checkpoint["ok"], checkpoint)
                rejected = attempt_facade.complete_attempt({
                    **pair,
                    "outcome": {
                        "status": "completed", "summary": "", "findings": [],
                        "decisions_needed": [], "unresolved": [], "claims": [],
                    },
                })
                self.assertFalse(rejected["ok"])

                bound = {"project_root": str(project), "task_ref": started["task_ref"]}
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                _task_dir, before_state, _task, _task_ref = resolved
                before_attempt = before_state["attempts"][0]
                root = cortex.ledger_root(bound)
                before_events = attempt_protocol.list_attempt_events(
                    root, task_id=before_state["task_id"], attempt_id=before_attempt["attempt_id"],
                )
                before_repair = ledger_db.get_pending_repair_escrow(
                    root, task_id=before_state["task_id"], attempt_id=before_attempt["attempt_id"],
                )
                self.assertIsNotNone(before_repair)

                request = {
                    "task_ref": started["task_ref"],
                    "coordinator_ref": started["coordinator_ref"],
                    "intent": "finalize_worker_failure",
                    "payload": {
                        "dispatch_ref": dispatch["dispatch_ref"],
                        "reason_code": "worker_nonretryable_terminal",
                    },
                }
                finalized = cortex.manage_orchestration(request)
                replayed = cortex.manage_orchestration(request)
                self.assertFalse(finalized["ok"])
                self.assertEqual(finalized["error"]["code"], "worker_terminal_failure")
                self.assertFalse(replayed["ok"])
                self.assertEqual(replayed["error"]["code"], "worker_terminal_failure")

                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                _task_dir, state, _task, _task_ref = resolved
                attempt = state["attempts"][0]
                self.assertEqual(state["status"], "blocked")
                self.assertEqual(attempt["status"], "failed")
                self.assertEqual(attempt["lifecycle_status"], "worker_terminal_failure")
                self.assertFalse(attempt["host_resumable"])
                sessions = cortex.db_list_worker_sessions(root, state["task_id"])
                self.assertTrue(sessions)
                self.assertTrue(all(item["status"] == "terminated_unavailable" for item in sessions))
                self.assertTrue(all(not item["resumable"] for item in sessions))
                self.assertEqual(
                    attempt_protocol.list_attempt_events(
                        root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                    ),
                    before_events,
                )
                after_repair = ledger_db.get_pending_repair_escrow(
                    root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                )
                self.assertEqual(after_repair, before_repair)
                self.assertIsNone(attempt_protocol.get_attempt_result(
                    root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                ))
                self.assertEqual(finalized.get("dispatches", []), [])
                read = attempt_facade.read_worker_result({
                    "task_ref": started["task_ref"],
                    "coordinator_ref": started["coordinator_ref"],
                    "step": 1,
                })
                self.assertFalse(read["ok"])
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

    def test_bootstrap_terminal_cleanup_closes_awaiting_spawn_without_orphan_result_or_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-private-store"
            host_store.mkdir(mode=0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Create one file after bootstrap recovery.",
                        "acceptance_criteria": ["The file exists."],
                        "verification": ["Read the exact file."],
                    },
                    "waves": [{"workers": [{
                        "phase": "implementation",
                        "profile": "general",
                        "objective": "Create result.txt.",
                        "allowed_paths": ["result.txt"],
                    }]}],
                })
                self.assertTrue(started["ok"], started)
                dispatch = started["dispatches"][0]
                finalized = cortex.manage_orchestration({
                    "task_ref": started["task_ref"],
                    "coordinator_ref": started["coordinator_ref"],
                    "intent": "finalize_bootstrap_failure",
                    "payload": {
                        "dispatch_ref": dispatch["dispatch_ref"],
                        "reason_code": "bootstrap_missing_identity",
                    },
                })
                self.assertFalse(finalized["ok"])
                self.assertEqual(finalized["error"]["code"], "bootstrap_terminal_failure")

                bound = {"project_root": str(project), "task_ref": started["task_ref"]}
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                self.assertIsInstance(resolved, tuple)
                assert isinstance(resolved, tuple)
                _task_dir, state, _task, _task_ref = resolved
                attempt = state["attempts"][0]
                self.assertEqual(attempt["status"], "failed")
                self.assertEqual(attempt["lifecycle_status"], "bootstrap_terminal_failure")
                self.assertFalse(attempt["host_resumable"])
                self.assertNotIn("attempt_result_ref", attempt)
                root = cortex.ledger_root(bound)
                sessions = cortex.db_list_worker_sessions(root, state["task_id"])
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]["status"], "terminated_unavailable")
                self.assertFalse(sessions[0]["resumable"])
                self.assertEqual(
                    attempt_protocol.list_attempt_events(
                        root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                    ),
                    [],
                )
                self.assertIsNone(attempt_protocol.get_attempt_result(
                    root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                ))
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

    def test_authenticated_zero_max_bytes_is_one_closed_direct_and_stdio_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-private-store"
            host_store.mkdir(mode=0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Produce one read-only plan.",
                        "acceptance_criteria": ["The plan is scoped."],
                        "verification": ["Inspect the plan."],
                    },
                    "waves": [{"workers": [{"phase": "plan"}]}],
                })
                self.assertTrue(started["ok"], started)
                bootstrap = str(started["dispatches"][0]["arguments"]["message"])
                match = re.search(r"read_dispatch_briefing\((\{[^\n]+?\})\)", bootstrap)
                self.assertIsNotNone(match)
                assert match is not None
                pair = json.loads(match.group(1))

                direct = read_dispatch_briefing({**pair, "max_bytes": 0})
                self.assertEqual(set(direct), {"schema", "ok", "outcome", "error", "recovery"})
                self.assertFalse(direct["ok"])
                correction = direct
                self.assertTrue(correction["recovery"]["retryable"])
                self.assertFalse(correction["recovery"]["state_mutated"])
                self.assertEqual(len(correction["error"]["diagnostics"]), 1)
                self.assertEqual(correction["error"]["diagnostics"][0]["json_pointer"], "/max_bytes")
                self.assertEqual(correction["error"]["diagnostics"][0]["field_schema"], {
                    "type": "integer", "minimum": 1, "maximum": 64 * 1024,
                })
                self.assertNotIn("task_ref", direct)
                self.assertNotIn("receipt", json.dumps(direct))

                request = {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "read_dispatch_briefing", "arguments": {**pair, "max_bytes": 0}},
                }
                stdin, stdout = io.StringIO(json.dumps(request) + "\n"), io.StringIO()
                with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
                    mcp_api.serve_stdio(
                        public_tools={"read_dispatch_briefing": (
                            read_dispatch_briefing,
                            cortex.PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"],
                        )},
                        internal_handlers={}, server_version="test", instructions="test",
                        audience="worker", log_tool_error=lambda *_args: None,
                    )
                stdio = json.loads(stdout.getvalue())["result"]["structuredContent"]
                self.assertEqual(stdio, direct)
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

    def test_unbound_worker_question_fails_closed_without_model_identity_fields(self):
        response = worker_question({"action": "invalid", "profile": "nope", "question": "", "Unexpected Field": True})
        self.assertFalse(response["ok"])
        correction = response
        self.assertEqual(correction["error"]["code"], "worker_question_request_invalid")
        self.assertEqual(
            {item["json_pointer"] for item in correction["error"]["diagnostics"]},
            {"/Unexpected Field", "/profile", "/task_ref", "/assignment_ref", "/action"},
        )
        self.assertEqual(
            {item["json_pointer"] for item in correction["error"]["diagnostics"] if item.get("value_source") == "cortex"},
            {"/task_ref", "/assignment_ref"},
        )
        self.assertEqual(correction["recovery"], {
            "kind": "terminal_stop", "operation": "worker_question",
            "retryable": False, "state_mutated": False,
        })
        self.assertTrue(correction["error"]["diagnostics"])
        self.assertTrue(all(item.get("field_schema") for item in correction["error"]["diagnostics"]))
        self.assertTrue(all(item["json_pointer"] == "" or item["json_pointer"].startswith("/") for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("path" not in item for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("received" not in item for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("expected" not in item for item in correction["error"]["diagnostics"]))
        self.assertNotIn("validation", response)
        self.assertNotIn("repair", response)
        self.assertNotIn("next_action", response)

    def test_question_ref_attempt_mismatch_is_exact_non_guessing_terminal(self):
        authority = {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
            "action": "poll",
            "question_ref": "question-0001",
        }
        with patch.object(
            questions,
            "authorize_worker_assignment",
            return_value=(Path("/tmp/project"), Path("/tmp/task"), {"task_id": "task-one"}, {"attempt_id": "attempt-one"}, "general"),
        ), patch.object(
            questions,
            "_worker_question_impl",
            side_effect=ValueError("question_ref is not bound to this authorized worker attempt"),
        ):
            response = questions.worker_question(authority)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "worker_question_reference_mismatch")
        self.assertEqual(response["error"]["diagnostics"][0]["json_pointer"], "/question_ref")
        self.assertEqual(response["error"]["diagnostics"][0]["value_source"], "cortex")
        self.assertEqual(response["recovery"], {
            "kind": "terminal_stop", "operation": "worker_question",
            "retryable": False, "state_mutated": False,
        })

    def test_unbound_dispatch_briefing_fails_closed_without_model_identity_fields(self):
        response = read_dispatch_briefing({"max_bytes": 0})
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "failed")
        correction = response
        self.assertEqual(correction["error"]["code"], "dispatch_briefing_request_invalid")
        self.assertFalse(correction["recovery"]["retryable"])
        self.assertEqual(correction["recovery"]["kind"], "terminal_stop")
        self.assertTrue(correction["error"]["diagnostics"])
        self.assertTrue(all(item["json_pointer"].startswith("/") for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("path" not in item for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("received" not in item for item in correction["error"]["diagnostics"]))
        self.assertTrue(all("expected" not in item for item in correction["error"]["diagnostics"]))
        self.assertNotIn("validation", response)
        self.assertNotIn("repair", response)
        self.assertNotIn("next_action", response)

    def test_next_cursor_is_one_closed_input_correction_without_task_or_receipt(self):
        response = read_dispatch_briefing({
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "a" * 64,
            "next_cursor": "not-an-input-field",
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "failed")
        self.assertEqual(set(response), {"schema", "ok", "outcome", "error", "recovery"})
        correction = response
        self.assertFalse(correction["recovery"]["state_mutated"])
        self.assertEqual(len(correction["error"]["diagnostics"]), 1)
        self.assertEqual(correction["error"]["diagnostics"][0]["json_pointer"], "/next_cursor")
        self.assertNotIn("task_ref", response)
        self.assertNotIn("receipt", json.dumps(response))

    def test_dispatch_briefing_success_exposes_only_content_completion_and_cursor_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-private-store"
            host_store.mkdir(mode=0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Produce one read-only plan.",
                        "acceptance_criteria": ["The plan is scoped."],
                        "verification": ["Inspect the plan."],
                    },
                    "waves": [{"workers": [{"phase": "plan"}]}],
                })
                self.assertTrue(started["ok"], started)
                bootstrap = str(started["dispatches"][0]["arguments"]["message"])
                match = re.search(r"read_dispatch_briefing\((\{[^\n]+?\})\)", bootstrap)
                self.assertIsNotNone(match)
                assert match is not None
                response = read_dispatch_briefing(json.loads(match.group(1)))
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

        self.assertTrue(response["ok"], response)
        self.assertTrue(response["complete"])
        self.assertEqual(response["encoding"], "utf-8")
        self.assertTrue(response["content"])
        self.assertEqual(set(response), {"schema", "ok", "outcome", "content", "encoding", "complete"})

    def test_dispatch_briefing_defaults_to_bounded_pages_and_acknowledges_only_after_final_page(self):
        briefing_schema = cortex.PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"]
        self.assertEqual(
            briefing_schema["properties"]["max_bytes"],
            {
                "type": "integer", "minimum": 1, "maximum": 64 * 1024,
                "description": "Optional caller-selected UTF-8 briefing page size. Omit it for the server default; every page remains bounded.",
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            host_store = Path(temporary) / "host-private-store"
            host_store.mkdir(mode=0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Produce one read-only plan.",
                        "acceptance_criteria": ["The plan is scoped."],
                        "verification": ["Inspect the plan."],
                    },
                    "waves": [{"workers": [{"phase": "plan"}]}],
                })
                self.assertTrue(started["ok"], started)
                bootstrap = str(started["dispatches"][0]["arguments"]["message"])
                match = re.search(r"read_dispatch_briefing\((\{[^\n]+?\})\)", bootstrap)
                self.assertIsNotNone(match)
                assert match is not None
                pair = json.loads(match.group(1))

                oversized = read_dispatch_briefing({
                    **pair,
                    "max_bytes": 64 * 1024 + 1,
                })
                self.assertFalse(oversized["ok"])
                self.assertEqual(oversized["error"]["diagnostics"][0]["json_pointer"], "/max_bytes")
                self.assertTrue(oversized["recovery"]["retryable"])
                self.assertFalse(oversized["recovery"]["state_mutated"])

                bound = {"project_root": str(project), "task_ref": started["task_ref"]}
                root = cortex.ledger_root(bound)
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                _task_dir, state, _task, _task_ref = resolved
                attempt = state["attempts"][0]
                expected = cortex.db_read_artifact_content(
                    root, state["task_id"], attempt["briefing_artifact_ref"],
                )

                with patch(
                    "cortex_runtime.dispatch_briefing.DEFAULT_DISPATCH_BRIEFING_PAGE_BYTES",
                    64,
                ):
                    page = read_dispatch_briefing(pair)
                    self.assertTrue(page["ok"], page)
                    self.assertFalse(page["complete"])
                    self.assertIn("next_cursor", page)
                    self.assertLessEqual(len(page["content"].encode("utf-8")), 64)
                    events = attempt_protocol.list_attempt_events(
                        root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                    )
                    self.assertFalse(any(event["event_type"] == "briefing_acknowledged" for event in events))

                    pages = [page["content"]]
                    while not page["complete"]:
                        page = read_dispatch_briefing({**pair, "cursor": page["next_cursor"]})
                        self.assertTrue(page["ok"], page)
                        pages.append(page["content"])

                self.assertEqual("".join(pages), expected)
                events = attempt_protocol.list_attempt_events(
                    root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
                )
                self.assertEqual(
                    [event["event_type"] for event in events].count("briefing_acknowledged"),
                    1,
                )
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous


if __name__ == "__main__":
    unittest.main()
