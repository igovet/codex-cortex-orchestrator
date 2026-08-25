"""Closed, side-effect-free v11 response contracts.

This module is deliberately independent from the lifecycle engine and MCP
transport. The public registry contains only the flat model-facing family;
active nested engine receipts live in a separate explicitly private registry.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

from cortex_runtime.v11_submission import MAX_DIAGNOSTICS


TASK_REF_PATTERN = r"^task-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
COORDINATOR_REF_PATTERN = r"^[0-9a-f]{64}$"
ASSIGNMENT_REF_PATTERN = r"^assignment-v1-[0-9a-f]{64}$"
DISPATCH_REF_PATTERN = r"^dispatch-[0-9a-f]{24}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
RESULT_REF_PATTERN = r"^attempt-result-[A-Za-z0-9._:-]{1,160}$"
REPAIR_HANDLE_PATTERN = r"^v11rh1\.[A-Za-z0-9_-]{22}\.[0-9a-f]{32}$"

PUBLIC_SUCCESS_ACTIONS = (
    "invoke_dispatches", "wait_for_bound_workers", "obtain_user_decision",
    "obtain_plan_approval", "deliver_handoff", "continue", "terminal_continue",
    "read_more", "use_result_as_context", "none",
)
PUBLIC_FAILURE_ACTIONS = (
    "retry_same_operation", "repair_patch_only", "inspect_or_retry", "none",
)
PUBLIC_ACTIONS = tuple(dict.fromkeys(PUBLIC_SUCCESS_ACTIONS + PUBLIC_FAILURE_ACTIONS))


class ResponseValidationError(ValueError):
    """A pure response-contract failure with stable JSON Pointer diagnostics."""

    def __init__(self, diagnostics: Sequence[Mapping[str, Any]]) -> None:
        self.diagnostics = [copy.deepcopy(dict(item)) for item in diagnostics]
        super().__init__("; ".join(str(item.get("message") or "invalid response") for item in self.diagnostics))


def _string(*, pattern: str | None = None, enum: list[str] | None = None, minimum: int = 1, maximum: int | None = 8192) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "minLength": minimum}
    if maximum is not None:
        value["maxLength"] = maximum
    if pattern:
        value["pattern"] = pattern
    if enum:
        value["enum"] = enum
    return value


REF_SCHEMA = _string(pattern=TASK_REF_PATTERN, maximum=160)
DIGEST_SCHEMA = _string(pattern=DIGEST_PATTERN, maximum=71)
RESULT_REF_SCHEMA = _string(pattern=RESULT_REF_PATTERN, maximum=180)
JSON_POINTER_SCHEMA = _string(pattern=r"^(|/.*)$", minimum=0, maximum=2048)

# Private lifecycle composition still validates the engine's historical
# nested action receipt before the sole public.flat projector consumes it.
COORDINATOR_ACTION_SEMANTICS: dict[str, dict[str, Any]] = {
    "invoke_dispatches": {
        "marker": "spawn_all_exact_before_wait",
        "instruction": "Execute every exact returned native spawn_agent dispatch before any wait.",
        "wait_permission": False,
    },
    "wait_for_bound_workers": {
        "marker": "wait_existing_returned_child_ids_only",
        "instruction": "Wait only on existing child IDs returned by successful spawn_agent calls.",
        "requires_child_ids": True,
    },
}

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["kind"],
    "properties": {"kind": _string(enum=[
        "invoke_dispatches", "wait_for_bound_workers", "retry_same_operation",
        "obtain_user_decision", "obtain_plan_approval", "deliver_handoff",
        "inspect_or_retry", "continue", "terminal_continue", "read_more",
        "use_result_as_context", "none",
    ], maximum=64)},
}

DISPATCH_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["task_name", "message", "fork_turns"],
    "properties": {
        "task_name": _string(maximum=256),
        "message": _string(maximum=65536),
        "fork_turns": {"type": "string", "const": "none"},
        "model": _string(maximum=64),
        "reasoning_effort": _string(maximum=32),
    },
}
DISPATCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["call", "dispatch_ref", "arguments"],
    "properties": {
        "call": {"type": "string", "const": "spawn_agent"},
        "dispatch_ref": _string(pattern=DISPATCH_REF_PATTERN, maximum=33),
        "arguments": DISPATCH_ARGUMENTS_SCHEMA,
    },
}

FIELD_ENUM_VALUE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        _string(maximum=256),
        {"type": "integer"},
        {"type": "boolean"},
    ],
}
def _field_card_schema(depth: int) -> dict[str, Any]:
    """Return a bounded schema for model-actionable validation constraints."""
    properties: dict[str, Any] = {
        "type": _string(enum=["object", "array", "string", "integer", "boolean"], maximum=16),
        "const": FIELD_ENUM_VALUE_SCHEMA,
        "enum": {"type": "array", "maxItems": 64, "items": FIELD_ENUM_VALUE_SCHEMA},
        "pattern": _string(maximum=1024), "minLength": {"type": "integer", "minimum": 0},
        "maxLength": {"type": "integer", "minimum": 0}, "minItems": {"type": "integer", "minimum": 0},
        "maxItems": {"type": "integer", "minimum": 0},
        "minProperties": {"type": "integer", "minimum": 0},
        "maxProperties": {"type": "integer", "minimum": 0},
        "minimum": {"type": "integer"}, "maximum": {"type": "integer"},
        "uniqueItems": {"type": "boolean"}, "additionalProperties": {"type": "boolean"},
        "format": _string(enum=["project-relative-path"], maximum=64),
        "required": {"type": "array", "maxItems": 64, "items": _string(maximum=256)},
    }
    if depth > 0:
        child = _field_card_schema(depth - 1)
        properties["properties"] = {
            "type": "object", "maxProperties": 64,
            "additionalProperties": child,
        }
        properties["items"] = child
    return {"type": "object", "additionalProperties": False, "properties": properties}


FIELD_ITEM_SCHEMA: dict[str, Any] = _field_card_schema(1)
FIELD_SCHEMA_SCHEMA: dict[str, Any] = _field_card_schema(2)
DIAGNOSTIC_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["code", "json_pointer", "message", "field_schema"],
    "properties": {
        "code": _string(maximum=160), "json_pointer": JSON_POINTER_SCHEMA, "message": _string(maximum=2000),
        "field_schema": FIELD_SCHEMA_SCHEMA,
        # Most validation fields are model-authored and need no ownership
        # annotation.  Mark only Cortex-issued references whose values must be
        # copied from an earlier server response rather than regenerated from
        # the adjacent field schema.
        "value_source": _string(enum=["model", "cortex"], maximum=16),
        "required_with": {"type": "array", "maxItems": 32, "items": JSON_POINTER_SCHEMA},
        "forbidden_with": {"type": "array", "maxItems": 32, "items": JSON_POINTER_SCHEMA},
        "branch": _string(maximum=160),
    },
}
ALLOWED_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["json_pointer", "allowed_ops"],
    "properties": {
        "json_pointer": JSON_POINTER_SCHEMA,
        "allowed_ops": {
            "type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
            "items": _string(enum=["add", "replace", "remove"], maximum=16),
        },
    },
}
REPAIR_DIAGNOSTIC_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["code", "json_pointer", "repair_pointer", "message", "field_schema", "allowed_ops"],
    "properties": {
        **DIAGNOSTIC_SCHEMA["properties"],
        "repair_pointer": {
            **JSON_POINTER_SCHEMA,
            "description": "Copy this exact value into patch.path; do not copy the repair_pointer property name.",
        },
        "allowed_ops": {
            "type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
            "items": _string(enum=["add", "replace", "remove"], maximum=16),
            "description": "Choose one entry and copy that entry into patch.op; do not copy the allowed_ops array.",
        },
    },
}
RETRY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "operation"],
    "properties": {
        "kind": _string(enum=["same_operation", "repair_patch_only", "inspect_server_state", "terminal_stop"], maximum=32),
        "operation": _string(maximum=64),
    },
}
ERROR_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["code", "category", "message", "diagnostics"],
    "properties": {
        "code": _string(maximum=160),
        "category": _string(enum=["validation", "authority", "stale", "integrity", "unavailable", "internal"], maximum=32),
        "message": _string(maximum=512),
        "diagnostics": {"type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS, "items": DIAGNOSTIC_SCHEMA},
    },
}
PATCH_CONTRACT: dict[str, Any] = {
    "repair_pointer_maps_to": "path",
    "allowed_ops_choice_maps_to": "op",
    "value_required_for": ["add", "replace"],
    "value_forbidden_for": ["remove"],
    "copy_diagnostic_metadata": False,
    "example": {
        "op": "replace",
        "path": "/claims/0",
        "value": {"summary": "...", "severity": "low"},
    },
}
PATCH_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "repair_pointer_maps_to", "allowed_ops_choice_maps_to",
        "value_required_for", "value_forbidden_for",
        "copy_diagnostic_metadata", "example",
    ],
    "properties": {
        "repair_pointer_maps_to": {"type": "string", "const": "path"},
        "allowed_ops_choice_maps_to": {"type": "string", "const": "op"},
        "value_required_for": {
            "type": "array", "minItems": 2, "maxItems": 2,
            "items": _string(enum=["add", "replace"], maximum=16),
        },
        "value_forbidden_for": {
            "type": "array", "minItems": 1, "maxItems": 1,
            "items": {"type": "string", "const": "remove"},
        },
        "copy_diagnostic_metadata": {"type": "boolean", "const": False},
        "example": {
            "type": "object", "additionalProperties": False,
            "required": ["op", "path", "value"],
            "properties": {
                "op": {"type": "string", "const": "replace"},
                "path": {"type": "string", "const": "/claims/0"},
                "value": {
                    "type": "object", "additionalProperties": False,
                    "required": ["summary", "severity"],
                    "properties": {
                        "summary": {"type": "string", "const": "..."},
                        "severity": {"type": "string", "const": "low"},
                    },
                },
            },
        },
    },
    "description": (
        "Executable RFC6902 mapping. Diagnostic metadata explains a patch but is never itself a patch property."
    ),
}
REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": [
        "repair_capsule", "base_payload_digest", "patch_paths", "diagnostics", "patch_contract",
    ],
    "properties": {
        "repair_capsule": {
            **_string(pattern=REPAIR_HANDLE_PATTERN, minimum=62, maximum=62),
            "description": "Opaque fixed-size server handle; copy exactly into the same complete_attempt repair form and never decode or reconstruct it.",
        },
        "base_payload_digest": DIGEST_SCHEMA,
        "patch_paths": {"type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS, "items": JSON_POINTER_SCHEMA},
        "diagnostics": {
            "type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS,
            "items": REPAIR_DIAGNOSTIC_SCHEMA,
            "description": "Complete self-contained validation cards; repair_pointer is the exact semantic RFC6902 patch path.",
        },
        "patch_contract": PATCH_CONTRACT_SCHEMA,
    },
}
TERMINAL_FAILURE_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["evidence", "coordinator_intent", "reason_code"],
    "properties": {
        "evidence": {"type": "string", "const": "server_bound"},
        "coordinator_intent": {"type": "string", "const": "finalize_worker_failure"},
        "reason_code": {"type": "string", "const": "worker_nonretryable_terminal"},
    },
    "description": (
        "No-ID coordinator cleanup action backed by private single-use evidence for the current assignment. "
        "The child status marker is not authority; finalize_worker_failure verifies and consumes this evidence."
    ),
}
_RECOVERY_COMMON_PROPERTIES: dict[str, Any] = {
    "operation": RETRY_SCHEMA["properties"]["operation"],
    "retryable": {"type": "boolean"},
    "state_mutated": {"type": "boolean", "const": False},
}
_ALLOWED_CHANGES_SCHEMA: dict[str, Any] = {
    "type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS,
    "items": ALLOWED_CHANGE_SCHEMA,
    "description": "Exact request paths and operations that make a same_operation retry legal; no Cortex-issued value is exposed here.",
}
# Recovery is a closed, discriminated union.  The rendered result is helpful,
# but structuredContent is authoritative: do not let an incomplete or
# cross-branch recovery become executable merely because it is well-formed JSON.
RECOVERY_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "operation", "retryable", "state_mutated", "allowed_changes"],
            "properties": {
                **_RECOVERY_COMMON_PROPERTIES,
                "kind": {"type": "string", "const": "same_operation"},
                "retryable": {"type": "boolean", "const": True},
                "allowed_changes": _ALLOWED_CHANGES_SCHEMA,
            },
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "operation", "retryable", "state_mutated", "repair"],
            "properties": {
                **_RECOVERY_COMMON_PROPERTIES,
                "kind": {"type": "string", "const": "repair_patch_only"},
                "retryable": {"type": "boolean", "const": True},
                "repair": REPAIR_SCHEMA,
            },
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "operation", "retryable", "state_mutated"],
            "properties": {
                **_RECOVERY_COMMON_PROPERTIES,
                "kind": {"type": "string", "const": "inspect_server_state"},
                "retryable": {"type": "boolean", "const": True},
            },
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "operation", "retryable", "state_mutated"],
            "properties": {
                **_RECOVERY_COMMON_PROPERTIES,
                "kind": {"type": "string", "const": "terminal_stop"},
                "retryable": {"type": "boolean", "const": False},
                "terminal_failure": TERMINAL_FAILURE_ACTION_SCHEMA,
            },
        },
    ],
}
QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["question_ref", "prompt"],
    "properties": {
        "question_ref": _string(pattern=r"^question-[A-Za-z0-9._:-]{1,160}$", maximum=180),
        "prompt": _string(maximum=None),
        "options": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["number", "option_id", "label"],
            "properties": {
                "number": {"type": "integer", "minimum": 1},
                "option_id": _string(maximum=None),
                "label": _string(maximum=None), "description": _string(minimum=0, maximum=None),
            },
        }},
    },
}

# These are deliberately display-only cards.  The server keeps option ids,
# source-language answers, and the durable resume context; the coordinator
# needs only the text it must present to the user.
QUESTION_REF_SCHEMA = _string(pattern=r"^question-[A-Za-z0-9._:-]{1,160}$", maximum=180)
BATCH_REF_SCHEMA = _string(pattern=r"^batch-[A-Za-z0-9._:-]{1,160}$", maximum=180)
DISPLAY_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["prompt"],
    "properties": {
        "prompt": _string(maximum=None),
        "options": QUESTION_SCHEMA["properties"]["options"],
        "recommendation": _string(minimum=1, maximum=None),
    },
}
CANONICAL_ANSWER_SCHEMA: dict[str, Any] = {
    "oneOf": [
        _string(maximum=8000),
        {
            "type": "object", "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "text": _string(maximum=8000),
                "option_ids": {"type": "array", "maxItems": 16, "items": _string(maximum=256)},
            },
        },
    ],
}
BATCH_PROGRESS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["answered", "total"],
    "properties": {
        "answered": {"type": "integer", "minimum": 0},
        "total": {"type": "integer", "minimum": 1},
        "next_question_key": _string(maximum=160),
    },
}
BATCH_ANSWERS_SCHEMA: dict[str, Any] = {
    "type": "object", "minProperties": 1, "maxProperties": 32,
    "additionalProperties": CANONICAL_ANSWER_SCHEMA,
}
QUESTION_RESUME_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "question_ref"],
            "properties": {
                "kind": {"type": "string", "const": "poll"},
                "question_ref": QUESTION_REF_SCHEMA,
            },
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "batch_ref"],
            "properties": {
                "kind": {"type": "string", "const": "poll_batch"},
                "batch_ref": BATCH_REF_SCHEMA,
            },
        },
    ],
}
TRANSLATION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["question_ref", "source_text"],
            "properties": {"question_ref": QUESTION_REF_SCHEMA, "source_text": _string(maximum=8000)},
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["batch_ref", "source_text_by_question"],
            "properties": {
                "batch_ref": BATCH_REF_SCHEMA,
                "source_text_by_question": {
                    "type": "object", "minProperties": 1, "maxProperties": 32,
                    "additionalProperties": _string(maximum=8000),
                },
            },
        },
    ],
}
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["request_id", "plan_result_ref", "plan_digest", "choices"],
    "properties": {
        "request_id": _string(pattern=r"^approval-[A-Za-z0-9._:-]{1,160}$", maximum=180),
        "plan_result_ref": RESULT_REF_SCHEMA, "plan_digest": DIGEST_SCHEMA,
        "choices": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": _string(
                enum=["approve_with_recommendations", "approve_without_recommendations", "cancel"],
                maximum=32,
            ),
        },
    },
}
HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["ref", "digest", "close_verified"],
    "properties": {
        "ref": _string(pattern=r"^handoff-[A-Za-z0-9._:-]{1,160}$", maximum=180),
        "digest": DIGEST_SCHEMA, "close_verified": {"type": "boolean", "const": True},
    },
}

_LIFECYCLE_COMMON: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "ok", "outcome", "task_ref", "action"],
    "properties": {
        "schema": {"type": "string", "const": "cortex/lifecycle-response/v11"},
        "ok": {"type": "boolean"},
        "outcome": _string(enum=["ready_to_spawn", "waiting", "needs_input", "plan_approval", "completed", "failed"], maximum=32),
        "task_ref": REF_SCHEMA, "action": ACTION_SCHEMA, "step": {"type": "integer", "minimum": 1},
        "dispatches": {"type": "array", "minItems": 1, "maxItems": 32, "items": DISPATCH_SCHEMA},
        "coordinator_ref": _string(pattern=COORDINATOR_REF_PATTERN, maximum=64),
        "question": QUESTION_SCHEMA, "decision": DECISION_SCHEMA, "handoff": HANDOFF_SCHEMA,
        "content": _string(minimum=0, maximum=65_536),
        "next_cursor": _string(pattern=r"^c11p\.[A-Za-z0-9_-]{16,512}$", maximum=517),
        "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA,
    },
}


def _variant(
    required: list[str], *, properties: Mapping[str, Any] | None = None, optional: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a closed variant, exposing only its common and explicit fields."""
    all_properties = _LIFECYCLE_COMMON["properties"]
    allowed = {"schema", "ok", "outcome", "task_ref", "action", *required, *optional}
    if properties:
        allowed.update(properties)
    schema = {
        "type": "object", "additionalProperties": False, "required": required,
        "properties": {key: copy.deepcopy(all_properties[key]) for key in allowed if key in all_properties},
    }
    if properties:
        schema["properties"].update(copy.deepcopy(dict(properties)))
    return schema


