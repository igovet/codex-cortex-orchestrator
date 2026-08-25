"""Durable plain-text worker question and answer transport."""
from __future__ import annotations

import re
from typing import Any, Mapping

from cortex_runtime import attempt_protocol
from cortex_runtime.ledger_db import _governance_lifecycle_hmac_key
from cortex_runtime.pagination import CURSOR_PATTERN, decode_cursor, encode_cursor, page_utf8_text, scope_digest
from cortex_runtime.public_contracts import backend_schema_for
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.core.runtime_bindings import bind_symbols


bind_symbols("questions", globals(), (
    "AWAITING_HOST_SPAWN", "MAX_QUESTIONS_PER_ATTEMPT", "MAX_QUESTIONS_PER_TASK",
    "PUBLIC_ORCHESTRATION_SCHEMA", "QUESTION_SCHEMA", "_attempt", "_question_records",
    "_question_sequence", "_task_document_root", "_write_question_record",
    "append_journal_best_effort", "authorize", "authorize_principal",
    "authorize_worker_assignment", "digest_text", "ledger_root", "load_state", "now",
    "question_bus_paths", "redact", "safe_id", "state_lock",
))


_QUESTION_REF_PATTERN = r"^question-[A-Za-z0-9._:-]{1,160}$"
_DURABLE_QUESTION_FIELDS = {
    "question_ref", "task_id", "attempt_id", "dispatch_ref", "profile",
    "task_revision", "attempt_generation", "submission_id", "question_text",
    "status", "content_digest", "published_sequence", "answer_text",
    "answer_submission_id", "answer_digest", "answered_sequence", "created_at",
    "answered_at", "superseded_at",
}


def _question_json_pointer(path: str) -> str:
    source = str(path or "").strip()
    if source in {"", "$"}:
        return ""
    source = source.removeprefix("$.").removeprefix("$").lstrip(".")
    return "/" + "/".join(
        part.replace("~", "~0").replace("/", "~1")
        for part in source.split(".") if part
    )


