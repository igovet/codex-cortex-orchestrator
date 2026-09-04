"""Decoder fixtures match exported Codex CLI 0.153.0 app-server schemas.

These are protocol fixtures, not a connected Desktop run or host attestations.
"""
import pytest
from cortex_runtime.host_boundary import CodexDesktopHostAdapter


def item(tool_name, **changes):
    return {"type": "collabAgentToolCall", "id": "call", "senderThreadId": "root",
            "receiverThreadIds": ["child"], "agentsStates": {}, "status": "completed",
            "tool": tool_name, "prompt": "private fixture text", **changes}


def observe(value):
    return CodexDesktopHostAdapter.observe_item(value, sender_thread="root", evidence_ref="fixture-event")


@pytest.mark.parametrize("tool,operation,status", [
    ("spawnAgent", "spawn", "started"), ("listAgents", "list", "observed"),
    ("wait", "wait", "unverified"), ("sendMessage", "send_message", "sent"),
    ("interruptAgent", "interrupt", "acknowledged"),
])
def test_exact_operations_preserve_limited_evidence(tool, operation, status):
    result = observe(item(tool))
    assert (result.operation, result.status) == (operation, status)
    assert not result.complete and not result.quiescent
    assert "private fixture text" not in repr(result)


def test_tool_completion_is_not_worker_completion_or_timeout():
    assert observe(item("wait", agentsStates={"child": {"status": "running"}})).status == "unverified"
    assert observe(item("wait", agentsStates={"other": {"status": "completed"}})).status == "unverified"
    assert observe(item("wait", agentsStates={"child": {"status": "completed"}})).status == "completed"
    assert observe(item("wait", agentsStates={"child": {"status": "errored"}})).status == "attention"


def test_failed_spawn_with_receiver_is_ambiguous_and_never_retried():
    assert observe(item("spawnAgent", status="failed")).status == "ambiguous"
    assert observe(item("spawnAgent", status="failed", receiverThreadIds=[])).status == "failed"


@pytest.mark.parametrize("changes", [
    {"senderThreadId": "foreign"}, {"type": "collabToolCall"},
    {"status": "inProgress"}, {"tool": "futureTool"},
    {"receiverThreadIds": ["child", "child"]}, {"receiverThreadIds": "child"},
    {"agentsStates": None}, {"id": ""},
])
def test_unknown_foreign_incomplete_and_legacy_items_grant_no_observation(changes):
    assert observe(item("spawnAgent", **changes)) is None