LIFECYCLE_RESPONSE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        _variant(["schema", "ok", "outcome", "task_ref", "action", "step", "dispatches"], properties={
            "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "ready_to_spawn"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "invoke_dispatches"}}},
        }),
        _variant(["schema", "ok", "outcome", "task_ref", "action", "step"], properties={
            "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "waiting"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "wait_for_bound_workers"}}},
        }),
        _variant(["schema", "ok", "outcome", "task_ref", "action", "question"], properties={
            "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "needs_input"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "obtain_user_decision"}}},
        }),
        _variant(["schema", "ok", "outcome", "task_ref", "action", "decision"], optional=("content", "next_cursor"), properties={
            "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "plan_approval"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "obtain_plan_approval"}}},
        }),
        _variant(["schema", "ok", "outcome", "task_ref", "action", "handoff"], properties={
            "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "completed"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "deliver_handoff"}}},
        }),
        _variant(["schema", "ok", "outcome", "action", "error", "recovery"], properties={
            "ok": {"type": "boolean", "const": False}, "outcome": {"type": "string", "const": "failed"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "enum": ["inspect_or_retry", "none"]}}},
        }),
        _variant(["schema", "ok", "outcome", "action", "error", "recovery"], properties={
            "ok": {"type": "boolean", "const": False}, "outcome": {"type": "string", "const": "needs_input"},
            "action": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "const": "retry_same_operation"}}},
        }),
    ]
}


