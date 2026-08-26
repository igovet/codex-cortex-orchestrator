"""Durable plain-text worker question and answer transport."""
from __future__ import annotations

import re
from typing import Any, Mapping

from cortex_runtime import attempt_protocol, ledger_db
from cortex_runtime.ledger_db import (
    count_durable_questions,
    durable_question_content_digest,
    durable_question_sequence,
    _governance_lifecycle_hmac_key,
    get_durable_question,
    get_durable_question_submission,
    load_task as load_durable_task,
    page_durable_question_updates,
    page_durable_questions,
)
from cortex_runtime.pagination import CURSOR_PATTERN, decode_cursor, encode_cursor, page_utf8_text, scope_digest
from cortex_runtime.public_contracts import backend_schema_for, is_internal_question_category
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.core.runtime_bindings import bind_symbols


bind_symbols("questions", globals(), (
    "AWAITING_HOST_SPAWN", "MAX_QUESTIONS_PER_ATTEMPT", "MAX_QUESTIONS_PER_TASK",
    "PUBLIC_ORCHESTRATION_SCHEMA", "QUESTION_SCHEMA", "_attempt",
    "_task_document_root", "_write_question_record",
    "append_journal_best_effort", "authorize", "authorize_principal",
    "authorize_worker_assignment", "digest_text", "ledger_root", "load_state", "now",
    "question_bus_paths", "redact", "safe_id", "save_state", "state_lock",
))


_QUESTION_REF_PATTERN = r"^question-[A-Za-z0-9._:-]{1,160}$"
_DURABLE_QUESTION_FIELDS = {
    "question_ref", "task_id", "attempt_id", "dispatch_ref", "profile",
    "task_revision", "attempt_generation", "submission_id", "question_category", "question_text",
    "status", "content_digest", "published_sequence", "answer",
    "answer_submission_id", "answer_digest", "answered_sequence", "created_at",
    "answered_at", "superseded_at",
}

class InternalQuestionCategoryError(ValueError):
    """A technical condition that must never become a durable user stop."""


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


def permitted_question_categories() -> tuple[str, ...]:
    """Read the only permitted user-stop categories from the public MCP schema."""
    schema = _question_schema("ask")
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    category_schema = properties.get("question_category") if isinstance(properties, Mapping) else None
    values = category_schema.get("enum") if isinstance(category_schema, Mapping) else None
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise RuntimeError("ask_worker_question category schema is unavailable")
    return tuple(values)


def question_record_is_permitted_user_stop(record: Mapping[str, Any]) -> bool:
    """Return whether one durable row may legally pause for a real user."""
    return (
        record.get("status") == "open"
        and record.get("question_category") in permitted_question_categories()
    )


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


def _iter_question_pages(root: Any, task_id: str, *, attempt_id: str = "", status: str = ""):
    """Yield bounded durable-question pages; never load the task collection at once."""
    offset = 0
    while True:
        page, has_more = page_durable_questions(
            root, task_id, offset=offset, limit=64, attempt_id=attempt_id, status=status,
        )
        if not page:
            return
        yield page
        if not has_more:
            return
        offset += len(page)


