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
    def test_retryable_repair_and_bare_terminal_marker_cannot_block_task(self):
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
                    "waves": [{"phase": "implementation", "workers": [{
                        "profile": "general",
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
                rejected = cortex.complete_worker_attempt({
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
                self.assertFalse(replayed["ok"])

                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                _task_dir, state, _task, _task_ref = resolved
                attempt = state["attempts"][0]
                self.assertEqual(state["status"], "active")
                self.assertNotEqual(attempt["status"], "failed")
                self.assertNotEqual(attempt["lifecycle_status"], "worker_terminal_failure")
                sessions = cortex.db_list_worker_sessions(root, state["task_id"])
                self.assertTrue(sessions)
                self.assertTrue(any(item["resumable"] for item in sessions))
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
                self.assertIsNone(ledger_db.get_task_document(
                    root, state["task_id"], cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                ))
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

    def test_server_terminal_evidence_is_safe_single_use_expiring_and_assignment_bound(self):
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
                        "user_request": "Exercise server-authoritative terminal evidence.",
                        "acceptance_criteria": ["Only genuine server evidence can block the task."],
                        "verification": ["Inspect evidence binding and one-time consumption."],
                    },
                    "waves": [{"phase": "implementation", "workers": [{
                        "profile": "general",
                        "objective": "Exercise terminal evidence.",
                        "allowed_paths": ["result.txt"],
                    }]}],
                })
                self.assertTrue(started["ok"], started)
                self.assertEqual(started["outcome"], "ready_to_spawn")
                self.assertEqual(len(started["dispatches"]), 1)
                dispatch = started["dispatches"][0]
                match = re.search(
                    r"read_dispatch_briefing\((\{[^\n]+?\})\)",
                    str(dispatch["arguments"]["message"]),
                )
                self.assertIsNotNone(match)
                assert match is not None
                pair = json.loads(match.group(1))
                self.assertTrue(cortex.read_dispatch_briefing(pair)["complete"])

                bound = {"project_root": str(project), "task_ref": started["task_ref"]}
                root = cortex.ledger_root(bound)
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                _task_dir, initial_state, _task, _task_ref = resolved
                task_id = initial_state["task_id"]

                retryable = {
                    "schema": "cortex/worker-completion/v11", "ok": False,
                    "error": {
                        "code": "complete_attempt_validation_failed", "category": "validation",
                        "message": "Correct the summary.",
                        "diagnostics": [{
                            "code": "complete_attempt_validation_failed",
                            "json_pointer": "/outcome/summary", "message": "summary is required",
                            "field_schema": {"type": "string", "minLength": 1},
                        }],
                    },
                    "recovery": {
                        "kind": "same_operation", "operation": "complete_attempt",
                        "retryable": True, "state_mutated": False,
                        "allowed_changes": [{
                            "json_pointer": "/outcome/summary", "allowed_ops": ["replace"],
                        }],
                    },
                }
                with patch.object(cortex, "_complete_worker_attempt_operation", return_value=retryable):
                    correction = cortex.complete_worker_attempt(pair)
                self.assertTrue(correction["recovery"]["retryable"])
                self.assertNotIn("terminal_failure", correction["recovery"])
                self.assertIsNone(ledger_db.get_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                ))

                terminal = {
                    "schema": "cortex/worker-completion/v11", "ok": False,
                    "error": {
                        "code": "complete_attempt_repair_rejected", "category": "integrity",
                        "message": "Cortex rejected invalid repair authority.",
                        "diagnostics": [{
                            "code": "complete_attempt_repair_rejected", "json_pointer": "",
                            "message": "repair authority failed integrity validation",
                            "field_schema": {"type": "object"},
                        }],
                    },
                    "recovery": {
                        "kind": "terminal_stop", "operation": "complete_attempt",
                        "retryable": False, "state_mutated": False,
                    },
                }
                with patch.object(cortex, "_complete_worker_attempt_operation", return_value=terminal):
                    failed = cortex.complete_worker_attempt(pair)
                self.assertEqual(failed["recovery"]["terminal_failure"], cortex.TERMINAL_FAILURE_ACTION)
                evidence = ledger_db.get_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                )
                self.assertIsInstance(evidence, dict)
                assert isinstance(evidence, dict)
                self.assertEqual(evidence["dispatch_ref"], dispatch["dispatch_ref"])
                self.assertEqual(evidence["error_category"], "integrity")
                self.assertEqual(evidence["error_code"], "complete_attempt_repair_rejected")
                rendered_evidence = json.dumps(evidence, sort_keys=True)
                self.assertNotIn(pair["assignment_ref"], rendered_evidence)
                self.assertNotIn("diagnostics", rendered_evidence)
                self.assertNotIn("prompt", rendered_evidence)
                self.assertNotIn("capability", rendered_evidence)

                request = {
                    "task_ref": started["task_ref"],
                    "coordinator_ref": started["coordinator_ref"],
                    "intent": "finalize_worker_failure",
                    "payload": {
                        "dispatch_ref": "dispatch-" + "f" * 24,
                        "reason_code": "worker_nonretryable_terminal",
                    },
                }
                wrong = cortex.manage_orchestration(request)
                self.assertFalse(wrong["ok"])
                self.assertEqual(
                    ledger_db.get_task_document(root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY),
                    evidence,
                )
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                self.assertEqual(resolved[1]["status"], "active")

                stale = {**evidence, "assignment_generation": evidence["assignment_generation"] + 1}
                ledger_db.put_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY, stale,
                )
                request["payload"]["dispatch_ref"] = dispatch["dispatch_ref"]
                rejected_stale = cortex.manage_orchestration(request)
                self.assertFalse(rejected_stale["ok"])
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                self.assertEqual(resolved[1]["status"], "active")

                with patch.object(cortex, "_complete_worker_attempt_operation", return_value=terminal):
                    cortex.complete_worker_attempt(pair)
                expiring = ledger_db.get_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                )
                self.assertIsInstance(expiring, dict)
                assert isinstance(expiring, dict)
                expired = {**expiring, "expires_at": "2000-01-01T00:00:00+00:00"}
                unrelated_key = "unrelated_control_evidence"
                unrelated = {"schema": "test/unrelated/v1", "updated_at": expired["updated_at"]}
                ledger_db.put_task_document(root, task_id, unrelated_key, unrelated)
                ledger_db.put_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY, expired,
                )
                rejected_expired = cortex.manage_orchestration(request)
                self.assertFalse(rejected_expired["ok"])
                self.assertIsNone(ledger_db.get_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                ))
                self.assertEqual(
                    ledger_db.get_task_document(root, task_id, unrelated_key), unrelated,
                )
                after_expiry = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(after_expiry, tuple)
                self.assertEqual(after_expiry[1]["status"], "active")
                self.assertNotEqual(after_expiry[1]["attempts"][0]["status"], "failed")

                with patch.object(cortex, "_complete_worker_attempt_operation", return_value=terminal):
                    cortex.complete_worker_attempt(pair)
                finalized = cortex.manage_orchestration(request)
                self.assertIsNone(ledger_db.get_task_document(
                    root, task_id, cortex.TERMINAL_FAILURE_EVIDENCE_KEY,
                ))
                resolved = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(resolved, tuple)
                terminal_state = resolved[1]
                self.assertEqual(terminal_state["status"], "blocked")
                self.assertEqual(terminal_state["attempts"][0]["status"], "failed")
                self.assertEqual(
                    terminal_state["worker_terminal_failure"]["error_code"],
                    "complete_attempt_repair_rejected",
                )

                replay = cortex.manage_orchestration(request)
                self.assertFalse(replay["ok"])
                replay_state = cortex._v11_resolve_task(bound, include_completed=True)
                assert isinstance(replay_state, tuple)
                self.assertEqual(replay_state[1], terminal_state)
                self.assertFalse(finalized["ok"])
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
                    "waves": [{"phase": "implementation", "workers": [{
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
                    "waves": [{"phase": "plan", "workers": [{}]}],
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
                    "waves": [{"phase": "plan", "workers": [{}]}],
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
                    "waves": [{"phase": "plan", "workers": [{}]}],
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