def _start_variant(variant: Mapping[str, Any]) -> dict[str, Any]:
    """Add the one-shot coordinator bearer only to successful start variants."""
    copied = copy.deepcopy(dict(variant))
    copied["required"] = [*copied.get("required", []), "coordinator_ref"]
    copied.setdefault("properties", {})["coordinator_ref"] = _string(pattern=COORDINATOR_REF_PATTERN, maximum=64)
    return copied


# Start is the only coordinator response family permitted to deliver the
# coordinator bearer. Its failures remain bearer-free lifecycle failures.
START_RESPONSE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        *[_start_variant(variant) for variant in LIFECYCLE_RESPONSE_SCHEMA["oneOf"][:5]],
        *copy.deepcopy(LIFECYCLE_RESPONSE_SCHEMA["oneOf"][5:]),
    ]
}

SEMANTIC_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "findings", "decisions_needed", "unresolved", "claims"],
    "properties": {
        "status": _string(enum=["completed", "blocked", "failed"], maximum=16), "summary": _string(maximum=8000),
        **{key: {"type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False, "required": ["summary"], "properties": {"summary": _string(maximum=2000), "severity": _string(enum=["low", "medium", "high", "critical"], maximum=16)}}} for key in ("findings", "decisions_needed", "unresolved", "claims")},
    },
}
CONTINUATION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "step", "results"],
    "properties": {
        "kind": _string(enum=["continue", "terminal_continue"], maximum=32), "step": {"type": "integer", "minimum": 1},
        "results": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object", "additionalProperties": False, "required": ["attempt_result_ref"], "properties": {"attempt_result_ref": RESULT_REF_SCHEMA, "worker": {"type": "integer", "minimum": 1}}}},
    },
}
RESULT_READ_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "results", "continuation"], "properties": {
            "schema": {"type": "string", "const": "cortex/worker-result-read/v11"}, "ok": {"type": "boolean", "const": True},
            "results": {"type": "array", "minItems": 1, "maxItems": 32, "items": SEMANTIC_RESULT_SCHEMA},
            "continuation": CONTINUATION_SCHEMA,
        }},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "results", "continuation_state"], "properties": {
            "schema": {"type": "string", "const": "cortex/worker-result-read/v11"}, "ok": {"type": "boolean", "const": True},
            "results": {"type": "array", "minItems": 1, "maxItems": 32, "items": SEMANTIC_RESULT_SCHEMA},
            "continuation_state": _string(enum=["unavailable"], maximum=32),
        }},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "result"], "properties": {
            "schema": {"type": "string", "const": "cortex/worker-result-read/v11"}, "ok": {"type": "boolean", "const": True},
            "result": SEMANTIC_RESULT_SCHEMA,
        }},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "error", "recovery"], "properties": {
            "schema": {"type": "string", "const": "cortex/worker-result-read/v11"}, "ok": {"type": "boolean", "const": False},
            "outcome": {"type": "string", "const": "failed"}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA,
        }},
    ]
}

