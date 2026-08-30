from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins/cortex/hooks/cortex_activation.py"
HOOKS_CONFIG = ROOT / "plugins/cortex/hooks/hooks.json"
CODEX_01491_SUCCESS_FIXTURE = ROOT / "tests/fixtures/codex-0.149.1-pre-tool-use-success.json"


def invoke(tmp_path: Path, event: dict) -> tuple[int, dict | None]:
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    completed = subprocess.run(
        [sys.executable, "-B", str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    output = completed.stdout.strip()
    return completed.returncode, (json.loads(output) if output else None)


def state_file(tmp_path: Path, turn: str) -> Path:
    digest = hashlib.sha256(("turn:" + turn).encode()).hexdigest()
    return tmp_path / "plugin-data" / "activation" / f"turn-{digest}.json"


def assert_codex_01491_pre_tool_success(payload: dict, *, updated_input: dict | None = None) -> None:
    """Validate the accepted non-blocking PreToolUse response shape.

    Codex rejects ``permissionDecision:allow`` when no ``updatedInput`` is
    present, and rejects ``updatedInput`` when the allow decision is absent.
    Context-only success therefore has no decision override; an input rewrite
    must carry the explicit allow+updatedInput pair.
    """
    expected = json.loads(CODEX_01491_SUCCESS_FIXTURE.read_text())
    assert set(payload) == set(expected)
    expected_keys = set(expected["hookSpecificOutput"])
    if updated_input is not None:
        expected_keys.update(("permissionDecision", "updatedInput"))
    assert set(payload["hookSpecificOutput"]) == expected_keys
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert isinstance(payload["hookSpecificOutput"]["additionalContext"], str)
    assert payload["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecisionReason" not in payload["hookSpecificOutput"]
    if updated_input is not None:
        assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert payload["hookSpecificOutput"]["updatedInput"] == updated_input
    else:
        assert "permissionDecision" not in payload["hookSpecificOutput"]
        assert "allow" not in json.dumps(payload).lower()


def load_activation_hook():
    """Load the hook in-process so the semantic bootstrap adapter is captured."""
    spec = importlib.util.spec_from_file_location("cortex_activation_test", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_loaded_hook(module, event: dict, monkeypatch, capsys) -> dict | None:
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(event)))
    module.main()
    output = capsys.readouterr().out.strip()
    return json.loads(output) if output else None


def test_coordinator_user_prompt_is_not_a_runtime_hook_boundary() -> None:
    """Coordinator routing belongs to MCP; only worker/tool boundaries need hooks."""
    configured = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))["hooks"]
    assert "UserPromptSubmit" not in configured


def test_unselected_prompt_is_passive(tmp_path: Path) -> None:
    code, payload = invoke(
        tmp_path,
        {"hook_event_name": "UserPromptSubmit", "turn_id": "turn-unselected", "prompt": "Build a page"},
    )
    assert code == 0
    assert payload is None
    assert not state_file(tmp_path, "turn-unselected").exists()


def test_selection_is_passive_and_coordinator_tools_are_not_hook_gated(tmp_path: Path) -> None:
    turn = "turn-1"
    code, output = invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator do the task"})
    assert code == 0
    assert output is None
    stored = state_file(tmp_path, turn)
    assert json.loads(stored.read_text()) == {
        "anchored": False,
        "selected": True,
        "turn_fingerprint": hashlib.sha256(turn.encode()).hexdigest(),
    }
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "Bash"})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task"})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "cortex.open_task"})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "read_mcp_resource"})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "mcp.cortex.resources_read"})
    assert code == 0 and output is None


