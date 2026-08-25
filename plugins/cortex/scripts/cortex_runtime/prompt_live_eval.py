"""Development-only prompt-live verification notice.

Use an ordinary interactive Codex CLI or tmux session for live prompt evidence.
Cortex deliberately does not automate a nested evaluator or turn that evidence
into a worker-lifecycle assertion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex_runtime.prompt_eval import FIXTURES_PATH, assert_live_prompt_eval_configuration


def run_live_prompt_evals(
    *, enabled: bool = False, fixtures_path: Path = FIXTURES_PATH,
    model: str = "gpt-5.6-luna", reasoning_effort: str = "high",
) -> list[dict[str, Any]]:
    """Return a safe reminder that live verification is an interactive check."""
    del fixtures_path
    if enabled:
        assert_live_prompt_eval_configuration(model=model, reasoning_effort=reasoning_effort)
    return [{
        "status": "SKIP",
        "reason": "live prompt verification is manual development evidence; use an ordinary interactive Codex CLI or tmux session",
    }]


__all__ = ["run_live_prompt_evals"]
