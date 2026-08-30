"""Source/stdio coverage for the owner-only sanitized MCP observation stream."""
from __future__ import annotations

import io
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from unittest import mock
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_INSTRUCTIONS, SERVER_VERSION  # noqa: E402
from cortex_runtime.event_journal import EventJournal, MAX_BYTES  # noqa: E402
from cortex_runtime.raw_diagnostic import append as raw_diagnostic  # noqa: E402
from cortex_runtime.mcp_api import serve_stdio  # noqa: E402


@contextmanager
def _claimed_generation(home: Path):
    """Source harness for the same runtime-owned path a candidate claims."""
    generation = home / ".cortex-mcp-observations" / "generations" / ("a" * 48)
    generation.mkdir(parents=True, mode=0o700)
    os.chmod(generation.parent.parent, 0o700)
    os.chmod(generation.parent, 0o700)
    os.chmod(generation, 0o700)
    with mock.patch("cortex_runtime.mcp_api.claim_generation", return_value=(generation, {})), mock.patch("cortex_runtime.mcp_api.candidate_codex_home", return_value=home):
        yield generation / "events.jsonl"


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in _event_path(path).read_text(encoding="ascii").splitlines()]


def _event_path(path: Path) -> Path:
    if path.exists():
        return path
    # Retired tests pass their former environment-selected destination only as
    # a convenient anchor for the isolated runtime. Resolve the actual claimed
    # generation beneath that same owner-only root; no MCP code reads this path.
    home = path.parents[2]
    matches = list((home / ".cortex-mcp-observations" / "generations").glob("*/events.jsonl"))
    assert len(matches) == 1
    return matches[0]


@pytest.fixture(autouse=True)
def _source_runtime_claim(monkeypatch: pytest.MonkeyPatch):
    """Give direct stdio tests a runtime-owned claimed generation, never env routing."""
    def claim(**_kwargs):
        home = Path(os.environ["CODEX_HOME"])
        generation = home / ".cortex-mcp-observations" / "generations" / ("a" * 48)
        generation.mkdir(parents=True, mode=0o700, exist_ok=True)
        for path in (generation.parent.parent, generation.parent, generation):
            os.chmod(path, 0o700)
        request = generation / "request.json"
        if not request.exists():
            request.write_text("{}\n", encoding="ascii")
            os.chmod(request, 0o600)
        return generation, {}
    monkeypatch.setattr("cortex_runtime.mcp_api.claim_generation", claim)
    monkeypatch.setattr("cortex_runtime.mcp_api.candidate_codex_home", lambda _root: Path(os.environ["CODEX_HOME"]))


def _isolated_code_home(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _generation_journal(home: Path) -> tuple[Path, EventJournal]:
    generation = home / ".cortex-mcp-observations" / "generations" / ("b" * 48)
    generation.mkdir(parents=True, mode=0o700)
    for directory in (generation.parent.parent, generation.parent, generation):
        os.chmod(directory, 0o700)
    return generation / "events.jsonl", EventJournal.from_generation(generation=generation, build_id="sha256:build", code_home=home)


def test_raw_diagnostic_accepts_only_launcher_isolated_home(monkeypatch, tmp_path: Path) -> None:
    isolated = tmp_path / ".cortex-dev"
    code_home = isolated / ".codex"
    code_home.mkdir(parents=True, mode=0o700)
    os.chmod(isolated, 0o700); os.chmod(code_home, 0o700)
    monkeypatch.setenv("CORTEX_RAW_DIAGNOSTIC", "1")
    monkeypatch.setenv("HOME", str(isolated)); monkeypatch.setenv("CODEX_HOME", str(code_home))
    raw_diagnostic(kind="test", payload={"marker": "private"})
    path = code_home / ".cortex-raw-diagnostic" / "events.jsonl"
    assert path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    path.unlink()
    raw_diagnostic(kind="test", payload={"marker": "rejected"})
    assert not path.exists()


def test_raw_diagnostic_reports_closed_disabled_and_serialization_categories(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_RAW_DIAGNOSTIC", raising=False)
    assert raw_diagnostic(kind="test", payload={}) == "disabled"
    monkeypatch.setenv("CORTEX_RAW_DIAGNOSTIC", "1")
    assert raw_diagnostic(kind="", payload={}) == "serialization_failed"


def test_stdio_claim_binds_live_session_nonce(monkeypatch, tmp_path: Path) -> None:
    """A live MCP process must not take over a different run's lease."""
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    generation = home / ".cortex-mcp-observations" / "generations" / ("a" * 48)
    generation.mkdir(parents=True, mode=0o700)
    for directory in (generation.parent.parent, generation.parent, generation):
        os.chmod(directory, 0o700)
    (generation / "request.json").write_text("{}\n", encoding="ascii")
    os.chmod(generation / "request.json", 0o600)
    observed: dict[str, object] = {}
    def claim(**kwargs):
        observed.update(kwargs)
        return generation, {}
    monkeypatch.setattr("cortex_runtime.mcp_api.claim_generation", claim)
    nonce = "a" * 64
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1", "CORTEX_SESSION_NONCE": nonce}
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "nonce", "version": "1"}}}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", io.StringIO()):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    assert observed["session_nonce"] == nonce


