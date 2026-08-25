"""Pure validation and same-attempt repair primitives for the v11 worker API.

The module deliberately has no ledger, host, session, transport, or persistence
dependency.  A caller resolves ``task_ref`` / ``assignment_ref`` separately and
may use these functions to validate a public submission before it performs any
state transition.  Rejected drafts live only in the server's private escrow;
the public ``repair_capsule`` field carries a fixed-size signed lookup handle.
Every transformation deep-copies its source and returns a new submission.
"""
from __future__ import annotations

import copy
import hmac
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


TASK_REF_PATTERN = r"^task-[0-9a-f]{12}$"
ASSIGNMENT_REF_PATTERN = r"^assignment-v1-[0-9a-f]{64}$"
COORDINATOR_REF_PATTERN = r"^[0-9a-f]{64}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
REPAIR_HANDLE_PATTERN = r"^v11rh1\.[A-Za-z0-9_-]{22}\.[0-9a-f]{32}$"
REPAIR_HANDLE_LENGTH = 62
MAX_ITEMS = 32
MAX_WORK_PACKAGES = 32
MAX_MICROTASKS_PER_PACKAGE = 32
MAX_MICROTASKS_PER_PLAN = 128


class ValidationFailure(ValueError):
    """Side-effect-free validation failure with normalized diagnostics."""

    def __init__(self, diagnostics: Sequence[Mapping[str, Any]]) -> None:
        self.diagnostics = [copy.deepcopy(dict(item)) for item in diagnostics]
        super().__init__("; ".join(str(item.get("message") or "validation failed") for item in self.diagnostics))


_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
    },
}

_MICROTASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "title": {"type": "string", "minLength": 1, "maxLength": 500},
        "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
        "profile": {"type": "string", "minLength": 1, "maxLength": 128},
        "allowed_paths": {"type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 512}},
        "depends_on": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 128}},
        "acceptance_criteria": {"type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 2000}},
        "verification": {"type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 2000}},
    },
}

_PACKAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "title", "objective", "allowed_paths", "microtasks"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "title": {"type": "string", "minLength": 1, "maxLength": 500},
        "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
        "allowed_paths": {"type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 512}},
        "depends_on": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 128}},
        "microtasks": {"type": "array", "minItems": 1, "maxItems": MAX_MICROTASKS_PER_PACKAGE, "items": _MICROTASK_SCHEMA},
    },
}

_RECOMMENDATION_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issue", "action", "plan_refs", "verification"],
    "properties": {
        "issue": {"type": "string", "minLength": 1, "maxLength": 2000},
        "action": {"type": "string", "minLength": 1, "maxLength": 4000},
        "plan_refs": {
            "type": "array", "maxItems": MAX_ITEMS,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "verification": {"type": "string", "minLength": 1, "maxLength": 4000},
    },
}

_REQUIREMENT_COVERAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement", "plan_refs", "verification", "status"],
    "properties": {
        "requirement": {"type": "string", "minLength": 1, "maxLength": 4000},
        "plan_refs": {
            "type": "array", "minItems": 1, "maxItems": MAX_ITEMS,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "verification": {
            "type": "array", "minItems": 1, "maxItems": MAX_ITEMS,
            "items": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "status": {"type": "string", "const": "covered"},
    },
}

PLANNER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["overview", "work_packages"],
    "properties": {
        "overview": {"type": "string", "minLength": 1, "maxLength": 8000},
        "work_packages": {"type": "array", "minItems": 1, "maxItems": MAX_WORK_PACKAGES, "items": _PACKAGE_SCHEMA},
        "requirement_coverage": {"type": "array", "maxItems": MAX_ITEMS, "items": _REQUIREMENT_COVERAGE_SCHEMA},
        "recommendation": {"type": "string", "enum": ["approve", "revise"]},
        "recommendation_rationale": {"type": "string", "maxLength": 4000},
        "recommendation_actions": {"type": "array", "maxItems": MAX_ITEMS, "items": _RECOMMENDATION_ACTION_SCHEMA},
        "resolved_questions": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 4000}},
        "risks": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "minLength": 1, "maxLength": 4000}},
    },
}

