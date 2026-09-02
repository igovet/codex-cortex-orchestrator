from __future__ import annotations

import importlib.machinery
import importlib.util
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cortex-live-smoke"


def module():
    loader = importlib.machinery.SourceFileLoader("cortex_live_smoke", str(SCRIPT))
    spec = importlib.util.spec_from_loader("cortex_live_smoke", loader)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_docs_use_classic_operator_workflow() -> None:
    docs = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in (
        "AGENTS.md", "README.md", "docs/project/verification.md",
        "docs/release-readiness.md",
    ))
    for literal in (
        "./scripts/cortex-live-smoke start",
        "./scripts/cortex-live-smoke send --prompt-file",
        "./scripts/cortex-live-smoke capture",
        "./scripts/cortex-live-smoke events",
        "./scripts/cortex-live-smoke stop",
        "default tmux server",
        "only permitted Cortex-specific content",
        "ordinary user request",
        "external operator",
        "nested tmux",
        "schema_unsupported",
    ):
        assert literal in docs
    assert "readiness_timeout" not in docs
    assert "Owned PTY fallback" not in docs
    assert "--workload" not in docs


def test_docs_require_visible_composer_before_send() -> None:
    docs = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in (
        "AGENTS.md", "README.md", "docs/project/verification.md",
        "docs/release-readiness.md",
    ))
    for text in (
        "visibly confirm that the interactive composer is rendered before sending",
        "visibly confirm the interactive composer",
        "pane_current_command=codex",
        "early text or submission can be lost during TUI initialization",
        "bounded sanitized structured event stream",
        "clean first worker-owned report-submission success",
        "helper may expose events but must not decide pass/fail",
        "final report reference alone is insufficient",
        "passive host-owned activation receipt",
        "exact isolated candidate",
        "advertised catalogue identity",
        "first project execution action",
        "route violation",
    ):
        assert text in docs
    assert docs.index("cortex-live-smoke start") < docs.index("visibly confirm") < docs.index("cortex-live-smoke send --prompt-file")


def test_docs_define_llm_owned_multiturn_e2e_acceptance() -> None:
    docs = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in (
        "AGENTS.md", "README.md", "docs/project/verification.md",
        "docs/release-readiness.md",
    ))
    for text in (
        "multi-turn",
        "separate test project",
        "exactly one product clarification",
        "predefined safe answer",
        "visibly rendered plan",
        "planner → implementation → independent verification → documentation-impact assessment → closure",
        "every native worker event stream",
        "hidden tool error or unexplained replay",
        "never answers or approves autonomously",
    ):
        assert text in docs


def _assert_ordinary_live_workload(prompt: str) -> None:
    normalized = prompt.strip()
    assert normalized.startswith("$cortex:orchestrator\n")
    assert normalized.lower().count("cortex") == 1
    assert "[$cortex:orchestrator]" not in normalized
    task_text = normalized.removeprefix("$cortex:orchestrator").strip()
    assert task_text
    for forbidden in (
        "live-dev", "orchestrat", "mcp", "worker", "subagent", "coordinator",
        "ledger", "governance", "tmux", "tool call", "tool-call", "replay",
        "sentinel", "stabilization", "harness", "pipeline", "task_ref",
        "delegation_ref", "report_ref", "idempotency_key",
        "оркестрац", "воркер", "сабагент", "координатор",
    ):
        assert forbidden not in task_text.lower()


def test_prompt_fixtures_are_ordinary_requests_with_only_the_skill_token() -> None:
    prompt = (ROOT / "tests/fixtures/live_cortex_stabilization_prompt.txt").read_text(encoding="utf-8")
    contract = json.loads((ROOT / "tests/fixtures/live_contract_workload.json").read_text(encoding="utf-8"))
    _assert_ordinary_live_workload(prompt)
    _assert_ordinary_live_workload(contract["prompt"])
    architecture = ROOT / "docs" / "architecture"
    for path in (
        architecture / "live-baseline-run2p-prompt.txt",
        architecture / "live-baseline-run3q-prompt.txt",
        architecture / "live-e2e-html-production-prompt.txt",
    ):
        _assert_ordinary_live_workload(path.read_text(encoding="utf-8"))
    assert "настроек уведомлений" in prompt
    assert "избранное" in contract["prompt"]
    assert "покажи план и дождись" in prompt
    assert "покажи план и дождись" in contract["prompt"]


