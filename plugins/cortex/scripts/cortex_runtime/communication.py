"""User-facing communication profiles and message quality checks.

This module deliberately sits at the presentation boundary.  Durable ledger
records and internal worker metadata must remain structured and are never used
as user prose.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


PROFILES = ("natural", "compact", "technical")
DEFAULT_PROFILE = "natural"
PROFILE_ENV = "CORTEX_COMMUNICATION_PROFILE"
_INTERNAL_RE = re.compile(
    r"\b(?:attempt[_ -]?id|task[_ -]?ref|dispatch[_ -]?ref|"
    r"sha256|digest|ledger|mcp|native worker|spawn_agent|followup_task)\b",
    re.IGNORECASE,
)
_INTERNAL_IDENTIFIER_RE = re.compile(
    r"\b(?:attempt[_ -]?id|task[_ -]?ref|dispatch[_ -]?ref|sha256|digest)\b",
    re.IGNORECASE,
)
_TECHNICAL_RE = re.compile(
    r"\b(?:orchestrat(?:or|ion)|pipeline|worker|gate|ledger|validator|delegat(?:e|ion)|"
    r"оркестратор\w*|пайплайн\w*|воркер\w*|гейт\w*|леджер\w*|валидатор\w*|делегац\w*)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_INTERNAL_VALUE_RE = re.compile(r"(?:codex://[^\s]+|\b(?:attempt|task|dispatch)[_-]?(?:id|ref)\s*[:=]\s*[^\s,.;]+|\b[a-f0-9]{16,}\b)", re.IGNORECASE)
_PATH_VALUE_RE = re.compile(
    r"(?:\b[\w.-]+/[\w./-]+\b|\b[\w.-]+\.(?:py|md|json|toml|ya?ml|ts|tsx|js|jsx|sql|cs|cpp|h|hpp)\b|"
    r"(?<!\w)/(?:[^\s,.;]+))",
    re.IGNORECASE,
)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

MESSAGE_TYPES = {
    "started": "Task started",
    "progress": "Progress update",
    "completed": "Task completed",
    "blocked": "Action needed",
    "question": "Question",
    "error": "Something went wrong",
}

_TECHNICAL_RECOVERY_MESSAGE = {
    "en": (
        "The task is continuing automatically.",
        "No action is needed while recovery continues.",
        "progress",
    ),
    "ru": (
        "Задача автоматически продолжает работу.",
        "Пока выполняется восстановление, действий не требуется.",
        "progress",
    ),
}


_LIFECYCLE_MESSAGES = {
    "ready_to_spawn": (
        "The task has started and the assigned work is ready to begin.",
        "Start the assigned work, then return when it is finished.",
        "started",
    ),
    "waiting_workers": (
        "The task is in progress and is waiting for the assigned work to finish.",
        "Wait for the assigned work to finish, then continue the task.",
        "progress",
    ),
    "completion_pending": (
        "A completed result is ready for review before the task can continue.",
        "Review the completed result and continue when it is accepted.",
        "completed",
    ),
    "awaiting_plan_approval": (
        "The plan is ready for your review. Approval is recommended when its risks and questions are resolved.",
        "Review the plan, then approve it or request a specific change.",
        "question",
    ),
    "completed": (
        "The task is complete and the verified result is ready.",
        "Review the verified result.",
        "completed",
    ),
    "blocked": (
        "The task needs automatic recovery for the recorded issue before it can continue.",
        "Cortex will route the issue to the same task or ask for one concrete decision.",
        "question",
    ),
    "needs_input": (
        "The task needs a clear decision before it can continue.",
        "Provide the requested decision or clarification.",
        "question",
    ),
    "error": (
        "The task could not continue because an input or validation issue was found.",
        "Correct the reported issue and retry.",
        "error",
    ),
}

_SAFE_FALLBACKS = {
    "started": ("The task has started.", "Continue when ready."),
    "progress": ("The task is in progress.", "Continue when the update is ready."),
    "completed": ("The task is complete.", "Review the result."),
    "blocked": ("The task needs recovery.", "Cortex will route the issue or ask for one decision."),
    "question": ("A decision is needed before the task can continue.", "Provide the requested decision."),
    "error": ("The task could not continue.", "Correct the issue and retry."),
}
_SAFE_FALLBACKS_RU = {
    "started": ("Задача начата.", "Продолжите, когда будете готовы."),
    "progress": ("Задача выполняется.", "Продолжите после получения обновления."),
    "completed": ("Задача завершена.", "Проверьте результат."),
    "blocked": ("Для задачи требуется восстановление.", "Cortex направит проблему на исправление или запросит одно решение."),
    "question": ("Для продолжения нужно решение.", "Предоставьте запрошенное решение."),
    "error": ("Задача не продолжилась.", "Исправьте проблему и повторите попытку."),
}

_LIFECYCLE_TRANSLATIONS = {
    "en": {
        "ready_to_spawn": ("The task has started and the assigned work is ready to begin.", "Start the assigned work, then return when it is finished."),
        "waiting_workers": ("The task is in progress; assigned work is still running.", "No action is needed while the work is running."),
        "completion_pending": ("A completed result is ready for review before the task can continue.", "Review the completed result and continue when it is accepted."),
        "awaiting_plan_approval": ("The plan is ready for your review. Approval is recommended when its risks and questions are resolved.", "Review the plan, then approve it or request a specific change."),
        "completed": ("The task is complete and the verified result is ready.", "Review the verified result."),
        "blocked": ("The task needs automatic recovery for the recorded issue.", "Cortex will route the issue or ask for one concrete decision."),
        "needs_input": ("The task needs a clear decision before it can continue.", "Provide the requested decision or clarification."),
        "error": ("The task could not continue because an input or validation issue was found.", "Correct the reported issue and retry."),
    },
    "ru": {
        "ready_to_spawn": ("Задача начата, работа готова к выполнению.", "Дождитесь завершения работы и вернитесь к задаче."),
        "waiting_workers": ("Задача выполняется; назначенная работа ещё продолжается.", "Пока работа выполняется, действий не требуется."),
        "completion_pending": ("Результат готов и ожидает проверки.", "Проверьте результат и продолжите после подтверждения."),
        "awaiting_plan_approval": ("План готов к проверке. Рекомендуется утвердить его после проверки рисков и вопросов.", "Проверьте план и утвердите его либо укажите конкретное изменение."),
        "completed": ("Задача завершена, проверенный результат готов.", "Проверьте результат."),
        "blocked": ("Для задачи требуется автоматическое восстановление.", "Cortex направит проблему на исправление или запросит одно решение."),
        "needs_input": ("Для продолжения нужно ваше решение.", "Предоставьте запрошенное решение или уточнение."),
        "error": ("Задача не продолжилась из-за ошибки входных данных или проверки.", "Исправьте указанную проблему и повторите попытку."),
    },
}
_COMPACT_LIFECYCLE = {
        "en": {"ready_to_spawn": ("Task started.", "Continue when ready."), "waiting_workers": ("Work is running.", "No action needed."), "completion_pending": ("Result ready for review.", "Review it to continue."), "awaiting_plan_approval": ("Plan ready for approval.", "Approve or request a change."), "completed": ("Task complete.", "Review the result."), "blocked": ("The task needs recovery.", "Cortex will route the issue or ask for one decision."), "needs_input": ("Decision needed.", "Provide the decision."), "error": ("Task paused by an error.", "Fix it and retry.")},
    "ru": {"ready_to_spawn": ("Задача начата.", "Вернитесь после завершения."), "waiting_workers": ("Работа выполняется.", "Действий не требуется."), "completion_pending": ("Результат готов к проверке.", "Проверьте его."), "awaiting_plan_approval": ("План готов к утверждению.", "Утвердите или запросите изменение."), "completed": ("Задача завершена.", "Проверьте результат."), "blocked": ("Для задачи требуется восстановление.", "Cortex направит проблему на исправление или запросит одно решение."), "needs_input": ("Нужно решение.", "Предоставьте его."), "error": ("Задача приостановлена из-за ошибки.", "Исправьте и повторите.")},
}


def _language(config: Mapping[str, Any] | None) -> str:
    return "ru" if str((config or {}).get("user_language") or "en").lower().startswith("ru") else "en"


def _public_text(value: object, profile: str) -> str:
    text = str(value or "").strip()
    # Identifiers, paths, and URI-like values are internal metadata in every
    # user profile, including ``technical``.  Technical mode may retain useful
    # implementation vocabulary, but it never authorizes exposing private
    # task/attempt refs or repository locations at the presentation boundary.
    text = _INTERNAL_VALUE_RE.sub("", text)
    text = _PATH_VALUE_RE.sub("", text)
    if profile in {"natural", "compact"}:
        text = re.sub(r"\s{2,}", " ", text).strip(" ,:;-")
    return text


def public_plan_copy(summary: object, steps: list[object], *, config: Mapping[str, Any] | None = None) -> tuple[str, list[str]]:
    """Return a bounded, localized plan projection safe for ordinary chat.

    Worker package titles are useful for the internal plan but are not a safe
    user contract: they can contain English copy, repository paths, or opaque
    references.  Keep an already-localized, plain-language item when it is
    safe; otherwise use deterministic language-specific summaries.
    """
    profile = select_profile(config)
    language = _language(config)
    if language == "ru":
        default_summary = "План готов к проверке."
        default_steps = [
            "Проверить цель и границы задачи.",
            "Выполнить запланированную работу.",
            "Запустить предусмотренные проверки.",
            "Проверить результат и закрыть задачу.",
        ]
    else:
        default_summary = "The plan is ready for review."
        default_steps = [
            "Confirm the requested outcome and scope.",
            "Complete the planned work.",
            "Run the planned verification.",
            "Review the result and close the task.",
        ]

    def safe(value: object, fallback: str) -> str:
        raw = str(value or "").strip()
        # Decide whether the whole item is safe before redaction.  Redacting a
        # path/ref first can leave a misleading fragment such as ``"Изменить
        # для"``; a natural projection must fall back to a complete sentence.
        if raw and (
            _PATH_VALUE_RE.search(raw)
            or (profile in {"natural", "compact"} and (_INTERNAL_RE.search(raw) or _TECHNICAL_RE.search(raw)))
        ):
            return fallback
        candidate = _public_text(raw, profile)
        if not candidate:
            return fallback
        if language == "ru" and not _CYRILLIC_RE.search(candidate):
            return fallback
        if language != "ru" and _CYRILLIC_RE.search(candidate):
            return fallback
        return candidate

    public_summary = safe(summary, default_summary)
    public_steps: list[str] = []
    for index, value in enumerate(steps or []):
        fallback = default_steps[index] if index < len(default_steps) else default_steps[-1]
        public_steps.append(safe(value, fallback))
    public_steps = public_steps[:5]
    while len(public_steps) < 3:
        fallback = default_steps[len(public_steps)]
        if fallback not in public_steps:
            public_steps.append(fallback)
        else:
            break
    public_steps = public_steps or default_steps[:3]
    return public_summary, public_steps


def public_risks(values: object, *, config: Mapping[str, Any] | None = None, limit: int = 4) -> list[str]:
    """Keep only localized, path-free risk summaries in the public view."""
    language = _language(config)
    profile = select_profile(config)
    fallback = (
        "Остаётся существенный риск или пробел в проверке."
        if language == "ru" else
        "A material risk or verification gap remains."
    )
    result: list[str] = []
    for value in values if isinstance(values, list) else []:
        raw = str(value or "").strip()
        if raw and (
            _PATH_VALUE_RE.search(raw)
            or (profile in {"natural", "compact"} and (_INTERNAL_RE.search(raw) or _TECHNICAL_RE.search(raw)))
        ):
            continue
        candidate = _public_text(raw, profile)
        if not candidate:
            continue
        if language == "ru" and not _CYRILLIC_RE.search(candidate):
            continue
        if language != "ru" and _CYRILLIC_RE.search(candidate):
            continue
        result.append(candidate)
    return result[:limit] or ([fallback] if values else [])


def _contract_path() -> Path:
    return Path(__file__).resolve().parents[2] / "profiles.json"


def _configured_profiles() -> Mapping[str, Any]:
    try:
        data = json.loads(_contract_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    value = data.get("communication", {})
    return value if isinstance(value, dict) else {}


def select_profile(config: Mapping[str, Any] | None = None, *, env: Mapping[str, str] | None = None) -> str:
    """Resolve a communication profile, falling back to natural safely."""
    config = config or {}
    env = os.environ if env is None else env
    value = config.get("communication_profile") or config.get("profile") or env.get(PROFILE_ENV)
    value = str(value or DEFAULT_PROFILE).strip().lower()
    aliases = _configured_profiles().get("aliases", {})
    value = str(aliases.get(value, value)) if isinstance(aliases, dict) else value
    return value if value in PROFILES else DEFAULT_PROFILE


def message_type(value: object) -> str:
    """Return a stable human-readable type, never an internal enum token."""
    key = str(value or "").strip().lower().replace(" ", "_")
    return MESSAGE_TYPES.get(key, "Update")


def separate_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Separate user-visible content from internal transport metadata."""
    internal = payload.get("metadata", {})
    visible = {key: value for key, value in payload.items() if key != "metadata"}
    return {"message": visible, "metadata": dict(internal) if isinstance(internal, Mapping) else {}}