BRIEFING_READ_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "content", "encoding", "complete"], "properties": {
            "schema": {"type": "string", "const": "cortex/briefing-read/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "briefing_read"},
            "content": _string(minimum=0, maximum=65536), "encoding": _string(enum=["utf-8", "base64"], maximum=16), "complete": {"type": "boolean", "const": True},
        }},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "content", "encoding", "complete", "next_cursor"], "properties": {
            "schema": {"type": "string", "const": "cortex/briefing-read/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "briefing_read"},
            "content": _string(minimum=0, maximum=65536), "encoding": _string(enum=["utf-8", "base64"], maximum=16), "complete": {"type": "boolean", "const": False}, "next_cursor": _string(minimum=1, maximum=1024),
        }},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "error", "recovery"], "properties": {
            "schema": {"type": "string", "const": "cortex/briefing-read/v11"}, "ok": {"type": "boolean", "const": False}, "outcome": {"type": "string", "const": "failed"}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA,
        }},
    ]
}

WORKER_EVENT_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok"], "properties": {"schema": {"type": "string", "const": "cortex/worker-event/v11"}, "ok": {"type": "boolean", "const": True}}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "error", "recovery"], "properties": {"schema": {"type": "string", "const": "cortex/worker-event/v11"}, "ok": {"type": "boolean", "const": False}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA}},
    ]
}
WORKER_QUESTION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_recorded"}, "question_ref": QUESTION_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "batch_recorded"}, "batch_ref": BATCH_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "awaiting_user"}, "question_ref": QUESTION_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "awaiting_user"}, "batch_ref": BATCH_REF_SCHEMA, "progress": BATCH_PROGRESS_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_superseded"}, "question_ref": QUESTION_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "batch_superseded"}, "batch_ref": BATCH_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref", "answer"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_answered"}, "question_ref": QUESTION_REF_SCHEMA, "answer": CANONICAL_ANSWER_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref", "answers"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "batch_answered"}, "batch_ref": BATCH_REF_SCHEMA, "progress": BATCH_PROGRESS_SCHEMA, "answers": BATCH_ANSWERS_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "error", "recovery"], "properties": {"schema": {"type": "string", "const": "cortex/worker-question/v11"}, "ok": {"type": "boolean", "const": False}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA}},
    ]
}