def test_actual_stdio_initialize_uses_raw_publication_boundary(monkeypatch, tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    generation = home / ".cortex-mcp-observations" / "generations" / ("c" * 48)
    generation.mkdir(parents=True, mode=0o700)
    for directory in (generation.parent.parent, generation.parent, generation): os.chmod(directory, 0o700)
    request_path = generation / "request.json"; request_path.write_text("{}\n", encoding="ascii"); os.chmod(request_path, 0o600)
    calls = []
    monkeypatch.setattr("cortex_runtime.event_journal._raw_diagnostic", lambda **kwargs: calls.append(kwargs) or "written")
    monkeypatch.setattr("cortex_runtime.mcp_api.claim_generation", lambda **kwargs: (generation, {}))
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "boundary", "version": "1"}}}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", io.StringIO()):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    assert any(item["kind"] == "journal_writer_attempt" for item in calls)


def test_journal_never_retains_raw_arguments_and_classifies_new_replay_conflict(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path, journal = _generation_journal(home)
    journal.emit(operation="open_task", kind="command", success=True, fault=None, mutation="new", task_anchor="t_secret", assignment_anchor=None)
    journal.emit(operation="open_task", kind="command", success=True, fault=None, mutation="replay", task_anchor="t_secret", assignment_anchor=None)
    journal.emit(operation="record_clarification", kind="command", success=False, fault="command_conflict", mutation="conflict", task_anchor="t_secret", assignment_anchor="d_secret")
    rendered = path.read_text(encoding="ascii")
    assert "secret" not in rendered
    assert "prompt" not in rendered
    values = _events(path)
    assert [value["sequence"] for value in values] == [1, 2, 3]
    assert [value["mutation"] for value in values] == ["new", "replay", "conflict"]
    assert [value["outcome"] for value in values] == ["success", "success", "failure"]
    assert values[-1]["fault"] == "command_conflict"
    assert values[-1]["scope"] == "assignment"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_dispatch_correlation_is_fingerprinted_observation_only(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path, journal = _generation_journal(home)
    marker = "dc_" + "a" * 32
    journal.emit(
        operation="open_assignment", kind="command", success=True, fault=None, mutation="new",
        task_anchor="t_anchor", assignment_anchor="d_anchor", dispatch_correlation_marker=marker,
    )
    event = _events(path)[0]
    assert marker not in path.read_text(encoding="ascii")
    assert isinstance(event["dispatch_correlation"], str)
    assert len(event["dispatch_correlation"]) == 20
    assert event["dispatch_correlation"] != marker


def test_publication_admission_failure_has_only_safe_semantic_recovery_metadata(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path, journal = _generation_journal(home)
    journal.emit(
        operation="publish_plan", kind="command", success=False,
        fault="report_incomplete", mutation="error", task_anchor="t_private",
        assignment_anchor="d_private", validation_expected="complete_evidence_envelope",
        corrective_action="correct_publication_evidence",
    )
    event = _events(path)[0]
    assert event["validation_expected"] == "complete_evidence_envelope"
    assert event["corrective_action"] == "correct_publication_evidence"
    rendered = path.read_text(encoding="ascii")
    assert "private" not in rendered


def test_journal_bounds_and_symlink_fault_is_observation_limited(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path, journal = _generation_journal(home)
    for _ in range(900):
        journal.emit(operation="read_task", kind="query", success=True, fault=None, mutation=None, task_anchor="t_anchor")
    assert path.stat().st_size <= MAX_BYTES
    assert len(_events(path)) <= 512
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    journal.emit(operation="read_task", kind="query", success=True, fault=None, mutation=None)
    assert journal.limited


def test_journal_rejects_every_isolated_runtime_ancestor_fault(tmp_path: Path) -> None:
    """The descriptor chain must not traverse a substituted isolated ancestor."""
    for fault in ("code_home_mode", "code_home_symlink", "event_root_symlink", "session_symlink"):
        root = tmp_path / fault
        home = _isolated_code_home(root / "isolated" / ".codex")
        path = home / ".cortex-mcp-observations" / "generations" / ("b" * 48) / "events.jsonl"
        if fault == "code_home_mode":
            os.chmod(home, 0o755)
        elif fault == "code_home_symlink":
            home.rmdir()
            home.symlink_to(root / "outside-code-home")
        elif fault == "event_root_symlink":
            (home / ".cortex-mcp-events").symlink_to(root / "outside-events")
        else:
            event_root = home / ".cortex-mcp-events"
            event_root.mkdir(mode=0o700)
            (event_root / "session").symlink_to(root / "outside-session")
        journal = EventJournal.from_generation(generation=path.parent, build_id="sha256:build", code_home=home)
        journal.emit(operation="read_task", kind="query", success=True, fault=None, mutation=None)
        assert journal.limited, fault
        assert not (root / "outside-code-home" / ".cortex-mcp-events" / "session" / "events.jsonl").exists()
        assert not (root / "outside-events" / "session" / "events.jsonl").exists()
        assert not (root / "outside-session" / "events.jsonl").exists()


def test_missing_code_home_is_a_non_mutating_observation_limitation(tmp_path: Path) -> None:
    home = tmp_path / "missing" / ".codex"
    generation = home / ".cortex-mcp-observations" / "generations" / ("b" * 48)
    journal = EventJournal.from_generation(generation=generation, build_id="sha256:build", code_home=home)
    journal.emit(operation="read_task", kind="query", success=True, fault=None, mutation=None)
    assert journal.limited
    assert not home.exists()


def test_stdio_records_hidden_validation_error_without_changing_tool_result(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": "not-a-ref"}}},
    ]
    output = io.StringIO()
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
    assert response["result"]["isError"] is True
    values = _events(path)
    assert values[-1]["operation"] == "read_task"
    assert values[-1]["kind"] == "query"
    assert values[-1]["outcome"] == "failure"
    assert values[-1]["fault"] == "validation_error"
    assert "not-a-ref" not in _event_path(path).read_text(encoding="ascii")


def test_stdio_records_safe_publication_validation_metadata_without_payload(tmp_path: Path) -> None:
    """A hidden worker failure remains distinguishable without retaining its report."""
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "publish_plan", "arguments": {"continuation_ref": "wc_0123456789abcdef0123456789abcdef", "assignment_ref": "d_0123456789ab", "evidence": {"schema": "cortex/report/plan/v3"}}}},
    ]
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    output = io.StringIO()
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
    assert response["result"]["isError"] is True
    event = _events(path)[-1]
    assert event["operation"] == "publish_plan"
    assert event["fault"] == "validation_error"
    assert event["validation_field"] == "summary"
    assert event["validation_expected"] == "required_field"
    assert event["corrective_action"] == "review_advertised_schema"
    rendered = _event_path(path).read_text(encoding="ascii")
    assert "cortex/report/plan/v3" not in rendered
    assert "0123456789ab" not in rendered


def test_stdio_emits_one_safe_server_ready_after_physical_initialize_reply(tmp_path: Path) -> None:
    """Registration is distinct from workload calls and contains no catalogue text."""
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "private-client-name", "version": "1"},
        },
    }
    output = io.StringIO()
    environment = {
        "HOME": str(tmp_path / "home"), "CODEX_HOME": str(home),
        "CORTEX_SOURCE_MODE": "1",
    }
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["id"] == 1
    observed = _events(path)
    assert len(observed) == 1
    ready = observed[0]
    assert ready["operation"] == "server_ready"
    assert ready["kind"] == "registration"
    assert ready["outcome"] == "success"
    assert ready["catalogue_count"] == len(PUBLIC_TOOLS)
    assert ready["catalogue_digest"] == hashlib.sha256(json.dumps(
        tuple({
            "name": name, "description": str(contract["description"]),
            "inputSchema": dict(contract["inputSchema"]),
            "outputSchema": dict(contract["outputSchema"]),
        } for name, contract in PUBLIC_TOOLS.items()),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).hexdigest()
    rendered = _event_path(path).read_text(encoding="ascii")
    assert "private-client-name" not in rendered
    assert "read_task" not in rendered
    assert "inputSchema" not in rendered


def test_stdio_does_not_emit_ready_when_initialize_wire_falls_back(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "journal", "version": "1"},
        },
    }
    output = io.StringIO()
    environment = {
        "HOME": str(tmp_path / "home"), "CODEX_HOME": str(home),
        "CORTEX_SOURCE_MODE": "1",
    }
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions="x" * (300 * 1024))
    response = json.loads(output.getvalue())
    assert response["error"]["code"] == -32603
    assert not path.exists()