def quality_checks(text: str, *, profile: str = DEFAULT_PROFILE, previous: str | None = None,
                   next_step: str | None = None) -> list[str]:
    """Return actionable quality failures for user-facing prose."""
    failures: list[str] = []
    value = str(text or "").strip()
    resolved = profile if profile in PROFILES else DEFAULT_PROFILE
    if not value:
        failures.append("message is empty")
        return failures
    if _INTERNAL_IDENTIFIER_RE.search(value) or _INTERNAL_VALUE_RE.search(value) or _PATH_VALUE_RE.search(value):
        failures.append("message exposes internal metadata")
    if resolved in {"natural", "compact"} and (_INTERNAL_RE.search(value) or _TECHNICAL_RE.search(value)):
        failures.append("message uses internal technical language")
    if previous and value.casefold() == str(previous).strip().casefold():
        failures.append("message repeats the previous update")
    if next_step is None or not str(next_step).strip():
        failures.append("message must include a next step")
    sentences = [part.strip() for part in _SENTENCE_RE.split(value) if part.strip()]
    normalized_sentences = [re.sub(r"\W+", " ", sentence.casefold()).strip() for sentence in sentences]
    if len(normalized_sentences) != len(set(normalized_sentences)):
        failures.append("message repeats a sentence")
    if resolved == "compact" and len(sentences) > 3:
        failures.append("compact profile is too verbose")
    if resolved == "technical" and len(value) < 20:
        failures.append("technical profile lacks useful detail")
    if resolved == "natural" and len(value) > 1200:
        failures.append("natural profile is too verbose")
    return failures