COORDINATOR_QUESTION_MANAGEMENT_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref", "question"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "awaiting_user"}, "question_ref": QUESTION_REF_SCHEMA, "question": DISPLAY_QUESTION_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref", "progress", "question"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "awaiting_user"}, "batch_ref": BATCH_REF_SCHEMA, "progress": BATCH_PROGRESS_SCHEMA, "question": DISPLAY_QUESTION_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "resume"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_answered"}, "resume": QUESTION_RESUME_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "question_ref"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_answered_not_resumable"}, "question_ref": QUESTION_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "question_answered_not_resumable"}, "batch_ref": BATCH_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "batch_ref"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": True}, "outcome": {"type": "string", "const": "batch_superseded"}, "batch_ref": BATCH_REF_SCHEMA}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "outcome", "error", "recovery"], "properties": {"schema": {"type": "string", "const": "cortex/question-management/v11"}, "ok": {"type": "boolean", "const": False}, "outcome": {"type": "string", "const": "needs_correction"}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA}},
    ],
}
WORKER_COMPLETION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "terminal"], "properties": {"schema": {"type": "string", "const": "cortex/worker-completion/v11"}, "ok": {"type": "boolean", "const": True}, "terminal": {"type": "boolean", "const": True}}},
        {"type": "object", "additionalProperties": False, "required": ["schema", "ok", "error", "recovery"], "properties": {"schema": {"type": "string", "const": "cortex/worker-completion/v11"}, "ok": {"type": "boolean", "const": False}, "error": ERROR_SCHEMA, "recovery": RECOVERY_SCHEMA}},
    ]
}