def _validate_question_record(record: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    if set(record) != _DURABLE_QUESTION_FIELDS:
        raise ValueError("durable question record has unsupported columns")
    if record.get("task_id") != state.get("task_id"):
        raise ValueError("durable question task binding is invalid")
    if not isinstance(record.get("question_text"), str) or not record["question_text"]:
        raise ValueError("durable question_text is invalid")
    if record.get("question_category") not in permitted_question_categories():
        raise ValueError("durable question category is not a permitted user decision")
    if re.fullmatch(_QUESTION_REF_PATTERN, str(record.get("question_ref") or "")) is None:
        raise ValueError("durable question_ref is invalid")
    if record.get("status") not in {"open", "answered", "superseded"}:
        raise ValueError("durable question status is invalid")
    if record.get("status") == "answered" and (
        not isinstance(record.get("answer"), str) or not record["answer"]
    ):
        raise ValueError("answered durable question has no answer")


def _authorized_question_attempt(
    record: Mapping[str, Any], state: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one durable question to its exact canonical task assignment."""
    _validate_question_record(record, state)
    attempt = _attempt(state, safe_id(str(record.get("attempt_id") or "")))
    if not (
        str(record.get("dispatch_ref") or "")
        == str(attempt.get("dispatch_ref") or "")
        and str(record.get("profile") or "")
        == str(attempt.get("profile") or attempt.get("agent") or "")
    ):
        raise ValueError("durable question assignment binding is invalid")
    return attempt


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
        "answer": redact(record.get("answer") or "", 1000),
        "answered_at": record.get("answered_at"),
    }
    common = {"task_id": str(state["task_id"]), "attempt_id": safe_id(str(record["attempt_id"])), "payload": payload}
    attempt_protocol.record_system_event(root, event_type="question_answered", event_key=f"question_answered:{question_ref}", **common)
    attempt_protocol.record_system_event(root, event_type="decision_resolved", event_key=f"decision_resolved:{question_ref}", **common)


def _load_authorized_question_state(
    root: Any,
    task_dir: Any,
    task_id: object,
) -> tuple[Any, dict[str, Any]]:
    """Reload one exact worker-authorized task without public workspace selection."""
    loaded = load_durable_task(root, safe_id(str(task_id or "")))
    if loaded is None:
        raise ValueError("worker question task is unavailable")
    _task, state, _plan, artifact_dir = loaded
    expected_task_dir = root / str(artifact_dir)
    if expected_task_dir.resolve() != task_dir.resolve():
        raise ValueError("worker question task binding is invalid")
    return task_dir, state


def publish_worker_question(
    params: dict[str, Any],
    *,
    authorized_root: Any = None,
    authorized_task_dir: Any = None,
) -> dict[str, Any]:
    """Persist one exact Unicode question_text bound to one worker dispatch."""
    facade_worker = bool(params.get("_facade_worker"))
    if facade_worker:
        if authorized_root is None or authorized_task_dir is None:
            raise ValueError("worker question internal ledger context is unavailable")
        root = authorized_root
    else:
        root = ledger_root(params)
    with state_lock(root):
        if facade_worker:
            task_dir, state = _load_authorized_question_state(
                root, authorized_task_dir, params.get("task_id"),
            )
        else:
            _, task_dir, state = load_state(str(params["task_id"]), params)
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
        question_category = params.get("question_category")
        if is_internal_question_category(question_category):
            raise InternalQuestionCategoryError(
                "Internal technical conditions cannot be recorded as user questions. End this native turn "
                "without a question so the coordinator can invoke read_worker_wave and the server-owned "
                "compiler/reconciler recovery path."
            )
        if question_category not in permitted_question_categories():
            raise ValueError("question_category is outside the advertised user-decision enum")
        submission_id = safe_id(str(params.get("submission_id") or ""))
        content_digest = durable_question_content_digest(str(question_category), question_text)
        existing = get_durable_question_submission(root, str(state["task_id"]), attempt_id, submission_id)
        for page in _iter_question_pages(root, str(state["task_id"]), attempt_id=attempt_id):
            _supersede_stale_questions(task_dir, state, page)
        if existing is None:
            existing = get_durable_question_submission(root, str(state["task_id"]), attempt_id, submission_id)
        if existing is not None:
            if (
                existing.get("content_digest") != content_digest
                or existing.get("question_category") != question_category
                or existing.get("question_text") != question_text
            ):
                raise ValueError("idempotent question submission_id was reused with different question content")
            _authorized_question_attempt(existing, state)
            _record_question_created_event(root, state, existing)
            return {"idempotent": True, "question": existing, "cursor": durable_question_sequence(root, str(state["task_id"]))}
        task_revision = int(state.get("task_revision") or 1)
        generation = _attempt_generation(attempt)
        active_revision_count = count_durable_questions(
            root, str(state["task_id"]), task_revision=task_revision, include_superseded=False,
            categories=permitted_question_categories(),
        )
        active_attempt_count = count_durable_questions(
            root, str(state["task_id"]), attempt_id=attempt_id,
            task_revision=task_revision, attempt_generation=generation,
            include_superseded=False,
            categories=permitted_question_categories(),
        )
        if active_revision_count >= MAX_QUESTIONS_PER_TASK:
            raise ValueError("question count quota exhausted for the active task revision")
        if active_attempt_count >= MAX_QUESTIONS_PER_ATTEMPT:
            raise ValueError("question count quota exhausted for the active attempt generation")
        question_ref = "question-" + digest_text("\0".join((str(state["task_id"]), attempt_id, submission_id)))[:24]
        sequence = durable_question_sequence(root, str(state["task_id"])) + 1
        record = {
            "question_ref": question_ref, "task_id": state["task_id"], "attempt_id": attempt_id,
            "dispatch_ref": str(attempt.get("dispatch_ref") or ""), "profile": str(attempt.get("profile") or ""),
            "task_revision": task_revision, "attempt_generation": generation,
            "submission_id": submission_id, "question_category": question_category,
            "question_text": question_text,
            "status": "open", "content_digest": content_digest, "published_sequence": sequence,
            "answer": None, "answer_submission_id": None, "answer_digest": None,
            "answered_sequence": None, "created_at": now(), "answered_at": None, "superseded_at": None,
        }
        _authorized_question_attempt(record, state)
        _write_question_record(task_dir, state, record)
        _record_question_created_event(root, state, record)
        append_journal_best_effort(task_dir, "worker_question", f"{attempt_id} published {question_ref}")
        return {"idempotent": False, "question": record, "cursor": sequence}


def _answer_resume_worker_session(
    root: Any,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and return only this attempt's exact existing session."""
    task_id = str(state.get("task_id") or "")
    attempt_id = str(attempt.get("attempt_id") or "")
    generation = int(attempt.get("worker_host_session_generation") or 1)
    sessions = [
        item for item in ledger_db.list_worker_sessions(root, task_id)
        if str(item.get("attempt_id") or "") == attempt_id
        and int(item.get("generation") or 1) == generation
    ]
    if len(sessions) != 1:
        raise ValueError("answered question resume has no exact existing worker session")
    session = sessions[0]
    if str(session.get("status") or "") not in {"running", "stopped_recoverable"}:
        raise ValueError("answered question resume worker session is not resumable")
    return session


def _resume_worker_session_after_answer_poll(root: Any, session: Mapping[str, Any]) -> bool:
    """Reopen the already-validated session without selecting another child."""
    if str(session.get("status") or "") == "stopped_recoverable":
        ledger_db.put_worker_session(root, {
            **session,
            "status": "running",
            "resumable": True,
            "terminated_at": None,
        })
        return True
    return False


def _acknowledge_answered_question_resume(
    root: Any,
    task_dir: Any,
    state: dict[str, Any],
    attempt: dict[str, Any],
    record: Mapping[str, Any],
) -> None:
    """Fence one same-attempt resume through its authorized answer poll.

    Native follow-up turns do not always emit another SubagentStart. The exact
    dispatch-bound poll is therefore the server-observed acknowledgement that
    the already-authorized same child received its durable answer. It may only
    consume the coordinator-issued offer for the same question and prior Stop;
    it never selects or creates an attempt, worker, or session.
    """
    question_ref = str(record.get("question_ref") or "")
    offer = attempt.get("native_question_resume_offer")
    if not isinstance(offer, Mapping) or str(offer.get("question_ref") or "") != question_ref:
        raise ValueError("answered question has no exact same-child resume offer")
    session = _answer_resume_worker_session(root, state, attempt)
    acknowledgement = attempt.get("native_question_resume_acknowledgement")
    if isinstance(acknowledgement, Mapping):
        if (
            str(acknowledgement.get("question_ref") or "") != question_ref
            or int(acknowledgement.get("offer_stop_sequence") or 0)
            != int(offer.get("stop_sequence") or 0)
            or int(acknowledgement.get("offer_session_generation") or 1)
            != int(offer.get("session_generation") or 1)
        ):
            raise ValueError("answered question resume acknowledgement conflicts with its offer")
        _resume_worker_session_after_answer_poll(root, session)
        return

    offer_generation = int(offer.get("session_generation") or 1)
    current_generation = int(attempt.get("worker_host_session_generation") or 1)
    evidence = attempt.get("native_incomplete_stop_evidence")
    paused_without_new_start = (
        attempt.get("status") == "waiting_question"
        and attempt.get("lifecycle_status") == "paused_awaiting_user"
        and attempt.get("host_stop_outcome") == "awaiting_user"
        and isinstance(evidence, Mapping)
        and evidence.get("observed") is True
        and int(evidence.get("session_generation") or 1) == offer_generation
        and int(offer.get("stop_sequence") or 0)
        in {0, int(evidence.get("sequence") or 0)}
        and current_generation == offer_generation
    )
    resumed_by_observed_start = (
        attempt.get("status") == "running"
        and attempt.get("lifecycle_status") == "running"
        and not isinstance(evidence, Mapping)
        and current_generation == offer_generation + 1
    )
    if not (paused_without_new_start or resumed_by_observed_start):
        raise ValueError("answered question resume does not match the authorized same-child lifecycle")

    # Reopen the already-validated existing session first. If the following
    # state commit fails, the attempt remains paused and a replay converges
    # safely from this running session; the worker receives no answer receipt
    # and therefore cannot submit against a partially committed transition.
    session_reopened = _resume_worker_session_after_answer_poll(root, session)
    attempt_before = dict(attempt)
    task_status_before = state.get("status")
    attempt["native_question_resume_acknowledgement"] = {
        "question_ref": question_ref,
        "offer_stop_sequence": int(offer.get("stop_sequence") or 0),
        "offer_session_generation": offer_generation,
        "acknowledged_session_generation": current_generation,
        "acknowledged_at": now(),
    }
    attempt["status"] = "running"
    attempt["lifecycle_status"] = "running"
    attempt["host_resumable"] = True
    attempt.pop("native_incomplete_stop_evidence", None)
    attempt.pop("host_stopped_at", None)
    attempt.pop("host_stop_outcome", None)
    if state.get("status") == "needs_input":
        state["status"] = "active"
    try:
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "worker_question_resume_acknowledged",
            "same authorized worker acknowledged its durable answer",
        )
    except Exception:
        attempt.clear()
        attempt.update(attempt_before)
        state["status"] = task_status_before
        if session_reopened:
            try:
                ledger_db.put_worker_session(root, dict(session))
            except Exception:
                # The paused task state remains authoritative and retryable;
                # a later exact poll revalidates and converges this session.
                pass
        raise


def _poll_worker_question(
    params: dict[str, Any],
    *,
    authorized_root: Any = None,
    authorized_task_dir: Any = None,
) -> dict[str, Any]:
    # This read is reachable only after the public facade has authenticated
    # the exact native assignment. Never reconstruct worker authority from
    # caller-supplied task/attempt/dispatch fields inside this helper.
    if authorized_root is None or authorized_task_dir is None:
        raise ValueError("worker question internal ledger context is unavailable")
    root = authorized_root
    with state_lock(root):
        task_dir, state = _load_authorized_question_state(
            root, authorized_task_dir, params.get("task_id"),
        )
        attempt_id = safe_id(str(params.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        question_ref = safe_id(str(params.get("question_ref") or ""))
        record = get_durable_question(root, str(state["task_id"]), question_ref)
        recovery_question = (
            isinstance(record, Mapping)
            and str(attempt.get("recovery_question_ref") or "") == question_ref
            and str(attempt.get("recovery_question_source_attempt_id") or "")
            == str(record.get("attempt_id") or "")
            and str(attempt.get("recovery_question_source_dispatch_ref") or "")
            == str(record.get("dispatch_ref") or "")
        )
        if record is None or (
            not recovery_question
            and (
                record.get("attempt_id") != attempt_id
                or record.get("dispatch_ref") != params.get("dispatch_ref")
            )
        ):
            raise ValueError("question_ref is not bound to this authorized worker dispatch")
        if not recovery_question:
            bound_attempt = _authorized_question_attempt(record, state)
            if str(bound_attempt.get("attempt_id") or "") != attempt_id:
                raise ValueError("question_ref is not bound to this authorized worker attempt")
        # A host-epoch replacement is deliberately a newer attempt reading
        # the exact durable question owned by its retired source. Its exact
        # server-issued source bindings above are authoritative; generation
        # staleness applies only to the original same-child path.
        if not recovery_question and _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)
        if record.get("status") == "superseded":
            return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_superseded", "question_ref": question_ref}
        if record.get("status") != "answered":
            return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "awaiting_user", "question_ref": question_ref}
        if recovery_question:
            attempt["recovery_question_answer_read_at"] = now()
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "recovery_question_answer_read",
                "replacement worker read the exact durable answer from its retired source assignment",
            )
        else:
            _acknowledge_answered_question_resume(root, task_dir, state, attempt, record)
        return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_answered", "question_ref": question_ref, "answer": record["answer"]}


