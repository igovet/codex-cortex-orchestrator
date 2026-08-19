"""Durable worker-question bus and MCP elicitation bridge."""
from __future__ import annotations

import json
import re
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
        "_task_document_root",
        "append_journal_best_effort",
        "authorize",
        "authorize_principal",
        "canonical_profile",
        "db_get_task_document",
        "db_list_task_documents",
        "db_put_task_document",
        "digest_text",
        "ledger_root",
        "load_state",
        "now",
        "question_bus_paths",
        "redact",
        "require_internal_english",
        "respond",
        "safe_id",
        "sanitize_structured",
        "state_lock",
    ),
)


BATCH_QUESTION_SCHEMA = "cortex/question-batch/v1"
_BATCH_DOCUMENT_PREFIX = "question_batch:"
_BATCH_OPEN_STATUSES = {"open", "awaiting_translation"}
_BATCH_QUESTION_TYPES = {"single_select", "multi_select", "text"}
_GENERIC_DECISION_LABELS = {
    "a", "b", "c", "d",
    "option", "recommended option", "alternative option", "other option",
    "variant", "recommended variant", "alternative variant",
    "choice", "answer", "decision",
    "вариант", "рекомендуемый вариант", "альтернативный вариант", "другой вариант",
    "выбор", "ответ", "решение",
}
_GENERIC_NUMBERED_LABEL = re.compile(
    r"^(?:option|variant|choice|answer|decision|вариант|выбор|ответ|решение)\s*(?:#?\d+|[a-zа-я])$",
    re.IGNORECASE,
)
_GENERIC_NUMBERED_QUESTION = re.compile(
    r"(?:\b(?:decision|question)\s*(?:#?\d+|one|two|three)\b|"
    r"\bрешени[ея]\s*#?\d+\b|\b(?:перв\w*|втор\w*|трет\w*)\s+решени\w*\b)",
    re.IGNORECASE,
)


def _normalized_display_text(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split()).strip(" .,:;!?()[]{}<>-—_")


def _require_meaningful_decision_label(value: object, label: str) -> None:
    """Reject placeholders that make a material decision impossible to understand."""
    normalized = _normalized_display_text(value)
    if (
        not normalized
        or normalized in _GENERIC_DECISION_LABELS
        or _GENERIC_NUMBERED_LABEL.fullmatch(normalized)
    ):
        raise ValueError(
            f"{label} must name the concrete outcome or trade-off; generic placeholders such as "
            "'Option 1', 'A/B', or 'Recommended option' are not allowed"
        )


def _require_self_contained_question(value: object, label: str) -> None:
    """Reject ordinal-only question copy that depends on unseen worker context."""
    normalized = " ".join(str(value or "").strip().split())
    if not normalized or _GENERIC_NUMBERED_QUESTION.search(normalized):
        raise ValueError(
            f"{label} must state the concrete decision and relevant constraint; numbered decision placeholders "
            "are not allowed"
        )


def _batch_document_key(batch_id: str) -> str:
    return _BATCH_DOCUMENT_PREFIX + safe_id(batch_id)


