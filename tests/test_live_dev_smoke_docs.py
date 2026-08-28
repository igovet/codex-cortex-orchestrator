from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

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
        "./scripts/cortex-live-smoke stop",
        "default tmux server",
        "already live-dev",
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


def test_prompt_fixture_is_task_specific_without_mcp_parameter_hints() -> None:
    prompt = (ROOT / "tests/fixtures/live_cortex_stabilization_prompt.txt").read_text(encoding="utf-8")
    assert "already live-dev" in prompt
    assert "selected route is $cortex:orchestrator" in prompt
    assert "Cortex MCP tools for ledger operations and orchestration evidence" in prompt
    assert "Native collaboration/subagents are allowed and required" in prompt
    assert "worker dispatch and report ownership" in prompt
    assert "Dispatch one bounded native worker and use its owned report" in prompt
    for prohibited in ("Do not run shell commands", "inspect source", "modify files", "start nested live-dev", "start nested tmux"):
        assert prohibited in prompt
    assert "advertised schemas and descriptions" in prompt
    assert "canonical current user-decision contract" in prompt
    assert "CORTEX_LIVE_TOOLS_OK" in prompt
    assert "nested tmux" in prompt
    assert "exactly one task-creation request" in prompt
    assert "exactly one non-replayed success" in prompt
    assert "Never repeat or replay any successful mutation" in prompt
    assert "transport-ambiguous" in prompt
    assert "If any mutation is repeated, do not emit the success sentinel" in prompt
    workload = (ROOT / "tests/fixtures/live_contract_workload.json").read_text(encoding="utf-8")
    assert "selected route is $cortex:orchestrator" in workload
    assert "Cortex MCP tools for ledger operations and orchestration evidence" in workload
    assert "Native collaboration/subagents are allowed and required" in workload
    assert "worker dispatch and report ownership" in workload
    assert "Dispatch one bounded native worker and use its owned report" in workload
    for prohibited in ("Do not run shell commands", "inspect source", "modify files", "start nested live-dev", "start nested tmux"):
        assert prohibited in workload
    for forbidden in ("prompt_en", "consumer_delegation_ref", "task_ref", "delegation_ref", "report_ref", "idempotency_key"):
        assert forbidden not in prompt
        assert forbidden not in (ROOT / "tests/fixtures/live_contract_workload.json").read_text(encoding="utf-8")
    assert "exactly one task-creation request" in workload
    assert "exactly one non-replayed success" in workload
    assert "Never repeat or replay any successful mutation" in workload
    assert "If any mutation is repeated, do not emit the success sentinel" in workload


def test_one_page_workload_covers_approved_semantic_lifecycle_without_parameter_hints() -> None:
    fixture = ROOT / "tests/fixtures/live_dev_one_page_workload.json"
    workload = json.loads(fixture.read_text(encoding="utf-8"))
    prompt = workload["prompt"]
    assert workload["project"] == "simple-one-page-html"
    assert workload["success_marker"] == "CORTEX_ONE_PAGE_OK"
    for required in (
        "selected route is $cortex:orchestrator",
        "Complete the task contract",
        "full or light governance",
        "planner worker",
        "immutable plan",
        "exactly one genuine product-clarification question",
        "driver's answer",
        "record that decision",
        "explicit approval",
        "implementation worker",
        "self-contained index.html",
        "independent verification worker",
        "worker-owned report evidence",
        "documentation-impact or no-impact stage",
        "advisory closure",
        "implementation worker authorized to use local project tooling",
        "create exactly one self-contained index.html",
        "verification worker authorized to inspect that test project read-only",
        "The coordinator must not run shell commands",
        "external actions remain forbidden",
        "zero tool errors and zero unauthorized replays",
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


def test_send_uses_literal_single_line_then_distinct_enter(monkeypatch, tmp_path: Path) -> None:
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
    assert calls == [
        ("send-keys", "-l", "-t", driver.TARGET, "--", "first line second line"),
        ("send-keys", "-t", driver.TARGET, "C-m"),
        ("send-keys", "-t", driver.TARGET, "C-m"),
    ]
    assert sleeps == [driver.SUBMIT_DRAIN_SECONDS]


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
    assert sum(call and call[0] == "send-keys" and call[-1] == "C-m" for call in calls) == 2
    assert not any(call and call[0] == "capture-pane" for call in calls)


def test_start_launches_cortex_dev_as_the_pane_process(monkeypatch, tmp_path: Path) -> None:
    driver = module()
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    state = {"exists": False}
    monkeypatch.setattr(driver, "exists", lambda: state["exists"])
    def fake_tmux(*args):
        calls.append(args)
        if args[:2] == ("new-session", "-d"):
            state["exists"] = True
        return Result()
    monkeypatch.setattr(driver, "tmux", fake_tmux)
    assert driver.start(tmp_path) == 0
    assert calls == [
        (
            "new-session", "-d", "-s", driver.SESSION, "-c", str(tmp_path),
            "bash", "-c",
            f"cd {str(tmp_path.resolve())} && {str((tmp_path.resolve() / 'scripts' / 'cortex-dev'))}"
            "; status=$?; printf 'Cortex live-dev exit=%s\\n' \"$status\"; exit \"$status\"",
        ),
    ]
    assert not any(call and call[0] == "send-keys" for call in calls)


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


def test_commands_are_exact_session_scoped(monkeypatch) -> None:
    driver = module()
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