def test_only_successful_opening_anchors_and_no_raw_data_is_stored(tmp_path: Path) -> None:
    turn = "turn-2"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "cortex:orchestrator"})
    failed = {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": True, "prompt": "secret"}}
    invoke(tmp_path, failed)
    assert json.loads(state_file(tmp_path, turn).read_text())["anchored"] is False
    succeeded = {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_private"}}}}
    invoke(tmp_path, succeeded)
    stored = state_file(tmp_path, turn).read_text()
    assert json.loads(stored)["anchored"] is True
    assert "private" not in stored and "secret" not in stored
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "Bash"})
    assert code == 0 and output is None


def test_coordinator_stop_is_passive_before_and_after_opening(tmp_path: Path) -> None:
    turn = "turn-3"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    code, first = invoke(tmp_path, {"hook_event_name": "Stop", "turn_id": turn, "stop_hook_active": False})
    assert code == 0
    assert first is None
    assert json.loads(state_file(tmp_path, turn).read_text())["anchored"] is False
    code, bounded = invoke(tmp_path, {"hook_event_name": "Stop", "turn_id": turn, "stop_hook_active": True})
    assert code == 0 and bounded is None
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_private"}}}})
    code, second = invoke(tmp_path, {"hook_event_name": "Stop", "turn_id": turn, "stop_hook_active": False})
    assert code == 0 and second is None
    assert json.loads(state_file(tmp_path, turn).read_text())["anchored"] is True


def test_selected_route_is_bound_to_session_and_turn_without_raw_identity(tmp_path: Path) -> None:
    session = "session-private"
    first = "turn-first"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": first, "prompt": "$cortex:orchestrator"})
    digest = hashlib.sha256(("session:" + session).encode()).hexdigest()
    stored = tmp_path / "plugin-data" / "activation" / f"turn-{digest}.json"
    text = stored.read_text()
    assert session not in text and first not in text
    assert json.loads(text)["turn_fingerprint"] == hashlib.sha256(first.encode()).hexdigest()

    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "foreign-turn", "tool_name": "Bash"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": "Bash"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    # Official lifecycle transitions are observable even when they carry no
    # turn identity; they must not leak or reset the selected route.
    code, output = invoke(tmp_path, {"hook_event_name": "PreCompact", "session_id": session})
    assert code == 0 and output is None


def test_new_turn_in_same_session_updates_binding_only_on_prompt_submit(tmp_path: Path) -> None:
    session = "session-follow-up"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-one", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-two", "prompt": "continue"})
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-two", "tool_name": "Bash"})
    assert code == 0 and output is None


def test_native_child_has_only_assignment_evidence_bootstrap_before_scoped_activation(tmp_path: Path) -> None:
    child = {"session_id": "child-session", "turn_id": "child-turn", "agent_id": "agent-1"}
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "coordinator-session", "turn_id": "coordinator-turn", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": child["session_id"], "turn_id": child["turn_id"], "parent_session_id": "coordinator-session"})
    child_hook = {key: value for key, value in child.items() if key != "agent_id"}
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", **child_hook, "tool_name": "read_task"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", **child_hook, "tool_name": "mcp__cortex__consume_assignment_evidence"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, output = invoke(tmp_path, {"hook_event_name": "PostToolUse", **child_hook, "tool_name": "mcp__cortex__consume_assignment_evidence", "tool_input": {"assignment_ref": "d_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"assignment_ref": "d_0123456789ab", "evidence": {"state": "none"}}}})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", **child_hook, "tool_name": "read_task"})
    assert code == 0 and output is None
    other = {"session_id": "other-child", "turn_id": "other-turn", "agent_id": "agent-2"}
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", **other, "tool_name": "read_task"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_anchored_route_survives_coordinator_steering_turn(tmp_path: Path) -> None:
    session = "steering-session"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-open", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    code, output = invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-steering", "prompt": "Please clarify the product direction."})
    assert code == 0 and output is None
    for tool_name in ("mcp__cortex__open_steering", "mcp__cortex__record_steering"):
        code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-steering", "tool_name": tool_name})
        assert code == 0 and output is None