def _question_diagnostic(
    path: str,
    message: str,
    field_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    field = str(path).rsplit(".", 1)[-1]
    schema = dict(field_schema or {"type": "object"})
    if message == "unsupported worker_question field":
        schema = {"type": "object", "additionalProperties": False}
    diagnostic: dict[str, Any] = {
        "code": "worker_question_request_invalid",
        "json_pointer": _question_json_pointer(path),
        "message": redact(message, 300),
        "field_schema": schema,
    }
    if str(schema.get("format") or "").startswith("cortex-"):
        diagnostic["value_source"] = "cortex"
    return diagnostic


def _dedupe_question_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        pointer = str(item.get("json_pointer") or "")
        if pointer not in seen:
            seen.add(pointer)
            result.append(item)
    return result


def _question_schema(action: object) -> dict[str, Any]:
    """Derive the backend form from the selected canonical MCP contract."""
    import cortex as runtime
    registry = getattr(runtime, "PUBLIC_CONTRACTS", None)
    arguments = {"action": action} if isinstance(action, str) else None
    return backend_schema_for(registry, "worker_question", arguments) if isinstance(registry, Mapping) else {
        "type": "object", "additionalProperties": False, "properties": {}, "required": [],
    }


def _validate_public_question_request(params: Mapping[str, Any]) -> str:
    diagnostics: list[dict[str, Any]] = []
    action = params.get("action")
    schema = _question_schema(action)
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    known = set(properties)
    for field in sorted(set(params) - known):
        diagnostics.append(_question_diagnostic(f"$.{field}", "unsupported worker_question field"))
    for field in schema.get("required", []) if isinstance(schema.get("required"), list) else []:
        if field not in params:
            diagnostics.append(_question_diagnostic(
                f"$.{field}", f"{field} is required for this operation",
                properties.get(field) if isinstance(properties.get(field), Mapping) else None,
            ))
    for field, value in params.items():
        field_schema = properties.get(field)
        if not isinstance(field_schema, Mapping):
            continue
        if field_schema.get("type") == "string" and not isinstance(value, str):
            diagnostics.append(_question_diagnostic(f"$.{field}", f"{field} must be a string", field_schema))
            continue
        if isinstance(value, str):
            minimum = field_schema.get("minLength")
            pattern = field_schema.get("pattern")
            if isinstance(minimum, int) and len(value) < minimum:
                diagnostics.append(_question_diagnostic(f"$.{field}", f"{field} is too short", field_schema))
            elif isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                diagnostics.append(_question_diagnostic(f"$.{field}", f"{field} has an invalid format", field_schema))
        if "const" in field_schema and value != field_schema["const"]:
            diagnostics.append(_question_diagnostic(f"$.{field}", f"{field} differs from the selected operation", field_schema))
        enum = field_schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            diagnostics.append(_question_diagnostic(f"$.{field}", f"{field} is outside the advertised enum", field_schema))
    if diagnostics:
        raise ValidationFailure(_dedupe_question_diagnostics(diagnostics))
    return str(action)


def _attempt_generation(attempt: Mapping[str, Any]) -> int:
    try:
        return max(1, int(attempt.get("attempt_generation") or 1))
    except (TypeError, ValueError):
        return 1


def _question_is_stale(record: Mapping[str, Any], state: Mapping[str, Any], attempt: Mapping[str, Any]) -> bool:
    if record.get("status") != "open":
        return False
    return (
        int(record.get("task_revision") or 1) < int(state.get("task_revision") or 1)
        or int(record.get("attempt_generation") or 1) < _attempt_generation(attempt)
        or bool(attempt.get("invalidated"))
    )


def _supersede_question(task_dir: Any, state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if record.get("status") == "open":
        record.update({"status": "superseded", "superseded_at": now()})
        _write_question_record(task_dir, state, record)
    return record


def _supersede_stale_questions(task_dir: Any, state: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for record in records:
        attempt = _attempt(state, safe_id(str(record.get("attempt_id") or "")))
        if _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)


def _validate_question_record(record: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    if set(record) != _DURABLE_QUESTION_FIELDS:
        raise ValueError("durable question record has unsupported columns")
    if record.get("task_id") != state.get("task_id"):
        raise ValueError("durable question task binding is invalid")
    if not isinstance(record.get("question_text"), str) or not record["question_text"]:
        raise ValueError("durable question_text is invalid")
    if re.fullmatch(_QUESTION_REF_PATTERN, str(record.get("question_ref") or "")) is None:
        raise ValueError("durable question_ref is invalid")
    if record.get("status") not in {"open", "answered", "superseded"}:
        raise ValueError("durable question status is invalid")
    if record.get("status") == "answered" and (
        not isinstance(record.get("answer_text"), str) or not record["answer_text"]
    ):
        raise ValueError("answered durable question has no answer_text")


def _record_question_created_event(root: Any, state: dict[str, Any], record: Mapping[str, Any]) -> None:
    question_ref = safe_id(str(record["question_ref"]))
    attempt_protocol.record_system_event(
        root, task_id=str(state["task_id"]), attempt_id=safe_id(str(record["attempt_id"])),
        event_type="question_created", event_key=f"question_created:{question_ref}",
        payload={
            "question_ref": question_ref,
            "question_text": redact(record["question_text"], 1000),
            "task_revision": int(record.get("task_revision") or 1),
            "created_at": record.get("created_at"),
        },
    )


def _record_question_answer_events(root: Any, state: dict[str, Any], record: Mapping[str, Any]) -> None:
    question_ref = safe_id(str(record["question_ref"]))
    payload = {
        "question_ref": question_ref,
        "question_text": redact(record["question_text"], 1000),
        "answer_text": redact(record.get("answer_text") or "", 1000),
        "answered_at": record.get("answered_at"),
    }
    common = {"task_id": str(state["task_id"]), "attempt_id": safe_id(str(record["attempt_id"])), "payload": payload}
    attempt_protocol.record_system_event(root, event_type="question_answered", event_key=f"question_answered:{question_ref}", **common)
    attempt_protocol.record_system_event(root, event_type="decision_resolved", event_key=f"decision_resolved:{question_ref}", **common)


def publish_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one exact Unicode question_text bound to one worker dispatch."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        facade_worker = bool(params.get("_facade_worker"))
        if facade_worker:
            authorize(state, {"project_root": params.get("project_root"), "principal": state.get("principal")})
        else:
            authorize(state, params)
        attempt_id = safe_id(str(params.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        allowed = {AWAITING_HOST_SPAWN, "running"} if facade_worker else {"running"}
        if attempt.get("invalidated") or attempt.get("status") not in allowed:
            raise ValueError("cannot publish a question for an invalidated or terminal attempt")
        if facade_worker and (
            not attempt.get("facade_managed")
            or str(attempt.get("dispatch_ref") or "") != str(params.get("dispatch_ref") or "")
        ):
            raise ValueError("worker question dispatch does not match this active attempt")
        question_text = params.get("question_text")
        if not isinstance(question_text, str) or not question_text:
            raise ValueError("question_text is required")
        submission_id = safe_id(str(params.get("submission_id") or ""))
        content_digest = digest_text(question_text)
        records = _question_records(question_bus_paths(task_dir), state)
        _supersede_stale_questions(task_dir, state, records)
        existing = next((item for item in records if item.get("attempt_id") == attempt_id and item.get("submission_id") == submission_id), None)
        if existing is not None:
            if existing.get("content_digest") != content_digest or existing.get("question_text") != question_text:
                raise ValueError("idempotent question submission_id was reused with different question_text")
            _record_question_created_event(root, state, existing)
            return {"idempotent": True, "question": existing, "cursor": _question_sequence(records)}
        task_revision = int(state.get("task_revision") or 1)
        generation = _attempt_generation(attempt)
        active_revision = [item for item in records if int(item.get("task_revision") or 1) == task_revision and item.get("status") != "superseded"]
        active_attempt = [item for item in active_revision if item.get("attempt_id") == attempt_id and int(item.get("attempt_generation") or 1) == generation]
        if len(active_revision) >= MAX_QUESTIONS_PER_TASK:
            raise ValueError("question count quota exhausted for the active task revision")
        if len(active_attempt) >= MAX_QUESTIONS_PER_ATTEMPT:
            raise ValueError("question count quota exhausted for the active attempt generation")
        question_ref = "question-" + digest_text("\0".join((str(state["task_id"]), attempt_id, submission_id)))[:24]
        sequence = _question_sequence(records) + 1
        record = {
            "question_ref": question_ref, "task_id": state["task_id"], "attempt_id": attempt_id,
            "dispatch_ref": str(attempt.get("dispatch_ref") or ""), "profile": str(attempt.get("profile") or ""),
            "task_revision": task_revision, "attempt_generation": generation,
            "submission_id": submission_id, "question_text": question_text,
            "status": "open", "content_digest": content_digest, "published_sequence": sequence,
            "answer_text": None, "answer_submission_id": None, "answer_digest": None,
            "answered_sequence": None, "created_at": now(), "answered_at": None, "superseded_at": None,
        }
        _validate_question_record(record, state)
        _write_question_record(task_dir, state, record)
        _record_question_created_event(root, state, record)
        append_journal_best_effort(task_dir, "worker_question", f"{attempt_id} published {question_ref}")
        return {"idempotent": False, "question": record, "cursor": sequence}


def _poll_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        attempt_id = safe_id(str(params.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        question_ref = safe_id(str(params.get("question_ref") or ""))
        records = _question_records(question_bus_paths(task_dir), state)
        record = next((item for item in records if item.get("question_ref") == question_ref), None)
        if record is None or record.get("attempt_id") != attempt_id or record.get("dispatch_ref") != params.get("dispatch_ref"):
            raise ValueError("question_ref is not bound to this authorized worker dispatch")
        if _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)
        if record.get("status") == "superseded":
            return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_superseded", "question_ref": question_ref}
        if record.get("status") != "answered":
            return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "awaiting_user", "question_ref": question_ref}
        return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_answered", "question_ref": question_ref, "answer_text": record["answer_text"]}


def _flat_question_page(result: dict[str, Any], cursor: object, *, secret: bytes, audience: str) -> dict[str, Any]:
    if result.get("outcome") != "question_answered":
        if cursor is not None:
            raise ValueError("cursor is valid only after the question is answered")
        return result
    answer_text = result.get("answer_text")
    if not isinstance(answer_text, str):
        raise ValueError("answered question has no answer_text")
    question_ref = str(result.get("question_ref") or "")
    binding = scope_digest({"question_ref": question_ref, "answer_text": answer_text})
    offset = 0 if cursor is None else decode_cursor(cursor, secret, selector="worker_question.poll", audience=audience, digest=binding)
    content, next_offset, complete = page_utf8_text(answer_text, offset, maximum_bytes=8_192)
    paged = {key: value for key, value in result.items() if key != "answer_text"}
    paged["content"] = content
    if not complete:
        paged["next_cursor"] = encode_cursor(secret, selector="worker_question.poll", audience=audience, digest=binding, offset=next_offset)
    return paged


def _flat_worker_question_facade(params: dict[str, Any]) -> dict[str, Any]:
    original = dict(params) if isinstance(params, dict) else {}
    action = _validate_public_question_request(original)
    project, task_dir, state, attempt, profile = authorize_worker_assignment(original, "worker_question")
    internal = {
        "action": action, "dispatch_ref": original["dispatch_ref"], "project_root": str(project),
        "task_id": state["task_id"], "attempt_id": attempt["attempt_id"], "profile": profile,
    }
    if action == "ask":
        question_text = original["question_text"]
        result = publish_worker_question({
            **internal, "question_text": question_text,
            "submission_id": safe_id(f"public-{attempt['attempt_id']}-question-{digest_text(question_text)[:24]}"),
            "_facade_worker": True,
        })
        return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_recorded", "question_ref": result["question"]["question_ref"], "idempotent": bool(result.get("idempotent"))}
    root = _task_document_root(task_dir, state["task_id"])
    return _flat_question_page(
        _poll_worker_question({**internal, "question_ref": original["question_ref"]}), original.get("cursor"),
        secret=_governance_lifecycle_hmac_key(root, create=False), audience=f"worker:{attempt['attempt_id']}",
    )


def worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Return the closed public plain-text worker-question state."""
    from cortex_runtime.mcp_api import project_public_response
    try:
        result = _flat_worker_question_facade(params)
    except (ValidationFailure, ValueError, TypeError, OSError, RuntimeError) as exc:
        raw = getattr(exc, "diagnostics", None)
        diagnostics = raw if isinstance(raw, list) and raw else [{
            "code": "worker_question_request_invalid", "json_pointer": "",
            "message": redact(str(exc) or "invalid worker_question request", 300), "field_schema": {"type": "object"},
        }]
        result = {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "needs_correction",
            "code": "worker_question_request_invalid", "diagnostics": _dedupe_question_diagnostics([dict(item) for item in diagnostics if isinstance(item, dict)]), "retryable": True,
        }
    return project_public_response("worker_question", result, arguments=params)


def _question_record_view(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record)


def list_worker_questions(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize_principal(state, params)
        records = _question_records(question_bus_paths(task_dir), state)
        _supersede_stale_questions(task_dir, state, records)
        attempt_id, status = str(params.get("attempt_id") or ""), str(params.get("status") or "")
        selected = [item for item in records if (not attempt_id or item.get("attempt_id") == attempt_id) and (not status or item.get("status") == status)]
        return {
            "schema": QUESTION_SCHEMA, "task_id": state["task_id"], "questions": [_question_record_view(item) for item in selected],
            "cursor": _question_sequence(records), "open_count": sum(item.get("status") == "open" for item in records),
            "open_question_refs": [item["question_ref"] for item in records if item.get("status") == "open"],
        }


def answer_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one exact Unicode answer_text for one durable question_ref."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        question_ref = safe_id(str(params.get("question_ref") or ""))
        submission_id = safe_id(str(params.get("submission_id") or ""))
        answer_text = params.get("answer_text")
        if not isinstance(answer_text, str) or not answer_text:
            raise ValueError("answer_text is required")
        records = _question_records(question_bus_paths(task_dir), state)
        record = next((item for item in records if item.get("question_ref") == question_ref), None)
        if record is None:
            raise ValueError("question_ref does not belong to this task")
        attempt = _attempt(state, safe_id(str(record.get("attempt_id") or "")))
        if _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)
        if record.get("status") == "superseded":
            return {"schema": QUESTION_SCHEMA, "status": "superseded", "question_ref": question_ref, "idempotent": False, "question": record, "cursor": _question_sequence(records)}
        answer_digest = digest_text(answer_text)
        if record.get("status") == "answered":
            if record.get("answer_submission_id") != submission_id:
                raise ValueError("worker question has already been answered")
            if record.get("answer_digest") != answer_digest or record.get("answer_text") != answer_text:
                raise ValueError("idempotent answer submission_id was reused with different answer_text")
            _record_question_answer_events(root, state, record)
            return {"schema": QUESTION_SCHEMA, "status": "answered", "question_ref": question_ref, "idempotent": True, "question": record, "cursor": _question_sequence(records)}
        record.update({
            "status": "answered", "answer_text": answer_text, "answer_submission_id": submission_id,
            "answer_digest": answer_digest, "answered_sequence": _question_sequence(records) + 1, "answered_at": now(),
        })
        _write_question_record(task_dir, state, record)
        _record_question_answer_events(root, state, record)
        append_journal_best_effort(task_dir, "worker_answer", f"{question_ref} answered for {record['attempt_id']}")
        return {"schema": QUESTION_SCHEMA, "status": "answered", "question_ref": question_ref, "idempotent": False, "question": record, "cursor": record["answered_sequence"]}


def _question_record_for_main(params: dict[str, Any], question_ref: str) -> dict[str, Any]:
    listed = list_worker_questions({"task_id": params["task_id"], "principal": params["principal"], "project_root": params.get("project_root")})
    record = next((item for item in listed["questions"] if item.get("question_ref") == question_ref), None)
    if record is None:
        raise ValueError("question_ref does not belong to this task")
    return _question_record_view(record)


def cortex_question(params: dict[str, Any]) -> dict[str, Any]:
    """Show one exact durable question in ordinary chat."""
    task_id, principal = str(params.get("task_id") or ""), str(params.get("principal") or "")
    question_ref = safe_id(str(params.get("question_ref") or ""))
    if not task_id or not principal or not question_ref:
        raise ValueError("cortex.question requires task_id, principal, and question_ref")
    record = _question_record_for_main(params, question_ref)
    if record.get("status") == "superseded":
        return {"schema": QUESTION_SCHEMA, "status": "superseded", "question_ref": question_ref, "question": record}
    if record.get("status") == "answered":
        return {"schema": QUESTION_SCHEMA, "status": "answered", "question_ref": question_ref, "question": record}
    return {"schema": QUESTION_SCHEMA, "status": "pending_user_message", "question_ref": question_ref, "question_text": record["question_text"], "question": record}


def get_worker_question_updates(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    attempt_id = safe_id(str(params.get("attempt_id") or "")); _attempt(state, attempt_id)
    after_sequence = int(params.get("after_sequence") or 0)
    if after_sequence < 0:
        raise ValueError("after_sequence must be nonnegative")
    records = [item for item in _question_records(question_bus_paths(task_dir), state) if item.get("attempt_id") == attempt_id]
    updates: list[dict[str, Any]] = []
    for record in records:
        if int(record["published_sequence"]) > after_sequence:
            updates.append({"sequence": record["published_sequence"], "kind": "question_published", "question_ref": record["question_ref"], "status": record["status"], "created_at": record["created_at"]})
        if record.get("answered_sequence") and int(record["answered_sequence"]) > after_sequence:
            updates.append({"sequence": record["answered_sequence"], "kind": "question_answered", "question_ref": record["question_ref"], "answer_text": record["answer_text"], "answered_at": record["answered_at"]})
    updates.sort(key=lambda item: int(item["sequence"]))
    return {"schema": QUESTION_SCHEMA, "task_id": state["task_id"], "attempt_id": attempt_id, "after_sequence": after_sequence, "updates": updates, "next_sequence": _question_sequence(records)}


__all__ = ["answer_worker_question", "cortex_question", "get_worker_question_updates", "list_worker_questions", "publish_worker_question", "worker_question"]
