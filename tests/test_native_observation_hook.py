import json

from cortex_runtime import native_observation as native
from test_activation_hook import invoke


def test_hook_binds_native_projection_to_original_task_and_latest_barrier(tmp_path):
    common = {"session_id": "root", "turn_id": "turn"}
    invoke(tmp_path, {**common, "hook_event_name": "UserPromptSubmit", "prompt": "$cortex:orchestrator"})
    task = "t_0123456789ab"
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__open_task",
        "tool_response": {"isError": False, "structuredContent": {"task_ref": task, "replayed": False}}})
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__record_steering",
        "tool_response": {"isError": False, "structuredContent": {"task_ref": task,
            "effect": {"effective_revision": 2, "reconciliation_epoch": 3}}}})
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "collaboration.list_agents", "tool_input": {},
        "tool_response": {"agents": [{"agent_name": "/root", "agent_status": "running"}]}})
    observed = native.verified_projection(tmp_path / "plugin-data", task_digest=native.digest(task), revision=2, barrier_epoch=3)
    assert observed and native.quiescent(observed, "old-worker")
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "collaborationlist_agents", "tool_input": {},
        "tool_response": json.dumps({"agents": [
            {"agent_name": "/root", "agent_status": "running"},
            {"agent_name": "/root/old-worker", "agent_status": "interrupted"}]})})
    observed = native.verified_projection(tmp_path / "plugin-data", task_digest=native.digest(task), revision=2, barrier_epoch=3)
    assert observed and native.quiescent(observed, "old-worker")
    assert native.verified_projection(tmp_path / "plugin-data", task_digest=native.digest(task), revision=2, barrier_epoch=4) is None
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "collaborationlist_agents", "tool_input": {},
        "tool_response": json.dumps({"agents": [
            {"agent_name": "/root", "agent_status": "running"},
            {"agent_name": "/root/old-worker", "agent_status": {"completed": "fixture result"}}]})})
    observed = native.verified_projection(tmp_path / "plugin-data", task_digest=native.digest(task), revision=2, barrier_epoch=3)
    assert observed and native.quiescent(observed, "old-worker")


def test_foreign_coordinator_cannot_rebind_task_by_copying_state(tmp_path):
    task = "t_0123456789ab"
    for session in ("original", "foreign"):
        common = {"session_id": session, "turn_id": "turn"}
        invoke(tmp_path, {**common, "hook_event_name": "UserPromptSubmit", "prompt": "$cortex:orchestrator"})
        invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__open_task",
            "tool_response": {"isError": False, "structuredContent": {"task_ref": task, "replayed": session == "foreign"}}})
    assert native.owns_task(tmp_path / "plugin-data", task_digest=native.digest(task), session_digest=native.digest("original"))
    assert not native.owns_task(tmp_path / "plugin-data", task_digest=native.digest(task), session_digest=native.digest("foreign"))
