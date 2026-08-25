#!/usr/bin/env python3
"""Fail-closed, privacy-preserving native lifecycle hook telemetry.

This hook is deliberately not an authorization, recovery, or runtime-control
plane. Cortex v11 authorizes coordinator calls with the explicit ``task_ref``
and ``coordinator_ref`` pair and worker calls with the explicit ``task_ref`` and
``assignment_ref`` pair. A hook event, process environment, host session,
thread, child id, project path, or ledger row can neither supply nor
reconstruct either bearer.

The host invokes this file around native lifecycle events. It returns only a
small, identity-free acknowledgement so a missed, reordered, or malformed hook
event cannot mutate orchestration state or direct the model to guess a task.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping


HOOK_SCHEMA = "cortex/hook-telemetry/v11"
LIFECYCLE_EVENTS = frozenset({"SessionStart", "SubagentStart", "SubagentStop", "Stop", "PostToolUse"})
NATIVE_TOOLS = frozenset({"spawn_agent", "wait", "wait_agent"})

# These constant markers intentionally contain no task, session, process,
# dispatch, path, capability, or assignment authority. They are safe to render in a
# host context window and safe when a hook sees only a partial event.
CAPABILITY_HANDOFF_MARKER = (
    "CORTEX_V11_CAPABILITY_HANDOFF: retain the exact task_ref and coordinator_ref already held "
    "by this coordinator only in its bounded private handoff. Do not place either value in worker prompts, "
    "logs, findings, artifacts, or native tool arguments. If either value is absent after handoff, fail closed and "
    "do not infer a task, capability, worker, or recovery route."
)
CAPABILITY_MISSING_MARKER = (
    "CORTEX_V11_CAPABILITY_MISSING_FAIL_CLOSED: no task-scoped Cortex action is authorized. "
    "Do not infer, reconstruct, request, or substitute task_ref, coordinator_ref, assignment_ref, session, "
    "environment, ledger, project, dispatch, or assignment authority."
)


def _event_mapping(value: object) -> Mapping[str, Any]:
    """Return an event mapping without coercing or retaining untrusted data."""
    return value if isinstance(value, Mapping) else {}


def lifecycle_kind(event: Mapping[str, Any]) -> str | None:
    """Classify only the exact lifecycle events the bundled hook subscribes to."""
    value = event.get("hook_event_name")
    return value if isinstance(value, str) and value in LIFECYCLE_EVENTS else None


def native_tool_name(event: Mapping[str, Any]) -> str | None:
    """Classify exact native V2 calls; aliases and arbitrary tools are ignored."""
    value = event.get("tool_name")
    return value if isinstance(value, str) and value in NATIVE_TOOLS else None


def is_compaction_boundary(event: Mapping[str, Any]) -> bool:
    """Recognize only the host's bounded SessionStart boundary label."""
    if lifecycle_kind(event) != "SessionStart":
        return False
    source = event.get("source")
    return isinstance(source, str) and source.strip().lower() in {"compact", "compaction", "clear", "reset", "resume"}


def telemetry_record(event: Mapping[str, Any]) -> dict[str, str] | None:
    """Return a non-identifying telemetry classification, never a durable record.

    The hook does not write a ledger, touch a project, inspect a tool result, or
    emit IDs. The parent runtime remains the only authority that can bind an
    exact spawn result and decide whether a worker is waitable or terminal.
    """
    kind = lifecycle_kind(event)
    if kind is None:
        return None
    tool = native_tool_name(event)
    if kind == "PostToolUse" and tool is None:
        return None
    if kind == "PostToolUse":
        return {"schema": HOOK_SCHEMA, "event": f"native_{tool}"}
    return {"schema": HOOK_SCHEMA, "event": kind.lower()}


def hook_context(event: Mapping[str, Any]) -> str | None:
    """Return the sole optional model-facing advisory for a lifecycle event.

    A compaction marker tells the coordinator to preserve an already-held
    capability pair. No hook can recover that pair. A host may report only a
    boolean absence marker; it never becomes an instruction to inspect a task,
    read a ledger, or launch a replacement.
    """
    if is_compaction_boundary(event):
        return CAPABILITY_HANDOFF_MARKER
    # A hook is not permitted to inspect a raw capability. A host may provide
    # this boolean capability-presence observation, but an omitted or malformed
    # field is deliberately not treated as evidence either way.
    if event.get("cortex_capability_present") is False:
        return CAPABILITY_MISSING_MARKER
    return None


def hook_response(event: Mapping[str, Any]) -> dict[str, Any]:
    """Build bounded hook output without reflecting event values."""
    record = telemetry_record(event)
    if record is None:
        return {}
    output: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": lifecycle_kind(event),
            "telemetry": record,
        }
    }
    context = hook_context(event)
    if context:
        output["hookSpecificOutput"]["additionalContext"] = context
    return output


def main() -> None:
    """Read one host event and emit a safe, fail-open JSON response."""
    try:
        raw = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        print("{}")
        return
    try:
        print(json.dumps(hook_response(_event_mapping(raw)), ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        # Never surface event values or exception text into the model context.
        print("{}")


if __name__ == "__main__":
    main()
