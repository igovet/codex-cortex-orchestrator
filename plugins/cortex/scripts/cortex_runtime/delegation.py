"""Pure policy decisions used while preparing a Cortex worker delegation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_GATE_PROFILES = {
    "scope": "planner", "plan": "planner", "discover": "explorer", "architecture": "architect",
    "database_architecture": "database_architect", "implementation": "general",
    "qa": "qa_engineer", "security": "security_auditor",
    "performance": "performance_engineer", "accessibility": "accessibility_engineer",
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
) -> tuple[str, str, dict[str, Any], str | None]:
    """Resolve hidden/visible transport without allowing a visible fallback."""
    dispatch_mode = str(params.get("dispatch_mode", "hidden_subagent")).strip() or "hidden_subagent"
    if dispatch_mode not in {"hidden_subagent", "visible_thread"}:
        raise ValueError("dispatch_mode must be hidden_subagent or visible_thread")
    luna_fallback = str(params.get("luna_fallback", "terra")).strip() or "terra"
    if luna_fallback != "terra":
        raise ValueError("luna_fallback must be terra")
    route_params = {
        **params,
        "agent": agent,
        "task_kind": task_kind,
        "risk": str(params.get("risk") or "").strip().lower() or ("high" if gate == "security" else "low" if gate in {"scope", "plan", "discover", "documentation"} else "moderate"),
        "complexity": complexity,
        "_security_gate": gate == "security",
    }
    if dispatch_mode == "visible_thread":
        route_params["available_models"] = params.get("available_thread_models")
    route = resolve_dispatch_route(route_params)
    raw_thread_environment = str(params.get("thread_environment") or "").strip().lower()
    if dispatch_mode == "visible_thread":
        if route["selected_model"] != "gpt-5.6-luna":
            raise ValueError("visible_thread is reserved for a Luna policy route")
        if route.get("host_available_models") is None:
            raise ValueError("visible_thread requires exact available_thread_models from native create_thread")
        route["model_resolution"] = "visible_thread"
        thread_environment = raw_thread_environment or "local"
        if thread_environment not in {"local", "worktree"}:
            raise ValueError("thread_environment must be local or worktree")
    else:
        if raw_thread_environment:
            raise ValueError("thread_environment applies only to visible_thread")
        thread_environment = None
    return dispatch_mode, luna_fallback, route, thread_environment


def delegation_lists(
    params: Mapping[str, Any],
    task_definition: Mapping[str, Any],
    briefing: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Select bounded worker contract lists with task-level inheritance only for paths."""
    def choose(field: str, fallback: list[str], *, inherit_task: bool = False) -> list[str]:
        supplied = params.get(field)
        if isinstance(supplied, list):
            cleaned = [item.strip() for item in supplied if isinstance(item, str) and item.strip()]
            if cleaned:
                return cleaned
        if inherit_task:
            inherited = task_definition.get(field)
            if isinstance(inherited, list):
                cleaned = [item.strip() for item in inherited if isinstance(item, str) and item.strip()]
                if cleaned:
                    return cleaned
        return fallback

    return {
        "allowed_paths": choose("allowed_paths", ["."], inherit_task=True),
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
    """Build the native host request while keeping policy expectation distinct from override."""
    host_tool = "create_thread" if dispatch_mode == "visible_thread" else "spawn_agent"
    result: dict[str, Any] = {
        "host_tool": host_tool,
        "phase": gate,
        "profile": agent,
        "display_name": display_name,
        "task_name": task_name,
        "capability": profiles[agent]["description"],
        "sandbox": profiles[agent]["sandbox"],
        "route_category": profiles[agent]["route_category"],
        "selection_reason": selection_reason,
        "expected_model": route.get("expected_model") or route["selected_model"],
        "model_resolution": route.get("model_resolution", "policy"),
        "reasoning_effort": route["selected_reasoning_effort"],
    }
    if host_tool == "spawn_agent":
        result["fork_turns"] = "none"
    if route.get("model_resolution") != "configured_default":
        result["model"] = route["selected_model"]
    if host_tool == "create_thread":
        result["thread_environment"] = thread_environment
    return result
