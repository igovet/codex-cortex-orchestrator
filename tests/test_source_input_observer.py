"""Selected root input is captured privately, not promoted to user authority."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cortex_runtime.source_input_observer import capture_prompt
from cortex_runtime.v12_store import V12Store
from cortex_runtime.audience_attestation import _key
from cortex_runtime import submission_queue
from test_activation_hook import invoke, state_file, HOOK, ROOT


@pytest.fixture
def source_host(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = tmp_path / "project"
    project.mkdir()
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "root", "turn_id": "turn",
             "cwd": str(project), "prompt": "$cortex:orchestrator Build an offline counter."}
    return project, tmp_path / "plugin-data", event


def test_registered_hook_captures_repeated_messages_without_emitting_source(tmp_path, source_host):
    project, data, event = source_host
    assert "UserPromptSubmit" in json.loads((Path(__file__).resolve().parents[1] / "plugins/cortex/hooks/hooks.json").read_text())["hooks"]
    assert invoke(tmp_path, event) == (0, None)
    changed = {**event, "prompt": "  Keep keyboard support.\nНе терять требования.\n"}
    assert invoke(tmp_path, changed) == (0, None)
    assert invoke(tmp_path, changed) == (0, None)
    store = V12Store(project)
    pending = store._read(lambda c: submission_queue.pending(c, session="root"))
    assert len(pending) == 3 and len(set(pending)) == 3
    key = _key(data, create=False)
    sources = store._read(lambda c: [submission_queue.read(c, reference=ref, session="root", key=key).text for ref in pending])
    assert sources == [event["prompt"], changed["prompt"], changed["prompt"]]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM source_consumptions").fetchone()[0]) == 0
    state = json.loads(state_file(tmp_path, "root").read_text())
    assert state["input_observation"] == "captured_unverified_origin"
    assert changed["prompt"] not in json.dumps(state, ensure_ascii=False)


@pytest.mark.parametrize("prompt", ["Hello", "$cortex:orchestrator help", "$cortex:orchestrator normal", "normal"])
def test_nonexecuting_routes_collect_no_source(tmp_path, source_host, prompt):
    _, data, event = source_host
    assert invoke(tmp_path, {**event, "prompt": prompt}) == (0, None)
    assert not (tmp_path / "codex-home" / "cortex").exists()
    assert not (data / "activation" / "audience-attestation.key").exists()


def test_normal_route_stops_collection_for_later_messages(tmp_path, source_host):
    project, _, event = source_host
    invoke(tmp_path, event)
    invoke(tmp_path, {**event, "prompt": "$cortex:orchestrator normal"})
    invoke(tmp_path, {**event, "prompt": "Ordinary unrelated message"})
    assert not json.loads(state_file(tmp_path, "root").read_text())["selected"]
    assert V12Store(project)._read(lambda c: c.execute("SELECT COUNT(*) FROM source_submissions").fetchone()[0]) == 1


@pytest.mark.parametrize("selected,child,agent", [(False, False, None), (True, True, None), (True, False, "worker")])
def test_worker_or_unselected_inputs_are_ignored(source_host, selected, child, agent):
    _, data, event = source_host
    if agent:
        event["agent_id"] = agent
    assert capture_prompt(event, selected=selected, child=child, plugin_data=data) == "ignored"
    assert not data.exists()


@pytest.mark.parametrize("field,value", [("cwd", "/"), ("cwd", "relative"), ("prompt", "\ud800"),
                                         ("prompt", []), ("session_id", ""), ("turn_id", "")])
def test_unusable_input_never_creates_authority(source_host, field, value):
    _, data, event = source_host
    event[field] = value
    assert capture_prompt(event, selected=True, child=False, plugin_data=data) == "unavailable"
    assert not data.exists()


def test_symlinked_plugin_data_is_rejected_without_changing_target(tmp_path, source_host):
    _, data, event = source_host
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    before = outside.stat().st_mode
    data.symlink_to(outside, target_is_directory=True)
    assert capture_prompt(event, selected=True, child=False, plugin_data=data) == "unavailable"
    assert outside.stat().st_mode == before
    assert list(outside.iterdir()) == []


def test_installed_input_hook_preserves_lifecycle_binding(tmp_path, source_host):
    project, data, event = source_host
    code_home = tmp_path / "isolated/.codex"
    hook = code_home / "plugins/cache/cortex/cortex/candidate/hooks/cortex_activation.py"
    hook.parent.mkdir(parents=True)
    hook.write_bytes(HOOK.read_bytes())
    launch = {"active": True, "cwd": str(project), "pid": os.getpid(), "session_nonce": "fixture-nonce",
              "profile_fingerprint": hashlib.sha256(str(code_home).encode()).hexdigest()}
    receipt = code_home / ".cortex-live-launch.json"
    receipt.write_text(json.dumps(launch))
    receipt.chmod(0o600)
    binding = code_home / ".cortex-live-binding.json"
    original = b'{"owner":"lifecycle observer","session_id":"root"}\n'
    binding.write_bytes(original)
    binding.chmod(0o600)
    environment = {**os.environ, "CODEX_HOME": str(code_home), "PLUGIN_DATA": str(data),
                   "PLUGIN_ROOT": str(ROOT / "plugins/cortex")}
    result = subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(event), text=True,
                            capture_output=True, env=environment, check=False)
    assert result.returncode == 0 and not result.stdout and not result.stderr
    assert binding.read_bytes() == original
