#!/usr/bin/env python3
"""Fail-closed, privacy-preserving native lifecycle hook telemetry.

This hook is deliberately not a public authorization or recovery plane. Cortex
authorizes a worker operation only after its exact opaque ``dispatch_ref`` is
resolved and the attempt is privately joined to the MCP host thread observed by
``SubagentStart`` and ``SubagentStop``. Coordinator capability checks are also
bound to the private MCP host thread. A hook event, process environment, host
session, child id, project path, or ledger row can neither supply nor reconstruct
public authority. Supported SubagentStart and SubagentStop events provide
trusted local observation for worker binding and exact terminal Stop authority.
This is the same-user plugin/database trust boundary, not cryptographic proof
or remote server attestation.

The host invokes this file around native lifecycle events. It returns only a
small, identity-free acknowledgement so a missed, reordered, or malformed hook
event cannot mutate orchestration state or direct the model to guess a task.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping


HOOK_SCHEMA = "cortex/hook-telemetry/v11"
LIFECYCLE_EVENTS = frozenset({"SessionStart", "SubagentStart", "SubagentStop"})
NATIVE_TOOLS = frozenset({"spawn_agent", "wait_agent"})

# Codex validates hookSpecificOutput against the event-specific output wire.
# These are the only registered events whose wire permits hookSpecificOutput;
# the nested fields below are the complete fields this hook may emit. Internal
# telemetry remains a private classification and is deliberately not serialized
# into the host response.
HOOK_SPECIFIC_OUTPUT_EVENTS = frozenset({"SessionStart", "SubagentStart"})

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
HOST_EPOCH_UNAVAILABLE_MARKER = (
    "CORTEX_NATIVE_HOST_EPOCH_UNAVAILABLE: the resumed Codex host process could not prove exclusive "
    "ownership of this session. Do not wait for, resume, or replace any native child. Call the bounded "
    "Cortex inspection once; if it does not return an explicit resume_orchestration action, stop fail-closed."
)


class NativeStopHookFailure(RuntimeError):
    """Content-free signal that the host must retry terminal Stop delivery."""


class NativeContextBoundaryHookFailure(RuntimeError):
    """Content-free signal that the host must retry compact-boundary capture."""


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
    """Return the identity-free classification used by the hook response.

    The separate private observer may durably bind a strictly decoded native
    lifecycle event. This classification itself never retains or emits IDs.
    """
    kind = lifecycle_kind(event)
    if kind is None:
        return None
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
    """Build output accepted by the registered event's Codex output schema.

    ``telemetry_record`` is intentionally computed only as an internal,
    identity-free classification. Codex's strict event-specific wire schemas
    do not define a ``telemetry`` field, so it is dropped at this boundary.
    ``SubagentStop`` and ``Stop`` do not permit ``hookSpecificOutput`` at all.
    """
    kind = lifecycle_kind(event)
    if kind is None:
        return {}
    session_epoch_observed = True
    if kind == "SessionStart":
        try:
            from cortex_runtime.host_workspace_binding import bind_session_workspace
            # SessionStart is a closed host envelope.  Only the host-issued
            # session identity and absolute cwd are used; source/model/etc.
            # remain advisory and never participate in workspace selection.
            bind_session_workspace(event.get("session_id"), event.get("cwd"))
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
    if kind == "SubagentStop":
        try:
            from cortex_runtime.native_lifecycle_observer import observe

            if observe(event) is not True:
                raise RuntimeError("native terminal Stop capture was not accepted")
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            # This exception is content-free and is handled by main with a
            # non-zero exit. Codex must retry instead of treating an
            # uncaptured terminal Stop as successfully acknowledged.
            raise NativeStopHookFailure from exc
    elif kind in {"SessionStart", "SubagentStart"}:
        try:
            from cortex_runtime.native_lifecycle_observer import observe

            session_epoch_observed = observe(event) is True
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            # Hook failure leaves the consumption barrier closed. Never echo
            # event values, child ids, paths, or exception text.
            session_epoch_observed = False
        if kind == "SessionStart" and is_compaction_boundary(event) and not session_epoch_observed:
            raise NativeContextBoundaryHookFailure
    # Keep the classification available to direct callers/tests without
    # leaking it into Codex's model-facing response schema.
    _ = telemetry_record(event)
    if kind not in HOOK_SPECIFIC_OUTPUT_EVENTS:
        return {}

    output: dict[str, Any] = {"hookSpecificOutput": {"hookEventName": kind}}
    context = (
        HOST_EPOCH_UNAVAILABLE_MARKER
        if kind == "SessionStart" and not session_epoch_observed
        else hook_context(event)
    )
    if context:
        output["hookSpecificOutput"]["additionalContext"] = context
    return output


def main() -> None:
    """Read one host event and emit a safe response.

    Ordinary advisory hooks remain fail-open. A relevant terminal Stop is the
    exception: acknowledging it without durable capture would be irreversible,
    so that path emits no details and exits non-zero for host retry.
    """
    try:
        raw = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        print("{}")
        return
    try:
        print(json.dumps(hook_response(_event_mapping(raw)), ensure_ascii=False, separators=(",", ":")))
    except NativeStopHookFailure:
        print("{}")
        raise SystemExit(1) from None
    except NativeContextBoundaryHookFailure:
        print("{}")
        raise SystemExit(1) from None
    except (TypeError, ValueError):
        # Never surface event values or exception text into the model context.
        print("{}")


if __name__ == "__main__":
    main()