def test_stdio_journals_every_malformed_tools_call_envelope_once(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": []},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_task", "arguments": {}, "extra": True}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "read_task", "arguments": []}},
    ]
    output = io.StringIO()
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    malformed = [value for value in responses if value.get("id") in {2, 3, 4, 5}]
    assert len(malformed) == 4
    assert all(value["error"]["code"] == -32602 for value in malformed)
    values = _events(path)
    observed = [value for value in values if value["operation"] == "unknown"]
    assert len(observed) == 4
    assert all(value["outcome"] == "failure" and value["fault"] == "validation_error" for value in observed)


def test_stdio_journals_malformed_tools_call_notifications_without_replies(tmp_path: Path) -> None:
    """Notification form has the same terminal observation, but no wire reply."""
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "untrusted_unknown_operation", "arguments": {}}},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "read_task", "arguments": []}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
    ]
    output = io.StringIO()
    environment = {"HOME": str(tmp_path / "home"), "CORTEX_SOURCE_MODE": "1", "CODEX_HOME": str(home)}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [value.get("id") for value in replies] == [1, 2]
    observed = [value for value in _events(path) if value["operation"] == "unknown"]
    assert len(observed) == 2
    assert all(value["outcome"] == "failure" and value["fault"] == "validation_error" for value in observed)
    rendered = _event_path(path).read_text(encoding="ascii")
    assert "untrusted_unknown_operation" not in rendered