OUTCOME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 8000},
        "findings": {"type": "array", "maxItems": MAX_ITEMS, "items": _CARD_SCHEMA},
        "decisions_needed": {"type": "array", "maxItems": MAX_ITEMS, "items": _CARD_SCHEMA},
        "unresolved": {"type": "array", "maxItems": MAX_ITEMS, "items": _CARD_SCHEMA},
        "claims": {"type": "array", "maxItems": MAX_ITEMS, "items": _CARD_SCHEMA},
    },
}

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["op", "path"],
    "properties": {
        "op": {
            "type": "string", "enum": ["add", "replace", "remove"],
            "description": "Use exactly add, replace, or remove. Any other value is a retryable unchanged repair.",
        },
        "path": {"type": "string", "pattern": r"^/.*"},
        "value": {},
    },
}

PUBLIC_SUBMISSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_ref", "assignment_ref"],
    "description": (
        "Choose exactly one branch. A planner assignment on the plan gate requires `plan`; "
        "every non-planner assignment requires `outcome`. After the server returns a repair "
        "capsule, only the repair branch is accepted until that exact patch succeeds."
    ),
    "properties": {
        "task_ref": {"type": "string", "pattern": TASK_REF_PATTERN},
        "assignment_ref": {"type": "string", "pattern": ASSIGNMENT_REF_PATTERN},
        "plan": PLANNER_PLAN_SCHEMA,
        "outcome": OUTCOME_SCHEMA,
        "base_payload_digest": {
            "type": "string", "pattern": DIGEST_PATTERN,
            "description": "Copy the exact digest returned with the repair handle; never recompute or alter it.",
        },
        "patches": {
            "type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": PATCH_SCHEMA,
            "description": (
                "Non-empty RFC6902 patches limited to the exact paths returned with this repair handle. "
                "An empty or invalid patch is retryable and leaves the pending repair unchanged."
            ),
        },
        "repair_capsule": {
            "type": "string", "minLength": REPAIR_HANDLE_LENGTH,
            "maxLength": REPAIR_HANDLE_LENGTH, "pattern": REPAIR_HANDLE_PATTERN,
            "description": "Copy this opaque fixed-size server handle exactly from repair.repair_capsule; never decode, reconstruct, summarize, or manually transcribe it.",
        },
    },
    "oneOf": [
        {
            "title": "Planner assignment: plan branch",
            "description": "Required for profile=planner on gate=plan; outcome and repair fields are forbidden.",
            "required": ["task_ref", "assignment_ref", "plan"],
            "not": {"anyOf": [
                {"required": ["outcome"]}, {"required": ["repair_capsule"]},
                {"required": ["base_payload_digest"]}, {"required": ["patches"]},
            ]},
        },
        {
            "title": "Non-planner assignment: outcome branch",
            "description": "Required for every non-planner assignment; plan and repair fields are forbidden.",
            "required": ["task_ref", "assignment_ref", "outcome"],
            "not": {"anyOf": [
                {"required": ["plan"]}, {"required": ["repair_capsule"]},
                {"required": ["base_payload_digest"]}, {"required": ["patches"]},
            ]},
        },
        {
            "title": "Pending repair: patch-only branch",
            "description": (
                "Use only after complete_attempt returns repair. Copy the exact capsule and digest, "
                "omit plan/outcome, and patch only the returned paths."
            ),
            "required": [
                "task_ref", "assignment_ref", "repair_capsule", "base_payload_digest", "patches",
            ],
            "not": {"anyOf": [{"required": ["plan"]}, {"required": ["outcome"]}]},
        },
    ],
}

PUBLIC_SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {
    "v11.submit": PUBLIC_SUBMISSION_SCHEMA,
    "v11.plan": PLANNER_PLAN_SCHEMA,
    "v11.outcome": OUTCOME_SCHEMA,
    "v11.patch": PATCH_SCHEMA,
}


