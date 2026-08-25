"""Focused transport-boundary coverage for closed v11 response projection."""
from __future__ import annotations

import sys
import unittest
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import mcp_api
from cortex_runtime.dispatch_briefing import read_dispatch_briefing
from cortex_runtime.v11_responses import validate_response


TASK = "task-abc"


class V11PublicResponseProjectionTests(unittest.TestCase):
    def test_question_management_projects_user_card_and_resume_without_runtime_fields(self) -> None:
        pending = mcp_api.project_public_response("manage_orchestration", {
            "ok": True, "task_ref": TASK, "outcome": "awaiting_user",
            "result": {"status": "pending_user_message", "question_id": "question-one"},
            "chat_interaction": {"user_view": {"question": "Choose scope.", "options": [{"number": 1, "label": "Narrow"}]}},
        }, arguments={"intent": "question", "task_ref": TASK})
        self.assertEqual(pending["question"], {"prompt": "Choose scope.", "options": [{"number": 1, "label": "Narrow"}]})
        self.assertNotIn("chat_interaction", pending)
        resumed = mcp_api.project_public_response("manage_orchestration", {
            "ok": True, "task_ref": TASK, "outcome": "question_answered", "result": {"status": "answered"},
            "resume_contract": {"poll_action": "poll", "question_ref": "question-one", "attempt_id": "internal"},
        }, arguments={"intent": "question", "task_ref": TASK})
        self.assertEqual(resumed["resume"], {"kind": "poll", "question_ref": "question-one"})
        self.assertNotIn("resume_contract", resumed)

    def test_worker_batch_poll_never_invents_alias_or_progress(self) -> None:
        pending = mcp_api.project_public_response("worker_question", {
            "ok": True, "outcome": "awaiting_user", "batch_ref": "batch-one",
        }, arguments={"task_ref": TASK})
        self.assertEqual(pending["batch_ref"], "batch-one")
        self.assertNotIn("question_ref", pending)
        self.assertNotIn("progress", pending)
        answered = mcp_api.project_public_response("worker_question", {
            "ok": True, "outcome": "batch_answered", "batch_ref": "batch-one",
            "answers": {"scope": {"answer_en": "Narrow"}},
        }, arguments={"task_ref": TASK})
        self.assertEqual(answered["answers"], {"scope": {"text": "Narrow"}})
        self.assertNotIn("progress", answered)

    def test_lifecycle_options_are_numbered_closed_cards(self) -> None:
        card = mcp_api._real_question({"result": {"question": {
            "question_id": "question-one", "question": "Choose.",
            "options": [{"label": "Narrow", "description": "One module."}],
        }}})
        self.assertEqual(card, {"question_ref": "question-one", "prompt": "Choose.", "options": [{"number": 1, "label": "Narrow", "description": "One module."}]})

    def test_all_nine_unavailable_fallbacks_are_family_valid_and_minimal(self) -> None:
        arguments = {"manage_orchestration": {"intent": "question"}}
        for tool, family in mcp_api._PUBLIC_RESPONSE_FAMILIES.items():
            with self.subTest(tool=tool):
                response = mcp_api._public_internal_failure(tool, arguments.get(tool, {}))
                self.assertEqual(validate_response(mcp_api._public_response_family(tool, arguments.get(tool, {})), response), response)
                self.assertIn("error", response)
                self.assertEqual(response["recovery"], {"kind": "terminal_stop", "operation": tool, "retryable": False, "state_mutated": False})
                for forbidden in ("user_message", "user_view", "internal", "pipeline", "governance", "next_action", "received", "expected", "path"):
                    self.assertNotIn(forbidden, response)

    def test_safe_projection_returns_closed_fallback_after_scrub(self) -> None:
        malformed_start = {
            "schema": "cortex/lifecycle-response/v11", "ok": True, "outcome": "ready_to_spawn",
            "task_ref": TASK, "coordinator_ref": "not-a-capability", "action": {"kind": "invoke_dispatches"},
            "step": 1, "dispatches": [{"call": "spawn_agent", "dispatch_ref": "dispatch-" + "d" * 24, "arguments": {"task_name": "worker", "message": "brief", "fork_turns": "none"}, "bootstrap_repair_message": "server-built repair"}],
        }
        with patch.object(mcp_api, "project_public_response", return_value=malformed_start):
            response = mcp_api._safe_public_response("start_orchestration", {}, arguments={}, supplied_coordinator_refs=frozenset())
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "public_response_projection_failed")

    def test_closed_direct_response_is_passed_through_without_rewriting_question_or_capability_failure(self) -> None:
        question = {
            "schema": "cortex/question-management/v11", "ok": True, "outcome": "question_answered",
            "resume": {"kind": "poll", "question_ref": "question-one"},
        }
        self.assertEqual(
            mcp_api.project_public_response("manage_orchestration", question, arguments={"intent": "question", "task_ref": TASK}),
            question,
        )
        lost = {
            "schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "failed",
            "action": {"kind": "none"},
            "error": {"code": "coordinator_capability_lost", "category": "authority", "message": "lost", "diagnostics": [{"code": "coordinator_capability_lost", "json_pointer": "", "message": "lost", "field_schema": {"type": "string"}}]}, "recovery": {"kind": "terminal_stop", "operation": "start_orchestration", "retryable": False, "state_mutated": False},
        }
        self.assertEqual(mcp_api.project_public_response("start_orchestration", lost, arguments={}), lost)

    def test_follow_up_uses_the_start_delivery_family_and_keeps_its_capability(self) -> None:
        follow_up_start = {
            "schema": "cortex/lifecycle-response/v11", "ok": True, "outcome": "ready_to_spawn", "task_ref": "task-followup",
            "coordinator_ref": "a" * 64, "action": {"kind": "invoke_dispatches"}, "step": 1,
            "dispatches": [{"call": "spawn_agent", "dispatch_ref": "dispatch-" + "a" * 24, "arguments": {"task_name": "followup", "message": "assignment-v1-" + "a" * 64, "fork_turns": "none"}, "bootstrap_repair_message": "assignment-v1-" + "a" * 64}],
        }
        arguments = {"intent": "follow_up", "task_ref": TASK}
        self.assertEqual(mcp_api._public_response_family("manage_orchestration", arguments), "coordinator.start")
        self.assertEqual(mcp_api.project_public_response("manage_orchestration", follow_up_start, arguments=arguments), follow_up_start)

    def test_minimal_diagnostic_preserves_only_safe_schema_facets(self) -> None:
        diagnostic = mcp_api._minimal_diagnostic({
            "code": "invalid", "json_pointer": "/choice", "message": "choose a value", "received": "secret",
            "field_schema": {"type": "string", "enum": ["a", "b"], "minLength": 1, "maxLength": 4,
                             "pattern": "^[ab]$", "additionalProperties": False, "properties": {"hidden": {}}, "example": "secret"},
        }, default_code="fallback")
        self.assertEqual(diagnostic["field_schema"], {"type": "string", "enum": ["a", "b"], "minLength": 1, "maxLength": 4, "pattern": "^[ab]$", "additionalProperties": False, "properties": {"hidden": {"type": "object"}}})
        self.assertNotIn("received", diagnostic)

    def test_briefing_stdio_rejects_next_cursor_and_malformed_incomplete_success(self) -> None:
        invalid_input = {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "read_dispatch_briefing", "arguments": {
                "task_ref": "task-000000000001",
                "assignment_ref": "assignment-v1-" + "a" * 64,
                "next_cursor": "not-an-input-field",
            }},
        }
        handler = Mock(side_effect=read_dispatch_briefing)
        stdin, stdout = io.StringIO(json.dumps(invalid_input) + "\n"), io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools={"read_dispatch_briefing": (
                    handler, cortex.PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"],
                )},
                internal_handlers={}, server_version="test", instructions="test",
                audience="worker", log_tool_error=lambda *_args: None,
            )
        invalid_tool_result = json.loads(stdout.getvalue())["result"]
        self.assertIs(invalid_tool_result["isError"], True)
        self.assertIn("/next_cursor", invalid_tool_result["content"][0]["text"])
        self.assertIn("allowed_changes:", invalid_tool_result["content"][0]["text"])
        invalid_response = invalid_tool_result["structuredContent"]
        self.assertFalse(invalid_response["ok"])
        self.assertEqual(set(invalid_response), {"schema", "ok", "outcome", "error", "recovery"})
        self.assertEqual(
            [item["json_pointer"] for item in invalid_response["error"]["diagnostics"]],
            ["/next_cursor"],
        )
        self.assertEqual(invalid_response["recovery"], {
            "kind": "same_operation", "operation": "read_dispatch_briefing", "retryable": True, "state_mutated": False,
            "allowed_changes": [{"json_pointer": "/next_cursor", "allowed_ops": ["remove"]}],
        })
        handler.assert_called_once()

        malformed_incomplete = {
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "read_dispatch_briefing", "arguments": {}},
        }
        stdin, stdout = io.StringIO(json.dumps(malformed_incomplete) + "\n"), io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools={"read_dispatch_briefing": (
                    lambda _args: {"ok": True, "content": "partial", "encoding": "utf-8", "complete": False},
                    {},
                )},
                internal_handlers={}, server_version="test", instructions="test",
                audience="worker", log_tool_error=lambda *_args: None,
            )
        malformed_tool_result = json.loads(stdout.getvalue())["result"]
        self.assertIs(malformed_tool_result["isError"], True)
        self.assertIn("instruction: stop", malformed_tool_result["content"][0]["text"])
        malformed_response = malformed_tool_result["structuredContent"]
        self.assertFalse(malformed_response["ok"])
        self.assertEqual(malformed_response["error"]["code"], "dispatch_briefing_response_invalid")
        self.assertEqual(malformed_response["error"]["diagnostics"][0]["json_pointer"], "/next_cursor")
        self.assertEqual(malformed_response["error"]["diagnostics"][0]["value_source"], "cortex")
        self.assertEqual(malformed_response["recovery"], {
            "kind": "terminal_stop", "operation": "read_dispatch_briefing",
            "retryable": False, "state_mutated": False,
        })
        self.assertNotIn("task_ref", malformed_response)

    def test_same_operation_requires_a_complete_legal_change_set(self) -> None:
        model_authored = mcp_api._minimal_failure_card(
            {
                "code": "worker_question_request_invalid", "retryable": True,
                "diagnostics": [{
                    "code": "worker_question_request_invalid", "json_pointer": "/action",
                    "message": "worker question action is unsupported",
                    "field_schema": {"type": "string", "enum": ["ask", "poll"]},
                }],
            },
            default_code="worker_question_request_invalid", retryable=True,
            operation="worker_question",
        )
        self.assertEqual(model_authored["recovery"]["kind"], "same_operation")
        self.assertEqual(model_authored["recovery"]["allowed_changes"], [
            {"json_pointer": "/action", "allowed_ops": ["replace"]},
        ])

        cortex_issued = mcp_api._minimal_failure_card(
            {
                "code": "worker_question_request_invalid", "retryable": True,
                "diagnostics": [{
                    "code": "worker_question_request_invalid", "json_pointer": "/assignment_ref",
                    "message": "assignment_ref is required",
                    "field_schema": {"type": "string", "pattern": "^assignment-v1-[0-9a-f]{64}$"},
                }],
            },
            default_code="worker_question_request_invalid", retryable=True,
            operation="worker_question",
        )
        self.assertEqual(cortex_issued["error"]["diagnostics"][0]["value_source"], "cortex")
        self.assertEqual(cortex_issued["recovery"], {
            "kind": "terminal_stop", "operation": "worker_question",
            "retryable": False, "state_mutated": False,
        })

    def test_stdio_passes_closed_question_unions_and_unavailable_correction(self) -> None:
        unions = [
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "question_answered", "resume": {"kind": "poll", "question_ref": "question-one"}},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "batch_superseded", "batch_ref": "batch-one"},
        ]
        for value in unions:
            with self.subTest(outcome=value["outcome"]):
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "manage_orchestration", "arguments": {"intent": "question", "task_ref": TASK}}}
                stdin, stdout = io.StringIO(json.dumps(request) + "\n"), io.StringIO()
                with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
                    mcp_api.serve_stdio(
                        public_tools={"manage_orchestration": (lambda _args, value=value: value, {})}, internal_handlers={},
                        server_version="test", instructions="test", audience="default", log_tool_error=lambda *_args: None,
                    )
                tool_result = json.loads(stdout.getvalue())["result"]
                self.assertIs(tool_result["isError"], False)
                payload = tool_result["structuredContent"]
                self.assertEqual(payload, value)
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "start_orchestration", "arguments": {}}}
        stdin, stdout = io.StringIO(json.dumps(request) + "\n"), io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(public_tools={}, internal_handlers={}, server_version="test", instructions="test", audience="worker", log_tool_error=lambda *_args: None)
        denied_result = json.loads(stdout.getvalue())["result"]
        self.assertIs(denied_result["isError"], True)
        payload = denied_result["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema"], "cortex/lifecycle-response/v11")
        self.assertNotIn("next_action", payload)


if __name__ == "__main__":
    unittest.main()