def test_stdio_malformed_notification_observation_matches_request_form(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_task", "arguments": []}},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "read_task", "arguments": []}},
    ]
    output = io.StringIO()
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    reply = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
    assert reply["error"]["code"] == -32602
    observed = [value for value in _events(path) if value["operation"] == "unknown"]
    assert len(observed) == 2
    assert [{key: value.get(key) for key in ("operation", "kind", "outcome", "fault", "scope")} for value in observed] == [
        {"operation": "unknown", "kind": "unknown", "outcome": "failure", "fault": "validation_error", "scope": "coordinator"},
        {"operation": "unknown", "kind": "unknown", "outcome": "failure", "fault": "validation_error", "scope": "coordinator"},
    ]


def test_stdio_observes_wire_size_failure_once_not_handler_success(tmp_path: Path) -> None:
    home = _isolated_code_home(tmp_path / "isolated" / ".codex")
    path = home / ".cortex-mcp-events" / "session" / "events.jsonl"
    tools = {name: dict(contract) for name, contract in PUBLIC_TOOLS.items()}
    tools["read_task"] = {
        **tools["read_task"],
        "outputSchema": {"type": "object"},
        "handler": lambda *, task_ref, after_sequence=0: {"payload": "x" * (300 * 1024)},
    }
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_task", "arguments": {"task_ref": "t_000000000000"}}},
    ]
    output = io.StringIO()
    environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "CORTEX_SOURCE_MODE": "1"}
    with mock.patch.dict(os.environ, environment, clear=False), mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(value) for value in requests) + "\n")), mock.patch("sys.stdout", output):
        serve_stdio(public_tools=tools, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
    response = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == 2)
    assert response["error"]["code"] == -32603
    values = [value for value in _events(path) if value["operation"] == "read_task"]
    assert len(values) == 1
    assert values[0]["outcome"] == "failure"
    assert values[0]["fault"] == "ledger_error"