def test_plain_steering_and_tool_error_retain_anchored_route(tmp_path: Path) -> None:
    session = "plain-steering-session"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-open", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__record_steering", "tool_response": {"isError": True}})
    code, output = invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-steering", "prompt": "The user answered the clarification."})
    assert code == 0 and output is None


def test_anchored_coordinator_route_survives_new_turn_without_prompt_hook(tmp_path: Path) -> None:
    """The configured hook set does not observe every coordinator prompt.

    After task anchoring, the root session—not the previous turn fingerprint—
    owns the route, so a later approval/steering mutation is not blocked as a
    foreign turn. Native-worker turn binding remains strict elsewhere.
    """
    session = "approval-session"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-open", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-approval", "tool_name": "mcp__cortex__record_decision"})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-steering", "tool_name": "mcp__cortex__record_steering"})
    assert code == 0 and output is None


def test_native_child_may_read_one_verified_packaged_skill_before_assignment_bootstrap(tmp_path: Path, monkeypatch) -> None:
    candidate = tmp_path / "candidate"
    skill = candidate / "skills" / "documentation-sync" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Documentation sync\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_CANDIDATE_PATH", str(candidate))
    parent, turn, agent = "selected-parent", "child-turn", "native-agent"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": parent, "turn_id": "parent-turn", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": parent, "turn_id": turn, "agent_id": agent})
    event = {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "agent_id": agent, "tool_name": "Bash"}
    code, output = invoke(tmp_path, {**event, "tool_input": {"command": f"sed -n '1,240p' {skill}"}})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {**event, "tool_input": {"command": f"sed -n '1,240p' {skill}; pwd"}})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    outside = tmp_path / "outside" / "SKILL.md"
    outside.parent.mkdir(); outside.write_text("outside", encoding="utf-8")
    code, output = invoke(tmp_path, {**event, "tool_input": {"command": f"cat {outside}"}})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
def test_coordinator_cannot_simulate_worker_evidence_or_publication(tmp_path: Path) -> None:
    session, turn = "coordinator", "turn-open"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    for tool in ("mcp__cortex__consume_assignment_evidence", "mcp__cortex__publish_plan"):
        code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_name": tool})
        assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_anchor_allows_coordinator_tools_without_hook_filtering(tmp_path: Path) -> None:
    session, turn = "coordination-session", "coordination-turn"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    for tool in ("update_plan", "TodoWrite", "todo_write"):
        code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_name": tool})
        assert code == 0 and output is None
    for tool in ("plan", "update_plan_extra", "mcp__cortex__update_plan", "foo.update_plan", "TodoWriter", "Bash", "Read"):
        code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_name": tool})
        assert code == 0 and output is None


def test_diagnostic_mode_records_metadata_without_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_HOOK_DIAGNOSTIC", "1")
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": "secret-session", "turn_id": "secret-turn", "tool_name": "Read", "tool_input": {"file_path": "/secret/path"}})
    record = (tmp_path / "plugin-data" / "activation" / "hook-diagnostic.jsonl").read_text()
    assert "PreToolUse" in record and "session_id" in record
    assert "secret-session" not in record and "secret-turn" not in record and "/secret/path" not in record
    assert "tool_class" not in record and "input_keys" not in record


def test_diagnostic_mode_records_hook_return_shape_without_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_HOOK_DIAGNOSTIC", "1")
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": "t", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": "t", "tool_name": "Bash"})
    record = (tmp_path / "plugin-data" / "activation" / "hook-diagnostic.jsonl").read_text()
    assert '"hook_return":"block"' not in record
    assert '"hook_return":"deny"' not in record
    assert "Bash" not in record and "prompt" not in record


def test_subagent_start_without_selected_parent_is_observation_only(tmp_path: Path) -> None:
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": "child", "turn_id": "turn", "parent_session_id": "missing-parent"})
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": "child", "turn_id": "turn", "tool_name": "read_task"})
    assert code == 0 and output is None


