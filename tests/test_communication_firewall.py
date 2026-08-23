from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime.communication import render_lifecycle


def test_technical_lifecycle_states_are_silent_recovery_updates() -> None:
    for outcome in ("blocked", "needs_input", "error"):
        rendered = render_lifecycle(outcome, ok=outcome != "error")
        assert rendered["message_type"] == "Progress update"
        assert rendered["output_policy"] == "silent"
        assert rendered["presentation_policy"] == "internal_recovery"
        assert rendered["quality"]["ok"]
        visible = f"{rendered['message']} {rendered['next_step']}".lower()
        assert "retry" not in visible
        assert "cortex" not in visible


def test_only_explicit_plan_approval_can_render_a_plan_decision() -> None:
    internal = render_lifecycle("awaiting_plan_approval")
    assert internal["output_policy"] == "silent"
    assert internal["presentation_policy"] == "internal_recovery"
    assert internal["message_type"] == "Progress update"

    explicit = render_lifecycle(
        "awaiting_plan_approval",
        metadata={"explicit_plan_approval": True},
    )
    assert explicit["message_type"] == "Question"
    assert "output_policy" not in explicit


def test_real_task_question_can_be_presented_explicitly() -> None:
    rendered = render_lifecycle(
        "needs_input",
        metadata={"user_question": True},
        user_question=True,
    )
    assert rendered["message_type"] == "Question"
    assert "output_policy" not in rendered
