"""Durable worker-question bus and MCP elicitation bridge."""
from __future__ import annotations

import json
import secrets
import sys
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols, bound_symbol


bind_symbols(
    "questions",
    globals(),
    (
        "AGENTS",
        "AWAITING_HOST_SPAWN",
        "MAX_QUESTIONS_PER_ATTEMPT",
        "MAX_QUESTIONS_PER_TASK",
        "PUBLIC_ORCHESTRATION_SCHEMA",
        "QUESTION_SCHEMA",
        "_attempt",
        "_question_config",
        "_question_options",
        "_question_payload",
        "_question_records",
        "_question_sequence",
        "_write_question_record",
        "append_journal_best_effort",
        "authorize",
        "authorize_principal",
        "canonical_profile",
        "digest_text",
        "ledger_root",
        "load_state",
        "now",
        "question_bus_paths",
        "redact",
        "respond",
        "safe_id",
        "sanitize_structured",
        "state_lock",
    ),
)

def publish_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        facade_worker = bool(params.get("_facade_worker"))
        if facade_worker:
            authorize(state, {
                "project_root": params.get("project_root"),
                "principal": state.get("principal"),
                "thread_id": state.get("thread_id"),
            })
        else:
            authorize(state, params)
        attempt_id = safe_id(str(params.get("attempt_id", "")))
        attempt = _attempt(state, attempt_id)
        allowed_statuses = {AWAITING_HOST_SPAWN, "running"} if facade_worker else {"running"}
        if facade_worker and (
            not attempt.get("facade_managed")
            or canonical_profile(params.get("profile") or "") != attempt.get("profile")
        ):
            raise ValueError("worker question identity does not match this facade-managed attempt")
        if attempt.get("invalidated") or attempt.get("status") not in allowed_statuses:
            raise ValueError("cannot publish a question for an invalidated or terminal attempt")
        submission_id = safe_id(str(params.get("submission_id", "")))
        question, context, blocking, config, content_digest = _question_payload(params)
        paths = question_bus_paths(task_dir)
        records = _question_records(paths, state)
        existing = next(
            (item for item in records if item.get("attempt_id") == attempt_id and item.get("submission_id") == submission_id),
            None,
        )
        if existing is not None:
            if existing.get("content_digest") != content_digest:
                raise ValueError("idempotent question submission_id was reused with different content")
            return {"idempotent": True, "question": existing, "cursor": _question_sequence(records)}
        if len(records) >= MAX_QUESTIONS_PER_TASK or sum(item.get("attempt_id") == attempt_id for item in records) >= MAX_QUESTIONS_PER_ATTEMPT:
            raise ValueError("question count quota exhausted")
        numbers = [int(str(item["question_id"]).removeprefix("question-")) for item in records]
        question_id = f"question-{max(numbers, default=0) + 1:04d}"
        sequence = _question_sequence(records) + 1
        record = {
            "schema": QUESTION_SCHEMA,
            "question_id": question_id,
            "task_id": state["task_id"],
            "gate": attempt["gate"],
            "attempt_id": attempt_id,
            "submission_id": submission_id,
            "profile": attempt["profile"],
            "question": question,
            "context": context,
            "blocking": blocking,
            "header": config["header"],
            "options": config["options"],
            "multiple": config["multiple"],
            "custom_label": config["custom_label"],
            "custom_response": True,
            "status": "open",
            "content_digest": content_digest,
            "published_sequence": sequence,
            "answer": None,
            "answer_text": None,
            "resume_context": None,
            "answer_submission_id": None,
            "answer_digest": None,
            "answered_sequence": None,
            "created_at": now(),
            "answered_at": None,
        }
        _write_question_record(task_dir, state, record)
        append_journal_best_effort(task_dir, "worker_question", f"{attempt_id} published {question_id}")
        return {"idempotent": False, "question": record, "cursor": sequence}


def worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Public facade adapter for durable ask/poll on one exact worker attempt."""
    action = str(params.get("action") or "").strip().lower()
    if action not in {"ask", "poll"}:
        raise ValueError("worker question action must be ask or poll")
    profile = canonical_profile(params.get("profile") or "")
    if profile not in AGENTS:
        raise ValueError("profile must be an exact Cortex worker profile")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params.get("task_id") or ""), params)
        attempt_id = safe_id(str(params.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        if (
            not attempt.get("facade_managed")
            or attempt.get("profile") != profile
            or attempt.get("invalidated")
            or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}
        ):
            raise ValueError("worker question identity does not match an active facade-managed attempt")
        if action == "ask":
            if str(params.get("question_ref") or "").strip():
                raise ValueError("ask must omit question_ref")
            question = str(params.get("question") or "").strip()
            if not question:
                raise ValueError("ask requires question")
            submission_id = safe_id(
                f"public-{attempt_id}-question-"
                + digest_text(json.dumps({
                    "question": question,
                    "context": params.get("context"),
                    "header": params.get("header"),
                    "options": params.get("options"),
                    "multiple": bool(params.get("multiple", False)),
                    "custom_label": params.get("custom_label"),
                }, ensure_ascii=False, sort_keys=True, default=str))[:16]
            )
            result = publish_worker_question({
                **params,
                "submission_id": submission_id,
                "blocking": True,
                "_facade_worker": True,
            })
            record = result["question"]
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": "question_recorded",
                "question_ref": record["question_id"],
                "status": record["status"],
                "idempotent": bool(result.get("idempotent")),
                "next_action": (
                    "Return only QUESTION_RECORDED question_ref=<value> plus a concise question summary to the "
                    "parent coordinator; remain available and do not record a report until this question is answered."
                ),
            }
        question_ref = safe_id(str(params.get("question_ref") or ""))
        if any(params.get(field) not in (None, "", [], {}) for field in (
            "question", "header", "options", "multiple", "custom_label", "context"
        )):
            raise ValueError("poll accepts only the question_ref and worker identity fields")
        records = _question_records(question_bus_paths(task_dir), state)
        record = next((item for item in records if item.get("question_id") == question_ref), None)
        if record is None or record.get("attempt_id") != attempt_id or record.get("profile") != profile:
            raise ValueError("question_ref does not belong to this worker attempt")
        if record.get("status") != "answered":
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": "awaiting_user",
                "question_ref": question_ref,
                "status": record.get("status"),
                "next_action": "Remain available; the parent coordinator must surface and answer this question.",
            }
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "question_answered",
            "question_ref": question_ref,
            "status": "answered",
            "answer": record.get("answer"),
            "answer_text": record.get("answer_text"),
            "resume_context": record.get("resume_context"),
            "next_action": "Resume this same worker attempt with the user's answer; record the report only after the mission is complete.",
        }


def _question_record_view(record: dict[str, Any]) -> dict[str, Any]:
    """Return a validated canonical question record."""
    if record.get("schema") != QUESTION_SCHEMA:
        raise ValueError("question record schema is not supported")
    return dict(record)


def _normalize_question_answer(value: object) -> tuple[Any, str]:
    """Keep structured host extensions (for example image attachments) intact."""
    if isinstance(value, (dict, list)):
        answer = sanitize_structured(value)
        answer_text = redact(json.dumps(answer, ensure_ascii=False, sort_keys=True), 8000)
    else:
        answer = redact(str(value or "").strip(), 8000)
        answer_text = answer
    return answer, answer_text


def list_worker_questions(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    all_records = _question_records(question_bus_paths(task_dir), state)
    records = list(all_records)
    attempt_id = str(params.get("attempt_id", "")).strip()
    if attempt_id:
        attempt_id = safe_id(attempt_id)
        _attempt(state, attempt_id)
        records = [item for item in records if item["attempt_id"] == attempt_id]
    requested_status = str(params.get("status", "")).strip()
    if requested_status:
        records = [item for item in records if item["status"] == requested_status]
    return {
        "schema": QUESTION_SCHEMA,
        "task_id": state["task_id"],
        "questions": [_question_record_view(item) for item in records],
        "cursor": _question_sequence(all_records),
        "open_count": sum(item.get("status") == "open" for item in all_records),
        "open_question_ids": [item["question_id"] for item in all_records if item.get("status") == "open"],
        "next_action": "answer each open question in published_sequence order; do not choose on the user's behalf" if any(item.get("status") == "open" for item in all_records) else "continue worker monitoring",
    }


def answer_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        question_id = safe_id(str(params.get("question_id", "")))
        submission_id = safe_id(str(params.get("submission_id", "")))
        answer, answer_text = _normalize_question_answer(params.get("answer"))
        resume_context = sanitize_structured(params.get("resume_context"))
        if not answer_text:
            raise ValueError("worker question answer is required")
        if resume_context in (None, "", [], {}):
            raise ValueError("worker question resume_context is required")
        answer_digest = digest_text(json.dumps({"answer": answer, "resume_context": resume_context}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        paths = question_bus_paths(task_dir)
        records = _question_records(paths, state)
        record = next((item for item in records if item.get("question_id") == question_id), None)
        if record is None:
            raise ValueError("question_id does not belong to this task")
        if record.get("status") == "answered":
            if record.get("answer_submission_id") != submission_id:
                raise ValueError("worker question has already been answered")
            if record.get("answer_digest") != answer_digest:
                raise ValueError("idempotent answer submission_id was reused with different content")
            return {"idempotent": True, "question": record, "cursor": _question_sequence(records)}
        record.update({
            "status": "answered",
            "answer": answer,
            "answer_text": answer_text,
            "resume_context": resume_context,
            "answer_submission_id": submission_id,
            "answer_digest": answer_digest,
            "answered_sequence": _question_sequence(records) + 1,
            "answered_at": now(),
        })
        _write_question_record(task_dir, state, record)
        append_journal_best_effort(task_dir, "worker_answer", f"{question_id} answered for {record['attempt_id']}")
        return {"idempotent": False, "question": record, "cursor": record["answered_sequence"]}


def _question_form_schema(config: dict[str, Any]) -> dict[str, Any]:
    """Build a native MCP form with optional single/multi-select and a final free-form field."""
    properties: dict[str, Any] = {}
    options = list(config.get("options") or [])
    if options:
        titled_options = [{"const": item["label"], "title": item["description"]} for item in options]
        if config.get("multiple"):
            properties["selections"] = {
                "type": "array",
                "title": config.get("header") or "Select all that apply",
                "items": {"anyOf": titled_options},
            }
        else:
            properties["selection"] = {
                "type": "string",
                "title": config.get("header") or "Select one",
                "oneOf": titled_options,
            }
    properties["custom_response"] = {
        "type": "string",
        "title": config.get("custom_label") or "Your answer / additional context",
        "description": "Optional free-form response. Add context, paste a screenshot/path, or explain another choice.",
    }
    return {"type": "object", "properties": properties}


def _request_mcp_elicitation(message: str, requested_schema: dict[str, Any], *, thread_id: str = "", turn_id: str = "") -> tuple[str, dict[str, Any] | None, str]:
    """Ask the Codex host to render its native MCP elicitation UI."""
    request_id = f"cortex-question-{secrets.token_hex(12)}"
    respond({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "elicitation/create",
        "params": {
            "message": message,
            # Codex's OpenAI extension can render richer form fallbacks (such
            # as attachment-capable free-form input). Use it only when the
            # connected host advertised the extension; otherwise remain
            # standards-compliant with MCP form mode.
            "mode": "openai/form" if bound_symbol("questions", "MCP_OPENAI_FORM") else "form",
            "requestedSchema": requested_schema,
            "_meta": {"cortex": {"schema": QUESTION_SCHEMA, "thread_id": thread_id, "turn_id": turn_id or None}},
        },
    })
    while True:
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("MCP client closed before answering cortex.question")
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") != request_id:
            if response.get("id") is not None and response.get("method"):
                respond({"jsonrpc": "2.0", "id": response.get("id"), "error": {"code": -32601, "message": "Cortex is waiting for the active user question"}})
            continue
        if "error" in response:
            error = response.get("error") or {}
            raise RuntimeError(redact(str(error.get("message") or "MCP elicitation was rejected"), 1000))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP elicitation returned an invalid response")
        action = str(result.get("action") or "cancel").strip().lower()
        content = result.get("content") if isinstance(result.get("content"), dict) else None
        return action, content, request_id


def _question_answer_from_content(content: dict[str, Any] | None, config: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    content = content or {}
    options = {item["label"] for item in config.get("options") or []}
    multiple = bool(config.get("multiple"))
    if multiple:
        raw_selections = content.get("selections", [])
        selections = raw_selections if isinstance(raw_selections, list) else [raw_selections]
        selections = [redact(item, 200) for item in selections if str(item).strip()]
        if options and any(item not in options for item in selections):
            raise ValueError("MCP elicitation returned an unknown question option")
    else:
        raw_selection = content.get("selection")
        selections = [redact(raw_selection, 200)] if raw_selection not in (None, "") else []
        if options and selections and selections[0] not in options:
            raise ValueError("MCP elicitation returned an unknown question option")
    custom = content.get("custom_response", "")
    normalized_custom, custom_text = _normalize_question_answer(custom)
    if not selections and not custom_text:
        return None, ""
    answer: dict[str, Any] = {
        "selections": selections if multiple else (selections[0] if selections else None),
        "custom_response": normalized_custom,
    }
    extras = {key: value for key, value in content.items() if key not in {"selection", "selections", "custom_response"}}
    if extras:
        answer["host_fields"] = sanitize_structured(extras)
    return answer, redact(json.dumps(answer, ensure_ascii=False, sort_keys=True), 8000)


def _question_record_for_main(params: dict[str, Any], question_id: str) -> dict[str, Any]:
    listed = list_worker_questions({"task_id": params["task_id"], "principal": params["principal"], "thread_id": params.get("thread_id"), "project_root": params.get("project_root")})
    record = next((item for item in listed["questions"] if item.get("question_id") == question_id), None)
    if record is None:
        raise ValueError("question_id does not belong to this task")
    return _question_record_view(record)


def _localized_question_view(record: dict[str, Any], params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Allow the coordinator to localize only the user-facing projection."""
    question = redact(params.get("localized_question") or record["question"], 4000)
    config = _question_config(record)
    if params.get("localized_header"):
        config["header"] = redact(params["localized_header"], 200)
    if isinstance(params.get("localized_options"), list):
        config["options"] = _question_options(params["localized_options"])
    if params.get("localized_custom_label"):
        config["custom_label"] = redact(params["localized_custom_label"], 200)
    return question, config


