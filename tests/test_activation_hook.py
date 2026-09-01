from __future__ import annotations

import hashlib
import importlib.util
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
    completed = subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(event), text=True, capture_output=True, env=environment, check=False)
    output = completed.stdout.strip()
    return completed.returncode, json.loads(output) if output else None


def state_file(tmp_path: Path, session: str) -> Path:
    digest = hashlib.sha256(("session:" + session).encode()).hexdigest()
    return tmp_path / "plugin-data" / "activation" / f"turn-{digest}.json"


def worker_message(worker_ref: str) -> str:
    return f'worker contract\n```json\n{{"assignment context":{{"task_ref":"{worker_ref}"}}}}\n```'


def native_dispatch(worker_ref: str, task_name: str) -> dict[str, str]:
    return {
        "fork_turns": "none",
        "message": worker_message(worker_ref),
        "task_name": task_name,
        "reasoning_effort": "low",
    }


def test_open_task_anchors_from_direct_task_ref_receipt(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"task_ref": "t_0123456789ab", "replayed": False}}})
    assert json.loads(state_file(tmp_path, session).read_text())["anchored"] is True


def test_open_assignment_receipt_is_correlated_without_public_assignment_identity(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native_dispatch(worker_ref, "qa_worker"), "replayed": False}}})
    records = list((tmp_path / "plugin-data/activation/sessions").glob("*/dispatch/dispatch-*.json"))
    assert len(records) == 1
    stored = json.loads(records[0].read_text())
    assert stored["worker_task_ref"] == worker_ref
    assert stored["task_ref"] == "t_0123456789ab"


def test_parallel_dispatches_claim_by_exact_native_task_name(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    for index in range(2):
        ref = "t_0123456789ab_" + f"{index + 1:032x}"
        native = native_dispatch(ref, f"worker_{index}")
        invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    for index in (1, 0):
        native = native_dispatch("t_0123456789ab_" + f"{index + 1:032x}", f"worker_{index}")
        code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": f"spawn-{index}", "tool_name": "collaboration.spawn_agent", "tool_input": native})
        assert code == 0 and result["hookSpecificOutput"]["additionalContext"]


def test_assignment_receipt_and_spawn_may_use_different_turns(tmp_path: Path) -> None:
    """A dispatch is session-scoped, not incorrectly coupled to one turn."""
    session = "root"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-open", "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-spawn", "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    assert code == 0 and result["hookSpecificOutput"]["additionalContext"]


def test_host_protected_message_preserves_server_dispatch_correlation(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    changed = dict(native)
    changed["message"] = "gAAAA-host-protected-native-message"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert result["hookSpecificOutput"]["additionalContext"]


def test_host_explicit_luna_matches_server_omitted_default_model(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    materialized = dict(native)
    materialized["model"] = "gpt-5.6-luna"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": materialized})
    assert code == 0
    assert result["hookSpecificOutput"]["additionalContext"]


def test_spawn_routing_must_equal_the_server_projection(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    changed = dict(native)
    changed["reasoning_effort"] = "high"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_does_not_act_as_worker_workflow_gate(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent"})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__publish_result", "tool_input": {"task_ref": ref}})
    assert code == 0 and result is None
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__read_task", "tool_input": {"task_ref": ref, "view": "assignment"}})
    assert code == 0 and allowed is None


def test_hook_leaves_task_authority_to_server_connection_context(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent"})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__read_task", "tool_input": {"task_ref": "t_0123456789ab_" + "b" * 32, "view": "assignment"}})
    assert code == 0 and result is None