def test_official_parent_session_child_agent_shape_enters_worker_bootstrap(tmp_path: Path) -> None:
    parent, turn, agent = "selected-parent", "child-turn", "native-agent"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": parent, "turn_id": "parent-turn", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": parent, "turn_id": turn, "agent_id": agent})
    # Later host hook payloads may omit agent_id; the shared child session/turn
    # binding must still resolve the worker lifecycle state.
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "tool_name": "read_task"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "tool_name": "Bash", "tool_input": {"command": "rg Cortex . | head"}})
    assert code == 0 and output is None
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "agent_id": agent, "tool_name": "read_task"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "agent_id": agent, "tool_name": "mcp__cortex__consume_assignment_evidence"})
    assert code == 0 and output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_subagent_start_binds_assignment_but_worker_must_consume_it(tmp_path: Path, monkeypatch, capsys) -> None:
    """Lifecycle correlation never consumes semantic evidence for the model."""
    module = load_activation_hook()
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-data"))
    parent, parent_turn, child_turn, agent, assignment = "parent-live", "parent-turn", "child-turn", "agent-1", "d_0123456789ab"
    parent_path = module._state_path(None, parent)
    module._write_state(parent_path, {"selected": True, "anchored": True, "turn_fingerprint": module._fingerprint(parent_turn)})
    module._record_pending_dispatch({
        "hook_event_name": "PostToolUse", "session_id": parent, "turn_id": parent_turn,
        "tool_response": {"isError": False, "structuredContent": {
            "assignment_ref": assignment,
            "native_dispatch": {"fork_turns": "none", "message": "server brief", "task_name": "planner"},
        }},
    })
    assert module._validate_native_dispatch({
        "hook_event_name": "PreToolUse", "session_id": parent, "turn_id": parent_turn,
        "tool_name": "collaborationspawn_agent",
        "tool_input": {"fork_turns": "none", "message": "host representation", "task_name": "planner"},
    }, parent_path)
    output = run_loaded_hook(module, {
        "hook_event_name": "SubagentStart", "session_id": parent,
        "turn_id": child_turn, "agent_id": agent,
    }, monkeypatch, capsys)
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.endswith("server brief")
    assert "server-owned and authoritative" in context
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 1
    stored = json.loads(records[0].read_text())
    assert stored["state"] == "worker_bound"
    assert "authority" not in stored
    child_path = module._child_state_path(child_turn, parent, agent)
    child_state = module._read_state(child_path)
    assert child_state["anchored"] is False
    assert child_state["assignment_ref_digest"] == module._value_fingerprint(assignment)

    denied = run_loaded_hook(module, {
        "hook_event_name": "PreToolUse", "session_id": parent,
        "turn_id": child_turn, "agent_id": agent, "tool_name": "mcp__cortex__consume_assignment_evidence",
        "tool_input": {"assignment_ref": "d_ffffffffffff"},
    }, monkeypatch, capsys)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert run_loaded_hook(module, {
        "hook_event_name": "PreToolUse", "session_id": parent,
        "turn_id": child_turn, "agent_id": agent, "tool_name": "mcp__cortex__consume_assignment_evidence",
        "tool_input": {"assignment_ref": assignment},
    }, monkeypatch, capsys) is None
    assert run_loaded_hook(module, {
        "hook_event_name": "PostToolUse", "session_id": parent,
        "turn_id": child_turn, "agent_id": agent, "tool_name": "mcp__cortex__consume_assignment_evidence",
        "tool_input": {"assignment_ref": assignment},
        "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment, "evidence": {"state": "none"}}},
    }, monkeypatch, capsys) is None
    assert module._read_state(child_path)["anchored"] is True
    stored = json.loads(records[0].read_text())
    assert stored["state"] == "consumed" and stored["authority"] == "authoritative"

    # A repeated lifecycle notification is harmless and cannot bind or consume again.
    assert run_loaded_hook(module, {
        "hook_event_name": "SubagentStart", "session_id": parent,
        "turn_id": child_turn, "agent_id": agent,
    }, monkeypatch, capsys) is None
    assert module._read_state(child_path)["anchored"] is True


