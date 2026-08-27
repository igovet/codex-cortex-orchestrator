"""Pure coordinator-selected model/effort projection for Cortex v12."""
from __future__ import annotations

from collections.abc import Mapping

from cortex_runtime.model_routing import ModelSelection, validate_model_selection


def coordinator_model_selection(values: Mapping[str, object]) -> ModelSelection:
    """Return the exact pair supplied by a coordinator without policy input.

    Roles, profiles, task type, risk, governance mode, and ledger state are
    intentionally absent. They can inform the coordinator's reasoning, but
    cannot become a backend routing or authorization decision.
    """
    return validate_model_selection(
        values.get("model"),
        values.get("reasoning_effort"),
    )