def canonical_digest(value: Any) -> str:
    """Return the stable digest used to bind a repair to its rejected draft."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def json_pointer(path: object) -> str:
    """Convert dotted or JSONPath-ish diagnostics to an RFC 6901 pointer."""
    raw = str(path or "$").strip()
    if raw in {"", "$"}:
        return ""
    if raw.startswith("/"):
        return raw
    raw = raw[2:] if raw.startswith("$.") else raw.lstrip("$.")
    # Only numeric bracket notation is structural.  Treating every ``[`` and
    # ``]`` as an array separator corrupts literal model-authored property
    # names such as ``bad[0]/~key`` and can make an advertised repair pointer
    # target a path that does not exist in the rejected draft.
    raw = re.sub(r"\[([0-9]+)\]", r".\1", raw)
    parts: list[str] = []
    for segment in raw.split("."):
        if segment:
            parts.append(segment.replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts)


def _pointer_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("patch path must be an RFC6901 JSON pointer")
    if re.search(r"~(?:[^01]|$)", path):
        raise ValueError("patch path must be an RFC6901 JSON pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _path_parts(path: object) -> list[str]:
    pointer = json_pointer(path)
    return _pointer_parts(pointer) if pointer else []


def schema_for_path(schema: Mapping[str, Any], path: object, *, aliases: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Project the closest public schema node for an actionable diagnostic."""
    parts = _path_parts(path)
    if aliases and parts and parts[0] in aliases:
        parts = _path_parts(aliases[parts[0]]) + parts[1:]
    selected: Any = schema
    for part in parts:
        if not isinstance(selected, Mapping):
            break
        properties = selected.get("properties")
        if isinstance(properties, Mapping) and part in properties:
            selected = properties[part]
        elif isinstance(selected.get("items"), Mapping) and part.isdigit():
            selected = selected["items"]
        else:
            break
    return copy.deepcopy(dict(selected)) if isinstance(selected, Mapping) else {"type": "object"}


def normalize_diagnostics(
    diagnostics: Sequence[Mapping[str, Any]],
    *,
    schema: Mapping[str, Any] = PUBLIC_SUBMISSION_SCHEMA,
) -> list[dict[str, Any]]:
    """Return stable, path-aware public diagnostics without mutating inputs."""
    normalized: list[dict[str, Any]] = []
    for item in diagnostics:
        copied = copy.deepcopy(dict(item))
        path = str(copied.get("path") or "$")
        copied["path"] = path
        supplied_pointer = copied.get("json_pointer")
        copied["json_pointer"] = (
            str(supplied_pointer)
            if isinstance(supplied_pointer, str) and (
                supplied_pointer == "" or supplied_pointer.startswith("/")
            )
            else json_pointer(path)
        )
        copied.setdefault("code", "validation_invalid")
        copied.setdefault("message", "invalid value")
        copied["field_schema"] = schema_for_path(schema, copied["json_pointer"] or path)
        normalized.append(copied)
    return normalized


def _issue(
    diagnostics: list[dict[str, Any]],
    path: str,
    message: str,
    code: str = "validation_invalid",
    *,
    pointer: str | None = None,
) -> None:
    item = {"code": code, "path": path, "message": message}
    if pointer is not None:
        item["json_pointer"] = pointer
    diagnostics.append(item)


def _validate_schema(value: Any, schema: Mapping[str, Any], path: str, diagnostics: list[dict[str, Any]]) -> None:
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
    }
    if expected in matches and not matches[expected]:
        _issue(diagnostics, path, f"must be a {expected}")
        return
    if "enum" in schema and value not in schema["enum"]:
        _issue(diagnostics, path, "must be one of the allowed values")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            _issue(diagnostics, path, "must not be empty")
        if len(value) > int(schema.get("maxLength", 2**31 - 1)):
            _issue(diagnostics, path, "is too long")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            _issue(diagnostics, path, "has an invalid format")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            _issue(diagnostics, path, "must not be empty")
        if len(value) > int(schema.get("maxItems", 2**31 - 1)):
            _issue(diagnostics, path, "has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, f"{path}[{index}]", diagnostics)
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return
        for required in schema.get("required", []):
            if required not in value:
                _issue(diagnostics, f"{path}.{required}", "is required", "validation_required")
        if schema.get("additionalProperties") is False:
            for key in sorted(set(value) - set(properties)):
                parent_pointer = json_pointer(path)
                escaped_key = str(key).replace("~", "~0").replace("/", "~1")
                _issue(
                    diagnostics,
                    f"{path}.{key}",
                    "is not allowed",
                    "validation_unknown",
                    pointer=f"{parent_pointer}/{escaped_key}",
                )
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, Mapping):
                _validate_schema(value[key], child_schema, f"{path}.{key}", diagnostics)