def test_one_page_workload_is_an_ordinary_feature_request_with_natural_acceptance() -> None:
    fixture = ROOT / "tests/fixtures/live_dev_one_page_workload.json"
    workload = json.loads(fixture.read_text(encoding="utf-8"))
    prompt = workload["prompt"]
    assert workload["project"] == "simple-one-page-html"
    _assert_ordinary_live_workload(prompt)
    for required in (
        "$cortex:orchestrator",
        "самодостаточную страницу index.html",
        "ровно тремя карточками преимуществ",
        "компактным FAQ",
        "переключателем темы",
        "Внешние ресурсы и сетевые зависимости не используй",
        "keyboard focus",
        "reduced motion",
        "один вопрос о цвете акцента",
        "покажи план и дождись моего одобрения",
        "независимо проверь функциональность",
        "короткий README",
    ):
        assert required in prompt
    for forbidden in (
        "prompt_en", "consumer_delegation_ref", "task_ref", "delegation_ref",
        "report_ref", "idempotency_key", "max_bytes", "inputSchema",
    ):
        assert forbidden not in prompt


def test_agents_requires_mcp_argument_contracts_to_stay_in_tool_schemas() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for required in (
        "MCP argument contracts belong exclusively",
        "Never put MCP parameter names",
        "live advertised tool contract",
        "schema/description defect",
        "first-call regression test",
    ):
        assert required in agents


def test_send_uses_literal_single_line_then_one_delayed_enter(monkeypatch, tmp_path: Path, capsys) -> None:
    driver = module()
    calls = []
    sleeps = []

    class Result:
        returncode = 0
        stdout = ""

    state = {"exists": True}
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: sleeps.append(seconds))
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("kill-session", "-t"):
            state["exists"] = False
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    prompt = tmp_path / "task.txt"
    prompt.write_text("first line\nsecond\tline\n", encoding="utf-8")
    assert driver.send_prompt(prompt) == 0
    assert len(calls) == 3
    assert calls[0][0:4] == ("send-keys", "-l", "-t", driver.TARGET)
    assert calls[0][4] == "--"
    assert calls[0][5] == "first line second line"
    assert calls[1][0:3] == ("display-message", "-p", "-t")
    assert calls[2] == ("send-keys", "-t", driver.TARGET, "Enter")
    assert sleeps == [driver.SUBMIT_DRAIN_SECONDS]
    output = capsys.readouterr().out
    assert "prompt-inserted=" in output
    assert "wait-seconds=5" in output
    assert "submit-key-sent-count=1" in output
    assert "submit-key-sequence=Enter" in output
    assert "prompt-submitted" not in output