# The public MCP boundary is intentionally flatter than the private v11
# lifecycle families above.  Keep one closed schema with scalar action and do
# the small success/failure dependency checks in ``validate_response`` so the
# advertised tool surface needs no response union or discriminator wrapper.
FLAT_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["path", "op", "expected"],
    "properties": {
        "path": _string(pattern=r"^(?:|(?:/(?:[^~/]|~[01])*)+)$", minimum=0, maximum=2048),
        "op": _string(enum=["add", "replace", "remove"], maximum=16),
        "expected": _string(minimum=0, maximum=8192),
    },
}
FLAT_DISPATCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["dispatch_ref", "task_name", "message", "fork_turns"],
    "properties": {
        "dispatch_ref": _string(pattern=DISPATCH_REF_PATTERN, maximum=33),
        "task_name": _string(maximum=256), "message": _string(maximum=65536),
        "fork_turns": _string(maximum=16), "model": _string(maximum=64),
        "reasoning_effort": _string(maximum=32),
    },
}
FLAT_PUBLIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["ok", "action", "retryable", "state_mutated"],
    "properties": {
        "ok": {"type": "boolean"}, "action": _string(enum=list(PUBLIC_ACTIONS), maximum=64),
        "retryable": {"type": "boolean"}, "state_mutated": {"type": "boolean"},
        "task_ref": _string(pattern=TASK_REF_PATTERN, maximum=160),
        "coordinator_ref": _string(pattern=COORDINATOR_REF_PATTERN, maximum=64),
        "step": {"type": "integer", "minimum": 1},
        "dispatches": {"type": "array", "maxItems": 8, "items": FLAT_DISPATCH_SCHEMA},
        "content": _string(minimum=0, maximum=65_536), "report": _string(minimum=0, maximum=65_536),
        "next_cursor": _string(pattern=r"^c11p\.[A-Za-z0-9_-]{16,512}$", maximum=517),
        "question_ref": _string(maximum=256), "request_id": _string(maximum=256),
        "choices": {"type": "array", "maxItems": 32, "items": _string(maximum=4096)},
        "result_refs": {"type": "array", "maxItems": 32, "items": _string(pattern=RESULT_REF_PATTERN, maximum=180)},
        "receipt_ref": _string(maximum=256), "digest": _string(maximum=256),
        "terminal": {"type": "boolean"}, "error_code": _string(maximum=160),
        "error": _string(maximum=2000),
        "allowed_changes": {"type": "array", "maxItems": MAX_DIAGNOSTICS, "items": FLAT_CHANGE_SCHEMA},
        "repair_capsule": _string(pattern=REPAIR_HANDLE_PATTERN, maximum=128),
        "base_payload_digest": _string(pattern=DIGEST_PATTERN, maximum=71),
        "repair_changes": {"type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS, "items": FLAT_CHANGE_SCHEMA},
    },
}

