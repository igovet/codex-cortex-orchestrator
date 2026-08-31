"""Codebase Memory is visible to workers but denied to the Cortex root."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins/cortex/hooks/cortex_activation.py"


def invoke(tmp_path: Path, event: dict) -> tuple[int, dict | None]:
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    completed = subprocess.run(
        [sys.executable, "-B", str(HOOK)],
        input=json.dumps(event), text=True, capture_output=True,
        env=environment, check=False,
    )
    output = completed.stdout.strip()
    return completed.returncode, json.loads(output) if output else None


def test_root_coordinator_is_denied_shared_codebase_memory_namespace(tmp_path: Path) -> None:
    session, turn = "root-session", "root-turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    code, result = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__codebase_memory__search",
        "tool_input": {"query": "ignored by coordinator boundary"},
    })
    assert code == 0
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "project-facing native workers" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_worker_child_is_allowed_shared_codebase_memory_namespace(tmp_path: Path) -> None:
    session, root_turn, child_turn, agent = "root-session", "root-turn", "child-turn", "worker-1"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": root_turn, "prompt": "$cortex:orchestrator",
    })
    # SubagentStart establishes the host-observed child identity. The semantic
    # assignment binding remains server-owned and is intentionally not faked by
    # this boundary test.
    invoke(tmp_path, {
        "hook_event_name": "SubagentStart", "session_id": session,
        "parent_session_id": session, "turn_id": child_turn,
        "agent_id": agent,
    })
    code, result = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": child_turn, "agent_id": agent,
        "tool_name": "mcp__codebase_memory__search",
        "tool_input": {"query": "worker may use Codebase Memory"},
    })
    assert code == 0
    assert result is None


def test_memory_namespace_denial_is_not_recorded_as_tool_input(tmp_path: Path) -> None:
    session, turn = "root-session", "root-turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__codebase-memory__search",
        "tool_input": {"query": "do not persist this"},
    })
    denials = tmp_path / "plugin-data/activation/denials.jsonl"
    assert denials.exists()
    body = denials.read_text(encoding="utf-8")
    assert "do not persist this" not in body
    assert "codebase-memory" not in body