def test_send_is_transport_only_and_does_not_parse_rollout(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(driver, "exists", lambda: True)
    monkeypatch.setattr(driver.time, "sleep", lambda _seconds: None)
    def fake_tmux(*args):
        calls.append(args)
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    prompt = tmp_path / "task.txt"
    prompt.write_text("A prompt that never reaches the composer tail", encoding="utf-8")
    assert driver.send_prompt(prompt) == 0
    assert sum(call and call[0] == "send-keys" and call[-1] == "Enter" for call in calls) == 1
    assert not any(call and call[0] == "capture-pane" for call in calls)


@pytest.mark.parametrize("length", [1, 1024, 1025])
def test_send_always_delivers_one_submit_key(monkeypatch, tmp_path: Path, length: int) -> None:
    driver = module()

    class Result:
        returncode = 0
        stdout = ""

    calls = []
    sleeps = []
    monkeypatch.setattr(driver, "exists", lambda: True)
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(driver, "tmux", lambda *args: calls.append(args) or Result())
    prompt = tmp_path / "task.txt"
    prompt.write_text("x" * length, encoding="utf-8")
    assert driver.send_prompt(prompt) == 0
    assert sum(call[-1:] == ("Enter",) for call in calls) == 1
    assert sleeps[-1] == driver.SUBMIT_DRAIN_SECONDS
    assert sleeps[:-1] == [driver.PROMPT_CHUNK_DRAIN_SECONDS] * ((length - 1) // driver.PROMPT_CHUNK_CHARS)


def test_send_key_count_uses_unicode_characters_not_utf8_bytes(monkeypatch, tmp_path: Path, capsys) -> None:
    driver = module()

    class Result:
        returncode = 0
        stdout = ""

    calls = []
    sleeps = []
    monkeypatch.setattr(driver, "exists", lambda: True)
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(driver, "tmux", lambda *args: calls.append(args) or Result())
    prompt = tmp_path / "task.txt"
    prompt.write_text("я" * 1024, encoding="utf-8")
    assert driver.send_prompt(prompt) == 0
    assert sum(call[-1:] == ("Enter",) for call in calls) == 1
    assert sleeps[-1] == driver.SUBMIT_DRAIN_SECONDS
    assert sleeps[:-1] == [driver.PROMPT_CHUNK_DRAIN_SECONDS] * ((1024 - 1) // driver.PROMPT_CHUNK_CHARS)
    assert "submit-key-sent-count=1" in capsys.readouterr().out


def test_start_creates_shell_then_submits_literal_launcher(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    state = {"exists": False}
    monkeypatch.setattr(driver, "_STATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(driver, "_STATE_FILE", tmp_path / "private" / "session.json")
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: Result())
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("new-session", "-d"):
            state["exists"] = True
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.start(tmp_path) == 0
    assert calls[0][0:6] == ("new-session", "-d", "-s", driver.SESSION, "-c", str(tmp_path))
    assert calls[0] == (
        "new-session", "-d", "-s", driver.SESSION, "-c", str(tmp_path), "bash",
    )
    assert calls[1][0:3] == ("pipe-pane", "-t", driver.TARGET)
    assert "cortex-live-capture-sink" in calls[1][-1]
    assert calls[2][0:3] == ("send-keys", "-l", "-t")
    assert calls[2][3] == driver.TARGET
    assert "scripts/cortex-dev" in calls[2][-1]
    assert "CORTEX_EVENT_JOURNAL_PATH" not in calls[2][-1]
    assert calls[3] == ("send-keys", "-t", driver.TARGET, "C-m")
    assert calls.index(calls[1]) < calls.index(calls[2]) < calls.index(calls[3])


def test_start_resume_last_uses_ordinary_interactive_resume(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    calls = []
    class Result:
        returncode = 0
        stdout = ""
    state = {"exists": False}
    monkeypatch.setattr(driver, "_STATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(driver, "_STATE_FILE", tmp_path / "private" / "session.json")
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: Result())
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("new-session", "-d"):
            state["exists"] = True
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.start(tmp_path, resume_last=True) == 2
    assert calls == []


def test_resume_launcher_uses_exact_stored_session_id() -> None:
    driver = module()
    launcher = driver._launcher_command(Path("/repo/scripts/cortex-dev"), Path("/project"), Path("/events"), resume_last=True, resume_session_id="session-123")
    assert "scripts/cortex-dev resume session-123" in launcher
    assert "resume --last" not in launcher
    assert "codex exec" not in launcher


def test_start_accepts_separate_canonical_workdir(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    project = tmp_path / "repository"
    workdir = tmp_path / "test-project"
    project.mkdir()
    workdir.mkdir()
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(driver, "exists", lambda: False)
    monkeypatch.setattr(driver, "_STATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(driver, "_STATE_FILE", tmp_path / "private" / "session.json")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(driver, "tmux", lambda *args: calls.append(args) or Result())
    assert driver.start(project, workdir) == 2
    assert calls[0][0:6] == ("new-session", "-d", "-s", driver.SESSION, "-c", str(workdir.resolve()))


def test_cortex_dev_restores_caller_workdir_after_repository_sync() -> None:
    launcher = (ROOT / "scripts" / "cortex-dev").read_text(encoding="utf-8")
    assert 'caller_workdir="$(pwd -P)"' in launcher
    assert 'cd -- "${project_dir}"' in launcher
    assert 'cd -- "${caller_workdir}"' in launcher
    assert launcher.index('cd -- "${project_dir}"') < launcher.index('./scripts/sync-cortex.sh')
    assert launcher.index('./scripts/sync-cortex.sh') < launcher.index('cd -- "${caller_workdir}"')
    assert launcher.index('cd -- "${caller_workdir}"') < launcher.index('exec codex "$@"')


def test_send_rejects_empty_and_nul(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    monkeypatch.setattr(driver, "exists", lambda: True)
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    nul = tmp_path / "nul"
    nul.write_bytes(b"ok\x00bad")
    assert driver.send_prompt(empty) == 2
    assert driver.send_prompt(nul) == 2


def test_commands_are_exact_session_scoped(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    _private_state(driver, tmp_path)
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    state = {"exists": True}
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("kill-session", "-t"):
            state["exists"] = False
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.status() == 0
    assert driver.capture(20) == 0
    assert driver.stop(False) == 0
    assert any(driver.TARGET in args for args in calls)
    assert all("kill-server" not in call for args in calls for call in args)


def _private_state(driver, tmp_path: Path, payload: bytes = b"") -> Path:
    driver._STATE_ROOT = tmp_path / "private"
    driver._STATE_FILE = driver._STATE_ROOT / "session.json"
    state = driver._new_capture_state()
    driver._write_state(state)
    capture_path = Path(str(state["capture_path"]))
    capture_path.write_bytes(payload)
    os.chmod(capture_path, 0o600)
    return capture_path


def _production_event_fixture(driver, tmp_path: Path, monkeypatch) -> tuple[dict, Path, Path]:
    """Provision the exact signed candidate/lease topology used by events()."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workdir = tmp_path / "project"
    workdir.mkdir()
    driver._STATE_ROOT = tmp_path / "private"
    driver._STATE_FILE = driver._STATE_ROOT / "session.json"
    state = driver._new_capture_state(workdir)
    driver._write_state(state)
    sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from cortex import PUBLIC_TOOLS
    from cortex_runtime.mcp_api import catalogue_identity
    from cortex_runtime.observation_generation import consume_intent, request_generation
    from cortex_candidate_receipt import write_receipt
    from cortex_release_candidate import build_source_candidate
    identity = catalogue_identity(PUBLIC_TOOLS)
    staged = tmp_path / "staged"
    build_source_candidate(ROOT, staged)
    version = json.loads((staged / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    codex_home = tmp_path / ".cortex-dev" / ".codex"
    candidate = codex_home / "plugins/cache/cortex/cortex" / version
    candidate.parent.mkdir(parents=True)
    shutil.copytree(staged / "plugins/cortex", candidate)
    receipt = write_receipt(source_root=ROOT, owner_home=tmp_path, isolated_home=tmp_path / ".cortex-dev", isolated_codex_home=codex_home, candidate_version=version)
    request_generation(code_home=codex_home, build_id=receipt["build_id"], candidate_version=version, session_nonce=str(state["session_nonce"]), catalogue_count=len(PUBLIC_TOOLS), catalogue_digest=str(identity["catalogue_digest"]))
    lease = consume_intent(code_home=codex_home, package_root=candidate, build_id=receipt["build_id"], candidate_version=version, session_nonce=str(state["session_nonce"]), catalogue_count=len(PUBLIC_TOOLS), catalogue_digest=str(identity["catalogue_digest"]))
    generation = Path(str(state["observation_root"])) / str(lease["generation_id"])
    generation.mkdir(parents=True, mode=0o700)
    for name in ("request.json", "ready.json"):
        path = generation / name
        path.write_text("{}\n", encoding="ascii")
        os.chmod(path, 0o600)
    event_path = generation / "events.jsonl"
    event_path.write_text('{"build_id":"' + receipt["build_id"] + '","kind":"command","monotonic_ns":1,"operation":"publish_result","outcome":"success","sequence":1}\n', encoding="ascii")
    os.chmod(event_path, 0o600)
    return state, Path(str(state["observation_root"])).parent / "lease.json", event_path


def test_capture_reads_the_bounded_pipe_stream_not_stale_pane(monkeypatch, tmp_path: Path, capsys) -> None:
    driver = module()
    _private_state(driver, tmp_path, b"\x1b[2JDo you trust the contents of this directory?\r\nPress enter to continue\r\n")
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.capture(20) == 0
    output = capsys.readouterr().out
    assert "Do you trust the contents" in output
    assert "Press enter to continue" in output


def test_trust_screen_documents_default_continue_option() -> None:
    docs = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "operator/LLM may run `./scripts/cortex-live-smoke enter` exactly once" in docs
    assert "never auto-trusts a directory" in docs


def test_events_accepts_ready_then_activation_observation_in_sequence() -> None:
    driver = module()
    build_id = "sha256:" + "a" * 64
    rows = b"\n".join([
        ("{\"build_id\":\"" + build_id + "\",\"catalogue_count\":15,\"catalogue_digest\":\"" + "b" * 64 + "\",\"kind\":\"registration\",\"monotonic_ns\":1,\"operation\":\"server_ready\",\"outcome\":\"success\",\"scope\":\"unattributed\",\"sequence\":1}").encode(),
        ("{\"build_id\":\"" + build_id + "\",\"kind\":\"pre_tool\",\"monotonic_ns\":2,\"operation\":\"activation_hook\",\"outcome\":\"success\",\"scope\":\"coordinator\",\"sequence\":2}").encode(),
    ]) + b"\n"
    stage, rendered = driver._validate_event_stream(rows, build_id)
    assert stage == ""
    assert len(rendered) == 2


def test_events_reads_exact_sanitized_stream_without_parser(monkeypatch, tmp_path: Path, capsys) -> None:
    driver = module()
    # Build the same isolated cache topology and signed receipt used by the
    # production candidate path.  A partial hand-written receipt is not a
    # valid observer fixture: the observer must validate source/candidate
    # digests, exact managed path, isolation relation, and receipt digest.
    monkeypatch.setenv("HOME", str(tmp_path))
    workdir = tmp_path / "project"
    workdir.mkdir()
    driver._STATE_ROOT = tmp_path / "private"
    driver._STATE_FILE = driver._STATE_ROOT / "session.json"
    state = driver._new_capture_state(workdir)
    driver._write_state(state)
    state = driver._state()
    assert state is not None
    sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from cortex import PUBLIC_TOOLS  # noqa: PLC0415
    from cortex_runtime.mcp_api import catalogue_identity  # noqa: PLC0415
    from cortex_runtime.observation_generation import consume_intent, request_generation  # noqa: PLC0415
    from cortex_candidate_receipt import write_receipt  # noqa: PLC0415
    from cortex_release_candidate import build_source_candidate  # noqa: PLC0415
    identity = catalogue_identity(PUBLIC_TOOLS)
    staged = tmp_path / "staged"
    build_source_candidate(ROOT, staged)
    version = json.loads((staged / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    codex_home = tmp_path / ".cortex-dev" / ".codex"
    candidate = codex_home / "plugins/cache/cortex/cortex" / version
    candidate.parent.mkdir(parents=True)
    shutil.copytree(staged / "plugins/cortex", candidate)
    receipt = write_receipt(
        source_root=ROOT, owner_home=tmp_path, isolated_home=tmp_path / ".cortex-dev",
        isolated_codex_home=codex_home, candidate_version=version,
    )
    request_generation(
        code_home=codex_home, build_id=receipt["build_id"], candidate_version=version,
        session_nonce=str(state["session_nonce"]), catalogue_count=len(PUBLIC_TOOLS),
        catalogue_digest=str(identity["catalogue_digest"]),
    )
    lease = consume_intent(
        code_home=codex_home, package_root=candidate, build_id=receipt["build_id"],
        candidate_version=version, session_nonce=str(state["session_nonce"]),
        catalogue_count=len(PUBLIC_TOOLS), catalogue_digest=str(identity["catalogue_digest"]),
    )
    generation = Path(str(state["observation_root"])) / str(lease["generation_id"])
    generation.mkdir(parents=True, mode=0o700, exist_ok=True)
    (generation / "request.json").write_text("{}\n", encoding="ascii")
    (generation / "ready.json").write_text("{}\n", encoding="ascii")
    os.chmod(generation / "request.json", 0o600)
    os.chmod(generation / "ready.json", 0o600)
    event_path = generation / "events.jsonl"
    event_path.write_text('{"build_id":"' + receipt["build_id"] + '","kind":"command","monotonic_ns":1,"operation":"publish_result","outcome":"success","sequence":1}\n', encoding="ascii")
    os.chmod(event_path, 0o600)
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.events(20) == 0
    assert "publish_result" in capsys.readouterr().out


def test_events_discards_only_a_partial_leading_jsonl_record(monkeypatch, tmp_path: Path, capsys) -> None:
    driver = module()
    monkeypatch.setattr(driver, "CAPTURE_MAX_BYTES", 500)
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    first = json.loads(event_path.read_text(encoding="ascii"))
    rows = []
    for sequence in range(1, 5):
        value = {**first, "sequence": sequence, "monotonic_ns": sequence, "padding": "x" * 80}
        rows.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
    event_path.write_text("\n".join(rows) + "\n", encoding="ascii")
    os.chmod(event_path, 0o600)
    assert len(event_path.read_bytes()) > driver.CAPTURE_MAX_BYTES
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.events(20) == 0
    output = capsys.readouterr().out
    assert '"sequence":4' in output
    assert "failure_stage" not in output


def test_events_remain_available_after_source_bytecode_appears_post_staging(monkeypatch, tmp_path: Path, capsys) -> None:
    """The issued candidate, not a mutable checkout, anchors post-launch reads."""
    driver = module()
    _state, _lease, _event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    bytecode = ROOT / "plugins/cortex/scripts/cortex_runtime/__pycache__"
    bytecode.mkdir()
    transient = bytecode / "post-stage.cpython-313.pyc"
    transient.write_bytes(b"not-executed")
    try:
        assert driver.events(20) == 0
        assert "publish_result" in capsys.readouterr().out
    finally:
        transient.unlink(missing_ok=True)
        bytecode.rmdir()


def test_events_rejects_tampered_lease_identity_without_opening_redirected_path(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    state, lease_path, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    lease = json.loads(lease_path.read_text(encoding="ascii"))
    lease["build_id"] = "sha256:tampered"
    lease_path.write_text(json.dumps(lease, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
    os.chmod(lease_path, 0o600)
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.events(20) == 2
    assert event_path.exists()


def test_events_rejects_resigned_stale_lease(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    state, lease_path, _event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    lease = json.loads(lease_path.read_text(encoding="ascii"))
    lease["created_ns"] = 1
    unsigned = json.dumps({key: value for key, value in lease.items() if key != "signature"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    lease["signature"] = hmac.new(bytes.fromhex(str(state["session_nonce"])), unsigned, hashlib.sha256).hexdigest()
    lease_path.write_text(json.dumps(lease, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
    os.chmod(lease_path, 0o600)
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.events(20) == 2


@pytest.mark.parametrize("field,value", [
    ("candidate_path", "/tmp/not-the-candidate"),
    ("candidate_version", "1.14.14+codex.sha256." + "0" * 16),
    ("build_id", "sha256:" + "0" * 64),
    ("source_digest", "0" * 64),
    ("candidate_digest", "0" * 64),
    ("base_version", "9.9.9"),
    ("isolated_home", "/tmp/other-isolated-profile"),
    ("isolated_codex_home", "/tmp/other-isolated-profile/.codex"),
])
def test_events_rejects_resigned_candidate_receipt_identity_tamper(monkeypatch, tmp_path: Path, field: str, value: str) -> None:
    driver = module()
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    receipt_path = event_path.parents[3] / ".cortex-candidate-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    receipt[field] = value
    # A mere checksum mismatch is not sufficient coverage: recompute the
    # canonical checksum to prove that `events` reaches receipt topology,
    # isolation, source and installed-payload verification rather than merely
    # rejecting syntactically corrupted JSON.
    from cortex_candidate_receipt import _digest  # noqa: PLC0415
    receipt["receipt_sha256"] = _digest({key: item for key, item in receipt.items() if key != "receipt_sha256"})
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
    os.chmod(receipt_path, 0o600)
    assert driver.events(20) == 2
    assert event_path.exists()


def test_events_rejects_receipt_checksum_tamper(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    receipt_path = event_path.parents[3] / ".cortex-candidate-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    receipt["receipt_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
    os.chmod(receipt_path, 0o600)
    assert driver.events(20) == 2
    assert event_path.exists()


def test_events_rejects_receipt_mode_and_candidate_tree_tamper(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    receipt_path = event_path.parents[3] / ".cortex-candidate-receipt.json"
    receipt_path.chmod(0o644)
    assert driver.events(20) == 2
    receipt_path.chmod(0o600)
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    candidate = Path(receipt["candidate_path"])
    (candidate / "unexpected-payload").write_text("tamper", encoding="utf-8")
    assert driver.events(20) == 2


def test_events_rejects_receipt_symlink(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    receipt_path = event_path.parents[3] / ".cortex-candidate-receipt.json"
    moved = tmp_path / "receipt-real.json"
    receipt_path.rename(moved)
    receipt_path.symlink_to(moved)
    assert driver.events(20) == 2
    assert event_path.exists()


def test_events_rejects_candidate_path_symlink(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    _state, _lease, event_path = _production_event_fixture(driver, tmp_path, monkeypatch)
    receipt_path = event_path.parents[3] / ".cortex-candidate-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    candidate = Path(receipt["candidate_path"])
    moved = candidate.with_name(candidate.name + "-real")
    candidate.rename(moved)
    candidate.symlink_to(moved, target_is_directory=True)
    assert driver.events(20) == 2
    assert event_path.exists()


def test_enter_is_one_exact_transport_key_and_has_no_observer(monkeypatch) -> None:
    driver = module()
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(driver, "exists", lambda: True)
    monkeypatch.setattr(driver, "tmux", lambda *args: calls.append(args) or Result())
    assert driver.send_enter() == 0
    assert calls == [("send-keys", "-t", driver.TARGET, "Enter")]


def test_enter_rejects_a_missing_session(monkeypatch) -> None:
    driver = module()
    monkeypatch.setattr(driver, "exists", lambda: False)
    assert driver.send_enter() == 2


def test_status_reports_missing_session_without_touching_a_profile(monkeypatch, capsys) -> None:
    driver = module()
    monkeypatch.setattr(driver, "exists", lambda: False)
    assert driver.status() == 1
    assert "has-session=no" in capsys.readouterr().out


def test_output_sink_is_owner_only_and_retains_only_its_tail(tmp_path: Path) -> None:
    capture = tmp_path / "capture.raw"
    capture.touch(mode=0o600)
    os.chmod(capture, 0o600)
    sink = ROOT / "scripts" / "cortex-live-capture-sink"
    data = b"abcdefghij"
    result = subprocess.run(
        [sys.executable, str(sink), "--path", str(capture), "--max-bytes", "4"],
        input=data, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert capture.read_bytes() == b"ghij"
    assert stat.S_IMODE(capture.stat().st_mode) == 0o600


def test_stop_closes_exact_pipe_then_removes_private_capture(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    capture = _private_state(driver, tmp_path)
    calls = []
    state = {"exists": True}

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("kill-session", "-t"):
            state["exists"] = False
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.stop(True) == 0
    assert calls[0] == ("pipe-pane", "-t", driver.TARGET)
    assert calls[1] == ("send-keys", "-t", driver.TARGET, "C-c")
    assert calls[2] == ("kill-session", "-t", f"={driver.SESSION}")
    assert capture.exists()
    assert driver._STATE_FILE.exists()
    driver._discard_state()
    assert not capture.exists()
    assert not driver._STATE_FILE.exists()
    assert all("kill-server" not in item for call in calls for item in call)


def test_stop_missing_session_discards_only_validated_stale_capture(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    capture = _private_state(driver, tmp_path)
    monkeypatch.setattr(driver, "exists", lambda: False)
    assert driver.stop(False) == 0
    assert capture.exists()
    assert driver._STATE_FILE.exists()
    driver._discard_state()
    assert not capture.exists()
    assert not driver._STATE_FILE.exists()


def test_start_private_capture_metadata_has_owner_only_modes(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    state = {"exists": False}

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(driver, "_STATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(driver, "_STATE_FILE", tmp_path / "private" / "session.json")
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: Result())
    def fake_tmux(*args):
        if args[:2] == ("new-session", "-d"):
            state["exists"] = True
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.start(tmp_path) == 0
    stored = driver._state()
    assert stored is not None
    assert stat.S_IMODE(driver._STATE_ROOT.stat().st_mode) == 0o700
    assert stat.S_IMODE(driver._STATE_FILE.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(str(stored["capture_dir"])).stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(str(stored["capture_path"])).stat().st_mode) == 0o600


def test_transport_source_contains_no_profile_or_acceptance_automation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("codex exec", "kill-server", "HOME=", "CODEX_HOME=", "capture-pane"):
        assert forbidden not in source
    assert "pipe-pane" in source
    assert "send_enter" in source
    assert "def events" in source
    assert "capture-pane" not in source


def test_prompt_transport_uses_literal_send_keys_and_named_enter() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'tmux("send-keys", "-l", "-t", TARGET, "--", chunk)' in source
    assert 'tmux("send-keys", "-t", TARGET, "Enter")' in source
    assert 'tmux("paste-buffer"' not in source
    assert 'tmux("send-keys", "-t", TARGET, "C-m")' not in source[source.index('def send_prompt'):source.index('def send_enter')]


def test_real_tmux_literal_insert_then_enter_executes_after_delay(tmp_path: Path) -> None:
    """Exercise the actual default tmux client and pane state transition."""
    import subprocess
    import time
    session = f"cortex-live-test-{os.getpid()}"
    target = f"{session}:0.0"
    prompt = "printf 'LIVE_TRANSPORT_OK\\n'"
    try:
        created = subprocess.run(["tmux", "new-session", "-d", "-s", session, "bash"], capture_output=True, text=True)
        assert created.returncode == 0, created.stderr
        inserted = subprocess.run(["tmux", "send-keys", "-l", "-t", target, "--", prompt], capture_output=True, text=True)
        assert inserted.returncode == 0, inserted.stderr
        time.sleep(5)
        submitted = subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], capture_output=True, text=True)
        assert submitted.returncode == 0, submitted.stderr
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            captured = subprocess.run(["tmux", "capture-pane", "-p", "-t", target], capture_output=True, text=True)
            if "LIVE_TRANSPORT_OK" in captured.stdout:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("standalone Enter did not submit literal prompt")
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True, text=True)


def test_real_tmux_long_literal_prompt_uses_subthreshold_chunks(tmp_path: Path) -> None:
    """A long literal delivery must still submit with one final Enter."""
    import subprocess
    import time
    session = f"cortex-live-long-{os.getpid()}"
    target = f"{session}:0.0"
    prompt = "printf '" + ("x" * 1200) + "\\nLONG_TRANSPORT_OK\\n'"
    try:
        created = subprocess.run(["tmux", "new-session", "-d", "-s", session, "bash"], capture_output=True, text=True)
        assert created.returncode == 0, created.stderr
        chunks = [prompt[i:i + 512] for i in range(0, len(prompt), 512)]
        for index, chunk in enumerate(chunks):
            inserted = subprocess.run(["tmux", "send-keys", "-l", "-t", target, "--", chunk], capture_output=True, text=True)
            assert inserted.returncode == 0, inserted.stderr
            if index + 1 < len(chunks):
                time.sleep(0.1)
        time.sleep(5)
        submitted = subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], capture_output=True, text=True)
        assert submitted.returncode == 0, submitted.stderr
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            captured = subprocess.run(["tmux", "capture-pane", "-p", "-t", target], capture_output=True, text=True)
            if "LONG_TRANSPORT_OK" in captured.stdout:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("long literal prompt was not submitted by one Enter")
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True, text=True)