def cortex_question(params: dict[str, Any]) -> dict[str, Any]:
    """Route worker questions to the coordinator and render main-chat UI questions."""
    task_id = str(params.get("task_id") or "").strip()
    principal = str(params.get("principal") or "").strip()
    question_id = str(params.get("question_id") or "").strip()
    question = redact(str(params.get("question") or "").strip(), 4000)
    if not task_id or not principal or (not question and not question_id):
        raise ValueError("cortex.question requires task_id, principal, and question or question_id")

    durable: dict[str, Any] | None = None
    if question_id:
        question_id = safe_id(question_id)
        record = _question_record_for_main(params, question_id)
        if record.get("status") == "answered":
            return {
                "schema": QUESTION_SCHEMA,
                "status": "answered",
                "question_id": question_id,
                "question": record.get("question"),
                "answer": record.get("answer"),
                "answer_text": record.get("answer_text"),
                "idempotent": True,
                "durable": {"question": record},
            }
        question, config = _localized_question_view(record, params)
        durable = {"question": record}
    else:
        config = _question_config(params)
        attempt_id = str(params.get("attempt_id") or "").strip()
        if attempt_id:
            attempt_id = safe_id(attempt_id)
            submission_id = str(params.get("submission_id") or "").strip()
            if not submission_id:
                submission_id = f"question-{attempt_id}-{digest_text(question)[:12]}"
            durable = publish_worker_question({
                **params,
                "attempt_id": attempt_id,
                "submission_id": submission_id,
                "question": question,
                "context": {**(params.get("context") if isinstance(params.get("context"), dict) else {}), "ui": config},
            })
            return {
                "schema": QUESTION_SCHEMA,
                "status": "pending_user_input",
                "question_id": durable["question"]["question_id"],
                "question": question,
                "ui": config,
                "next_action": "coordinator must list_worker_questions and invoke cortex.question in the main chat with this question_id",
                "recoverable": True,
                "durable": durable,
            }

    if not bool(params.get("interactive", True)):
        return {
            "schema": QUESTION_SCHEMA,
            "status": "pending_user_input",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "ui": config,
            "next_action": "invoke cortex.question with interactive=true from the main Codex chat",
            "recoverable": True,
            "durable": durable,
        }
    try:
        # The composition binding resolves this narrow host seam at call time,
        # allowing integrations and tests to replace it without a runtime
        # dependency on the executable facade.
        action, content, elicitation_id = bound_symbol("questions", "_request_mcp_elicitation")(
            question,
            _question_form_schema(config),
            thread_id=str(params.get("thread_id") or ""),
            turn_id=str(params.get("turn_id") or ""),
        )
    except RuntimeError as exc:
        return {
            "schema": QUESTION_SCHEMA,
            "status": "elicitation_unavailable",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "error": redact(str(exc), 1000),
            "next_action": "surface this question with a host-native user-input UI or retry from the main chat",
            "recoverable": True,
            "durable": durable,
        }
    if action != "accept":
        return {
            "schema": QUESTION_SCHEMA,
            "status": action if action in {"decline", "cancel"} else "cancel",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "elicitation_id": elicitation_id,
            "answer": None,
            "durable": durable,
        }
    try:
        answer, answer_text = _question_answer_from_content(content, config)
    except ValueError as exc:
        return {"schema": QUESTION_SCHEMA, "status": "invalid_answer", "question": question, "error": str(exc), "recoverable": True, "durable": durable}
    if answer is None:
        return {
            "schema": QUESTION_SCHEMA,
            "status": "invalid_answer",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "elicitation_id": elicitation_id,
            "next_action": "retry cortex.question and choose an option or enter a custom response",
            "recoverable": True,
            "durable": durable,
        }
    answered = None
    if question_id:
        answer_submission_id = str(params.get("answer_submission_id") or "").strip()
        if not answer_submission_id:
            answer_submission_id = f"answer-{question_id}-{digest_text(answer_text)[:16]}"
        answered = answer_worker_question({
            **params,
            "question_id": question_id,
            "submission_id": safe_id(answer_submission_id),
            "answer": answer,
            "resume_context": {"source": "cortex.question", "elicitation_id": elicitation_id, "ui": config},
        })
    return {
        "schema": QUESTION_SCHEMA,
        "status": "answered",
        "question_id": question_id or (durable or {}).get("question", {}).get("question_id"),
        "question": question,
        "elicitation_id": elicitation_id,
        "answer": answer,
        "answer_text": answer_text,
        "durable": answered or durable,
    }


