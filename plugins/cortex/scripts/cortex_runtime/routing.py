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
    """Return whether a declared profile/phase pair is structurally valid.

    Phase ownership is an orchestrator decision, not a backend routing matrix.
    Profile metadata may describe preferred/automatic ownership for discovery,
    but it must not reject an explicit worker selection.  The backend still
    validates that both identifiers are known and that the phase is non-empty.
    """
    return bool(profile_name in profiles and isinstance(gate, str) and gate.strip())


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

    # The coordinator owns routing.  The policy document remains a capability
    # registry, but profile classes, task kind, risk, and complexity must not
    # override an explicit model/effort choice.
    raw_model = str(params.get("model") or "").strip()
    if not raw_model:
        raise ValueError("model is required; the orchestrator must select the worker model")
    configured_default_model = str(
        params.get("configured_default_model")
        or (policy["configured_default_model"] if params.get("configured_default") is True else "")
    ).strip()
    configured_default_available = configured_default_model == policy["configured_default_model"]
    # Public worker specs are authoritative.  Policy may reject an unsafe or
    # unavailable choice, but it must never manufacture or replace one.
    chosen_model = raw_model
    if chosen_model not in policy["requestable_models"]:
        raise ValueError("model is not supported by Cortex routing policy")

    selected_model = chosen_model
    model_choice_reason = "explicit_coordinator_request"
    supplied_effort = str(params.get("reasoning_effort") or "").strip().lower()
    if not supplied_effort:
        raise ValueError("reasoning_effort is required; the orchestrator must select worker effort")
    requested_effort = supplied_effort
    selected_effort = requested_effort
    if selected_effort not in policy["supported_efforts"]:
        raise ValueError("reasoning_effort cannot be resolved to a supported effort")
    if selected_model not in policy["supported_models"]:
        raise ValueError("dispatch route cannot be resolved to a Cortex policy model")
    allowed_efforts = policy["model_efforts"].get(selected_model, ())
    if selected_effort not in allowed_efforts:
        raise ValueError(
            f"reasoning_effort for {selected_model} must be one of: "
            + ", ".join(allowed_efforts)
        )
    model_resolution = "explicit_override"
    return {
        "model": chosen_model,
        "configured_default_model": configured_default_model or None,
        "selected_model": selected_model,
        "expected_model": selected_model,
        "model_resolution": model_resolution,
        "reasoning_effort": requested_effort,
        "selected_reasoning_effort": selected_effort,
        "task_kind": task_kind,
        "risk": risk,
        "complexity": complexity,
        "read_only": read_only,
        "capability_source": policy["capability_source"],
        "policy_model": selected_model,
        "policy_reason": "explicit_coordinator_request",
        "model_choice_reason": model_choice_reason,
    }
