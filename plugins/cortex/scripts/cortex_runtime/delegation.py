"""Pure policy decisions used while preparing a Cortex worker delegation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_GATE_PROFILES = {
    "scope": "planner", "plan": "planner", "discover": "explorer", "architecture": "architect",
    "database_architecture": "database_architect", "implementation": "general",
    "qa": "qa_engineer", "security": "security_auditor",
    "performance": "performance_engineer", "accessibility": "accessibility_auditor",
    "ux": "ux_designer", "review": "code_reviewer",
    "documentation": "technical_writer", "close": "build_verification",
}

DEFAULT_GATE_TASK_KINDS = {
    "scope": "scoping", "plan": "planning", "discover": "discovery", "architecture": "architecture",
    "database_architecture": "database", "implementation": "implementation",
    "qa": "testing", "security": "security", "review": "review",
    "documentation": "documentation", "close": "verification",
}


def select_profile(
    params: Mapping[str, Any],
    *,
    gate: str,
    task_definition: Mapping[str, Any],
    agents: Mapping[str, Any],
    canonical_profile: Callable[[object], str],
    select_implementation_profile: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    profiles_for_gate: Callable[[str], list[str]],
) -> tuple[str, str, Mapping[str, Any] | None]:
    """Resolve profile ownership and retain an auditable selection explanation."""
    requested_agent = canonical_profile(params.get("agent") or "")
    implementation_selection = select_implementation_profile(task_definition) if gate == "implementation" else None
    agent = (
        requested_agent
        or (implementation_selection or {}).get("profile")
        or DEFAULT_GATE_PROFILES.get(gate)
        or (profiles_for_gate(gate) or ["general"])[0]
    )
    if agent not in agents:
        raise ValueError(f"unknown agent '{agent}'")
    selection_reason = str(params.get("selection_reason") or "").strip()
    if not selection_reason:
        if requested_agent:
            selection_reason = f"The coordinator explicitly selected `{agent}` for the `{gate}` phase."
        elif implementation_selection is not None:
            selection_reason = str(implementation_selection["reason"])
        else:
            selection_reason = f"`{agent}` is the canonical automatic owner for the `{gate}` phase."
    return agent, selection_reason, implementation_selection


def task_kind_and_risk(params: Mapping[str, Any], gate: str) -> tuple[str, str]:
    """Apply canonical defaults before model routing evaluates risk and complexity."""
    task_kind = str(params.get("task_kind") or "").strip() or DEFAULT_GATE_TASK_KINDS.get(gate, gate)
    risk = str(params.get("risk") or "").strip().lower()
    if not risk:
        risk = "high" if gate == "security" else "low" if gate in {"scope", "plan", "discover", "documentation"} else "moderate"
    return task_kind, risk


def dispatch_context(
    params: Mapping[str, Any],
    *,
    gate: str,
    agent: str,
    task_kind: str,
    complexity: str,
    resolve_dispatch_route: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[str, dict[str, Any], str | None]:
    """Resolve the native V2 hidden-subagent transport."""
    dispatch_mode = str(params.get("dispatch_mode", "hidden_subagent")).strip() or "hidden_subagent"
    if dispatch_mode != "hidden_subagent":
        raise ValueError("native spawn_agent dispatch is the only supported worker transport")
    route_params = {
        **params,
        "agent": agent,
        "task_kind": task_kind,
        "risk": str(params.get("risk") or "").strip().lower() or ("high" if gate == "security" else "low" if gate in {"scope", "plan", "discover", "documentation"} else "moderate"),
        "complexity": complexity,
        "_security_gate": gate == "security",
    }
    route = resolve_dispatch_route(route_params)
    raw_thread_environment = str(params.get("thread_environment") or "").strip().lower()
    if raw_thread_environment not in {"", "local"}:
        raise ValueError("native hidden subagents use the current workspace")
    return dispatch_mode, route, "local"


def delegation_lists(
    params: Mapping[str, Any],
    task_definition: Mapping[str, Any],
    briefing: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Select bounded worker contract lists with task-level inheritance only for paths."""
    def text_items(value: object) -> list[str]:
        # A malformed task definition may persist one path/criterion as a
        # scalar.  Treat it as one item instead of silently falling back to a
        # broad default or iterating it character-by-character.
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list):
            return []
        return [item.strip() for item in values if isinstance(item, str) and item.strip()]

    def choose(field: str, fallback: list[str], *, inherit_task: bool = False) -> list[str]:
        supplied = params.get(field)
        cleaned = text_items(supplied)
        if cleaned:
            return cleaned
        if inherit_task:
            cleaned = text_items(task_definition.get(field))
            if cleaned:
                return cleaned
        return fallback

    return {
        "acceptance_criteria": choose("acceptance_criteria", list(briefing["acceptance_criteria"])),
        "verification": choose("verification", list(briefing["verification"])),
    }


def spawn_request(
    *,
    dispatch_mode: str,
    gate: str,
    agent: str,
    display_name: str,
    task_name: str,
    profiles: Mapping[str, Mapping[str, Any]],
    selection_reason: str,
    route: Mapping[str, Any],
    thread_environment: str | None,
) -> dict[str, Any]:
    """Build the sole native V2 spawn_agent host request."""
    if dispatch_mode != "hidden_subagent":
        raise ValueError("native spawn_agent dispatch is the only supported worker transport")
    result: dict[str, Any] = {
        "host_tool": "spawn_agent",
        "phase": gate,
        "profile": agent,
        "display_name": display_name,
        "task_name": task_name,
        "capability": profiles[agent]["description"],
        "sandbox": profiles[agent]["sandbox"],
        "route_category": profiles[agent]["route_category"],
        "selection_reason": selection_reason,
        "expected_model": route["selected_model"],
        # Keep the host route confirmation with the private dispatch template.
        # Luna is selected by the coordinator but must be inherited from the
        # host default at native serialization time; Terra/Sol remain explicit.
        "configured_default_model": route.get("configured_default_model"),
        "model_resolution": route.get("model_resolution", "policy"),
        "reasoning_effort": route["selected_reasoning_effort"],
        "fork_turns": "none",
    }
    # The coordinator owns the canonical selection.  The native Codex
    # transport has one special case: Luna is only selectable through the
    # configured default route, so serialization omits model after the host
    # default has been confirmed.  Other models remain explicit.
    del thread_environment
    return result