def test_subagent_start_missing_or_ambiguous_lease_fails_closed_before_worker_work(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_activation_hook()
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-data"))
    parent, parent_turn = "parent-unbound", "parent-turn"
    module._write_state(module._state_path(None, parent), {"selected": True, "anchored": True, "turn_fingerprint": module._fingerprint(parent_turn)})
    assert run_loaded_hook(module, {
        "hook_event_name": "SubagentStart", "session_id": parent,
        "turn_id": "child-turn", "agent_id": "agent-1",
    }, monkeypatch, capsys) is None
    denied = run_loaded_hook(module, {
        "hook_event_name": "PreToolUse", "session_id": parent,
        "turn_id": "child-turn", "agent_id": "agent-1", "tool_name": "read_task",
    }, monkeypatch, capsys)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    for index in range(2):
        module._record_pending_dispatch({
            "hook_event_name": "PostToolUse", "session_id": parent, "turn_id": parent_turn,
            "tool_response": {"isError": False, "structuredContent": {
                "assignment_ref": f"d_{index:012d}",
                "native_dispatch": {"fork_turns": "none", "message": "brief", "task_name": f"worker-{index}"},
            }},
        })
    assert run_loaded_hook(module, {
        "hook_event_name": "SubagentStart", "session_id": parent,
        "turn_id": "second-child", "agent_id": "agent-2",
    }, monkeypatch, capsys) is None
    denied = run_loaded_hook(module, {
        "hook_event_name": "PreToolUse", "session_id": parent,
        "turn_id": "second-child", "agent_id": "agent-2", "tool_name": "read_task",
    }, monkeypatch, capsys)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_agent_authority_survives_followup_turn_until_terminal_publication(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """SubagentStop is a turn boundary; the report is the assignment boundary."""
    module = load_activation_hook()
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-data"))
    parent = "followup-parent"
    agent = "stable-native-agent"
    first_turn = "worker-turn-one"
    second_turn = "worker-turn-two"
    child_path = module._child_state_path(first_turn, parent, agent)
    module._write_state(module._state_path(None, parent), {
        "selected": True, "anchored": True,
    })
    module._write_state(child_path, {
        "selected": True,
        "anchored": True,
        "child_mode": True,
        "agent_fingerprint": module._fingerprint(agent),
        "turn_fingerprint": module._fingerprint(first_turn),
    })

    assert run_loaded_hook(module, {
        "hook_event_name": "SubagentStop",
        "session_id": parent,
        "turn_id": first_turn,
        "agent_id": agent,
    }, monkeypatch, capsys) is None
    assert child_path.exists()
    assert module._child_state_path(second_turn, parent, agent) == child_path

    # Codex follow-up turns do not emit another SubagentStart. The same
    # server-bound native identity must remain active without a second consume.
    assert run_loaded_hook(module, {
        "hook_event_name": "PreToolUse",
        "session_id": parent,
        "turn_id": second_turn,
        "agent_id": agent,
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"opaque": "schema-owned"},
    }, monkeypatch, capsys) is None

    assert run_loaded_hook(module, {
        "hook_event_name": "PostToolUse",
        "session_id": parent,
        "turn_id": second_turn,
        "agent_id": agent,
        "tool_name": "mcp__cortex__publish_result",
        "tool_response": {
            "isError": False,
            "structuredContent": {
                "publication_status": "completed",
                "report_ref": "r_0123456789ab",
            },
        },
    }, monkeypatch, capsys) is None
    assert not child_path.exists()

    denied = run_loaded_hook(module, {
        "hook_event_name": "PreToolUse",
        "session_id": parent,
        "turn_id": "worker-turn-three",
        "agent_id": agent,
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
    }, monkeypatch, capsys)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_spawn_claim_queue_binds_two_parallel_assignments_without_reuse(tmp_path: Path, monkeypatch) -> None:
    """PreToolUse claims preserve one-to-one assignment binding across starts."""
    module = load_activation_hook()
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-data"))
    parent, turn = "parallel-parent", "parallel-turn"
    module._write_state(module._state_path(None, parent), {"selected": True, "anchored": True})
    assignments = ["d_000000000001", "d_000000000002"]
    for index, assignment in enumerate(assignments):
        module._record_pending_dispatch({
            "hook_event_name": "PostToolUse", "session_id": parent, "turn_id": turn,
            "tool_response": {"isError": False, "structuredContent": {
                "assignment_ref": assignment,
                "native_dispatch": {"fork_turns": "none", "message": "brief-" + assignment, "task_name": f"worker-{index}"},
            }},
        })
    for index in range(2):
        delivered = module._validate_native_dispatch({
            "hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn,
            "tool_name": "collaborationspawn_agent", "tool_use_id": f"spawn-{index}",
            "tool_input": {"fork_turns": "none", "message": "host representation", "task_name": f"worker-{index}"},
        }, module._state_path(None, parent))
        assert delivered is True

    children = []
    for index in range(2):
        child_turn = f"child-turn-{index}"
        child_path = module._child_state_path(child_turn, parent, f"agent-{index}")
        child_state = {"selected": True, "anchored": False, "child_mode": True}
        module._write_state(child_path, child_state)
        children.append((child_turn, child_path, child_state))

    def bind(item):
        child_turn, child_path, child_state = item
        return module._bind_worker_dispatch({"session_id": parent, "turn_id": child_turn, "agent_id": "agent-" + child_turn.rsplit("-", 1)[-1]}, child_path, child_state, parent)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bound = list(pool.map(bind, children))
    assert all(result[0] is True for result in bound)
    assert {result[1] for result in bound} == {"brief-" + assignment for assignment in assignments}
    records = [json.loads(path.read_text()) for path in (tmp_path / "plugin-data" / "activation").glob("dispatch-*.json")]
    assert len(records) == 2 and all(record["state"] == "worker_bound" for record in records)
    assert [record["assignment_ref"] for record in sorted(records, key=lambda record: record["spawn_claim_order"])] == assignments
    child_states = [module._read_state(path) for _turn, path, _state in children]
    assert all(not state["anchored"] for state in child_states)
    assert {state["assignment_ref_digest"] for state in child_states} == {module._value_fingerprint(value) for value in assignments}


def test_native_spawn_accepts_bounded_opaque_host_message_and_records_delivery(tmp_path: Path) -> None:
    turn = "turn-native-dispatch"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})

    assignment_ref = "d_0123456789ab"
    native_args = {"fork_turns": "none", "message": "server-rendered worker contract", "task_name": "planner"}
    projection = native_args
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment_ref, "native_dispatch": projection}}})

    host_args = dict(native_args, message="arbitrary host representation: gAAAA$opaque", task_name="/root/planner", role="planner", model="gpt-5.6-luna", reasoning_effort="high")
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "Agent", "tool_input": host_args})
    assert code == 0
    # This is the actual response shape accepted by the Codex 0.149.1
    # PreToolUse parser for a non-blocking success path.
    assert_codex_01491_pre_tool_success(allowed)
    output = allowed["hookSpecificOutput"]
    assert "correlated" in output["additionalContext"]
    assert "did not rewrite" in output["additionalContext"]
    assert "worker alone" in output["additionalContext"]
    assert "coordinator must wait" in output["additionalContext"]
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 1
    stored = records[0].read_text()
    assert '"state":"delivery_pending"' in stored
    assert json.loads(stored)["native_arguments"] == native_args

    # A second identical native call is an unexplained replay, not a second
    # delivery of the same server-issued assignment.
    code, replay = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "Agent", "tool_input": host_args})
    assert code == 0
    assert replay["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_spawn_delivers_large_server_message_byte_identically(tmp_path: Path) -> None:
    turn = "turn-large-native-dispatch"
    assignment_ref = "d_abcdef012345"
    server_message = ("trusted worker policy\n" * 430) + f'{{"anchor":"{assignment_ref}"}}'
    server_args = {"fork_turns": "none", "message": server_message, "task_name": "implementation-owner"}
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment_ref, "native_dispatch": server_args}}})

    code, output = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "turn_id": turn,
        "tool_name": "collaborationspawn_agent", "tool_use_id": "spawn-large",
        "tool_input": {"fork_turns": "none", "message": "abbreviated by the coordinator", "task_name": "implementation-owner"},
    })
    assert code == 0
    assert_codex_01491_pre_tool_success(output)
    assert "updatedInput" not in output["hookSpecificOutput"]
    record = next((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    stored = json.loads(record.read_text())
    assert stored["native_arguments"]["message"].encode("utf-8") == server_message.encode("utf-8")
    assert stored["context_digest"] == hashlib.sha256(server_message.encode("utf-8")).hexdigest()


def test_native_spawn_accepts_arbitrary_bounded_host_task_names_with_one_receipt(tmp_path: Path) -> None:
    """Host routing names are metadata; the pending receipt owns authority."""
    for index, host_name in enumerate(("planner-7", "/root/planner", "planner.instance:7")):
        isolated = tmp_path / str(index)
        turn = f"turn-host-name-{index}"
        invoke(isolated, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
        invoke(isolated, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
        invoke(isolated, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": f"d_{index:012d}", "native_dispatch": {"fork_turns": "none", "message": "server contract", "task_name": "planner"}}}})
        code, output = invoke(isolated, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": {"fork_turns": "none", "message": "host-rendered representation", "task_name": host_name}})
        assert code == 0
        assert_codex_01491_pre_tool_success(output)


def test_native_spawn_denies_multiple_pending_receipts_for_same_session_turn(tmp_path: Path) -> None:
    turn = "turn-native-dispatch-ambiguous"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    for index in range(2):
        invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": f"d_{index:012d}", "native_dispatch": {"fork_turns": "none", "message": "server contract", "task_name": f"planner-{index}"}}}})
    code, denied = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": {"fork_turns": "none", "message": "host representation", "task_name": "host-planner"}})
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 2
    assert all('"state":"pending"' in record.read_text() for record in records)


def test_successful_assignment_activates_route_when_ui_omits_prompt_marker(tmp_path: Path) -> None:
    """A UI-selected route may not echo the skill marker in UserPromptSubmit."""
    turn = "turn-ui-selected"
    assignment = {"fork_turns": "none", "message": "server-rendered worker contract", "task_name": "planner"}
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": "d_0123456789ab", "native_dispatch": assignment}}})
    code, output = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": assignment})
    assert code == 0
    assert_codex_01491_pre_tool_success(output)


def test_native_spawn_host_representation_is_allowed_once_and_replay_is_denied(tmp_path: Path) -> None:
    turn = "turn-native-dispatch-mismatch"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    assignment_ref = "d_abcdefabcdef"
    native_args = {"fork_turns": "none", "message": "worker bootstrap wb_" + "b" * 32, "task_name": "planner"}
    canonical = json.dumps({"assignment_ref": assignment_ref, "native_arguments": native_args}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    projection = native_args
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment_ref, "native_dispatch": projection}}})
    changed = dict(native_args, message="gAAAA" + "b" * 96, task_name="other-worker")
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert_codex_01491_pre_tool_success(allowed)
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 1
    assert '"state":"delivery_pending"' in records[0].read_text()
    code, denied = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_spawn_receipt_becomes_authoritative_only_after_worker_consumes(tmp_path: Path) -> None:
    parent, turn, child_turn, agent = "parent-session", "parent-turn", "child-turn", "native-agent"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": parent, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": parent, "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    assignment_ref = "d_0123456789ab"
    server_args = {"fork_turns": "none", "message": "worker bootstrap", "task_name": "planner_d_0123456789ab"}
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": parent, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment_ref, "native_dispatch": server_args}}})
    protected = dict(server_args, message="gAAAA" + "c" * 96)
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": parent, "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": protected})
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 1 and '"state":"delivery_pending"' in records[0].read_text()
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": parent, "turn_id": child_turn, "agent_id": agent})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": parent, "turn_id": child_turn, "agent_id": agent, "tool_name": "mcp__cortex__consume_assignment_evidence", "tool_input": {"assignment_ref": assignment_ref}, "tool_response": {"isError": False, "structuredContent": {"assignment_ref": assignment_ref, "evidence": {"state": "none"}}}})
    assert '"state":"consumed"' in records[0].read_text()
    assert '"authority":"authoritative"' in records[0].read_text()


def test_native_spawn_rejects_shape_correlation_and_bounds_mismatches(tmp_path: Path) -> None:
    turn = "turn-native-dispatch-shape"
    server_args = {"fork_turns": "none", "message": "server-rendered worker contract", "task_name": "planner_d_0123456789ab"}
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": "d_0123456789ab", "native_dispatch": server_args}}})

    # Host message contents are deliberately not compared.  Correlation and
    # shape remain strict.
    for changed in (
        dict(server_args, fork_turns="history"),
        dict(server_args, message=""),
        dict(server_args, message="x" * 65_537),
        {"fork_turns": "none", "message": "opaque", "task_name": server_args["task_name"], "extra": True},
        dict(server_args, model="unsupported-model", reasoning_effort="high"),
        dict(server_args, model="gpt-5.6-luna"),
        dict(server_args, reasoning_effort="high"),
        dict(server_args, role="Planner", model="gpt-5.6-luna", reasoning_effort="high"),
        dict(server_args, role="planner", model="unsupported-model", reasoning_effort="high"),
        dict(server_args, role="planner", model="gpt-5.6-luna"),
    ):
        code, denied = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": changed})
        assert code == 0
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "pending server-issued assignment boundary" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    for event in (
        {"session_id": "foreign-session", "turn_id": turn},
        {"turn_id": "foreign-turn"},
    ):
        code, denied = invoke(tmp_path, {"hook_event_name": "PreToolUse", **event, "tool_name": "collaborationspawn_agent", "tool_input": server_args})
        assert code == 0
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    records = list((tmp_path / "plugin-data" / "activation").glob("dispatch-*.json"))
    assert len(records) == 1 and '"state":"pending"' in records[0].read_text()

    observed_host_args = dict(server_args, role="planner", model="gpt-5.6-luna", reasoning_effort="high")
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": observed_host_args})
    assert code == 0
    assert_codex_01491_pre_tool_success(allowed)
    stored = records[0].read_text()
    assert '"state":"delivery_pending"' in stored


def test_native_spawn_accepts_observed_host_role_model_effort_envelope(tmp_path: Path) -> None:
    turn = "turn-observed-host-envelope"
    server_args = {"fork_turns": "none", "message": "server-rendered worker contract", "task_name": "planner"}
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"handles": {"task_ref": "t_0123456789ab"}}}})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_response": {"isError": False, "structuredContent": {"assignment_ref": "d_0123456789ab", "native_dispatch": server_args}}})
    observed = dict(server_args, message="host-rendered representation", task_name="/root/planner", role="planner", model="gpt-5.6-luna", reasoning_effort="high")
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "turn_id": turn, "tool_name": "collaborationspawn_agent", "tool_input": observed})
    assert code == 0
    assert_codex_01491_pre_tool_success(allowed)
