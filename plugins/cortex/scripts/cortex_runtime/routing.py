"""Model-routing policy evaluation separated from ledger mutation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def profiles_for_gate(profiles: Mapping[str, Mapping[str, Any]], gate: str) -> list[str]:
    """Return only automatic profiles explicitly assigned to a lifecycle gate."""
    return sorted(
        name
        for name, profile in profiles.items()
        if profile.get("route_category") == "automatic" and gate in profile.get("gates", [])
    )


def profile_can_own_gate(profiles: Mapping[str, Mapping[str, Any]], profile_name: str, gate: str) -> bool:
    profile = profiles.get(profile_name)
    if profile is None:
        return False
    return gate in profile.get("gates", []) or (profile.get("route_category") == "manual" and gate == "implementation")


def resolve_dispatch_route(
    params: dict[str, Any],
    *,
    profiles: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
    canonical_profile: Callable[[Any], str],
    normalize_routing_id: Callable[[Any, str], str],
    select_project_root: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Resolve a policy-compliant model and effort without writing ledger state."""
    select_project_root(params)
    profile_name = canonical_profile(params.get("agent") or params.get("profile") or "")
    profile = profiles.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown agent '{profile_name}'")
    task_kind = normalize_routing_id(params.get("task_kind"), "task_kind")
    risk = str(params.get("risk", "")).strip().lower()
    if risk not in {"low", "moderate", "high", "critical"}:
        raise ValueError("risk must be low, moderate, high, or critical")
    complexity = str(params.get("complexity") or "C1").strip().upper()
    if complexity not in {"C1", "C2", "C3"}:
        raise ValueError("complexity must be C1, C2, or C3")
    read_only = profile.get("sandbox") == "read-only"
    security_context = task_kind == "security" or profile_name == "security_auditor" or params.get("_security_gate") is True
    if security_context:
        task_kind = "security"
    lightweight_dispatch = (
        task_kind in policy["lightweight_task_kinds"]
        or task_kind.startswith("read_only")
        or task_kind.startswith("read_discovery")
        or task_kind.startswith("data_gather")
    )
    analysis_dispatch = lightweight_dispatch or task_kind in policy["analysis_task_kinds"] or any(
        task_kind.startswith(prefix) for prefix in policy["analysis_task_kind_prefixes"]
    )
    read_only = read_only or analysis_dispatch

    profile_classes = policy["profile_classes"]
    def profile_class(name: str) -> str:
        for class_name, names in profile_classes.items():
            if name in names:
                return class_name
        raise RuntimeError(f"Cortex model routing has no class for profile {name}")

    if security_context:
        policy_model, policy_reason = policy["security_model"], "security_profile_or_gate"
    elif profile_name == "explorer":
        policy_model, policy_reason = policy["explorer_model"], "explorer_always_luna"
    else:
        selected_class = profile_class(profile_name)
        if selected_class == "deep":
            policy_model, policy_reason = "gpt-5.6-terra", "deep_profile"
        elif profile_name == "planner" and complexity in {"C2", "C3"}:
            policy_model, policy_reason = "gpt-5.6-terra", "complex_planning"
        elif task_kind in policy["terra_task_kinds"]:
            policy_model, policy_reason = "gpt-5.6-terra", "terra_task_kind"
        elif risk in {"high", "critical"}:
            policy_model, policy_reason = "gpt-5.6-terra", "high_failure_cost"
        elif selected_class == "efficient":
            policy_model, policy_reason = policy["configured_default_model"], "efficient_profile"
        else:
            policy_model, policy_reason = policy["configured_default_model"], "bounded_adaptive_work"
    raw_requested_model = str(params.get("requested_model") or "").strip()
    raw_user_requested_model = str(params.get("user_requested_model") or "").strip()
    configured_default_model = str(
        params.get("configured_default_model")
        or (policy["configured_default_model"] if params.get("configured_default") is True else "")
    ).strip()
    configured_default_available = configured_default_model == policy["configured_default_model"]
    requested_model = raw_requested_model or raw_user_requested_model or policy_model
    if requested_model not in policy["requestable_models"]:
        raise ValueError("requested_model is not supported by Cortex routing policy")
    if raw_user_requested_model and raw_user_requested_model not in policy["requestable_models"]:
        raise ValueError("user_requested_model is not supported by Cortex routing policy")
    if raw_user_requested_model and raw_user_requested_model != requested_model:
        raise ValueError("user_requested_model must match requested_model")

    if profile_name == "explorer":
        if requested_model != policy["configured_default_model"]:
            raise ValueError("explorer always uses gpt-5.6-luna; Terra is reserved for host fallback")
        selected_model = policy["configured_default_model"]
        model_choice_reason = "explorer_policy"
    elif security_context:
        if requested_model != policy["security_model"]:
            raise ValueError("security work always uses gpt-5.6-sol")
        selected_model = policy["security_model"]
        model_choice_reason = "security_policy"
    elif requested_model == "gpt-5.6-sol":
        if raw_user_requested_model != "gpt-5.6-sol":
            raise ValueError("non-security gpt-5.6-sol requires user_requested_model=gpt-5.6-sol")
        selected_model = requested_model
        model_choice_reason = "explicit_user_request"
    else:
        selected_model = requested_model
        if raw_user_requested_model:
            model_choice_reason = "explicit_user_request"
        elif raw_requested_model:
            model_choice_reason = "coordinator_selected_terra" if selected_model == "gpt-5.6-terra" else "coordinator_selected_luna"
        else:
            model_choice_reason = policy_reason

    effort_order = policy["reasoning_effort_order"]
    def higher_effort(*efforts: str) -> str:
        return max(efforts, key=effort_order.__getitem__)

    if profile_name == "explorer":
        default_effort = policy["explorer_effort_by_risk"][risk]
    elif security_context:
        default_effort = policy["security_effort_by_complexity"][complexity]
    else:
        if selected_model != "gpt-5.6-luna":
            model_effort = policy["terra_effort_by_complexity"][complexity]
        elif profile_class(profile_name) == "efficient":
            model_effort = policy["luna_efficient_effort_by_complexity"][complexity]
        else:
            model_effort = policy["luna_bounded_effort_by_complexity"][complexity]
        default_effort = higher_effort(model_effort, policy["model_effort_floor_by_risk"][risk])
    requested_effort = str(params.get("requested_reasoning_effort") or "").strip().lower() or default_effort
    selected_effort = "low" if requested_effort == "none" else requested_effort
    if selected_effort not in policy["supported_efforts"]:
        raise ValueError("requested_reasoning_effort cannot be resolved to a supported effort")
    minimum_effort = None
    if security_context:
        minimum_effort = policy["security_effort_by_complexity"][complexity]
    elif profile_name != "explorer":
        minimum_effort = default_effort
    if minimum_effort and effort_order[selected_effort] < effort_order[minimum_effort]:
        selected_effort = minimum_effort
    if selected_model not in policy["supported_models"]:
        raise ValueError("dispatch route cannot be resolved to a Cortex policy model")
    model_resolution = "configured_default" if selected_model == policy["configured_default_model"] and configured_default_available else "explicit_override"
    return {
        "requested_model": requested_model,
        "configured_default_model": configured_default_model or None,
        "selected_model": selected_model,
        "expected_model": selected_model,
        "model_resolution": model_resolution,
        "requested_reasoning_effort": requested_effort,
        "selected_reasoning_effort": selected_effort,
        "task_kind": task_kind,
        "risk": risk,
        "complexity": complexity,
        "read_only": read_only,
        "capability_source": policy["capability_source"],
        "policy_model": policy_model,
        "policy_reason": policy_reason,
        "model_choice_reason": model_choice_reason,
        "user_requested_model": raw_user_requested_model or None,
    }
