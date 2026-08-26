"""Canonical model/effort policy derived from the bundled profile contract."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def model_effort_registry(model_routing: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Return the ordered native model -> supported-efforts capability registry.

    ``profiles.json`` is the data authority. Callers consume this projection
    instead of deriving validation from advisory profile defaults or routing
    recommendations.
    """
    if model_routing.get("schema") != "cortex/model-routing/v1":
        raise ValueError("model routing schema is invalid")
    capabilities = model_routing.get("model_capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
    ):
        raise ValueError("model routing capability registry is invalid")

    registry: dict[str, tuple[str, ...]] = {}
    for capability in capabilities:
        if not isinstance(capability, Mapping) or set(capability) != {"model", "reasoning_efforts"}:
            raise ValueError("model routing capability entry is invalid")
        model = capability.get("model")
        efforts = capability.get("reasoning_efforts")
        if (
            not isinstance(model, str)
            or not model
            or model in registry
            or not isinstance(efforts, list)
            or not efforts
            or any(
                not isinstance(effort, str)
                or not effort
                for effort in efforts
            )
            or len(efforts) != len(set(efforts))
        ):
            raise ValueError(f"model routing efforts are invalid for {model}")
        registry[model] = tuple(efforts)
    configured_default = model_routing.get("configured_default_model")
    if configured_default not in registry:
        raise ValueError("configured default model is outside the routing registry")
    return registry


def supported_effort_sequence(model_routing: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the stable union of native efforts without duplicating overlap."""
    return tuple(dict.fromkeys(
        effort
        for efforts in model_effort_registry(model_routing).values()
        for effort in efforts
    ))


def model_recommendation_registry(model_routing: Mapping[str, Any]) -> dict[str, str]:
    """Return advisory per-model efforts, independent of capability validation."""
    registry = model_effort_registry(model_routing)
    selection = model_routing.get("selection_policy")
    routes = selection.get("routes") if isinstance(selection, Mapping) else None
    if not isinstance(routes, list) or len(routes) != len(registry):
        raise ValueError("model recommendation routes are invalid")
    recommendations: dict[str, str] = {}
    for expected_model, route in zip(registry, routes, strict=True):
        if (
            not isinstance(route, Mapping)
            or set(route) != {"model", "recommended_effort", "choose_for"}
            or route.get("model") != expected_model
            or not isinstance(route.get("choose_for"), str)
            or not str(route.get("choose_for") or "").strip()
            or route.get("recommended_effort") not in registry[expected_model]
        ):
            raise ValueError("model recommendation route is invalid")
        recommendations[expected_model] = str(route["recommended_effort"])
    return recommendations


def model_effort_pair_text(model_routing: Mapping[str, Any]) -> str:
    """Render the exact allowed pairs from the canonical registry."""
    registry = model_effort_registry(model_routing)
    return "; ".join(
        f"{model} -> {' or '.join(efforts)}"
        for model, efforts in registry.items()
    )


def model_effort_pair_is_allowed(
    registry: Mapping[str, Sequence[str]], model: object, effort: object,
) -> bool:
    """Return whether an exact coordinator-selected pair is executable."""
    return (
        isinstance(model, str)
        and isinstance(effort, str)
        and effort in registry.get(model, ())
    )
