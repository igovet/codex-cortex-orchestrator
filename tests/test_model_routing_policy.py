"""Regression coverage for the coordinator-owned model recommendations."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.model_routing import (
    NATIVE_MODELS,
    NATIVE_REASONING_EFFORTS,
    model_effort_pair_is_allowed,
    profile_default_selection,
    validate_model_selection,
)


def _routing():
    return json.loads((ROOT / "plugins/cortex/profiles.json").read_text())["model_routing"]


def test_catalogue_and_default_are_luna_first_without_ultra():
    assert NATIVE_MODELS == ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
    assert NATIVE_REASONING_EFFORTS == ("low", "medium", "high", "xhigh", "max")
    assert profile_default_selection("general").model == "gpt-5.6-luna"
    assert profile_default_selection("general").reasoning_effort == "high"
    assert "ultra" not in json.dumps(_routing()).lower()


def test_all_supported_efforts_validate_for_luna_and_terra():
    for model in ("gpt-5.6-luna", "gpt-5.6-terra"):
        for effort in NATIVE_REASONING_EFFORTS:
            assert validate_model_selection(model, effort).model == model


def test_sol_guidance_is_rare_and_very_high_risk_only():
    sol = next(item for item in _routing()["recommendations"] if item["model"] == "gpt-5.6-sol")
    text = sol["choose_for"].lower()
    assert "rarely" in text
    assert "very-high-risk" in text
    assert "security-related" in text
    assert "routine security review" in text
    assert "ordinary difficult work" in text


def test_terra_guidance_is_limited_to_complex_planning_or_architecture():
    terra = next(item for item in _routing()["recommendations"] if item["model"] == "gpt-5.6-terra")
    text = terra["choose_for"].lower()
    assert "complex planning" in text
    assert "architecture" in text
    assert "cannot be safely resolved by luna" in text
    assert "ordinary" not in text


def test_ultra_and_unknown_efforts_are_rejected():
    assert not model_effort_pair_is_allowed(None, "gpt-5.6-ultra", "max")
    assert not model_effort_pair_is_allowed(None, "gpt-5.6-luna", "ultra")
    with pytest.raises(ValueError):
        validate_model_selection("gpt-5.6-luna", "ultra")


def test_native_luna_is_default_and_other_routes_are_explicit():
    from cortex_runtime.delegation import native_dispatch_projection, validate_native_dispatch_projection
    for model in NATIVE_MODELS:
        dispatch = native_dispatch_projection(
            assignment_ref="fixture", task_name="review", message="Bounded bootstrap",
            model=model, reasoning_effort="max",
        )
        native = validate_native_dispatch_projection(dispatch, assignment_ref="fixture")
        if model == "gpt-5.6-luna":
            assert "model" not in native
        else:
            assert native["model"] == model
        assert native["fork_turns"] == "none"
        assert native["reasoning_effort"] == "max"


def test_explicit_native_luna_is_rejected():
    from cortex_runtime.delegation import validate_native_dispatch_projection
    with pytest.raises(ValueError):
        validate_native_dispatch_projection({
            "fork_turns": "none", "task_name": "review", "message": "Bootstrap",
            "reasoning_effort": "high",
            "model": "gpt-5.6-luna",
        }, assignment_ref="fixture")


def test_default_native_route_is_valid_without_model_argument():
    from cortex_runtime.delegation import validate_native_dispatch_projection
    native = {"fork_turns": "none", "task_name": "review", "message": "Bootstrap", "reasoning_effort": "max"}
    assert validate_native_dispatch_projection(native, assignment_ref="fixture") == native