PUBLIC_RESPONSE_SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {
    "public.flat": FLAT_PUBLIC_RESPONSE_SCHEMA,
}
_PRIVATE_RESPONSE_SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {
    "private.coordinator.lifecycle": LIFECYCLE_RESPONSE_SCHEMA,
    "private.coordinator.start": START_RESPONSE_SCHEMA,
    "private.result.read": RESULT_READ_SCHEMA,
    "private.worker.briefing": BRIEFING_READ_SCHEMA,
    "private.worker.event": WORKER_EVENT_SCHEMA,
    "private.worker.question": WORKER_QUESTION_SCHEMA,
    "private.coordinator.question_management": COORDINATOR_QUESTION_MANAGEMENT_SCHEMA,
    "private.worker.completion": WORKER_COMPLETION_SCHEMA,
}


def response_schema(name: str) -> dict[str, Any]:
    """Return a defensive copy of one public response schema."""
    try:
        return copy.deepcopy(PUBLIC_RESPONSE_SCHEMA_REGISTRY[name])
    except KeyError as exc:
        raise KeyError(f"unknown v11 response schema: {name}") from exc


def _pointer(path: str) -> str:
    if path == "$":
        return ""
    parts = path[2:].replace("]", "").replace("[", ".").split(".")
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts if part)


def _diagnostic(path: str, message: str) -> dict[str, Any]:
    return {"code": "response_schema_invalid", "json_pointer": _pointer(path), "message": message, "field_schema": {"type": "object"}}