def _plan_microtask_limit(plan: Mapping[str, Any], diagnostics: list[dict[str, Any]]) -> None:
    packages = plan.get("work_packages")
    if not isinstance(packages, list):
        return
    total = sum(len(item.get("microtasks", [])) for item in packages if isinstance(item, Mapping) and isinstance(item.get("microtasks"), list))
    if total > MAX_MICROTASKS_PER_PLAN:
        _issue(diagnostics, "$.plan.work_packages", f"contains more than {MAX_MICROTASKS_PER_PLAN} total microtasks")


def validate_submission(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deep-copy one closed v11 full or repair submission.

    No identity is inferred here.  The returned ``mode`` is ``full`` or
    ``repair``; a full submission additionally has ``kind`` of ``plan`` or
    ``outcome``.  Any error is aggregated before raising ``ValidationFailure``.
    """
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(payload, Mapping):
        raise ValidationFailure(normalize_diagnostics([{"path": "$", "message": "must be an object"}]))
    _validate_schema(payload, PUBLIC_SUBMISSION_SCHEMA, "$", diagnostics)
    has_plan = "plan" in payload
    has_outcome = "outcome" in payload
    has_digest = "base_payload_digest" in payload
    has_patches = "patches" in payload
    has_capsule = "repair_capsule" in payload
    full = has_plan or has_outcome
    repair = has_digest or has_patches or has_capsule
    if full and repair:
        _issue(diagnostics, "$", "full submission and repair fields are mutually exclusive", "validation_branch")
    elif not full and not repair:
        _issue(diagnostics, "$", "provide exactly one plan, outcome, or digest-bound patch repair", "validation_branch")
    elif full:
        if has_plan == has_outcome:
            _issue(diagnostics, "$", "provide exactly one of plan or outcome", "validation_branch")
        if has_plan and isinstance(payload.get("plan"), Mapping):
            _plan_microtask_limit(payload["plan"], diagnostics)
    else:
        if not (has_digest and has_patches and has_capsule):
            _issue(diagnostics, "$", "repair requires repair_capsule, base_payload_digest, and patches", "validation_branch")
    if diagnostics:
        raise ValidationFailure(normalize_diagnostics(diagnostics))
    result = copy.deepcopy(dict(payload))
    result["mode"] = "repair" if repair else "full"
    if full:
        result["kind"] = "plan" if has_plan else "outcome"
    return result


def _target_from_submission(submission: Mapping[str, Any]) -> tuple[str, Any]:
    if "plan" in submission:
        return "plan", submission["plan"]
    if "outcome" in submission:
        return "outcome", submission["outcome"]
    raise ValueError("rejected-draft escrow requires a full submission")


def create_rejected_draft_escrow(
    submission: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create immutable private repair input from a rejected v11 draft.

    The semantic payload is intentionally *not* fully validated here: this
    function is called precisely after that validation rejected it.  The public
    envelope and explicit identity are still fail-closed before an escrow row is
    made, and the repaired reconstruction is fully validated by
    :func:`apply_repair_escrow`.
    """
    if not isinstance(submission, Mapping):
        raise ValueError("rejected-draft escrow requires an object submission")
    permitted = {"task_ref", "assignment_ref", "plan", "outcome"}
    if set(submission) - permitted:
        raise ValueError("rejected-draft escrow permits only a full v11 submission")
    task_ref = submission.get("task_ref")
    assignment_ref = submission.get("assignment_ref")
    if not isinstance(task_ref, str) or re.fullmatch(TASK_REF_PATTERN, task_ref) is None:
        raise ValueError("rejected-draft task_ref is invalid")
    if not isinstance(assignment_ref, str) or re.fullmatch(ASSIGNMENT_REF_PATTERN, assignment_ref) is None:
        raise ValueError("rejected-draft assignment_ref is invalid")
    has_plan = "plan" in submission
    has_outcome = "outcome" in submission
    if has_plan == has_outcome:
        raise ValueError("rejected-draft escrow requires exactly one plan or outcome")
    kind, target = _target_from_submission(submission)
    if not isinstance(target, Mapping):
        raise ValueError("rejected-draft semantic payload must be an object")
    prefix = f"/{kind}"
    normalized = normalize_diagnostics(diagnostics)
    scoped: list[dict[str, Any]] = []
    for item in normalized:
        pointer = str(item["json_pointer"])
        if pointer == prefix:
            item["repair_pointer"] = "/"
        elif pointer.startswith(prefix + "/"):
            item["repair_pointer"] = pointer[len(prefix):]
        else:
            # Identity and envelope errors cannot be repaired by a semantic patch.
            continue
        scoped.append(item)
    if not scoped:
        raise ValueError("rejected-draft diagnostics contain no repairable semantic paths")
    return {
        "schema": "cortex/private-repair-draft/v1",
        "task_ref": task_ref,
        "assignment_ref": assignment_ref,
        "kind": kind,
        "base_payload_digest": canonical_digest(target),
        "payload": copy.deepcopy(target),
        "diagnostics": scoped,
    }


def _repair_handle_key(secret: bytes | bytearray | memoryview) -> bytes:
    key = bytes(secret)
    if len(key) < 32:
        raise ValueError("repair handle signing key must be at least 32 bytes")
    return key


def repair_handle_id(token: str) -> str:
    """Return the structurally valid random id from one current handle."""
    if not isinstance(token, str) or re.fullmatch(REPAIR_HANDLE_PATTERN, token) is None:
        raise ValueError("repair handle is malformed")
    return token.split(".", 2)[1]


def repair_handle_digest(handle_id: str) -> str:
    """Return the private lookup digest for one 128-bit random handle id."""
    if re.fullmatch(r"[A-Za-z0-9_-]{22}", str(handle_id or "")) is None:
        raise ValueError("repair handle id is malformed")
    return hashlib.sha256(handle_id.encode("ascii")).hexdigest()


def sign_repair_handle(
    handle_id: str,
    escrow_digest: str,
    secret: bytes | bytearray | memoryview,
) -> str:
    """Return the fixed-size public handle bound to one immutable escrow row."""
    repair_handle_digest(handle_id)
    if re.fullmatch(r"[0-9a-f]{64}", str(escrow_digest or "")) is None:
        raise ValueError("repair escrow digest is invalid")
    message = (
        b"cortex/v11-repair-handle/v1\0"
        + handle_id.encode("ascii")
        + b"\0"
        + escrow_digest.encode("ascii")
    )
    # A 128-bit MAC remains cryptographically strong while halving the former
    # 64-character tag that models damaged during native-tool transcription.
    signature = hmac.new(_repair_handle_key(secret), message, hashlib.sha256).hexdigest()[:32]
    token = f"v11rh1.{handle_id}.{signature}"
    if len(token) != REPAIR_HANDLE_LENGTH:
        raise RuntimeError("repair handle has an invalid length")
    return token


def verify_repair_handle(
    token: str,
    escrow_digest: str,
    secret: bytes | bytearray | memoryview,
) -> str:
    """Verify one handle against the digest of its server-side escrow row."""
    handle_id = repair_handle_id(token)
    expected = sign_repair_handle(handle_id, escrow_digest, secret)
    if not hmac.compare_digest(token, expected):
        raise ValueError("repair handle integrity check failed")
    return repair_handle_digest(handle_id)


def changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Return deterministic RFC6901 leaf paths changed by a deep-copy repair."""
    if type(before) is not type(after):
        return [prefix or "/"]
    if isinstance(before, Mapping):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list):
        paths = []
        for index in range(max(len(before), len(after))):
            child = f"{prefix}/{index}"
            if index >= len(before) or index >= len(after):
                paths.append(child)
            else:
                paths.extend(changed_paths(before[index], after[index], child))
        return paths
    return [] if before == after else [prefix or "/"]


def diagnostic_scope_allows(diagnostics: Sequence[Mapping[str, Any]], paths: Sequence[str]) -> bool:
    """Allow only a diagnostic JSON Pointer or a descendant of one."""
    scopes = [str(item.get("repair_pointer") or item.get("json_pointer") or "") for item in diagnostics]
    scopes = [scope for scope in scopes if scope.startswith("/")]
    return bool(scopes) and all(any(scope == "/" or path == scope or path.startswith(scope + "/") for scope in scopes) for path in paths)


def _apply_patches(value: Any, patches: Sequence[Mapping[str, Any]]) -> Any:
    repaired = copy.deepcopy(value)
    for patch in patches:
        op = patch.get("op")
        if op not in {"add", "replace", "remove"}:
            raise ValueError("patch op must be add, replace, or remove")
        tokens = _pointer_parts(str(patch.get("path") or ""))
        if not tokens:
            raise ValueError("patches may not replace the complete semantic payload")
        cursor: Any = repaired
        for token in tokens[:-1]:
            if isinstance(cursor, list):
                if not token.isdigit() or int(token) >= len(cursor):
                    raise ValueError("patch path does not exist")
                cursor = cursor[int(token)]
            elif isinstance(cursor, dict) and token in cursor:
                cursor = cursor[token]
            else:
                raise ValueError("patch path does not exist")
        leaf = tokens[-1]
        if isinstance(cursor, list):
            if op == "add" and leaf == "-":
                cursor.append(copy.deepcopy(patch.get("value")))
            elif leaf.isdigit() and int(leaf) < len(cursor):
                if op == "remove":
                    cursor.pop(int(leaf))
                else:
                    cursor[int(leaf)] = copy.deepcopy(patch.get("value"))
            else:
                raise ValueError("patch list index does not exist")
        elif isinstance(cursor, dict):
            if op == "remove":
                if leaf not in cursor:
                    raise ValueError("patch field does not exist")
                del cursor[leaf]
            elif op == "replace" and leaf not in cursor:
                raise ValueError("patch field does not exist")
            else:
                cursor[leaf] = copy.deepcopy(patch.get("value"))
        else:
            raise ValueError("patch parent is not an object or array")
    return repaired


def apply_repair_escrow(
    escrow: Mapping[str, Any],
    repair: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a validated repaired full submission without mutating either input.

    The task and assignment refs in the repair must exactly match the immutable
    escrow.  Patch paths are rooted at the semantic payload, so they cannot
    mutate identity or envelope fields.
    """
    candidate = validate_submission(repair)
    if candidate["mode"] != "repair":
        raise ValueError("repair escrow requires a digest-bound patch submission")
    required = {"schema", "task_ref", "assignment_ref", "kind", "base_payload_digest", "payload", "diagnostics"}
    if not required.issubset(escrow):
        raise ValueError("rejected-draft escrow is invalid")
    if escrow.get("schema") != "cortex/private-repair-draft/v1":
        raise ValueError("rejected-draft escrow schema is unsupported")
    if candidate["task_ref"] != escrow["task_ref"] or candidate["assignment_ref"] != escrow["assignment_ref"]:
        raise ValueError("repair identity does not match the rejected draft")
    base = escrow["payload"]
    digest = canonical_digest(base)
    if candidate["base_payload_digest"] != escrow["base_payload_digest"] or digest != escrow["base_payload_digest"]:
        raise ValueError("repair base_payload_digest is stale or does not match the rejected draft")
    patches = candidate["patches"]
    paths = [str(patch["path"]) for patch in patches]
    # Validate pointer syntax before comparing scopes.  Otherwise malformed
    # escape sequences are misreported as merely out-of-scope and the public
    # repair card cannot tell the caller which property is actually invalid.
    for path in paths:
        _pointer_parts(path)
    if not diagnostic_scope_allows(escrow["diagnostics"], paths):
        raise ValueError("repair patch path is outside the diagnosed semantic scope")
    repaired = _apply_patches(base, patches)
    if not diagnostic_scope_allows(escrow["diagnostics"], changed_paths(base, repaired)):
        raise ValueError("repair changed a path outside the diagnosed semantic scope")
    full = {"task_ref": escrow["task_ref"], "assignment_ref": escrow["assignment_ref"], str(escrow["kind"]): repaired}
    return validate_submission(full)
