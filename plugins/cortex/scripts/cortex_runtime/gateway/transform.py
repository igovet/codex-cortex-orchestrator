"""Strict two-field-only compaction transformation."""

from __future__ import annotations

from collections.abc import Mapping
import copy

from .classifier import has_unsupported_control, is_compaction_request


class TransformError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_compaction_controls(body: object) -> None:
    if has_unsupported_control(body):
        raise TransformError(
            "unsupported_compaction_control",
            "The compaction request contains an unsupported configuration control.",
        )


def transform_compaction(body: object, policy: object) -> dict[str, object]:
    """Return a deep copy with the supported compaction fields normalized."""

    if not isinstance(body, Mapping) or not is_compaction_request(body):
        raise TransformError("not_compaction_request", "The request is not a V2 compaction request.")
    validate_compaction_controls(body)
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, Mapping):
        raise TransformError(
            "invalid_compaction_reasoning",
            "The compaction request contains an unsupported reasoning value.",
        )
    model = getattr(policy, "model", None)
    effort = getattr(policy, "effort", None)
    if not isinstance(model, str) or not isinstance(effort, str):
        raise TransformError("invalid_compaction_policy", "The active compaction policy is invalid.")
    transformed = copy.deepcopy(dict(body))
    next_reasoning = dict(reasoning) if isinstance(reasoning, Mapping) else {}
    next_reasoning["effort"] = effort
    transformed["model"] = model
    transformed["reasoning"] = next_reasoning
    # The upstream compaction contract requires storage to be disabled. This
    # is scoped to recognized compaction requests; ordinary traffic remains
    # untouched.
    transformed["store"] = False
    transformed["stream"] = True
    return transformed


def transform_legacy_compaction(body: object, policy: object) -> dict[str, object]:
    """Route a decoded legacy compact request through the active policy.

    The legacy endpoint itself is the compaction signal, so it does not carry
    the V2 ``compaction_trigger`` item.  Keep the same narrow rewrite contract
    as V2: only ``model`` and ``reasoning.effort`` may change.
    """
    if not isinstance(body, Mapping):
        raise TransformError("not_legacy_compaction_request", "The legacy compaction request is not a JSON object.")
    validate_compaction_controls(body)
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, Mapping):
        raise TransformError(
            "invalid_compaction_reasoning",
            "The compaction request contains an unsupported reasoning value.",
        )
    model = getattr(policy, "model", None)
    effort = getattr(policy, "effort", None)
    if not isinstance(model, str) or not isinstance(effort, str):
        raise TransformError("invalid_compaction_policy", "The active compaction policy is invalid.")
    transformed = copy.deepcopy(dict(body))
    next_reasoning = dict(reasoning) if isinstance(reasoning, Mapping) else {}
    next_reasoning["effort"] = effort
    transformed["model"] = model
    transformed["reasoning"] = next_reasoning
    transformed["store"] = False
    transformed["stream"] = True
    return transformed
