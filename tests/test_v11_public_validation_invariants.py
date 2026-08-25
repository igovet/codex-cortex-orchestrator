"""Cross-tool invariants for the closed Cortex v11 validation surface."""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import mcp_api
from cortex_runtime.attempt_facade import (
    complete_attempt,
    read_worker_result,
    record_attempt_event,
)
from cortex_runtime.dispatch_briefing import read_dispatch_briefing
from cortex_runtime.questions import worker_question


TASK_REF = "task-000000000001"
ASSIGNMENT_REF = "assignment-v1-" + "a" * 64
COORDINATOR_REF = "b" * 64
LITERAL_UNKNOWN = "bad[0]/~key"
LITERAL_POINTER = "/bad[0]~1~0key"


def diagnostics(response: dict[str, object]) -> list[dict[str, object]]:
    error = response.get("error")
    assert isinstance(error, dict)
    values = error.get("diagnostics")
    assert isinstance(values, list)
    return values


class V11PublicValidationInvariantTests(unittest.TestCase):
    def assert_closed_failure(self, response: dict[str, object], operation: str) -> None:
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["recovery"]["operation"], operation)
        self.assertFalse(response["recovery"]["state_mutated"])
        self.assertNotIn("received", json.dumps(response))
        self.assertNotIn("expected", json.dumps(response))
        for item in diagnostics(response):
            pointer = item["json_pointer"]
            self.assertTrue(pointer == "" or str(pointer).startswith("/"), item)
            self.assertIsInstance(item["field_schema"], dict)

    def test_unknown_literal_keys_preserve_one_escaped_pointer_segment(self) -> None:
        cases = [
            (
                "worker_question",
                worker_question,
                {
                    "task_ref": TASK_REF,
                    "assignment_ref": ASSIGNMENT_REF,
                    "action": "not-an-action",
                    LITERAL_UNKNOWN: True,
                },
            ),
            (
                "record_attempt_event",
                record_attempt_event,
                {
                    "task_ref": TASK_REF,
                    "assignment_ref": ASSIGNMENT_REF,
                    "event_type": "not-an-event",
                    "payload": {},
                    LITERAL_UNKNOWN: True,
                },
            ),
            (
                "read_dispatch_briefing",
                read_dispatch_briefing,
                {
                    "task_ref": TASK_REF,
                    "assignment_ref": ASSIGNMENT_REF,
                    "max_bytes": 0,
                    LITERAL_UNKNOWN: True,
                },
            ),
            (
                "read_worker_result",
                read_worker_result,
                {
                    "task_ref": TASK_REF,
                    "coordinator_ref": COORDINATOR_REF,
                    "step": 0,
                    LITERAL_UNKNOWN: True,
                },
            ),
        ]
        for operation, handler, arguments in cases:
            with self.subTest(operation=operation):
                response = handler(arguments)
                self.assert_closed_failure(response, operation)
                item = next(
                    value for value in diagnostics(response)
                    if value["json_pointer"] == LITERAL_POINTER
                )
                self.assertEqual(item["field_schema"], {
                    "type": "object", "additionalProperties": False,
                })

    def test_event_preflight_aggregates_independent_form_errors(self) -> None:
        response = record_attempt_event({
            "task_ref": TASK_REF,
            "assignment_ref": ASSIGNMENT_REF,
            "event_type": "not-an-event",
            LITERAL_UNKNOWN: True,
        })
        self.assert_closed_failure(response, "record_attempt_event")
        self.assertEqual(
            {item["json_pointer"] for item in diagnostics(response)},
            {LITERAL_POINTER, "/event_type", "/payload"},
        )
        self.assertEqual(response["recovery"]["kind"], "same_operation")
        self.assertEqual(
            {item["json_pointer"]: item["allowed_ops"] for item in response["recovery"]["allowed_changes"]},
            {
                LITERAL_POINTER: ["remove"],
                "/event_type": ["replace"],
                "/payload": ["add"],
            },
        )
        event = next(item for item in diagnostics(response) if item["json_pointer"] == "/event_type")
        self.assertEqual(event["field_schema"]["enum"], [
            "finding_added", "decision_evidence", "blocker",
            "verification_claimed", "progress", "note",
        ])

    def test_missing_worker_authority_stops_without_hiding_model_form_errors(self) -> None:
        response = record_attempt_event({
            "event_type": "not-an-event",
            LITERAL_UNKNOWN: True,
        })
        self.assert_closed_failure(response, "record_attempt_event")
        self.assertEqual(response["recovery"]["kind"], "terminal_stop")
        self.assertFalse(response["recovery"]["retryable"])
        pointers = {item["json_pointer"] for item in diagnostics(response)}
        self.assertTrue({
            "/task_ref", "/assignment_ref", "/event_type", "/payload",
            LITERAL_POINTER,
        }.issubset(pointers))
        self.assertNotIn("allowed_changes", response["recovery"])

    def test_briefing_preflight_aggregates_and_prescribes_exact_edits(self) -> None:
        response = read_dispatch_briefing({
            "task_ref": TASK_REF,
            "assignment_ref": ASSIGNMENT_REF,
            "max_bytes": 0,
            LITERAL_UNKNOWN: True,
        })
        self.assert_closed_failure(response, "read_dispatch_briefing")
        self.assertEqual(
            {item["json_pointer"] for item in diagnostics(response)},
            {LITERAL_POINTER, "/max_bytes"},
        )
        self.assertEqual(response["recovery"]["kind"], "same_operation")
        changes = {
            item["json_pointer"]: item["allowed_ops"]
            for item in response["recovery"]["allowed_changes"]
        }
        self.assertEqual(changes, {
            LITERAL_POINTER: ["remove"], "/max_bytes": ["replace"],
        })

    def test_result_read_is_a_closed_non_mixed_union(self) -> None:
        mixed = read_worker_result({
            "task_ref": TASK_REF,
            "assignment_ref": ASSIGNMENT_REF,
            "attempt_result_ref": "attempt-result-one",
            "coordinator_ref": COORDINATOR_REF,
            "step": 1,
        })
        self.assert_closed_failure(mixed, "read_worker_result")
        self.assertEqual(mixed["recovery"]["kind"], "same_operation")
        self.assertEqual(diagnostics(mixed)[0]["json_pointer"], "/assignment_ref")
        self.assertEqual(diagnostics(mixed)[0]["branch"], "closed_result_read_union")
        self.assertEqual(mixed["recovery"]["allowed_changes"], [
            {"json_pointer": "/assignment_ref", "allowed_ops": ["remove"]},
            {"json_pointer": "/attempt_result_ref", "allowed_ops": ["remove"]},
        ])

        malformed = read_worker_result({
            "task_ref": TASK_REF,
            "coordinator_ref": COORDINATOR_REF,
            "step": "1",
            LITERAL_UNKNOWN: True,
        })
        self.assert_closed_failure(malformed, "read_worker_result")
        self.assertEqual(
            {item["json_pointer"] for item in diagnostics(malformed)},
            {LITERAL_POINTER, "/step"},
        )
        # The step is server-issued.  A shape card must never authorize the
        # model to synthesize a replacement value.
        step = next(item for item in diagnostics(malformed) if item["json_pointer"] == "/step")
        self.assertEqual(step["value_source"], "cortex")
        self.assertEqual(malformed["recovery"]["kind"], "terminal_stop")

    def test_complete_attempt_invalid_authority_aggregates_without_repair_escrow(self) -> None:
        response = complete_attempt({
            "task_ref": "not-a-task-ref",
            "assignment_ref": "not-an-assignment-ref",
            "outcome": {
                "status": "not-a-status",
                "summary": "",
                "findings": [{"severity": "impossible"}],
            },
        })
        self.assert_closed_failure(response, "complete_attempt")
        self.assertEqual(response["recovery"], {
            "kind": "terminal_stop", "operation": "complete_attempt",
            "retryable": False, "state_mutated": False,
        })
        pointers = {item["json_pointer"] for item in diagnostics(response)}
        self.assertTrue({
            "/task_ref", "/assignment_ref", "/outcome/status",
            "/outcome/summary", "/outcome/findings/0/summary",
            "/outcome/findings/0/severity",
        }.issubset(pointers))
        self.assertNotIn("repair", response["recovery"])
        self.assertNotIn("repair_capsule", json.dumps(response))

    def test_all_public_invalid_domain_calls_are_mcp_tool_errors_not_jsonrpc_errors(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0", "id": index, "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
            for index, name in enumerate(mcp_api.DEFAULT_PUBLIC_TOOL_NAMES, 1)
        ]
        stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        stdout = io.StringIO()
        logged: list[object] = []
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools=cortex.PUBLIC_TOOLS,
                internal_handlers=cortex.TOOLS,
                server_version="test",
                instructions="test",
                audience="default",
                log_tool_error=lambda *args: logged.append(args),
            )
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), len(requests))
        self.assertFalse(logged)
        for expected, response in zip(requests, responses):
            with self.subTest(tool=expected["params"]["name"]):
                self.assertEqual(response["id"], expected["id"])
                self.assertIn("result", response)
                self.assertNotIn("error", response)
                self.assertIs(response["result"]["isError"], True)
                content = response["result"]["content"]
                self.assertEqual(content[0]["type"], "text")
                self.assertIn("recovery: kind=", content[0]["text"])
                self.assertIn("state_mutated=false", content[0]["text"])
                structured = response["result"]["structuredContent"]
                self.assertFalse(structured["ok"])
                self.assertIn("error", structured)
                self.assertIn("recovery", structured)

    def test_known_tool_non_object_arguments_are_actionable_mcp_tool_errors(self) -> None:
        argument_shapes: list[object] = [[], None, "not-an-object", 7, False]
        requests: list[dict[str, object]] = []
        request_id = 100
        for name in mcp_api.DEFAULT_PUBLIC_TOOL_NAMES:
            for arguments in argument_shapes:
                requests.append({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
                request_id += 1
        handlers = {
            name: (Mock(side_effect=AssertionError("invalid arguments reached handler")), schema)
            for name, (_handler, schema) in cortex.PUBLIC_TOOLS.items()
        }
        stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        stdout = io.StringIO()
        logged: list[object] = []
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools=handlers,
                internal_handlers={},
                server_version="test",
                instructions="test",
                audience="default",
                log_tool_error=lambda *args: logged.append(args),
            )
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), len(requests))
        self.assertFalse(logged)
        for request, response in zip(requests, responses):
            with self.subTest(
                tool=request["params"]["name"],  # type: ignore[index]
                arguments=request["params"]["arguments"],  # type: ignore[index]
            ):
                self.assertNotIn("error", response)
                result = response["result"]
                self.assertIs(result["isError"], True)
                structured = result["structuredContent"]
                self.assertFalse(structured["ok"])
                self.assertEqual(structured["error"]["code"], "tool_arguments_invalid")
                self.assertEqual(structured["error"]["diagnostics"], [{
                    "code": "tool_arguments_invalid",
                    "json_pointer": "/arguments",
                    "message": "arguments must be an object conforming to the advertised inputSchema",
                    "field_schema": {"type": "object"},
                }])
                self.assertEqual(structured["recovery"], {
                    "kind": "same_operation",
                    "operation": request["params"]["name"],  # type: ignore[index]
                    "retryable": True,
                    "state_mutated": False,
                    "allowed_changes": [{
                        "json_pointer": "/arguments",
                        "allowed_ops": ["replace"],
                    }],
                })
                content = result["content"][0]["text"]
                self.assertIn("/arguments", content)
                self.assertIn("allowed_changes:", content)
                self.assertIn("instruction: apply only allowed_changes", content)
        for handler, _schema in handlers.values():
            handler.assert_not_called()

    def test_non_object_calltool_params_remain_jsonrpc_invalid_params(self) -> None:
        params_shapes: list[object] = [[], None, "not-an-object", 7, False]
        requests = [
            {"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": params}
            for index, params in enumerate(params_shapes, 300)
        ]
        stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        stdout = io.StringIO()
        logged: list[object] = []
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools=cortex.PUBLIC_TOOLS,
                internal_handlers=cortex.TOOLS,
                server_version="test",
                instructions="test",
                audience="default",
                log_tool_error=lambda *args: logged.append(args),
            )
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), len(requests))
        self.assertEqual(len(logged), len(requests))
        for request, response in zip(requests, responses):
            with self.subTest(params=request["params"]):
                self.assertNotIn("result", response)
                self.assertEqual(response["error"], {
                    "code": -32602,
                    "message": "tools/call params must be an object",
                })

    def test_live_worker_question_shape_error_is_one_actionable_mcp_tool_error(self) -> None:
        request = {
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {"name": "worker_question", "arguments": {
                "task_ref": TASK_REF,
                "assignment_ref": ASSIGNMENT_REF,
                "action": "ask",
                "answer_mode": "single_select",
                "question_type": "single_select",
                "decision_scope": "This prose is not a public enum value",
                "question": "Which execution mode should be used?",
                "recommendation": "Use safe_mode because it preserves rollback.",
            }},
        }
        stdin, stdout = io.StringIO(json.dumps(request) + "\n"), io.StringIO()
        logged: list[object] = []
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            mcp_api.serve_stdio(
                public_tools={"worker_question": (
                    worker_question, cortex.PUBLIC_SCHEMA_REGISTRY["worker_question"],
                )},
                internal_handlers={}, server_version="test", instructions="test",
                audience="worker", log_tool_error=lambda *args: logged.append(args),
            )
        response = json.loads(stdout.getvalue())
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertIs(result["isError"], True)
        structured = result["structuredContent"]
        pointers = [item["json_pointer"] for item in structured["error"]["diagnostics"]]
        self.assertEqual(pointers, [
            "/answer_mode", "/decision_scope", "/options", "/recommended_option_ids",
        ])
        self.assertEqual(len(pointers), len(set(pointers)))
        self.assertEqual(structured["recovery"]["allowed_changes"], [
            {"json_pointer": "/answer_mode", "allowed_ops": ["remove"]},
            {"json_pointer": "/decision_scope", "allowed_ops": ["replace"]},
            {"json_pointer": "/options", "allowed_ops": ["add"]},
            {"json_pointer": "/recommended_option_ids", "allowed_ops": ["add"]},
        ])
        content = result["content"][0]["text"]
        for pointer in pointers:
            self.assertIn(pointer, content)
        self.assertIn("instruction: apply only allowed_changes", content)
        self.assertFalse(logged)


if __name__ == "__main__":
    unittest.main()