def render(message: str, *, kind: object = "progress", next_step: str = "Continue when ready",
           metadata: Mapping[str, Any] | None = None, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a user message while keeping internal metadata in its own field."""
    profile = select_profile(config)
    normalized_kind = str(kind or "progress").strip().lower().replace(" ", "_")
    raw_message = str(message or "").strip()
    raw_next_step = str(next_step or "").strip()
    public_message = _public_text(raw_message, profile)
    public_next_step = _public_text(raw_next_step, profile)
    # Validate the caller's original candidate before redaction.  Redacting
    # first can turn unsafe text into misleading fragments such as ``Use`` or
    # ``Keep .`` and incorrectly set quality.ok=true.
    failures = quality_checks(raw_message, profile=profile, next_step=raw_next_step)
    fallback_applied = bool(failures)
    if fallback_applied:
        fallbacks = _SAFE_FALLBACKS_RU if _language(config) == "ru" else _SAFE_FALLBACKS
        safe_message, safe_next_step = fallbacks.get(
            normalized_kind, fallbacks["progress"]
        )
        message = safe_message
        next_step = safe_next_step
    result = {
        "message_type": message_type(kind),
        "message": _public_text(message, profile),
        "next_step": _public_text(next_step, profile),
        "profile": profile,
        "metadata": dict(metadata or {}),
    }
    result["quality"] = {
        "ok": not quality_checks(result["message"], profile=profile, next_step=result["next_step"]),
        "checks": failures,
        "fallback_applied": fallback_applied,
    }
    result["detail_level"] = {"natural": "plain", "compact": "minimal", "technical": "diagnostic"}[profile]
    return result


def render_lifecycle(outcome: object, *, ok: bool = True, config: Mapping[str, Any] | None = None,
                     metadata: Mapping[str, Any] | None = None,
                     user_question: bool = False,
                     explicit_plan_approval: bool = False) -> dict[str, Any]:
    """Render lifecycle text with a strict technical-state presentation firewall.

    ``blocked``, technical ``needs_input`` and ``error`` receipts are internal
    recovery states. They never become a visible user question or tell the
    user to repair/retry Cortex. A visible decision is opt-in only for a real
    task question or an explicitly requested plan approval.
    """
    metadata_value = metadata if isinstance(metadata, Mapping) else {}
    raw_key = str(outcome or "error").strip().lower()
    allow_decision = bool(
        user_question
        or explicit_plan_approval
        or metadata_value.get("user_question") is True
        or metadata_value.get("explicit_plan_approval") is True
    )
    technical_keys = {"blocked", "needs_input", "error"}
    if (raw_key in technical_keys and not allow_decision) or (raw_key == "awaiting_plan_approval" and not allow_decision):
        key = "technical_recovery"
    else:
        key = raw_key if ok else "technical_recovery"
    message, next_step, kind = _LIFECYCLE_MESSAGES.get(key, _LIFECYCLE_MESSAGES["needs_input"])
    language = _language(config)
    if key == "technical_recovery":
        message, next_step, kind = _TECHNICAL_RECOVERY_MESSAGE[language]
    else:
        message, next_step = _LIFECYCLE_TRANSLATIONS[language].get(key, (message, next_step))
    if select_profile(config) == "compact" and key != "technical_recovery":
        message, next_step = _COMPACT_LIFECYCLE[language].get(key, (message, next_step))
    result = render(message, kind=kind, next_step=next_step, config=config, metadata=metadata)
    if key in {"waiting_workers", "technical_recovery"}:
        result["output_policy"] = "silent"
        result["allowed_visible_events"] = []
    if key == "technical_recovery":
        result["presentation_policy"] = "internal_recovery"
    if select_profile(config) == "technical":
        result["technical_context"] = {"outcome": raw_key, "ok": bool(ok), "visible": key != "technical_recovery"}
    return result


def render_plan(summary: str, steps: list[str], *, question: str | None = None,
                recommendation: str | None = None, config: Mapping[str, Any] | None = None,
                metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Render a bounded plan summary with no more than five visible steps."""
    profile = select_profile(config)
    language = _language(config)
    summary, safe_steps = public_plan_copy(summary, steps, config=config)
    visible_steps = [_public_text(item, profile) for item in safe_steps if str(item).strip()][:5]
    title, next_label, recommendation_label = (("План", "Ответьте на вопрос ниже.", "Рекомендация")
                                                if language == "ru" else ("Plan", "Reply to the question below.", "Recommendation"))
    lines = [f"{title}: {_public_text(summary, profile)}"]
    lines.extend(f"{index}. {step}" for index, step in enumerate(visible_steps, 1))
    if recommendation and profile == "technical":
        lines.append(f"{recommendation_label}: {_public_text(recommendation, profile)}")
    result = render("\n".join(lines), kind="question", next_step=next_label, config=config, metadata=metadata)
    if question:
        question_projection = render(
            str(question), kind="question", next_step=next_label, config=config,
        )
        result["question"] = question_projection["message"]
        result["question_quality"] = question_projection["quality"]
    else:
        result["question"] = None
    return result