def get_worker_question_updates(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    attempt_id = safe_id(str(params.get("attempt_id", "")))
    _attempt(state, attempt_id)
    after_sequence = int(params.get("after_sequence", 0))
    if after_sequence < 0:
        raise ValueError("after_sequence must be nonnegative")
    records = _question_records(question_bus_paths(task_dir), state)
    attempt_records = [item for item in records if item["attempt_id"] == attempt_id]
    updates = []
    for record in attempt_records:
        if int(record["published_sequence"]) > after_sequence:
            updates.append({
                "sequence": record["published_sequence"],
                "kind": "question_published",
                "question_id": record["question_id"],
                "status": record["status"],
                "created_at": record["created_at"],
            })
        if record.get("answered_sequence") and int(record["answered_sequence"]) > after_sequence:
            updates.append({
                "sequence": record["answered_sequence"],
                "kind": "question_answered",
                "question_id": record["question_id"],
                "answer": record["answer"],
                "answer_text": record.get("answer_text"),
                "resume_context": record["resume_context"],
                "answered_at": record["answered_at"],
            })
    updates.sort(key=lambda item: int(item["sequence"]))
    return {
        "schema": QUESTION_SCHEMA,
        "task_id": state["task_id"],
        "attempt_id": attempt_id,
        "after_sequence": after_sequence,
        "updates": updates,
        "next_sequence": _question_sequence(attempt_records),
    }
