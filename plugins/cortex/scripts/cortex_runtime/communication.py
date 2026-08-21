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
    r"\b(?:attempt[_ -]?id|task[_ -]?ref|dispatch[_ -]?ref|report[_ -]?ref|"
    r"sha256|digest|ledger|mcp|native worker|spawn_agent|followup_task)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

MESSAGE_TYPES = {
    "started": "Task started",
    "progress": "Progress update",
    "completed": "Task completed",
    "blocked": "Action needed",
    "question": "Question",
    "error": "Something went wrong",
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
        "The task is blocked until the reported issue is resolved.",
        "Resolve the blocker, then resume the task.",
        "blocked",
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
    if _INTERNAL_RE.search(value):
        failures.append("message exposes internal metadata")
    if previous and value.casefold() == str(previous).strip().casefold():
        failures.append("message repeats the previous update")
    if next_step is None or not str(next_step).strip():
        failures.append("message must include a next step")
    sentences = [part.strip() for part in _SENTENCE_RE.split(value) if part.strip()]
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
    result = {
        "message_type": message_type(kind),
        "message": str(message).strip(),
        "next_step": str(next_step).strip(),
        "profile": profile,
        "metadata": dict(metadata or {}),
    }
    result["quality"] = {"ok": not quality_checks(result["message"], profile=profile, next_step=result["next_step"]),
                         "checks": quality_checks(result["message"], profile=profile, next_step=result["next_step"])}
    return result


def render_lifecycle(outcome: object, *, ok: bool = True, config: Mapping[str, Any] | None = None,
                     metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Render a safe human-facing lifecycle update beside the coordinator receipt."""
    key = str(outcome or "error").strip().lower() if ok else "error"
    message, next_step, kind = _LIFECYCLE_MESSAGES.get(key, _LIFECYCLE_MESSAGES["needs_input"])
    return render(message, kind=kind, next_step=next_step, config=config, metadata=metadata)