def _batch_records(task_dir: Any, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Read validated batch records from their SQLite task-document projection.

    Batch records deliberately share the existing task-document store. Every
    accepted slide is checkpointed under ``state_lock`` so cancellation can
    resume at the next unanswered question without losing prior decisions.
    """
    root = _task_document_root(task_dir, str(state["task_id"]))
    records: list[dict[str, Any]] = []
    for document_key, record in db_list_task_documents(root, str(state["task_id"]), _BATCH_DOCUMENT_PREFIX):
        batch_id = str(record.get("batch_id") or "")
        if (
            record.get("schema") != BATCH_QUESTION_SCHEMA
            or record.get("task_id") != state["task_id"]
            or document_key != _batch_document_key(batch_id)
            or record.get("status") not in {"open", "awaiting_translation", "answered", "superseded"}
        ):
            raise ValueError("question batch record failed validation")
        _attempt(state, safe_id(str(record.get("attempt_id") or "")))
        records.append(record)
    return records


def _batch_record(task_dir: Any, state: dict[str, Any], batch_id: str) -> dict[str, Any] | None:
    root = _task_document_root(task_dir, str(state["task_id"]))
    record = db_get_task_document(root, str(state["task_id"]), _batch_document_key(batch_id))
    if record is None:
        return None
    if (
        record.get("schema") != BATCH_QUESTION_SCHEMA
        or record.get("task_id") != state["task_id"]
        or record.get("batch_id") != batch_id
    ):
        raise ValueError("question batch record failed validation")
    return record


def _write_batch_record(task_dir: Any, state: dict[str, Any], record: dict[str, Any]) -> None:
    batch_id = safe_id(str(record.get("batch_id") or ""))
    if record.get("task_id") != state.get("task_id"):
        raise ValueError("question batch task identity is invalid")
    root = _task_document_root(task_dir, str(state["task_id"]))
    db_put_task_document(root, str(state["task_id"]), _batch_document_key(batch_id), record)


def _batch_id(task_id: str, attempt_id: str, batch_key: str) -> str:
    return "batch-" + digest_text("\0".join((task_id, attempt_id, batch_key)))[:24]


def _batch_revision(state: dict[str, Any]) -> int:
    return int(state.get("task_revision") or 1)


def _batch_is_stale(record: dict[str, Any], state: dict[str, Any]) -> bool:
    return int(record.get("task_revision") or 1) < _batch_revision(state)


def _supersede_batch(
    task_dir: Any,
    state: dict[str, Any],
    record: dict[str, Any],
    *,
    reason: str,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Persist a terminal non-resumable outcome for a stale open batch."""
    if record.get("status") in _BATCH_OPEN_STATUSES:
        record["status"] = "superseded"
        record["superseded_at"] = now()
        record["superseded_reason"] = redact(reason, 400)
        if superseded_by:
            record["superseded_by"] = safe_id(superseded_by)
        _write_batch_record(task_dir, state, record)
    return record


def _batch_question_config(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("batch question must be an object")
    question_key = safe_id(str(value.get("question_key") or ""))
    question = redact(str(value.get("question") or "").strip(), 4000)
    question_type = str(value.get("type") or "").strip().lower()
    if not question_key or not question:
        raise ValueError("batch questions require stable question_key and question")
    if question_type not in _BATCH_QUESTION_TYPES:
        raise ValueError("batch question type must be single_select, multi_select, or text")
    if question_type != "text" and question_key == "custom_response":
        raise ValueError("choice batch question_key custom_response is reserved for free-form input")
    header = redact(str(value.get("header") or question).strip(), 200) or question
    custom_label = redact(str(value.get("custom_label") or "Your answer").strip(), 160) or "Your answer"
    context = redact(str(value.get("context") or "").strip(), 2000)
    recommendation = redact(str(value.get("recommendation") or "").strip(), 1000)
    options = _question_options(value.get("options"))
    if question_type == "text" and options:
        raise ValueError("text batch questions must not define options")
    if question_type != "text" and not options:
        raise ValueError("selection batch questions require options")
    require_internal_english(question, "batch worker question")
    require_internal_english(header, "batch worker question header")
    require_internal_english(custom_label, "batch worker question custom_label")
    require_internal_english(context, "batch worker question context")
    require_internal_english(recommendation, "batch worker question recommendation")
    require_internal_english(options, "batch worker question options")
    _require_self_contained_question(question, "batch worker question")
    _require_meaningful_decision_label(header, "batch worker question header")
    for option in options:
        _require_meaningful_decision_label(option.get("label_en"), "batch worker question option")
    return {
        "question_key": question_key,
        "question_type": question_type,
        "canonical_question": question,
        "localized_question": question,
        "header": header,
        "localized_header": header,
        "options": options,
        "custom_label": custom_label,
        "localized_custom_label": custom_label,
        "context": context,
        "recommendation": recommendation,
    }


def _batch_payload(params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw_batch = params.get("batch")
    if not isinstance(raw_batch, dict):
        raise ValueError("ask_batch requires batch")
    unknown = sorted(set(raw_batch) - {"batch_key", "questions"})
    if unknown:
        raise ValueError("batch contains unsupported fields: " + ", ".join(unknown))
    batch_key = safe_id(str(raw_batch.get("batch_key") or ""))
    raw_questions = raw_batch.get("questions")
    if not batch_key or not isinstance(raw_questions, list) or not raw_questions or len(raw_questions) > 32:
        raise ValueError("batch requires batch_key and 1..32 questions")
    questions = [_batch_question_config(item) for item in raw_questions]
    keys = [item["question_key"] for item in questions]
    if len(keys) != len(set(keys)):
        raise ValueError("batch question_key values must be unique")
    batch = {"batch_key": batch_key, "questions": questions}
    content_digest = digest_text(json.dumps(batch, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return batch, content_digest


def _batch_answer_view(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only canonical English values to a worker polling a batch."""
    answers = record.get("answers") if isinstance(record.get("answers"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for item in record.get("questions") or []:
        question_key = str(item.get("question_key") or "")
        answer = answers.get(question_key) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or not str(answer.get("answer_en") or "").strip():
            raise ValueError("answered batch has no canonical answer")
        result[question_key] = {
            "answer_en": str(answer["answer_en"]),
            "answer_option_ids": list(answer.get("answer_option_ids") or []),
        }
    return result


def _batch_progress(record: dict[str, Any]) -> dict[str, Any]:
    """Return bounded durable progress for the sequential batch UI."""
    questions = list(record.get("questions") or [])
    answers = record.get("answers") if isinstance(record.get("answers"), dict) else {}
    remaining = [
        str(item.get("question_key") or "")
        for item in questions
        if str(item.get("question_key") or "") not in answers
    ]
    return {
        "answered": len(questions) - len(remaining),
        "total": len(questions),
        "next_question_key": remaining[0] if remaining else None,
    }

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


def _publish_worker_question_batch(
    params: dict[str, Any],
    task_dir: Any,
    state: dict[str, Any],
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Create one durable batch while the caller holds the state transaction."""
    batch, content_digest = _batch_payload(params)
    attempt_id = str(attempt["attempt_id"])
    batch_key = batch["batch_key"]
    records = _batch_records(task_dir, state)
    existing = next(
        (
            item for item in records
            if item.get("attempt_id") == attempt_id and item.get("batch_key") == batch_key
        ),
        None,
    )
    if existing is not None:
        if existing.get("content_digest") != content_digest:
            raise ValueError("stable batch_key was reused with different batch content")
        return {"idempotent": True, "batch": existing}

    batch_id = _batch_id(str(state["task_id"]), attempt_id, batch_key)
    # A single attempt has at most one unresolved batch.  Replacing it makes
    # the old ref explicitly non-resumable instead of allowing two competing
    # user decisions to wake the same native worker.
    for previous in records:
        if previous.get("attempt_id") == attempt_id and previous.get("status") in _BATCH_OPEN_STATUSES:
            _supersede_batch(
                task_dir,
                state,
                previous,
                reason="replaced by a newer batch for the same worker attempt",
                superseded_by=batch_id,
            )
    timestamp = now()
    record = {
        "schema": BATCH_QUESTION_SCHEMA,
        "batch_id": batch_id,
        "batch_key": batch_key,
        "task_id": state["task_id"],
        "gate": attempt["gate"],
        "attempt_id": attempt_id,
        "profile": attempt["profile"],
        "task_revision": _batch_revision(state),
        "language": "en",
        "status": "open",
        "questions": batch["questions"],
        "answers": {},
        "answered_count": 0,
        "total_questions": len(batch["questions"]),
        "next_question_key": batch["questions"][0]["question_key"],
        "content_digest": content_digest,
        "created_at": timestamp,
        "answered_at": None,
        "superseded_at": None,
    }
    _write_batch_record(task_dir, state, record)
    append_journal_best_effort(task_dir, "worker_question_batch", f"{attempt_id} published {batch_id}")
    return {"idempotent": False, "batch": record}


def _poll_worker_question_batch(
    task_dir: Any,
    state: dict[str, Any],
    attempt: dict[str, Any],
    profile: str,
    batch_ref: str,
) -> dict[str, Any]:
    record = _batch_record(task_dir, state, batch_ref)
    if record is None or record.get("attempt_id") != attempt.get("attempt_id") or record.get("profile") != profile:
        raise ValueError("batch_ref does not belong to this worker attempt")
    if _batch_is_stale(record, state):
        _supersede_batch(
            task_dir,
            state,
            record,
            reason="task revision superseded this unresolved batch",
        )
    if attempt.get("invalidated"):
        _supersede_batch(
            task_dir,
            state,
            record,
            reason="worker attempt was superseded before its batch was answered",
        )
    if record.get("status") == "superseded":
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "batch_superseded",
            "batch_ref": batch_ref,
            "status": "superseded",
            "resume": False,
            "next_action": "Do not resume this worker from the superseded batch; wait for a replacement dispatch or current revision guidance.",
        }
    if record.get("status") != "answered":
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "awaiting_user",
            "batch_ref": batch_ref,
            "status": record.get("status"),
            "next_action": "Remain available; the parent coordinator must complete this same durable batch.",
        }
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "batch_answered",
        "batch_ref": batch_ref,
        "status": "answered",
        "answers": _batch_answer_view(record),
        "next_action": "Resume this same worker attempt with the canonical English batch answers; record the report only after the mission is complete.",
    }


def _worker_question_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Public facade adapter for durable ask/poll on one exact worker attempt."""
    action = str(params.get("action") or "").strip().lower()
    if action not in {"ask", "poll", "ask_batch", "poll_batch"}:
        raise ValueError("worker question action must be ask, poll, ask_batch, or poll_batch")
    profile = canonical_profile(params.get("profile") or "")
    if profile not in AGENTS:
        raise ValueError("profile must be an exact Cortex worker profile")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params.get("task_id") or ""), params)
        attempt_id = safe_id(str(params.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        if not attempt.get("facade_managed") or attempt.get("profile") != profile:
            raise ValueError("worker question identity does not match an active facade-managed attempt")
        if action == "poll_batch":
            if any(params.get(field) not in (None, "", [], {}) for field in (
                "question_ref", "question", "header", "options", "multiple", "custom_label", "context", "batch"
            )):
                raise ValueError("poll_batch accepts only batch_ref and worker identity fields")
            batch_ref = safe_id(str(params.get("batch_ref") or ""))
            if not batch_ref:
                raise ValueError("poll_batch requires batch_ref")
            return _poll_worker_question_batch(task_dir, state, attempt, profile, batch_ref)
        if (
            attempt.get("invalidated")
            or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}
        ):
            raise ValueError("worker question attempt is no longer active")
        if action == "ask_batch":
            if str(params.get("question_ref") or "").strip() or str(params.get("batch_ref") or "").strip():
                raise ValueError("ask_batch must omit question_ref and batch_ref")
            if any(params.get(field) not in (None, "", [], {}) for field in (
                "question", "header", "options", "multiple", "custom_label", "context"
            )):
                raise ValueError("ask_batch accepts only batch and worker identity fields")
            result = _publish_worker_question_batch(params, task_dir, state, attempt)
            record = result["batch"]
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": "batch_recorded",
                "batch_ref": record["batch_id"],
                # Keep the established coordinator transport compact: batch
                # refs travel through the existing question_ref envelope.
                "question_ref": record["batch_id"],
                "status": record["status"],
                "idempotent": bool(result.get("idempotent")),
                "next_action": (
                    "Return QUESTION_RECORDED question_ref=<value>, then a complete decision handoff to the parent: "
                    "why input is needed, every full question, every concrete option label and description, material "
                    "trade-offs, and your recommendation. Do not use placeholders such as Option 1 or Recommended "
                    "option. Remain available and do not record a report until this batch is answered."
                ),
            }
        if action == "ask":
            if str(params.get("question_ref") or "").strip():
                raise ValueError("ask must omit question_ref")
            question = str(params.get("question") or "").strip()
            if not question:
                raise ValueError("ask requires question")
            _require_self_contained_question(question, "worker question")
            _require_meaningful_decision_label(params.get("header") or question, "worker question header")
            for option in _question_options(params.get("options")):
                _require_meaningful_decision_label(option.get("label_en"), "worker question option")
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
                    "Return QUESTION_RECORDED question_ref=<value>, then a complete decision handoff to the parent: "
                    "why input is needed, the full question, every concrete option label and description, material "
                    "trade-offs, and your recommendation. Do not use placeholders such as Option 1 or Recommended "
                    "option. Remain available and do not record a report until this question is answered."
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
            "answer": record.get("answer_en") or record.get("answer"),
            "answer_text": record.get("answer_en_text") or record.get("answer_text"),
            "answer_option_ids": record.get("answer_option_ids") or [],
            "resume_context": record.get("resume_context"),
            "next_action": "Resume this same worker attempt with the user's answer; record the report only after the mission is complete.",
        }


def _worker_question_error_path(message: str) -> str:
    lowered = message.lower()
    for marker, path in (
        ("question_ref", "question_ref"), ("batch_ref", "batch_ref"),
        ("question_key", "batch.questions"), ("batch", "batch"),
        ("question", "question"), ("action", "action"),
        ("profile", "profile"), ("attempt", "attempt_id"),
        ("task", "task_id"), ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    return "$"


def worker_question(params: dict[str, Any]) -> dict[str, Any]:
    """Run durable ask/poll while keeping caller mistakes on the same attempt."""
    try:
        return _worker_question_impl(params)
    except (ValueError, OSError) as exc:
        message = redact(str(exc), 1000)
        lowered = message.lower()
        terminal = isinstance(exc, OSError) or any(fragment in lowered for fragment in (
            "attempt is no longer active",
            "invalidated or terminal attempt",
            "question batch record failed validation",
            "question batch task identity is invalid",
            "answered batch has no canonical answer",
            "question count quota exhausted",
        ))
        code = "worker_question_unavailable" if terminal else "worker_question_request_invalid"
        path = _worker_question_error_path(message)
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "blocked" if terminal else "needs_correction",
            "code": code,
            "diagnostics": [{
                "code": code,
                "path": path,
                "message": message,
                "fix": (
                    "The worker attempt is no longer active; do not create a replacement or guess another identity."
                    if terminal else
                    "Correct only this field from the active briefing or the last worker_question response, then "
                    "retry worker_question on this same worker attempt."
                ),
            }],
            "retryable": not terminal,
            "attempt_budget_consumed": False,
            "next_action": (
                "Stop this worker call because the response is explicitly non-retryable."
                if terminal else
                "Correct the diagnostic field and retry worker_question on this same attempt; rejected caller "
                "validation does not consume an attempt and must not end the worker."
            ),
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
        supplied_answer_en, supplied_answer_en_text = _normalize_question_answer(params.get("answer_en"))
        resume_context = sanitize_structured(params.get("resume_context"))
        if not answer_text:
            raise ValueError("worker question answer is required")
        if resume_context in (None, "", [], {}):
            raise ValueError("worker question resume_context is required")
        paths = question_bus_paths(task_dir)
        records = _question_records(paths, state)
        record = next((item for item in records if item.get("question_id") == question_id), None)
        if record is None:
            raise ValueError("question_id does not belong to this task")
        option_map = {item["option_id"]: item.get("label_en") or item.get("label") for item in record.get("options") or []}
        option_ids = []
        custom_text = ""
        if isinstance(answer, dict):
            raw_ids = answer.get("option_ids")
            if raw_ids is None:
                raw_ids = answer.get("selections")
            option_ids = raw_ids if isinstance(raw_ids, list) else [raw_ids] if raw_ids else []
            if any(item not in option_map for item in option_ids):
                raise ValueError("worker question answer contains an unknown option_id")
            custom = answer.get("custom_response")
            custom_text = str(custom or "").strip() if not isinstance(custom, (dict, list)) else json.dumps(custom, ensure_ascii=False, sort_keys=True)
        canonical_parts = [str(option_map[item]) for item in option_ids]
        if supplied_answer_en_text:
            canonical_parts.append(supplied_answer_en_text)
        elif custom_text:
            user_language = str((resume_context if isinstance(resume_context, dict) else {}).get("user_language") or "en")
            if not user_language.lower().startswith("en"):
                raise ValueError("localized free-text answer requires answer_en translation")
            canonical_parts.append(custom_text)
        answer_en_text = "\n".join(part for part in canonical_parts if part).strip()
        answer_en = supplied_answer_en if supplied_answer_en_text else {
            "option_ids": option_ids,
            "selections": [option_map[item] for item in option_ids],
            "custom_response": custom_text,
        }
        answer_digest = digest_text(json.dumps({"answer": answer, "answer_en": answer_en, "resume_context": resume_context}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if record.get("status") == "answered":
            if record.get("answer_submission_id") != submission_id:
                raise ValueError("worker question has already been answered")
            if record.get("answer_digest") != answer_digest:
                raise ValueError("idempotent answer submission_id was reused with different content")
            return {
                "schema": QUESTION_SCHEMA,
                "status": "answered",
                "question_id": question_id,
                "idempotent": True,
                "question": record,
                "cursor": _question_sequence(records),
            }
        record.update({
            "status": "answered",
            "answer": answer,
            "answer_text": answer_text,
            "answer_original": answer,
            "answer_original_language": str((resume_context if isinstance(resume_context, dict) else {}).get("user_language") or "en"),
            "answer_option_ids": option_ids,
            "answer_en": answer_en,
            "answer_en_text": answer_en_text,
            "resume_context": resume_context,
            "answer_submission_id": submission_id,
            "answer_digest": answer_digest,
            "answered_sequence": _question_sequence(records) + 1,
            "answered_at": now(),
        })
        _write_question_record(task_dir, state, record)
        append_journal_best_effort(task_dir, "worker_answer", f"{question_id} answered for {record['attempt_id']}")
        return {
            "schema": QUESTION_SCHEMA,
            "status": "answered",
            "question_id": question_id,
            "idempotent": False,
            "question": record,
            "cursor": record["answered_sequence"],
        }


def _question_form_schema(config: dict[str, Any]) -> dict[str, Any]:
    """Build a native MCP form with optional single/multi-select and a final free-form field."""
    properties: dict[str, Any] = {}
    options = list(config.get("options") or [])
    if options:
        titled_options = []
        for item in options:
            option_title = item.get("label_localized") or item.get("label") or item["option_id"]
            option_description = item.get("description_localized") or (
                "" if config.get("localized_for_user") else item.get("description") or ""
            )
            choice = {"const": item["option_id"], "title": option_title}
            if option_description and option_description != option_title:
                choice["description"] = option_description
            titled_options.append(choice)
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
        "description": "" if config.get("localized_for_user") else "Optional free-form response. Add context, paste a screenshot/path, or explain another choice.",
    }
    return {"type": "object", "properties": properties}


def _request_mcp_elicitation(
    message: str,
    requested_schema: dict[str, Any],
    *,
    thread_id: str = "",
    turn_id: str = "",
    meta: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None, str]:
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
            "_meta": {
                "cortex": {
                    "schema": QUESTION_SCHEMA,
                    "thread_id": thread_id,
                    "turn_id": turn_id or None,
                    **(meta if isinstance(meta, dict) else {}),
                },
            },
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
    option_aliases: dict[str, str] = {}
    for item in config.get("options") or []:
        option_id = item["option_id"]
        for alias in (option_id, item.get("label_localized"), item.get("label"), item.get("label_en")):
            if str(alias or "").strip():
                option_aliases[str(alias)] = option_id
    options = set(option_aliases.values())
    multiple = bool(config.get("multiple"))
    if multiple:
        raw_selections = content.get("selections", [])
        selections = raw_selections if isinstance(raw_selections, list) else [raw_selections]
        selections = [redact(item, 200) for item in selections if str(item).strip()]
        selections = [option_aliases.get(item, item) for item in selections]
        if options and any(item not in options for item in selections):
            raise ValueError("MCP elicitation returned an unknown question option")
    else:
        raw_selection = content.get("selection")
        selections = [redact(raw_selection, 200)] if raw_selection not in (None, "") else []
        selections = [option_aliases.get(item, item) for item in selections]
        if options and selections and selections[0] not in options:
            raise ValueError("MCP elicitation returned an unknown question option")
    custom = content.get("custom_response", "")
    normalized_custom, custom_text = _normalize_question_answer(custom)
    # Some hosts return a ``selection`` value even when the rendered form has
    # only the free-form field.  It is user prose in that shape, never a
    # canonical option id; preserve it as custom text for compatibility.
    if not options and selections:
        selected_text = "\n".join(str(item) for item in selections)
        if custom_text:
            selected_text = selected_text + "\n" + custom_text
        normalized_custom, custom_text = _normalize_question_answer(selected_text)
        selections = []
    if not selections and not custom_text:
        return None, ""
    answer: dict[str, Any] = {
        "selections": selections if multiple else (selections[0] if selections else None),
        "option_ids": selections,
        "custom_response": normalized_custom,
    }
    extras = {key: value for key, value in content.items() if key not in {"selection", "selections", "custom_response"}}
    if extras:
        answer["host_fields"] = sanitize_structured(extras)
    return answer, redact(json.dumps(answer, ensure_ascii=False, sort_keys=True), 8000)


def _batch_record_for_main(params: dict[str, Any], batch_id: str) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    record = _batch_record(task_dir, state, batch_id)
    if record is None:
        raise ValueError("batch_ref does not belong to this task")
    return dict(record)


def _localized_batch_view(record: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Build a localized form view without changing canonical batch values."""
    questions = [dict(item) for item in record.get("questions") or []]
    requires_localization = not str(params.get("user_language") or "en").lower().startswith("en")
    raw_localized = params.get("localized_questions")
    if raw_localized is None and isinstance(params.get("localized_batch"), dict):
        raw_localized = params["localized_batch"].get("questions")
    if raw_localized is None and requires_localization:
        raise ValueError(
            "non-English user questions require localized_questions in the task's user_language"
        )
    if raw_localized is None:
        return {**record, "questions": questions}
    if not isinstance(raw_localized, list) or len(raw_localized) != len(questions):
        raise ValueError("localized_questions must contain one projection for every batch question")
    # ``localized_questions`` is a display projection supplied by the
    # coordinator, not a second copy of the canonical batch.  In particular,
    # a translating model must never be required to preserve opaque IDs.  Use
    # exact keys when the whole projection preserves them, otherwise keep the
    # server-owned batch order and ignore display-only keys altogether.
    by_key: dict[str, dict[str, Any]] = {}
    preserves_canonical_keys = True
    for item in raw_localized:
        if not isinstance(item, dict):
            raise ValueError("localized batch question must be an object")
        raw_key = str(item.get("question_key") or "").strip()
        try:
            key = safe_id(raw_key) if raw_key else ""
        except ValueError:
            # A localized display ID is not protocol data.  Fall back to the
            # canonical position instead of making the user form depend on a
            # model-generated identifier.
            key = ""
        if not key or key in by_key:
            preserves_canonical_keys = False
            continue
        by_key[key] = item
    canonical_keys = {str(item["question_key"]) for item in questions}
    if set(by_key) != canonical_keys:
        preserves_canonical_keys = False
    localized_items = (
        [by_key[question["question_key"]] for question in questions]
        if preserves_canonical_keys
        else list(raw_localized)
    )
    for question, localized in zip(questions, localized_items):
        question["localized_for_user"] = requires_localization
        localized_question = localized.get("localized_question", localized.get("question"))
        if requires_localization and not str(localized_question or "").strip():
            raise ValueError("every localized batch item requires localized_question")
        if localized_question is not None:
            question["localized_question"] = redact(str(localized_question).strip(), 4000) or question["canonical_question"]
        localized_header = localized.get("localized_header", localized.get("header"))
        if requires_localization and not str(localized_header or "").strip():
            localized_header = localized_question
        if localized_header is not None:
            question["localized_header"] = redact(str(localized_header).strip(), 200) or question["localized_question"]
        localized_custom_label = localized.get("localized_custom_label", localized.get("custom_label"))
        if requires_localization and not str(localized_custom_label or "").strip():
            localized_custom_label = localized_question
        if localized_custom_label is not None:
            question["localized_custom_label"] = redact(str(localized_custom_label).strip(), 160) or question["localized_question"]
        effective_question = question.get("localized_question") or question["canonical_question"]
        effective_header = question.get("localized_header") or question.get("header") or effective_question
        _require_self_contained_question(effective_question, "localized batch question")
        _require_meaningful_decision_label(effective_header, "localized batch question header")
        has_localized_options = "localized_options" in localized or "options" in localized
        if not has_localized_options and requires_localization and question.get("question_type") != "text":
            raise ValueError("localized choice questions require localized options")
        if not has_localized_options:
            continue
        raw_options = localized.get("localized_options", localized.get("options"))
        canonical_options = list(question.get("options") or [])
        if not isinstance(raw_options, list) or len(raw_options) != len(canonical_options):
            raise ValueError("localized batch options must match the canonical option count")
        merged = []
        for canonical, display in zip(canonical_options, raw_options):
            if isinstance(display, dict):
                title = display.get("label_localized", display.get("label", display.get("label_en", "")))
                description = display.get("description_localized", display.get("description", ""))
            else:
                title = display
                description = ""
            localized_title = redact(str(title or "").strip(), 120)
            if not localized_title:
                raise ValueError("localized batch options require non-empty labels")
            _require_meaningful_decision_label(localized_title, "localized batch option")
            localized_description = redact(str(description or "").strip(), 400)
            # Localization changes only a display title.  The form position
            # selects the server-owned option, so any model-supplied
            # ``option_id`` is display metadata and cannot alter the answer.
            merged.append({
                **canonical,
                "label_localized": localized_title,
                **({"description_localized": localized_description} if localized_description else {}),
            })
        question["options"] = merged
    return {**record, "questions": questions}


def _batch_form_schema(question: dict[str, Any]) -> dict[str, Any]:
    """Render exactly one batch item, with free-form context beside choices."""
    key = question["question_key"]
    question_type = question["question_type"]
    title = question.get("localized_question") or question["canonical_question"]
    description = question.get("localized_header") or question.get("header") or ""
    if question_type == "text":
        field = {
            "type": "string",
            "minLength": 1,
            "title": title,
            "description": description or question.get("localized_custom_label") or question.get("custom_label"),
        }
    else:
        choices = []
        for option in question.get("options") or []:
            option_title = option.get("label_localized") or option.get("label_en") or option["option_id"]
            option_description = option.get("description_localized") or (
                "" if question.get("localized_for_user") else option.get("description") or ""
            )
            choice = {"const": option["option_id"], "title": option_title}
            if option_description and option_description != option_title:
                choice["description"] = option_description
            choices.append(choice)
        if question_type == "multi_select":
            field = {
                "type": "array",
                "minItems": 1,
                "title": title,
                "description": description,
                "items": {"anyOf": choices},
            }
        else:
            field = {
                "type": "string",
                "title": title,
                "description": description,
                "oneOf": choices,
            }
    properties = {key: field}
    if question_type != "text":
        properties["custom_response"] = {
            "type": "string",
            "title": (
                question.get("localized_custom_label")
                or question.get("custom_label")
                or "Your answer / additional context"
            ),
            "description": (
                "" if question.get("localized_for_user") else
                "Optional free-form response. Explain another choice or add constraints the listed options do not capture."
            ),
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": [key],
    }


def _batch_answer_from_content(
    content: dict[str, Any] | None,
    question: dict[str, Any],
) -> dict[str, Any]:
    """Validate one native step without accepting answers for later slides."""
    if not isinstance(content, dict):
        raise ValueError("MCP elicitation returned an invalid batch response")
    key = str(question["question_key"])
    question_type = question["question_type"]
    allowed_fields = {key} if question_type == "text" else {key, "custom_response"}
    if key not in content or not set(content).issubset(allowed_fields):
        raise ValueError("MCP elicitation must answer only the current batch question")
    raw = content[key]
    custom_original = ""
    if question_type == "text":
        original = redact(str(raw or "").strip(), 8000)
        if not original:
            raise ValueError("batch text answers must be non-empty")
        option_ids: list[str] = []
    else:
        option_map = {
            option["option_id"]: option.get("label_en") or option.get("label") or option["option_id"]
            for option in question.get("options") or []
        }
        if question_type == "multi_select":
            raw_ids = raw if isinstance(raw, list) else [raw]
            option_ids = [safe_id(str(item)) for item in raw_ids if str(item).strip()]
        else:
            option_ids = [safe_id(str(raw))] if str(raw or "").strip() else []
        if not option_ids or len(option_ids) != len(set(option_ids)) or any(item not in option_map for item in option_ids):
            raise ValueError("MCP elicitation returned an unknown or empty batch option")
        original = option_ids if question_type == "multi_select" else option_ids[0]
        raw_custom = content.get("custom_response", "")
        if isinstance(raw_custom, (dict, list)):
            raise ValueError("batch custom_response must be text")
        custom_original = redact(str(raw_custom or "").strip(), 8000)
    return {
        "answer_original": original,
        "answer_option_ids": option_ids,
        "answer_custom_original": custom_original,
        "answer_original_text": (
            json.dumps(original, ensure_ascii=False, sort_keys=True)
            if isinstance(original, list) else str(original)
        ),
    }


def _refresh_batch_answer_state(record: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Derive canonical answers and terminal state from durable slide progress."""
    stored = record.get("answers") if isinstance(record.get("answers"), dict) else {}
    language = str(record.get("answer_original_language") or params.get("user_language") or "en")
    supplied = params.get("canonical_answers")
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValueError("canonical_answers must be an object keyed by a free-text or custom-response question_key")
    question_by_key = {str(item["question_key"]): item for item in record.get("questions") or []}
    unknown = sorted(set(supplied) - set(question_by_key))
    if unknown:
        raise ValueError("canonical_answers contains an unknown question_key: " + ", ".join(unknown))
    pending_translation: list[str] = []
    for key, answer in stored.items():
        question = question_by_key.get(key)
        if not isinstance(question, dict) or not isinstance(answer, dict):
            raise ValueError("batch answer record is invalid")
        option_ids = list(answer.get("answer_option_ids") or [])
        if question["question_type"] != "text":
            option_map = {
                option["option_id"]: option.get("label_en") or option.get("label") or option["option_id"]
                for option in question.get("options") or []
            }
            if not option_ids or any(item not in option_map for item in option_ids):
                raise ValueError("batch answer record has an invalid canonical option_id")
            selected = "\n".join(str(option_map[item]) for item in option_ids)
            custom_original = redact(str(answer.get("answer_custom_original") or "").strip(), 8000)
            if not custom_original:
                answer["answer_en"] = selected
                answer["translation_status"] = "not_required"
            elif language.lower().startswith("en"):
                require_internal_english(custom_original, "batch custom response")
                answer["answer_en"] = selected + "\nAdditional user context: " + custom_original
                answer["translation_status"] = "not_required"
            elif key in supplied:
                translated = redact(str(supplied[key] or "").strip(), 8000)
                if not translated:
                    raise ValueError("canonical_answers translations must be non-empty")
                require_internal_english(translated, "canonical batch custom-response translation")
                answer["answer_en"] = selected + "\nAdditional user context: " + translated
                answer["translation_status"] = "translated"
                answer["translated_by"] = redact(str(params.get("translated_by") or "coordinator"), 160)
                answer["translated_at"] = now()
            elif answer.get("translation_status") == "translated" and str(answer.get("answer_en") or "").strip():
                continue
            else:
                answer["translation_status"] = "awaiting_translation"
                pending_translation.append(key)
            continue
        original = redact(str(answer.get("answer_original") or "").strip(), 8000)
        if not original:
            raise ValueError("batch answer record has an empty free-text answer")
        if language.lower().startswith("en"):
            require_internal_english(original, "free-text batch answer")
            answer["answer_en"] = original
            answer["translation_status"] = "not_required"
        elif key in supplied:
            translated = redact(str(supplied[key] or "").strip(), 8000)
            if not translated:
                raise ValueError("canonical_answers translations must be non-empty")
            require_internal_english(translated, "canonical batch answer translation")
            answer["answer_en"] = translated
            answer["translation_status"] = "translated"
            answer["translated_by"] = redact(str(params.get("translated_by") or "coordinator"), 160)
            answer["translated_at"] = now()
        elif answer.get("translation_status") == "translated" and str(answer.get("answer_en") or "").strip():
            continue
        else:
            answer["translation_status"] = "awaiting_translation"
            pending_translation.append(key)

    missing = [key for key in question_by_key if key not in stored]
    record["answers"] = stored
    record["answered_count"] = len(stored)
    record["total_questions"] = len(question_by_key)
    record["next_question_key"] = missing[0] if missing else None
    record["answer_original"] = {key: item["answer_original"] for key, item in stored.items()}
    record["answer_custom_original"] = {
        key: str(item.get("answer_custom_original") or "") for key, item in stored.items()
    }
    record["answer_option_ids"] = {key: list(item.get("answer_option_ids") or []) for key, item in stored.items()}
    record["answer_en"] = {
        key: str(item["answer_en"])
        for key, item in stored.items()
        if str(item.get("answer_en") or "").strip()
    }
    if pending_translation:
        record["translation_status"] = "awaiting_translation"
    elif stored and all(item.get("translation_status") == "not_required" for item in stored.values()):
        record["translation_status"] = "not_required"
    elif stored:
        record["translation_status"] = "translated"
    else:
        record["translation_status"] = "pending"
    record["translation_required_for"] = pending_translation
    if missing:
        record["status"] = "open"
        record["answered_at"] = None
    elif pending_translation:
        record["status"] = "awaiting_translation"
        record["translation_requested_at"] = now()
        record["answered_at"] = None
    else:
        record["status"] = "answered"
        record["answered_at"] = now()
    return record


def _persist_batch_answers(
    params: dict[str, Any],
    batch_id: str,
    answers: dict[str, dict[str, Any]] | None,
    *,
    elicitation_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist a complete compatibility answer set or canonical translations."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        record = _batch_record(task_dir, state, batch_id)
        if record is None:
            raise ValueError("batch_ref does not belong to this task")
        attempt = _attempt(state, safe_id(str(record.get("attempt_id") or "")))
        if _batch_is_stale(record, state):
            _supersede_batch(task_dir, state, record, reason="task revision superseded this unresolved batch")
        if attempt.get("invalidated"):
            _supersede_batch(task_dir, state, record, reason="worker attempt was superseded before its batch was answered")
        if record.get("status") == "superseded":
            return record, False
        if record.get("status") == "answered":
            return record, True
        if answers is not None:
            if record.get("status") != "open":
                raise ValueError("batch is awaiting translation; submit canonical_answers without reopening the form")
            stored = record.get("answers") if isinstance(record.get("answers"), dict) else {}
            for key, answer in answers.items():
                if key in stored and stored[key] != answer:
                    raise ValueError("a durable batch answer cannot be replaced")
                stored[key] = answer
            record["answers"] = stored
            record["answer_original_language"] = str(
                record.get("answer_original_language") or params.get("user_language") or "en"
            )
            if elicitation_id:
                record["elicitation_id"] = elicitation_id
        elif record.get("status") == "open":
            raise ValueError("open batch requires native question answers")
        _refresh_batch_answer_state(record, params)
        _write_batch_record(task_dir, state, record)
        append_journal_best_effort(task_dir, "worker_question_batch_answer", f"{batch_id} {record['status']}")
        return record, False


def _persist_batch_step_answer(
    params: dict[str, Any],
    batch_id: str,
    question_key: str,
    answer: dict[str, Any],
    *,
    elicitation_id: str,
) -> dict[str, Any]:
    """Checkpoint one accepted slide so cancellation resumes at the next item."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        record = _batch_record(task_dir, state, batch_id)
        if record is None:
            raise ValueError("batch_ref does not belong to this task")
        attempt = _attempt(state, safe_id(str(record.get("attempt_id") or "")))
        if _batch_is_stale(record, state):
            _supersede_batch(task_dir, state, record, reason="task revision superseded this unresolved batch")
        if attempt.get("invalidated"):
            _supersede_batch(task_dir, state, record, reason="worker attempt was superseded before its batch was answered")
        if record.get("status") == "superseded":
            return record
        if record.get("status") != "open":
            raise ValueError("the question batch is not accepting another slide answer")
        question_keys = {str(item.get("question_key") or "") for item in record.get("questions") or []}
        if question_key not in question_keys:
            raise ValueError("question_key does not belong to this batch")
        stored = record.get("answers") if isinstance(record.get("answers"), dict) else {}
        if question_key in stored:
            if stored[question_key] != answer:
                raise ValueError("a durable batch answer cannot be replaced")
            return record
        answer = dict(answer)
        answer["elicitation_id"] = elicitation_id
        answer["answered_at"] = now()
        stored[question_key] = answer
        record["answers"] = stored
        record["answer_original_language"] = str(
            record.get("answer_original_language") or params.get("user_language") or "en"
        )
        record.setdefault("elicitation_ids", {})[question_key] = elicitation_id
        record["last_elicitation_id"] = elicitation_id
        _refresh_batch_answer_state(record, params)
        _write_batch_record(task_dir, state, record)
        append_journal_best_effort(
            task_dir,
            "worker_question_batch_step",
            f"{batch_id} {question_key} {record['answered_count']}/{record['total_questions']}",
        )
        return record


def _supersede_batch_for_main(params: dict[str, Any], batch_id: str) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        record = _batch_record(task_dir, state, batch_id)
        if record is None:
            raise ValueError("batch_ref does not belong to this task")
        if _batch_is_stale(record, state):
            _supersede_batch(task_dir, state, record, reason="task revision superseded this unresolved batch")
        return record


def _cortex_question_batch(params: dict[str, Any], batch_id: str) -> dict[str, Any]:
    """Surface a durable batch as one native question at a time."""
    record = _supersede_batch_for_main(params, batch_id)
    if record.get("status") == "superseded":
        return {
            "schema": QUESTION_SCHEMA,
            "status": "superseded",
            "question_id": batch_id,
            "batch_ref": batch_id,
            "resume": False,
            "durable": {"batch": record},
            "next_action": "Do not resume the worker from a superseded question batch.",
        }
    if record.get("status") == "answered":
        return {
            "schema": QUESTION_SCHEMA,
            "status": "answered",
            "question_id": batch_id,
            "batch_ref": batch_id,
            "answers": _batch_answer_view(record),
            "idempotent": True,
            "durable": {"batch": record},
        }
    if record.get("status") == "awaiting_translation":
        if params.get("canonical_answers") is None:
            return {
                "schema": QUESTION_SCHEMA,
                "status": "awaiting_translation",
                "question_id": batch_id,
                "batch_ref": batch_id,
                "answer_original": record.get("answer_original"),
                "answer_custom_original": record.get("answer_custom_original"),
                "answer_original_language": record.get("answer_original_language"),
                "answer_option_ids": record.get("answer_option_ids"),
                "translation_required_for": record.get("translation_required_for") or [],
                "next_action": "Translate only the listed free-text answers or custom responses, then call the same batch question_ref with canonical_answers.",
                "durable": {"batch": record},
            }
        record, idempotent = _persist_batch_answers(params, batch_id, None)
        if record.get("status") == "superseded":
            return {
                "schema": QUESTION_SCHEMA, "status": "superseded", "question_id": batch_id,
                "batch_ref": batch_id, "resume": False, "durable": {"batch": record},
            }
        return {
            "schema": QUESTION_SCHEMA,
            "status": "answered",
            "question_id": batch_id,
            "batch_ref": batch_id,
            "answers": _batch_answer_view(record),
            "idempotent": idempotent,
            "durable": {"batch": record},
        }

    view = _localized_batch_view(record, params)
    stored = record.get("answers") if isinstance(record.get("answers"), dict) else {}
    unanswered = [
        item for item in view.get("questions") or []
        if str(item.get("question_key") or "") not in stored
    ]
    progress = _batch_progress(record)
    if not bool(params.get("interactive", True)):
        current = unanswered[0] if unanswered else None
        return {
            "schema": QUESTION_SCHEMA,
            "status": "pending_user_input",
            "question_id": batch_id,
            "batch_ref": batch_id,
            "question": current,
            "ui": _batch_form_schema(current) if current else None,
            "progress": progress,
            "next_action": "invoke cortex.question with interactive=true from the main Codex chat",
            "recoverable": True,
            "durable": {"batch": record},
        }
    last_elicitation_id = str(record.get("last_elicitation_id") or "") or None
    total = len(view.get("questions") or [])
    for question in unanswered:
        current_progress = _batch_progress(record)
        position = int(current_progress["answered"]) + 1
        try:
            action, content, elicitation_id = bound_symbol("questions", "_request_mcp_elicitation")(
                f"{position} / {total}",
                _batch_form_schema(question),
                thread_id=str(params.get("thread_id") or ""),
                turn_id=str(params.get("turn_id") or ""),
            )
        except RuntimeError as exc:
            return {
                "schema": QUESTION_SCHEMA,
                "status": "elicitation_unavailable",
                "question_id": batch_id,
                "batch_ref": batch_id,
                "error": redact(str(exc), 1000),
                "progress": current_progress,
                "recoverable": True,
                "durable": {"batch": record},
            }
        if action != "accept":
            return {
                "schema": QUESTION_SCHEMA,
                "status": action if action in {"decline", "cancel"} else "cancel",
                "question_id": batch_id,
                "batch_ref": batch_id,
                "elicitation_id": elicitation_id,
                "progress": current_progress,
                "durable": {"batch": record},
            }
        try:
            answer = _batch_answer_from_content(content, question)
            record = _persist_batch_step_answer(
                params,
                batch_id,
                str(question["question_key"]),
                answer,
                elicitation_id=elicitation_id,
            )
        except ValueError as exc:
            return {
                "schema": QUESTION_SCHEMA,
                "status": "invalid_answer",
                "question_id": batch_id,
                "batch_ref": batch_id,
                "error": redact(str(exc), 1000),
                "progress": _batch_progress(record),
                "recoverable": True,
                "durable": {"batch": record},
            }
        last_elicitation_id = elicitation_id
        if record.get("status") == "superseded":
            return {
                "schema": QUESTION_SCHEMA, "status": "superseded", "question_id": batch_id,
                "batch_ref": batch_id, "resume": False, "durable": {"batch": record},
            }
    if record.get("status") == "awaiting_translation":
        return {
            "schema": QUESTION_SCHEMA,
            "status": "awaiting_translation",
            "question_id": batch_id,
            "batch_ref": batch_id,
            "elicitation_id": last_elicitation_id,
            "progress": _batch_progress(record),
            "answer_original": record.get("answer_original"),
            "answer_custom_original": record.get("answer_custom_original"),
            "answer_original_language": record.get("answer_original_language"),
            "answer_option_ids": record.get("answer_option_ids"),
            "translation_required_for": record.get("translation_required_for") or [],
            "next_action": "Translate only the listed free-text answers or custom responses, then call the same batch question_ref with canonical_answers.",
            "durable": {"batch": record},
        }
    return {
        "schema": QUESTION_SCHEMA,
        "status": "answered",
        "question_id": batch_id,
        "batch_ref": batch_id,
        "elicitation_id": last_elicitation_id,
        "progress": _batch_progress(record),
        "answers": _batch_answer_view(record),
        "durable": {"batch": record},
    }


def _question_record_for_main(params: dict[str, Any], question_id: str) -> dict[str, Any]:
    listed = list_worker_questions({"task_id": params["task_id"], "principal": params["principal"], "thread_id": params.get("thread_id"), "project_root": params.get("project_root")})
    record = next((item for item in listed["questions"] if item.get("question_id") == question_id), None)
    if record is None:
        raise ValueError("question_id does not belong to this task")
    return _question_record_view(record)


def _localized_question_view(record: dict[str, Any], params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Allow the coordinator to localize only the user-facing projection."""
    requires_localization = not str(params.get("user_language") or "en").lower().startswith("en")
    if requires_localization and not str(params.get("localized_question") or "").strip():
        raise ValueError(
            "non-English user questions require localized_question in the task's user_language"
        )
    question = redact(params.get("localized_question") or record["question"], 4000)
    config = _question_config(record)
    localized_header = params.get("localized_header")
    if requires_localization and not str(localized_header or "").strip():
        localized_header = question
    if localized_header:
        config["header"] = redact(localized_header, 200)
    _require_self_contained_question(question, "localized question")
    _require_meaningful_decision_label(config["header"], "localized question header")
    if requires_localization and config.get("options") and not isinstance(params.get("localized_options"), list):
        raise ValueError("non-English choice questions require localized_options")
    if isinstance(params.get("localized_options"), list):
        localized = _question_options(params["localized_options"])
        canonical_options = list(config.get("options") or [])
        if len(localized) != len(canonical_options):
            raise ValueError("localized_options must match the canonical option count")
        merged = []
        for index, (canonical, display) in enumerate(zip(canonical_options, localized)):
            raw_display = params["localized_options"][index]
            supplied_id = str(raw_display.get("option_id") or "") if isinstance(raw_display, dict) else ""
            if supplied_id and safe_id(supplied_id.lower()) != canonical["option_id"]:
                raise ValueError("localized option_id must match the canonical option_id")
            localized_label = display["label"]
            _require_meaningful_decision_label(localized_label, "localized question option")
            description = ""
            if isinstance(raw_display, dict):
                description = redact(
                    str(raw_display.get("description_localized", raw_display.get("description", ""))).strip(),
                    400,
                )
            merged.append({
                **canonical,
                "label_localized": localized_label,
                **({"description_localized": description} if description else {}),
            })
        config["options"] = merged
    localized_custom_label = params.get("localized_custom_label")
    if requires_localization and not str(localized_custom_label or "").strip():
        localized_custom_label = question
    if localized_custom_label:
        config["custom_label"] = redact(localized_custom_label, 200)
    config["localized_for_user"] = requires_localization
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
        if question_id.startswith("batch-"):
            return _cortex_question_batch(params, question_id)
        record = _question_record_for_main(params, question_id)
        if record.get("status") == "answered":
            return {
                "schema": QUESTION_SCHEMA,
                "status": "answered",
                "question_id": question_id,
                "question": record.get("question"),
                "answer": record.get("answer_original") or record.get("answer"),
                "answer_text": record.get("answer_text"),
                "answer_en": record.get("answer_en"),
                "answer_option_ids": record.get("answer_option_ids") or [],
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
    custom_answer = answer.get("custom_response") if isinstance(answer, dict) else None
    user_language = str(params.get("user_language") or "en")
    if custom_answer not in (None, "", [], {}) and not user_language.lower().startswith("en") and not params.get("canonical_answer"):
        return {
            "schema": QUESTION_SCHEMA,
            "status": "awaiting_translation",
            "question_id": question_id or (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "elicitation_id": elicitation_id,
            "answer_original": answer,
            "answer_original_language": user_language,
            "answer_option_ids": answer.get("option_ids") or [],
            "next_action": "Translate only the free-text portion to English, then answer the same durable question with answer plus answer_en.",
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
            "answer_en": params.get("canonical_answer"),
            "resume_context": {"source": "cortex.question", "elicitation_id": elicitation_id, "ui": config, "user_language": user_language},
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
                "answer": record.get("answer_en") or record["answer"],
                "answer_text": record.get("answer_en_text") or record.get("answer_text"),
                "answer_option_ids": record.get("answer_option_ids") or [],
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
