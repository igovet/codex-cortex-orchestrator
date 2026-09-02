"""Black-box coverage for the official Codex lifecycle hook observer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
HOOK = ROOT / "plugins" / "cortex" / "hooks" / "cortex_lifecycle_observer.py"
sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.observation_generation import claim_generation, consume_intent, request_generation  # noqa: E402


def _fixture(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"; home.mkdir(mode=0o700)
    codex = home / ".codex"; codex.mkdir(mode=0o700)
    candidate = codex / "plugins/cache/cortex/cortex/1.14.11"; candidate.mkdir(parents=True, mode=0o700)
    nonce = "a" * 64; build = "sha256:" + "b" * 64
    request_generation(code_home=codex, build_id=build, candidate_version="1.14.11", catalogue_count=15, catalogue_digest="c" * 64, session_nonce=nonce)
    consume_intent(code_home=codex, package_root=candidate, build_id=build, candidate_version="1.14.11", catalogue_count=15, catalogue_digest="c" * 64, session_nonce=nonce)
    claim_generation(package_root=candidate, build_id=build, candidate_version="1.14.11", catalogue_count=15, catalogue_digest="c" * 64, session_nonce=nonce)
    env = {"PLUGIN_ROOT": str(ROOT / "plugins/cortex"), "CODEX_HOME": str(codex), "CORTEX_CANDIDATE_PATH": str(candidate), "CORTEX_BUILD_ID": build}
    generation = codex / ".cortex-mcp-observations/generations"
    return env, generation


def _invoke(tmp_path: Path, event: dict) -> list[dict]:
    env, generations = _fixture(tmp_path)
    completed = subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(event), text=True, capture_output=True, env={**os.environ, **env}, check=False)
    assert completed.returncode == 0, completed.stderr
    files = list(generations.glob("*/events.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="ascii").splitlines()]


def test_official_payloads_are_sanitized_and_parent_correlated(tmp_path: Path) -> None:
    events = _invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": "session-native", "turn_id": "turn-native", "agent_id": "agent-native", "agent_type": "worker", "transcript_path": "/private/transcript"})
    assert events[-1]["kind"] == "subagent_start"
    assert events[-1]["session"] == events[-1]["parent"]
    rendered = json.dumps(events)
    assert "native" not in rendered and "transcript" not in rendered and "/private" not in rendered


def test_stop_does_not_infer_continuation(tmp_path: Path) -> None:
    events = _invoke(tmp_path, {"hook_event_name": "SubagentStop", "session_id": "s", "turn_id": "t", "agent_id": "a", "stop_hook_active": False, "last_assistant_message": "secret"})
    assert events[-1]["kind"] == "subagent_stop"
    assert "status" not in events[-1] or events[-1]["status"] == "unknown"


def test_compaction_and_session_end_are_observation_only(tmp_path: Path) -> None:
    events = _invoke(tmp_path, {"hook_event_name": "PreCompact", "session_id": "s", "turn_id": "t", "trigger": "auto", "transcript_path": "secret"})
    assert events[-1]["kind"] == "pre_compact"
    assert events[-1]["source"] == "auto"


def test_agent_marker_is_only_hashed_and_unrelated_agent_is_ignored(tmp_path: Path) -> None:
    marker = "dc_" + "d" * 32
    events = _invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "s", "turn_id": "t", "agent_id": "native-agent", "tool_input": {"brief": "trusted " + marker, "secret": "do-not-store"}})
    assert events[-1]["kind"] == "native_dispatch"
    assert events[-1]["source"] == "unavailable"
    assert events[-1]["dispatch_correlation"] != marker
    assert marker not in json.dumps(events) and "do-not-store" not in json.dumps(events)