def _validate(value: Any, schema: Mapping[str, Any], path: str, diagnostics: list[dict[str, Any]]) -> None:
    variants = schema.get("oneOf")
    if isinstance(variants, list):
        matching = 0
        variant_diagnostics: list[list[dict[str, Any]]] = []
        for variant in variants:
            variant_errors: list[dict[str, Any]] = []
            _validate(value, variant, path, variant_errors)
            variant_diagnostics.append(variant_errors)
            if not variant_errors:
                matching += 1
        if matching != 1:
            # Project the narrowest candidate's own field diagnostics rather
            # than replacing useful validation evidence with a generic oneOf.
            viable = [item for item in variant_diagnostics if item]
            if viable:
                best = min(viable, key=len)
                diagnostics.extend(best)
            else:
                diagnostics.append(_diagnostic(path, "response outcome has no executable branch"))
        return
    expected = schema.get("type")
    valid_type = {
        "object": isinstance(value, Mapping), "array": isinstance(value, list), "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
    }
    if expected in valid_type and not valid_type[expected]:
        diagnostics.append(_diagnostic(path, f"must be a {expected}"))
        return
    if "const" in schema and value != schema["const"]:
        diagnostics.append(_diagnostic(path, "must equal the required constant"))
    if "enum" in schema and value not in schema["enum"]:
        diagnostics.append(_diagnostic(path, "must be one of the allowed values"))
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)) or len(value) > int(schema.get("maxLength", 2**31 - 1)):
            diagnostics.append(_diagnostic(path, "has an invalid length"))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            diagnostics.append(_diagnostic(path, "has an invalid format"))
    if isinstance(value, int) and not isinstance(value, bool) and value < int(schema.get("minimum", -(2**63))):
        diagnostics.append(_diagnostic(path, "is below the minimum"))
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)) or len(value) > int(schema.get("maxItems", 2**31 - 1)):
            diagnostics.append(_diagnostic(path, "has an invalid item count"))
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", diagnostics)
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return
        if len(value) < int(schema.get("minProperties", 0)) or len(value) > int(schema.get("maxProperties", 2**31 - 1)):
            diagnostics.append(_diagnostic(path, "has an invalid property count"))
        for key in schema.get("required", []):
            if key not in value:
                diagnostics.append(_diagnostic(f"{path}.{key}", "is required"))
        unknown_keys = sorted(set(value) - set(properties))
        additional = schema.get("additionalProperties")
        if additional is False:
            for key in unknown_keys:
                diagnostics.append(_diagnostic(f"{path}.{key}", "is not allowed"))
        elif isinstance(additional, Mapping):
            for key in unknown_keys:
                _validate(value[key], additional, f"{path}.{key}", diagnostics)
        for key, child in properties.items():
            if key in value and isinstance(child, Mapping):
                _validate(value[key], child, f"{path}.{key}", diagnostics)


def _validate_registered_response(schema: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deep-copy against one already-selected schema."""
    if not isinstance(value, Mapping):
        raise ResponseValidationError([_diagnostic("$", "must be an object")])
    normalized = copy.deepcopy(dict(value))
    diagnostics: list[dict[str, Any]] = []
    _validate(normalized, schema, "$", diagnostics)
    if diagnostics:
        raise ResponseValidationError(diagnostics)
    return normalized


def validate_private_response(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one active private engine receipt; never use for MCP output."""
    try:
        schema = _PRIVATE_RESPONSE_SCHEMA_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown private v11 response schema: {name}") from exc
    return _validate_registered_response(schema, value)


def validate_response(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deep-copy one selected public response family."""
    if not isinstance(value, Mapping):
        raise ResponseValidationError([_diagnostic("$", "must be an object")])
    normalized = copy.deepcopy(dict(value))
    diagnostics: list[dict[str, Any]] = []
    try:
        schema = PUBLIC_RESPONSE_SCHEMA_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown public v11 response schema: {name}") from exc
    _validate(normalized, schema, "$", diagnostics)
    if name == "public.flat" and not diagnostics:
        if normalized["ok"] is False:
            if normalized["action"] not in PUBLIC_FAILURE_ACTIONS:
                diagnostics.append(_diagnostic("$.action", "must be a canonical failure action"))
            for field in ("error_code", "error"):
                if field not in normalized:
                    diagnostics.append(_diagnostic(f"$.{field}", "is required on failure"))
            if normalized["action"] == "repair_patch_only":
                for field in ("repair_capsule", "base_payload_digest", "repair_changes"):
                    if field not in normalized:
                        diagnostics.append(_diagnostic(f"$.{field}", "is required for repair"))
            elif normalized["action"] == "retry_same_operation" and not normalized.get("allowed_changes"):
                diagnostics.append(_diagnostic("$.allowed_changes", "is required for a retryable failure"))
        else:
            if normalized["action"] not in PUBLIC_SUCCESS_ACTIONS:
                diagnostics.append(_diagnostic("$.action", "must be a canonical success action"))
            if normalized["retryable"]:
                diagnostics.append(_diagnostic("$.retryable", "must be false on success"))
    if diagnostics:
        raise ResponseValidationError(diagnostics)
    return normalized


__all__ = [
    "COORDINATOR_REF_PATTERN", "DIGEST_PATTERN", "PATCH_CONTRACT",
    "PUBLIC_RESPONSE_SCHEMA_REGISTRY", "ResponseValidationError", "TASK_REF_PATTERN", "FLAT_PUBLIC_RESPONSE_SCHEMA",
    "PUBLIC_ACTIONS", "PUBLIC_FAILURE_ACTIONS", "PUBLIC_SUCCESS_ACTIONS",
    "response_schema", "validate_private_response", "validate_response",
]