def _flat_question_page(result: dict[str, Any], cursor: object, *, secret: bytes, audience: str) -> dict[str, Any]:
    if result.get("outcome") != "question_answered":
        if cursor is not None:
            raise ValueError("cursor is valid only after the question is answered")
        return result
    answer = result.get("answer")
    if not isinstance(answer, str):
        raise ValueError("answered question has no answer")
    question_ref = str(result.get("question_ref") or "")
    binding = scope_digest({"question_ref": question_ref, "answer": answer})
    offset = 0 if cursor is None else decode_cursor(cursor, secret, selector="worker_question.poll", audience=audience, digest=binding)
    content, next_offset, complete = page_utf8_text(answer, offset, maximum_bytes=8_192)
    paged = {key: value for key, value in result.items() if key != "answer"}
    paged["content"] = content
    if not complete:
        paged["next_cursor"] = encode_cursor(secret, selector="worker_question.poll", audience=audience, digest=binding, offset=next_offset)
    return paged


def _flat_worker_question_facade(params: dict[str, Any]) -> dict[str, Any]:
    original = dict(params) if isinstance(params, dict) else {}
    if original.get("action") == "ask" and is_internal_question_category(original.get("question_category")):
        raise InternalQuestionCategoryError(
            "Internal technical conditions cannot be recorded as user questions. End this native turn "
            "without a question so the coordinator can invoke read_worker_wave and the server-owned "
            "compiler/reconciler recovery path."
        )
    action = _validate_public_question_request(original)
    project, task_dir, state, attempt, profile = authorize_worker_assignment(original, "worker_question")
    internal = {
        "action": action, "dispatch_ref": original["dispatch_ref"], "project_root": str(project),
        "task_id": state["task_id"], "attempt_id": attempt["attempt_id"], "profile": profile,
    }
    root = _task_document_root(task_dir, state["task_id"])
    if action == "ask":
        question_category = original["question_category"]
        question_text = original["question_text"]
        result = publish_worker_question({
            **internal, "question_category": question_category, "question_text": question_text,
            "submission_id": safe_id(
                f"public-{attempt['attempt_id']}-question-"
                f"{digest_text(str(question_category) + chr(0) + question_text)[:24]}"
            ),
            "_facade_worker": True,
        }, authorized_root=root, authorized_task_dir=task_dir)
        return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "question_recorded", "question_ref": result["question"]["question_ref"], "idempotent": bool(result.get("idempotent"))}
    return _flat_question_page(
        _poll_worker_question(
            {**internal, "question_ref": original["question_ref"]},
            authorized_root=root,
            authorized_task_dir=task_dir,
        ), original.get("cursor"),
        secret=_governance_lifecycle_hmac_key(root, create=False), audience=f"worker:{attempt['attempt_id']}",
    )


def worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Return the closed public plain-text worker-question state."""
    from cortex_runtime.mcp_api import project_public_response
    try:
        result = _flat_worker_question_facade(params)
    except (ValidationFailure, ValueError, TypeError, OSError, RuntimeError) as exc:
        import cortex as runtime
        if isinstance(exc, runtime.WorkerAssignmentError):
            pending_identity = exc.code == "native_subagent_start_required"
            model_attestation_failure = exc.code.startswith("native_subagent_model_")
            result = {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": (
                    "native_identity_pending" if pending_identity else
                    "native_model_attestation_failed" if model_attestation_failure else
                    "dispatch_unavailable"
                ),
                "code": (
                    exc.code if pending_identity or model_attestation_failure
                    else "worker_dispatch_unavailable"
                ),
                "message": "Native worker authorization is unavailable; coordinator recovery is required.",
                "retryable": pending_identity,
                "state_mutated": False,
            }
            return project_public_response("worker_question", result, arguments=params)
        if isinstance(exc, InternalQuestionCategoryError):
            result = {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "technical_recovery_required",
                "code": "internal_worker_question_forbidden",
                "message": str(exc),
                "retryable": False,
                "state_mutated": False,
            }
            return project_public_response("worker_question", result, arguments=params)
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
    """Return only the semantic durable question contract to model-visible callers."""
    return {
        "question_ref": record.get("question_ref"),
        "status": record.get("status"),
        "question_text": record.get("question_text"),
        **({"answer": record.get("answer")} if record.get("status") == "answered" else {}),
        "created_at": record.get("created_at"),
        **({"answered_at": record.get("answered_at")} if record.get("answered_at") else {}),
    }


def list_worker_questions(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize_principal(state, params)
        attempt_id, status = str(params.get("attempt_id") or ""), str(params.get("status") or "")
        scope = {"task_id": state["task_id"], "attempt_id": attempt_id, "status": status}
        digest = scope_digest(scope)
        secret = _governance_lifecycle_hmac_key(ledger_root(params), create=False)
        cursor = params.get("cursor")
        offset = 0 if cursor is None else decode_cursor(
            cursor, secret, selector="questions.list", audience=f"task:{state['task_id']}", digest=digest,
        )
        root = ledger_root(params)
        selected, has_more = page_durable_questions(
            root, str(state["task_id"]), offset=offset, limit=64,
            attempt_id=attempt_id, status=status,
        )
        for item in selected:
            attempt = _authorized_question_attempt(item, state)
            if _question_is_stale(item, state, attempt):
                _supersede_question(task_dir, state, item)
        return {
            "schema": QUESTION_SCHEMA, "task_id": state["task_id"], "questions": [_question_record_view(item) for item in selected],
            "cursor": durable_question_sequence(root, str(state["task_id"])),
            "open_count": sum(question_record_is_permitted_user_stop(item) for item in selected),
            "open_question_refs": [item["question_ref"] for item in selected if question_record_is_permitted_user_stop(item)],
            **({"next_cursor": encode_cursor(secret, selector="questions.list", audience=f"task:{state['task_id']}", digest=digest, offset=offset + len(selected))} if has_more else {}),
        }


def answer_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one exact Unicode answer for one durable question_ref."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        question_ref = safe_id(str(params.get("question_ref") or ""))
        submission_id = safe_id(str(params.get("submission_id") or ""))
        answer = params.get("answer")
        if not isinstance(answer, str) or not answer:
            raise ValueError("answer is required")
        record = get_durable_question(root, str(state["task_id"]), question_ref)
        if record is None:
            raise ValueError("question_ref does not belong to this task")
        attempt = _authorized_question_attempt(record, state)
        if _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)
        if record.get("status") == "superseded":
            return {"schema": QUESTION_SCHEMA, "status": "superseded", "question_ref": question_ref, "idempotent": False, "question": _question_record_view(record), "cursor": durable_question_sequence(root, str(state["task_id"]))}
        answer_digest = digest_text(answer)
        if record.get("status") == "answered":
            if record.get("answer_submission_id") != submission_id:
                raise ValueError("worker question has already been answered")
            if record.get("answer_digest") != answer_digest or record.get("answer") != answer:
                raise ValueError("idempotent answer submission_id was reused with different answer")
            _record_question_answer_events(root, state, record)
            return {"schema": QUESTION_SCHEMA, "status": "answered", "question_ref": question_ref, "idempotent": True, "question": _question_record_view(record), "cursor": durable_question_sequence(root, str(state["task_id"]))}
        record.update({
            "status": "answered", "answer": answer, "answer_submission_id": submission_id,
            "answer_digest": answer_digest, "answered_sequence": durable_question_sequence(root, str(state["task_id"])) + 1, "answered_at": now(),
        })
        _write_question_record(task_dir, state, record)
        _record_question_answer_events(root, state, record)
        append_journal_best_effort(task_dir, "worker_answer", f"{question_ref} answered for {record['attempt_id']}")
        return {"schema": QUESTION_SCHEMA, "status": "answered", "question_ref": question_ref, "idempotent": False, "question": _question_record_view(record), "cursor": record["answered_sequence"]}


def _question_record_for_main(params: dict[str, Any], question_ref: str) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize_principal(state, params)
        record = get_durable_question(root, str(state["task_id"]), question_ref)
        if record is None:
            raise ValueError("question_ref does not belong to this authorized task")
        attempt = _authorized_question_attempt(record, state)
        if _question_is_stale(record, state, attempt):
            _supersede_question(task_dir, state, record)
        if not question_record_is_permitted_user_stop(record) and record.get("status") == "open":
            raise ValueError("question_ref is not an authorized user-decision stop")
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
    root = ledger_root(params)
    with state_lock(root):
        _, _task_dir, state = load_state(str(params["task_id"]), params)
        authorize_principal(state, params)
        attempt_id = safe_id(str(params.get("attempt_id") or "")); _attempt(state, attempt_id)
        after_sequence = int(params.get("after_sequence") or 0)
        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        records, has_more = page_durable_question_updates(
            root, str(state["task_id"]), attempt_id, after_sequence=after_sequence, limit=64,
        )
        updates: list[dict[str, Any]] = []
        for record in records:
            bound_attempt = _authorized_question_attempt(record, state)
            if str(bound_attempt.get("attempt_id") or "") != attempt_id:
                raise ValueError("question update is not bound to the authorized attempt")
            if int(record["published_sequence"]) > after_sequence:
                updates.append({"sequence": record["published_sequence"], "kind": "question_published", "question_ref": record["question_ref"], "status": record["status"], "created_at": record["created_at"]})
            if record.get("answered_sequence") and int(record["answered_sequence"]) > after_sequence:
                updates.append({"sequence": record["answered_sequence"], "kind": "question_answered", "question_ref": record["question_ref"], "answer": record["answer"], "answered_at": record["answered_at"]})
        updates.sort(key=lambda item: int(item["sequence"]))
        next_sequence = max(
            [after_sequence, *[int(item["sequence"]) for item in updates]],
        )
        return {
            "schema": QUESTION_SCHEMA, "task_id": state["task_id"], "attempt_id": attempt_id,
            "after_sequence": after_sequence, "updates": updates, "next_sequence": next_sequence,
            **({"has_more": True, "next_after_sequence": next_sequence} if has_more else {}),
        }


__all__ = [
    "answer_worker_question", "cortex_question", "get_worker_question_updates",
    "list_worker_questions", "permitted_question_categories", "publish_worker_question",
    "question_record_is_permitted_user_stop", "worker_question",
]
