"""Focused pure contracts for the closed v11 public response registry."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime import v11_responses as responses
from cortex_runtime import mcp_api


TASK = "task-abc"
DIGEST = "sha256:" + "a" * 64
RESULT = "attempt-result-abc"


def dispatch() -> dict[str, object]:
    return {
        "call": "spawn_agent",
        "dispatch_ref": "dispatch-" + "d" * 24,
        "arguments": {"task_name": "backend_change", "message": "assignment bootstrap", "fork_turns": "none", "model": "gpt-5.6-terra"},
        "bootstrap_repair_message": "server-built bootstrap repair",
    }


def correction() -> dict[str, object]:
    return {"error": {"code": "invalid", "category": "validation", "message": "Invalid test request.", "diagnostics": [{"code": "invalid", "json_pointer": "/task", "message": "is required", "field_schema": {"type": "object"}}]}, "recovery": {"kind": "same_operation", "operation": "test", "retryable": True, "state_mutated": False}}


class V11ResponseContractTests(unittest.TestCase):
    def lifecycle(self, **extra: object) -> dict[str, object]:
        return {"schema": "cortex/lifecycle-response/v11", "task_ref": TASK, **extra}

    def test_every_public_response_union_is_pairwise_exclusive_by_construction(self) -> None:
        for family, schema in responses.RESPONSE_SCHEMA_REGISTRY.items():
            variants = schema["oneOf"]
            for left_index, left in enumerate(variants):
                for right_index, right in enumerate(variants[left_index + 1:], left_index + 1):
                    left_properties = left.get("properties", {})
                    right_properties = right.get("properties", {})
                    conflicting_const = any(
                        key in right_properties
                        and "const" in left_schema
                        and "const" in right_properties[key]
                        and left_schema["const"] != right_properties[key]["const"]
                        for key, left_schema in left_properties.items()
                        if isinstance(left_schema, dict) and isinstance(right_properties.get(key), dict)
                    )
                    closed_required_conflict = any(
                        key not in right_properties for key in left.get("required", [])
                    ) or any(
                        key not in left_properties for key in right.get("required", [])
                    )
                    self.assertTrue(
                        conflicting_const or closed_required_conflict,
                        f"{family} variants {left_index} and {right_index} are not structurally exclusive",
                    )

    def test_all_lifecycle_outcomes_are_closed_and_actionable(self) -> None:
        values = [
            self.lifecycle(ok=True, outcome="ready_to_spawn", action={"kind": "invoke_dispatches"}, step=1, dispatches=[dispatch()]),
            self.lifecycle(ok=True, outcome="waiting", action={"kind": "wait_for_bound_workers"}, step=1),
            self.lifecycle(ok=True, outcome="needs_input", action={"kind": "obtain_user_decision"}, question={"question_ref": "question-abc", "prompt": "Choose one."}),
            self.lifecycle(ok=True, outcome="plan_approval", action={"kind": "obtain_plan_approval"}, decision={"request_id": "approval-abc", "plan_result_ref": RESULT, "plan_digest": DIGEST, "choices": ["approve", "revise", "cancel"]}),
            self.lifecycle(ok=True, outcome="completed", action={"kind": "deliver_handoff"}, handoff={"ref": "handoff-abc", "digest": DIGEST, "close_verified": True}),
            {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "failed", "action": {"kind": "inspect_or_retry"}, **correction()},
            {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "failed", "action": {"kind": "none"}, "error": {"code": "terminal", "category": "internal", "message": "Stopped.", "diagnostics": [{"code": "terminal", "json_pointer": "", "message": "Stopped.", "field_schema": {"type": "object"}}]}, "recovery": {"kind": "terminal_stop", "operation": "manage_orchestration", "retryable": False, "state_mutated": False}},
            {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "needs_input", "action": {"kind": "retry_same_operation"}, **correction()},
        ]
        for value in values:
            with self.subTest(outcome=value["outcome"]):
                self.assertEqual(responses.validate_response("coordinator.lifecycle", value), value)

    def test_lifecycle_rejects_broad_legacy_fields_and_wrong_outcome_fields(self) -> None:
        value = self.lifecycle(ok=True, outcome="waiting", action={"kind": "wait_for_bound_workers"}, step=1)
        for field in ("user_message", "user_view", "internal", "pipeline", "governance", "next_action", "received", "expected"):
            with self.subTest(field=field):
                bad = {**value, field: "legacy"}
                with self.assertRaises(responses.ResponseValidationError):
                    responses.validate_response("coordinator.lifecycle", bad)
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("coordinator.lifecycle", {**value, "dispatches": [dispatch()]})

    def test_start_only_coordinator_capability_and_exact_spawn_shape(self) -> None:
        start = self.lifecycle(ok=True, outcome="ready_to_spawn", action={"kind": "invoke_dispatches"}, step=1, dispatches=[dispatch()], coordinator_ref="b" * 64)
        self.assertEqual(responses.validate_response("coordinator.start", start), start)
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("coordinator.lifecycle", start)
        malformed = copy.deepcopy(start)
        malformed["dispatches"][0]["arguments"]["fork_turns"] = "all"
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("coordinator.lifecycle", malformed)
        # The registry validates lifecycle shape. Runtime routing additionally
        # enforces that coordinator_ref is emitted only by start_orchestration.
        self.assertNotIn("coordinator_ref", responses.response_schema("result.read").get("properties", {}))

    def test_planner_and_explorer_ready_dispatches_share_one_no_wait_action_contract(self) -> None:
        def native_arguments(request: dict[str, object]) -> dict[str, object]:
            value = {
                "task_name": request["task_name"],
                "message": request["message"],
                "reasoning_effort": request["reasoning_effort"],
                "fork_turns": request["fork_turns"],
            }
            if request.get("model"):
                value["model"] = request["model"]
            return value

        base = {
            "ok": True,
            "state": "ready_to_spawn",
            "wave_id": "wave-1",
        }
        planner = mcp_api.v11_response({
            **base,
            "spawn_requests": [{
                "dispatch_ref": "dispatch-" + "a" * 24,
                "task_name": "planner_task",
                "message": "planner bootstrap",
                "reasoning_effort": "high",
                "fork_turns": "none",
                "model": "gpt-5.6-terra",
                "bootstrap_repair_message": "server-built planner bootstrap repair",
            }],
        }, TASK, native_arguments=native_arguments, public_schema="unused", coordinator_lock="unused")
        explorer = mcp_api.v11_response({
            **base,
            "spawn_requests": [{
                "dispatch_ref": "dispatch-" + "b" * 24,
                "task_name": "explorer_task",
                "message": "explorer bootstrap",
                "reasoning_effort": "medium",
                "fork_turns": "none",
                "bootstrap_repair_message": "server-built explorer bootstrap repair",
            }],
        }, TASK, native_arguments=native_arguments, public_schema="unused", coordinator_lock="unused")

        for value in (planner, explorer):
            self.assertEqual(value["outcome"], "ready_to_spawn")
            self.assertEqual(value["action"], {"kind": "invoke_dispatches"})
            self.assertEqual(value["dispatches"][0]["call"], "spawn_agent")
            self.assertTrue(value["dispatches"][0]["bootstrap_repair_message"])
            self.assertNotEqual(value["action"]["kind"], "wait_for_bound_workers")
        self.assertEqual(
            set(planner["dispatches"][0]["arguments"]),
            {"task_name", "message", "reasoning_effort", "fork_turns", "model"},
        )
        self.assertEqual(planner["dispatches"][0]["arguments"]["model"], "gpt-5.6-terra")
        self.assertEqual(planner["dispatches"][0]["arguments"]["reasoning_effort"], "high")
        self.assertEqual(
            set(explorer["dispatches"][0]["arguments"]),
            {"task_name", "message", "reasoning_effort", "fork_turns"},
        )
        self.assertEqual(explorer["dispatches"][0]["arguments"]["reasoning_effort"], "medium")
        self.assertFalse(responses.COORDINATOR_ACTION_SEMANTICS["invoke_dispatches"]["wait_permission"])

    def test_coordinator_action_semantics_are_parity_locked_across_model_contracts(self) -> None:
        plugin = Path(__file__).parents[1] / "plugins" / "cortex"
        profiles = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
        mapping = profiles["shared_worker_contract"]["coordinator_action_semantics"]
        self.assertEqual(mapping, responses.COORDINATOR_ACTION_SEMANTICS)
        self.assertFalse(mapping["invoke_dispatches"]["wait_permission"])
        self.assertTrue(mapping["wait_for_bound_workers"]["requires_child_ids"])

        operation_cards = profiles["shared_worker_contract"]["operation_cards"]
        expected_actions = ["invoke_dispatches", "wait_for_bound_workers"]
        self.assertEqual(operation_cards["start_orchestration"]["action_semantics"], expected_actions)
        self.assertEqual(operation_cards["continue_orchestration"]["action_semantics"], expected_actions)

        for name in ("start_orchestration", "continue_orchestration"):
            description = mcp_api.PUBLIC_TOOL_DESCRIPTIONS[name]
            for action in expected_actions:
                self.assertIn(f"action.kind={action}", description)
                self.assertIn(mapping[action]["instruction"], description)
            self.assertIn("grants no wait permission", description)

        for relative in ("skills/orchestrator/SKILL.md", "skills/cortex-control/SKILL.md"):
            guidance = (plugin / relative).read_text(encoding="utf-8")
            for action in expected_actions:
                self.assertIn(f"action.kind={action}", guidance)
                self.assertIn(mapping[action]["marker"], guidance)
                self.assertIn(mapping[action]["instruction"], guidance)
            self.assertIn("grants no wait permission", guidance)

    def test_correction_and_repair_have_no_mutation_or_echo_fields(self) -> None:
        repair = {
            "repair_capsule": "v11rh1." + "A" * 22 + "." + "b" * 32,
            "base_payload_digest": DIGEST,
            "patch_paths": ["/summary"],
            "diagnostics": [{
                "code": "validation_required", "json_pointer": "/outcome/summary",
                "repair_pointer": "/summary", "message": "is required",
                "field_schema": {"type": "string", "minLength": 1}, "allowed_ops": ["add"],
            }],
        }
        response = {"schema": "cortex/worker-completion/v11", "ok": False, "error": {"code": "complete_attempt_validation_failed", "category": "validation", "message": "Invalid completion.", "diagnostics": [{"code": "validation_required", "json_pointer": "/outcome/summary", "message": "is required", "field_schema": {"type": "string", "minLength": 1}}]}, "recovery": {"kind": "repair_patch_only", "operation": "complete_attempt", "retryable": True, "state_mutated": False, "repair": repair}}
        self.assertEqual(responses.validate_response("worker.completion", response), response)
        for forbidden in ("received", "expected", "next_action", "assignment_ref"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(responses.ResponseValidationError):
                    responses.validate_response("worker.completion", {**response, forbidden: "no"})
        bad_repair = copy.deepcopy(response)
        bad_repair["recovery"]["state_mutated"] = True
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("worker.completion", bad_repair)

    def test_validation_retry_contract_is_parity_locked_and_forbids_source_inspection(self) -> None:
        plugin = Path(__file__).parents[1] / "plugins" / "cortex"
        profiles = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
        control = (plugin / "skills" / "cortex-control" / "SKILL.md").read_text(encoding="utf-8")
        description = mcp_api.PUBLIC_TOOL_DESCRIPTIONS["complete_attempt"]
        for text in (
            description,
            control,
            profiles["shared_worker_contract"]["worker_response_contract"],
            profiles["shared_worker_contract"]["operation_cards"]["complete_attempt"]["purpose"],
            profiles["shared_worker_contract"]["complete_attempt_v11"],
        ):
            self.assertIn("allowed_ops", text)
        for token in ("source", "schema", "ledger"):
            self.assertIn(token, description.lower())
            self.assertIn(token, control.lower())
        self.assertIn("CORTEX_PROTOCOL_FAILURE retryable=false", control)
        self.assertIn("## MCP Cortex response handling", control)
        for token in (
            "top-level `error` and `recovery`",
            "same_operation",
            "repair_patch_only",
            "inspect_server_state",
            "terminal_stop",
            "byte-exactly",
        ):
            self.assertIn(token, control)
        self.assertIn("top-level error={code,category,message,diagnostics}", profiles["shared_worker_contract"]["worker_response_contract"])
        self.assertIn("error/recovery", mcp_api.PUBLIC_TOOL_DESCRIPTIONS["complete_attempt"])

    def test_runtime_has_no_obsolete_public_failure_wrapper_or_retry_strategy(self) -> None:
        runtime = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts" / "cortex_runtime"
        for filename in ("v11_responses.py", "mcp_api.py"):
            source = (runtime / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn('"correction"', source)
                self.assertNotIn('"failure"', source)
                self.assertNotIn("retry_strategy", source)
                self.assertNotIn("required_branch", source)

    def test_repair_operation_cards_are_executable_without_source_lookup(self) -> None:
        required = mcp_api._minimal_repair_diagnostic({
            "code": "validation_required", "json_pointer": "/outcome/summary",
            "repair_pointer": "/summary", "message": "is required",
            "field_schema": {"type": "string", "minLength": 1},
        })
        unknown = mcp_api._minimal_repair_diagnostic({
            "code": "validation_unknown", "json_pointer": "/outcome/obsolete",
            "repair_pointer": "/obsolete", "message": "is not allowed",
            "field_schema": {"type": "object", "additionalProperties": False},
        })
        invalid = mcp_api._minimal_repair_diagnostic({
            "code": "validation_invalid", "json_pointer": "/outcome/status",
            "repair_pointer": "/status", "message": "has an invalid value",
            "field_schema": {"type": "string", "enum": ["completed"]},
        })
        self.assertEqual(required["allowed_ops"], ["add"])
        self.assertEqual(unknown["allowed_ops"], ["remove"])
        self.assertEqual(invalid["allowed_ops"], ["add", "replace"])

    def test_invalid_closed_union_returns_branch_diagnostics_not_generic_oneof_text(self) -> None:
        with self.assertRaises(responses.ResponseValidationError) as raised:
            responses.validate_response("worker.completion", {
                "schema": "cortex/worker-completion/v11", "ok": False,
            })
        self.assertTrue(raised.exception.diagnostics)
        self.assertTrue(all(
            "must match exactly one response outcome variant" not in item["message"]
            for item in raised.exception.diagnostics
        ))

    def test_governance_mutation_and_explicit_inspection_are_separate(self) -> None:
        updated = {"schema": "cortex/governance-response/v11", "ok": True, "outcome": "updated", "receipt": {"resource_kind": "record", "resource_ref": "record-abc", "revision": 1, "digest": DIGEST}}
        inspected = {"schema": "cortex/governance-response/v11", "ok": True, "outcome": "inspected", "inspection": {"ref": "governance-abc", "digest": DIGEST, "items": []}}
        self.assertEqual(responses.validate_response("coordinator.governance", updated), updated)
        self.assertEqual(responses.validate_response("coordinator.governance", inspected), inspected)
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("coordinator.governance", {**updated, "authorization": {"source": "legacy"}})

    def test_coordinator_and_worker_result_reads_are_closed(self) -> None:
        semantic = {"status": "completed", "summary": "Done.", "findings": [], "decisions_needed": [], "unresolved": [], "claims": []}
        coordinator = {"schema": "cortex/worker-result-read/v11", "ok": True, "results": [semantic], "continuation": {"kind": "continue", "step": 2, "results": [{"attempt_result_ref": RESULT}]}}
        worker = {"schema": "cortex/worker-result-read/v11", "ok": True, "result": semantic}
        self.assertEqual(responses.validate_response("result.read", coordinator), coordinator)
        self.assertEqual(responses.validate_response("result.read", worker), worker)
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("result.read", {**coordinator, "phase": "implementation"})

    def test_worker_completion_success_has_no_result_transport_reference(self) -> None:
        completed = {"schema": "cortex/worker-completion/v11", "ok": True, "terminal": True}
        self.assertEqual(responses.validate_response("worker.completion", completed), completed)
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("worker.completion", {**completed, "attempt_result_ref": RESULT})

    def test_briefing_event_and_question_preserve_only_required_progress_data(self) -> None:
        briefing = {"schema": "cortex/briefing-read/v11", "ok": True, "outcome": "briefing_read", "content": "partial", "encoding": "utf-8", "complete": False, "next_cursor": "cursor-abc"}
        event = {"schema": "cortex/worker-event/v11", "ok": True}
        question = {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "question_recorded", "question_ref": "question-abc"}
        self.assertEqual(responses.validate_response("worker.briefing", briefing), briefing)
        self.assertEqual(responses.validate_response("worker.event", event), event)
        self.assertEqual(responses.validate_response("worker.question", question), question)
        self.assertEqual(responses.validate_response("worker.event", {"schema": "cortex/worker-event/v11", "ok": False, **correction()} )["ok"], False)
        for forbidden in ("task_ref", "assignment_ref", "briefing_receipt_ref", "artifact_ref", "cursor", "action"):
            with self.subTest(forbidden=forbidden), self.assertRaises(responses.ResponseValidationError):
                responses.validate_response("worker.briefing", {**briefing, forbidden: "internal"})

    def test_minimal_diagnostics_have_one_pointer_without_legacy_path_alias(self) -> None:
        response = {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "needs_input", "action": {"kind": "retry_same_operation"}, **correction()}
        self.assertEqual(responses.validate_response("coordinator.lifecycle", response), response)
        duplicated = copy.deepcopy(response)
        duplicated["error"]["diagnostics"][0]["path"] = "$.task"
        with self.assertRaises(responses.ResponseValidationError):
            responses.validate_response("coordinator.lifecycle", duplicated)

    def test_correction_variants_can_fail_before_task_resolution_but_success_cannot(self) -> None:
        failures = {
            "coordinator.lifecycle": {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "needs_input", "action": {"kind": "retry_same_operation"}, **correction()},
            "coordinator.start": {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "failed", "action": {"kind": "inspect_or_retry"}, **correction()},
            "coordinator.governance": {"schema": "cortex/governance-response/v11", "ok": False, "outcome": "failed", **correction()},
            "result.read": {"schema": "cortex/worker-result-read/v11", "ok": False, "outcome": "failed", **correction()},
            "worker.briefing": {"schema": "cortex/briefing-read/v11", "ok": False, "outcome": "failed", **correction()},
            "worker.event": {"schema": "cortex/worker-event/v11", "ok": False, **correction()},
            "worker.question": {"schema": "cortex/worker-question/v11", "ok": False, **correction()},
            "worker.completion": {"schema": "cortex/worker-completion/v11", "ok": False, **correction()},
        }
        for name, failure in failures.items():
            with self.subTest(name=name):
                self.assertEqual(responses.validate_response(name, failure), failure)
                malformed = {**failure, "task_ref": "not-a-task-ref"}
                with self.assertRaises(responses.ResponseValidationError):
                    responses.validate_response(name, malformed)
        success = {"schema": "cortex/worker-event/v11", "ok": True}
        self.assertEqual(responses.validate_response("worker.event", success), success)

    def test_every_diagnostic_family_accepts_the_exact_safe_field_card_facets(self) -> None:
        raw_field_schema = {
            "type": "array", "enum": ["one", 2, True], "pattern": "^[a-z]+$",
            "minLength": 1, "maxLength": 8, "minItems": 1, "maxItems": 4,
            "minProperties": 1, "maxProperties": 3, "minimum": -2, "maximum": 9,
            "uniqueItems": True, "additionalProperties": False,
            "format": "project-relative-path", "properties": {"alpha": {}, "beta": {}},
            "items": {"type": "string", "enum": ["a", 1, False], "minLength": 1,
                      "maxLength": 12, "minimum": -1, "maximum": 7,
                      "pattern": "^[a-z]+$", "format": "project-relative-path",
                      "additionalProperties": False},
            "const": "public-constraint", "example": "not-public", "required": ["alpha"],
        }
        card = mcp_api._minimal_field_schema(raw_field_schema)
        self.assertEqual(set(card), set(responses.FIELD_SCHEMA_SCHEMA["properties"]))
        self.assertNotIn("example", card)
        self.assertEqual(card["const"], "public-constraint")
        self.assertEqual(card["required"], ["alpha"])
        self.assertEqual(card["properties"]["alpha"], {"type": "object"})
        full_correction = {"error": {"code": "invalid", "category": "validation", "message": "Invalid test request.", "diagnostics": [{"code": "invalid", "json_pointer": "/field", "message": "invalid", "field_schema": card}]}, "recovery": {"kind": "same_operation", "operation": "test", "retryable": True, "state_mutated": False}}
        failures = {
            "coordinator.lifecycle": {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "needs_input", "action": {"kind": "retry_same_operation"}, **full_correction},
            "coordinator.start": {"schema": "cortex/lifecycle-response/v11", "ok": False, "outcome": "failed", "action": {"kind": "inspect_or_retry"}, **full_correction},
            "coordinator.governance": {"schema": "cortex/governance-response/v11", "ok": False, "outcome": "failed", **full_correction},
            "result.read": {"schema": "cortex/worker-result-read/v11", "ok": False, "outcome": "failed", **full_correction},
            "worker.briefing": {"schema": "cortex/briefing-read/v11", "ok": False, "outcome": "failed", **full_correction},
            "worker.event": {"schema": "cortex/worker-event/v11", "ok": False, **full_correction},
            "worker.question": {"schema": "cortex/worker-question/v11", "ok": False, **full_correction},
            "coordinator.question_management": {"schema": "cortex/question-management/v11", "ok": False, "outcome": "needs_correction", **full_correction},
            "worker.completion": {"schema": "cortex/worker-completion/v11", "ok": False, **full_correction},
        }
        self.assertEqual(set(failures), set(responses.RESPONSE_SCHEMA_REGISTRY))
        for family, failure in failures.items():
            with self.subTest(family=family):
                self.assertEqual(responses.validate_response(family, failure), failure)

    def test_worker_question_matrix_is_compact_and_canonical(self) -> None:
        progress = {"answered": 1, "total": 2, "next_question_key": "scope"}
        values = [
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "question_recorded", "question_ref": "question-one"},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "batch_recorded", "batch_ref": "batch-one"},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "awaiting_user", "question_ref": "question-one"},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "awaiting_user", "batch_ref": "batch-one", "progress": progress},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "question_superseded", "question_ref": "question-one"},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "batch_superseded", "batch_ref": "batch-one"},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "question_answered", "question_ref": "question-one", "answer": {"text": "Use the narrow scope.", "option_ids": ["narrow"]}},
            {"schema": "cortex/worker-question/v11", "ok": True, "outcome": "batch_answered", "batch_ref": "batch-one", "progress": {"answered": 2, "total": 2}, "answers": {"scope": {"text": "Narrow."}, "timing": "Now."}},
        ]
        for value in values:
            with self.subTest(outcome=value["outcome"]):
                self.assertEqual(responses.validate_response("worker.question", value), value)
        for forbidden in ("question", "prompt", "options", "status", "resume_context", "next_action", "durable"):
            with self.subTest(forbidden=forbidden), self.assertRaises(responses.ResponseValidationError):
                responses.validate_response("worker.question", {**values[6], forbidden: "legacy"})

    def test_coordinator_question_management_matrix_has_only_user_cards_or_resume_receipts(self) -> None:
        card = {"prompt": "Choose a scope.", "options": [{"number": 1, "label": "Narrow", "description": "Only the requested module."}]}
        progress = {"answered": 0, "total": 2, "next_question_key": "scope"}
        values = [
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "awaiting_user", "question_ref": "question-one", "question": card},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "awaiting_user", "batch_ref": "batch-one", "progress": progress, "question": card},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "question_answered", "resume": {"kind": "poll", "question_ref": "question-one"}},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "question_answered", "resume": {"kind": "poll_batch", "batch_ref": "batch-one"}},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "question_answered_not_resumable", "question_ref": "question-one"},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "batch_superseded", "batch_ref": "batch-one"},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "awaiting_translation", "translation": {"question_ref": "question-one", "source_text": "Исходный ответ"}},
            {"schema": "cortex/question-management/v11", "ok": True, "outcome": "awaiting_translation", "translation": {"batch_ref": "batch-one", "source_text_by_question": {"scope": "Исходный ответ"}}},
        ]
        for value in values:
            with self.subTest(outcome=value["outcome"]):
                self.assertEqual(responses.validate_response("coordinator.question_management", value), value)
        for forbidden in ("status", "question_id", "resume_context", "resume_contract", "next_action", "chat_interaction", "durable"):
            with self.subTest(forbidden=forbidden), self.assertRaises(responses.ResponseValidationError):
                responses.validate_response("coordinator.question_management", {**values[2], forbidden: "legacy"})

    def test_event_success_drops_unused_internal_metadata_and_bearers(self) -> None:
        bearer = "assignment-v1-" + "d" * 64
        malformed = {
            "ok": True,
            "event_ref": "not-an-event-ref",
            "private_state": {"message": "must not escape " + bearer},
        }
        projected = mcp_api._safe_public_response(
            "record_attempt_event",
            malformed,
            arguments={"task_ref": TASK, "assignment_ref": bearer},
            supplied_coordinator_refs=frozenset(),
        )
        self.assertEqual(projected, {"schema": "cortex/worker-event/v11", "ok": True})
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(bearer, serialized)
        self.assertNotIn("private_state", serialized)
        self.assertNotIn("not-an-event-ref", serialized)

    def test_hard_projection_preserves_bearers_only_at_exact_executable_paths(self) -> None:
        assignment_ref = "assignment-v1-" + "e" * 64
        coordinator_ref = "f" * 64
        value = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": True,
            "outcome": "ready_to_spawn",
            "task_ref": TASK,
            "coordinator_ref": coordinator_ref,
            "action": {"kind": "invoke_dispatches"},
            "step": 1,
            "dispatches": [{
                "call": "spawn_agent",
                "dispatch_ref": "dispatch-" + "e" * 24,
                "arguments": {
                    "task_name": "bounded_worker",
                    "message": "Use assignment_ref=" + assignment_ref,
                    "fork_turns": "none",
                },
                "bootstrap_repair_message": "Use assignment_ref=" + assignment_ref,
            }],
        }
        projected = mcp_api._safe_public_response(
            "start_orchestration",
            value,
            arguments={"project_root": "/not-returned"},
            supplied_coordinator_refs=frozenset(),
        )
        serialized = json.dumps(projected, sort_keys=True)
        self.assertEqual(serialized.count(assignment_ref), 2)
        self.assertEqual(serialized.count(coordinator_ref), 1)
        self.assertEqual(projected["coordinator_ref"], coordinator_ref)
        self.assertEqual(projected["dispatches"][0]["arguments"]["message"].count(assignment_ref), 1)
        self.assertEqual(projected["dispatches"][0]["bootstrap_repair_message"].count(assignment_ref), 1)


if __name__ == "__main__":
    unittest.main()
