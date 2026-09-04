import json

import pytest

from cortex_runtime import native_observation as native
from cortex_runtime.host_boundary import normalize_agent_projection


def projection(*children):
    return {"agents": [{"agent_name": "/root", "agent_status": "running"}, *children]}


def test_cli_json_projection_reports_stopped_turns_without_retaining_reports():
    response = json.dumps(projection(
        {"agent_name": "/root/done", "agent_status": {"completed": "private fixture report"}},
        {"agent_name": "/root/stopping", "agent_status": "interrupted"}))
    observed = normalize_agent_projection(response, {})
    assert observed is not None
    assert native.quiescent({"agents": observed}, "done")
    assert native.quiescent({"agents": observed}, "stopping")
    assert "private fixture report" not in json.dumps(observed)


def test_interruption_reply_or_filtered_list_cannot_prove_quiescence():
    assert normalize_agent_projection({"previous_status": "interrupted"}, {}) is None
    assert normalize_agent_projection(projection(
        {"agent_name": "/root/check", "agent_status": "interrupted"}),
        {"path_prefix": "/root/check"}) is None
    assert normalize_agent_projection({"agents": [
        {"agent_name": "/root/check", "agent_status": "interrupted"}]}, {}) is None


@pytest.mark.parametrize("response", [
    '{"agents": [], "agents": []}', 'not JSON', '"nested JSON string"',
    '[' * 2000, ' ' * (256 * 1024 + 1), '\ud800',
    json.dumps(projection({"agent_name": "/root/check", "agent_status": {"completed": {}, "running": True}})),
])
def test_malformed_or_ambiguous_cli_envelope_grants_no_quiescence(response):
    assert normalize_agent_projection(response, {}) is None


def test_complete_unfiltered_projection_preserves_unknown_state_as_present():
    observed = normalize_agent_projection(projection({"agent_name": "/root/check", "agent_status": "unknown"}), {})
    assert observed is not None
    assert not native.quiescent({"agents": observed}, "check")
    assert native.quiescent({"agents": observed}, "absent-worker")
    assert normalize_agent_projection(projection(), {"path_prefix": "/root/check"}) is None
    assert normalize_agent_projection({**projection(), "truncated": True}, {}) is None
    assert normalize_agent_projection({"agents": []}, {}) is None


def test_signed_projection_is_bound_to_task_session_revision_and_barrier(tmp_path):
    task, session = native.digest("task"), native.digest("session")
    assert native.bind_task(tmp_path, task_digest=task, session_digest=session)
    assert not native.bind_task(tmp_path, task_digest=task, session_digest=native.digest("foreign"))
    assert not native.record_projection(tmp_path, task_digest=task, session_digest=native.digest("foreign"),
        revision=2, barrier_epoch=3, response=projection(), arguments={})
    assert native.record_projection(tmp_path, task_digest=task, session_digest=session,
        revision=2, barrier_epoch=3, response=projection({"agent_name": "/root/check", "agent_status": "idle"}), arguments={})
    verified = native.verified_projection(tmp_path, task_digest=task, revision=2, barrier_epoch=3)
    assert verified and native.quiescent(verified, "check")
    assert native.verified_projection(tmp_path, task_digest=task, revision=1, barrier_epoch=3) is None
    assert native.verified_projection(tmp_path, task_digest=task, revision=2, barrier_epoch=4) is None
    path = tmp_path / "activation" / "native-observations" / (task + ".json")
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert "check" not in text and "session" not in text.replace('"session"', '')
    tampered = json.loads(text)
    tampered["observation"]["barrier_epoch"] = 4
    path.write_text(json.dumps(tampered))
    assert native.verified_projection(tmp_path, task_digest=task, revision=2, barrier_epoch=4) is None


def test_duplicate_short_names_cannot_prove_native_absence():
    assert normalize_agent_projection(projection(
        {"agent_name": "/root/one/check", "agent_status": "idle"},
        {"agent_name": "/root/two/check", "agent_status": "running"}), {}) is None


@pytest.mark.parametrize("agents", [
    [{"agent_name": "/foreign/root", "agent_status": "running"}],
    [{"agent_name": "/root/child/root", "agent_status": "running"}],
    [{"agent_name": "/root", "agent_status": "running"},
     {"agent_name": "/foreign/check", "agent_status": "idle"}],
])
def test_only_the_complete_root_tree_can_prove_absence(agents):
    assert normalize_agent_projection({"agents": agents}, {}) is None
