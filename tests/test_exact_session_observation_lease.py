"""Adversarial source tests for the exact-session observation lease boundary."""
from __future__ import annotations

import json
import importlib.util
import importlib.machinery
import multiprocessing
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

from cortex_runtime.observation_generation import (  # noqa: E402
    ObservationGenerationError,
    REQUEST_TTL_NS,
    claim_generation,
    consume_intent,
    request_generation,
    revoke_session,
    verify_lease_record,
)
from cortex_runtime.event_journal import EventJournal  # noqa: E402


def _fixture(tmp_path: Path) -> tuple[Path, str, str, str, str, str]:
    code_home = tmp_path / "codex"
    code_home.mkdir(mode=0o700)
    candidate = code_home / "plugins/cache/cortex/cortex/1.14.11"
    candidate.mkdir(parents=True, mode=0o700)
    build_id = "sha256:" + ("a" * 64)
    version = "1.14.11"
    digest = "b" * 64
    nonce = "c" * 64
    request_generation(code_home=code_home, build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    consume_intent(code_home=code_home, package_root=candidate, build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    return code_home, str(candidate), build_id, version, digest, nonce


def _claim_worker(candidate: str, build_id: str, version: str, digest: str, nonce: str, result_queue) -> None:
    try:
        generation, lease = claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
        result_queue.put({"ok": True, "generation": generation.name, "registration": lease["active_process_registration"]["registration"], "pid": os.getpid()})
    except Exception as exc:  # pragma: no cover - surfaced through the queue
        result_queue.put({"ok": False, "error": type(exc).__name__ + ":" + str(exc)})


def _claim_or_revoke_worker(candidate: str, code_home: str, build_id: str, version: str, digest: str, nonce: str, barrier, result_queue, revoke: bool) -> None:
    barrier.wait(timeout=10)
    try:
        if revoke:
            revoke_session(code_home=Path(code_home), session_nonce=nonce)
            result_queue.put({"action": "revoke", "ok": True})
        else:
            generation, lease = claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
            result_queue.put({"action": "claim", "ok": True, "generation": generation.name, "registration": lease["active_process_registration"]["registration"]})
    except ObservationGenerationError as exc:
        result_queue.put({"action": "claim", "ok": False, "error": str(exc)})


def test_two_processes_claim_one_lease_without_lost_registration(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=_claim_worker, args=(candidate, build_id, version, digest, nonce, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    results = [queue.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert all(item["ok"] for item in results), results
    assert {item["generation"] for item in results} == {results[0]["generation"]}
    lease = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    assert len({item["registration"] for item in results}) == 2
    assert len(lease["processes"]) == 2
    assert {item["pid"] for item in lease["processes"]} == {item["pid"] for item in results}


def test_worker_connection_event_is_attributed_to_assignment_scope(tmp_path: Path) -> None:
    code_home = tmp_path / "codex"
    code_home.mkdir(mode=0o700)
    path = code_home / ".cortex-mcp-events" / "worker-test" / "events.jsonl"
    journal = EventJournal(
        path,
        build_id="sha256:" + ("a" * 64),
        code_home=code_home,
    )
    worker_ref = "t_0123456789ab_" + ("b" * 32)
    journal.emit(
        operation="read_task",
        kind="query",
        success=True,
        fault=None,
        mutation=None,
        task_anchor=worker_ref,
        connection_role="worker",
    )
    event = json.loads(path.read_text(encoding="ascii"))
    assert event["role"] == "worker"
    assert event["scope"] == "assignment"
    assert "assignment" in event
    assert worker_ref not in path.read_text(encoding="ascii")


def test_same_process_restart_reuses_registration_and_generation(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    first_generation, first = claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    second_generation, second = claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    assert first_generation == second_generation
    assert first["active_process_registration"] == second["active_process_registration"]
    lease = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    assert len(lease["processes"]) == 1


def test_claimed_exact_session_lease_outlives_handshake_ttl_until_revoked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Long live orchestration keeps observations after the launch TTL."""
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    _generation, claimed = claim_generation(
        package_root=Path(candidate), build_id=build_id,
        candidate_version=version, catalogue_count=15,
        catalogue_digest=digest, session_nonce=nonce,
    )
    monkeypatch.setattr(
        "cortex_runtime.observation_generation.time.time_ns",
        lambda: int(claimed["created_ns"]) + REQUEST_TTL_NS + 1,
    )
    verified = verify_lease_record(
        claimed, session_nonce=nonce, candidate_path=candidate,
        build_id=build_id, candidate_version=version,
        catalogue_count=15, catalogue_digest=digest,
    )
    assert verified["state"] == "claimed"


def test_revoke_wins_against_all_post_revoke_claims(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    revoke_session(code_home=code_home, session_nonce=nonce)
    with pytest.raises(ObservationGenerationError):
        claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    lease = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    assert lease["state"] == "revoked"


def test_revoked_exact_session_lease_remains_readable_for_final_observation(tmp_path: Path) -> None:
    """Stopping a session revokes authority but does not erase its audit stream."""
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    _generation, claimed = claim_generation(
        package_root=Path(candidate), build_id=build_id,
        candidate_version=version, catalogue_count=15,
        catalogue_digest=digest, session_nonce=nonce,
    )
    revoke_session(code_home=code_home, session_nonce=nonce)
    revoked = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    with pytest.raises(ObservationGenerationError):
        verify_lease_record(revoked, session_nonce=nonce, candidate_path=candidate,
                            build_id=build_id, candidate_version=version,
                            catalogue_count=15, catalogue_digest=digest)
    observed = verify_lease_record(revoked, session_nonce=nonce, candidate_path=candidate,
                                   build_id=build_id, candidate_version=version,
                                   catalogue_count=15, catalogue_digest=digest,
                                   allow_revoked=True)
    assert observed["state"] == "revoked"


def test_concurrent_revoke_and_claim_leaves_revoked_lease_and_no_post_revoke_claim(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    claim = context.Process(target=_claim_or_revoke_worker, args=(candidate, str(code_home), build_id, version, digest, nonce, barrier, queue, False))
    revoke = context.Process(target=_claim_or_revoke_worker, args=(candidate, str(code_home), build_id, version, digest, nonce, barrier, queue, True))
    claim.start(); revoke.start()
    results = [queue.get(timeout=10) for _ in (claim, revoke)]
    claim.join(timeout=10); revoke.join(timeout=10)
    assert claim.exitcode == 0 and revoke.exitcode == 0
    assert any(item["action"] == "revoke" and item["ok"] for item in results)
    lease = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    assert lease["state"] == "revoked"
    with pytest.raises(ObservationGenerationError):
        claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)


def test_partial_intent_and_lease_writes_are_rejected_without_false_ready(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    root = code_home / ".cortex-mcp-observations"
    lease_path = root / "lease.json"
    original = lease_path.read_bytes()
    lease_path.write_bytes(original[: max(1, len(original) // 2)])
    with pytest.raises(ObservationGenerationError):
        claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    lease_path.write_bytes(original)
    intent = root / "intent.json"
    intent.write_text("{\"state\":\"pending\"}\n", encoding="ascii")
    with pytest.raises(ObservationGenerationError):
        consume_intent(code_home=code_home, package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    assert not (root / "generations" / "ready.json").exists()


def test_observation_root_generation_and_files_reject_symlink_or_unsafe_mode(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    root = code_home / ".cortex-mcp-observations"
    lease = json.loads((root / "lease.json").read_text(encoding="ascii"))
    generation = root / "generations" / lease["generation_id"]
    generation.mkdir(parents=True, mode=0o700, exist_ok=True)
    request = generation / "request.json"
    request.write_text("{}\n", encoding="ascii")
    os.chmod(request, 0o644)
    with pytest.raises((ObservationGenerationError, OSError)):
        claim_generation(package_root=Path(candidate), build_id=build_id, candidate_version=version, catalogue_count=15, catalogue_digest=digest, session_nonce=nonce)
    os.chmod(request, 0o600)
    outside = tmp_path / "outside-events"
    outside.write_text("secret\n", encoding="ascii")
    event = generation / "events.jsonl"
    event.symlink_to(outside)
    assert event.is_symlink()
    assert outside.read_text(encoding="ascii") == "secret\n"


def test_stop_revokes_exact_lease_when_tmux_session_is_already_absent(monkeypatch, tmp_path: Path) -> None:
    loader = importlib.machinery.SourceFileLoader("cortex_live_smoke_stop", str(ROOT / "scripts/cortex-live-smoke"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    driver_module = importlib.util.module_from_spec(spec)
    loader.exec_module(driver_module)
    code_home, _candidate, _build_id, _version, _digest, nonce = _fixture(tmp_path)
    state = {"session_nonce": nonce}
    calls = []
    monkeypatch.setattr(driver_module, "_state", lambda: state)
    monkeypatch.setattr(driver_module, "exists", lambda: False)
    monkeypatch.setattr(driver_module, "_discard_state", lambda: None)
    def revoke_command(command, **kwargs):
        calls.append(command)
        revoke_session(code_home=code_home, session_nonce=nonce)
        return type("Result", (), {"returncode": 0})()
    monkeypatch.setattr(driver_module.subprocess, "run", revoke_command)
    assert driver_module.stop(False) == 0
    lease = json.loads((code_home / ".cortex-mcp-observations/lease.json").read_text(encoding="ascii"))
    assert lease["state"] == "revoked"
    assert calls and "--revoke" in calls[0]


@pytest.mark.parametrize("record", ["lease", "intent", "nonce"])
def test_malformed_observation_record_is_nonblocking_at_packaged_mcp_initialize(tmp_path: Path, record: str) -> None:
    staged = tmp_path / "staged"
    from cortex_release_candidate import build_source_candidate
    build_source_candidate(ROOT, staged)
    version = json.loads((staged / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    owner = tmp_path / "owner"
    home = owner / ".cortex-dev"
    codex_home = home / ".codex"
    candidate = codex_home / "plugins/cache/cortex/cortex" / version
    candidate.parent.mkdir(parents=True)
    shutil.copytree(staged / "plugins/cortex", candidate)
    observation = codex_home / ".cortex-mcp-observations"
    observation.mkdir(mode=0o700)
    if record == "nonce":
        lease = {"schema_version": 2, "session": "cortex-v12-smoke", "nonce": "not-a-valid-nonce", "generation_id": "a" * 48, "build_id": "sha256:" + "a" * 64, "candidate_version": version, "candidate_path": str(candidate), "catalogue_count": 15, "catalogue_digest": "b" * 64, "created_ns": time.time_ns(), "state": "pending", "processes": [], "signature": "0" * 64}
        (observation / "lease.json").write_text(json.dumps(lease) + "\n", encoding="ascii")
    else:
        (observation / f"{record}.json").write_text("not-json\n", encoding="ascii")
    os.chmod(observation / ("lease.json" if record == "nonce" else f"{record}.json"), 0o600)
    env = dict(os.environ, HOME=str(home), CODEX_HOME=str(codex_home), PWD=str(tmp_path))
    env.pop("PYTHONPATH", None)
    process = subprocess.Popen([sys.executable, "-B", str(candidate / "scripts/cortex.py")], cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "lease-test", "version": "1"}}}
    stdout, stderr = process.communicate(json.dumps(payload) + "\n", timeout=15)
    assert process.returncode == 0, stderr
    response = json.loads(stdout.splitlines()[0])
    assert response.get("result", {}).get("serverInfo", {}).get("parityVerified") is True
    assert "Traceback" not in stderr


def _observer_module_and_fixture(tmp_path: Path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader("cortex_live_smoke_observer_matrix", str(ROOT / "scripts/cortex-live-smoke"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    driver = importlib.util.module_from_spec(spec)
    loader.exec_module(driver)
    source_loader = importlib.machinery.SourceFileLoader("live_docs_fixture", str(ROOT / "tests/test_live_dev_smoke_docs.py"))
    source_spec = importlib.util.spec_from_loader(source_loader.name, source_loader)
    assert source_spec is not None
    live_docs = importlib.util.module_from_spec(source_spec)
    source_loader.exec_module(live_docs)
    state, lease_path, event_path = live_docs._production_event_fixture(driver, tmp_path, monkeypatch)
    return driver, state, lease_path, event_path


@pytest.mark.parametrize("field,value", [("candidate_version", "9.9.9"), ("catalogue_count", 999), ("catalogue_digest", "d" * 64)])
def test_events_rejects_signed_lease_with_wrong_authoritative_identity(tmp_path: Path, monkeypatch, field: str, value: object, capsys) -> None:
    driver, state, lease_path, _event_path = _observer_module_and_fixture(tmp_path, monkeypatch)
    lease = json.loads(lease_path.read_text(encoding="ascii"))
    lease[field] = value
    unsigned = json.dumps({key: item for key, item in lease.items() if key != "signature"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    lease["signature"] = __import__("hmac").new(bytes.fromhex(str(state["session_nonce"])), unsigned, __import__("hashlib").sha256).hexdigest()
    lease_path.write_text(json.dumps(lease, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
    os.chmod(lease_path, 0o600)
    monkeypatch.setattr(driver, "tmux", lambda *args: (_ for _ in ()).throw(AssertionError(args)))
    assert driver.events(20) == 2
    assert "failure_stage=lease" in capsys.readouterr().err


def test_events_no_follow_race_rejects_replaced_event_file(tmp_path: Path, monkeypatch, capsys) -> None:
    driver, _state, _lease_path, event_path = _observer_module_and_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "redirected"
    outside.write_text("SECRET-REDIRECT\n", encoding="ascii")
    original_open = driver.os.open
    replaced = {"done": False}
    def replace_before_open(path, flags, *args, **kwargs):
        if str(path) == str(event_path) and not replaced["done"]:
            event_path.unlink()
            event_path.symlink_to(outside)
            replaced["done"] = True
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(driver.os, "open", replace_before_open)
    assert driver.events(20) == 2
    assert "SECRET-REDIRECT" not in capsys.readouterr().out


@pytest.mark.parametrize("control_name", ["request.json", "ready.json"])
def test_events_no_follow_race_rejects_replaced_control_file(tmp_path: Path, monkeypatch, control_name: str, capsys) -> None:
    driver, _state, _lease_path, event_path = _observer_module_and_fixture(tmp_path, monkeypatch)
    generation = event_path.parent
    control_path = generation / control_name
    if control_name == "ready.json":
        control_path.write_text('{"schema_version":2}\n', encoding="ascii")
        os.chmod(control_path, 0o600)
    outside = tmp_path / ("redirected-" + control_name)
    outside.write_text("SECRET-REDIRECT-CONTROL\n", encoding="ascii")
    original_open = driver.os.open
    replaced = {"done": False}
    def replace_before_open(path, flags, *args, **kwargs):
        if str(path) == str(control_path) and not replaced["done"]:
            control_path.unlink()
            control_path.symlink_to(outside)
            replaced["done"] = True
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(driver.os, "open", replace_before_open)
    assert driver.events(20) == 2
    assert "SECRET-REDIRECT-CONTROL" not in capsys.readouterr().out


def test_partial_ready_and_event_records_are_unavailable_not_false_ready(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    root = code_home / ".cortex-mcp-observations"
    lease = json.loads((root / "lease.json").read_text(encoding="ascii"))
    generation = root / "generations" / lease["generation_id"]
    generation.mkdir(parents=True, mode=0o700, exist_ok=True)
    (generation / "request.json").write_text("{}\n", encoding="ascii")
    os.chmod(generation / "request.json", 0o600)
    (generation / "ready.json").write_text('{"schema_version":2', encoding="ascii")
    os.chmod(generation / "ready.json", 0o600)
    (generation / "events.jsonl").write_text('{"operation":"publish_result"', encoding="ascii")
    os.chmod(generation / "events.jsonl", 0o600)
    from cortex_runtime.observation_generation import write_ready_receipt
    with pytest.raises((ObservationGenerationError, json.JSONDecodeError)):
        write_ready_receipt(generation, build_id=build_id, catalogue_count=15, catalogue_digest=digest)
    # A truncated JSON object is a malformed journal record, not merely a
    # content decoding problem.  The observer must classify the envelope
    # failure precisely so the coordinator can repair the writer/reader path.
    assert (generation / "events.jsonl").read_text(encoding="ascii") == '{"operation":"publish_result"'


@pytest.mark.parametrize("mutation", ["nonce", "session", "build_id", "version", "catalogue", "generation"])
def test_lease_identity_and_lexical_generation_drift_is_rejected(tmp_path: Path, mutation: str) -> None:
    code_home, candidate, build_id, version, digest, nonce = _fixture(tmp_path)
    lease_path = code_home / ".cortex-mcp-observations/lease.json"
    lease = json.loads(lease_path.read_text(encoding="ascii"))
    expected = {"candidate_path": candidate, "build_id": build_id, "candidate_version": version, "catalogue_count": 15, "catalogue_digest": digest}
    if mutation == "nonce": lease["nonce"] = "d" * 64
    elif mutation == "session": lease["session"] = "other-session"
    elif mutation == "build_id": lease["build_id"] = "sha256:" + ("e" * 64)
    elif mutation == "version": lease["candidate_version"] = "9.9.9"
    elif mutation == "catalogue": lease["catalogue_digest"] = "f" * 64
    else: lease["generation_id"] = "../outside"
    with pytest.raises(ObservationGenerationError):
        verify_lease_record(lease, session_nonce=nonce, **expected)


def test_nonce_less_claim_is_rejected_for_an_existing_live_lease(tmp_path: Path) -> None:
    code_home, candidate, build_id, version, digest, _nonce = _fixture(tmp_path)
    with pytest.raises(ObservationGenerationError, match="requires session nonce"):
        claim_generation(
            package_root=Path(candidate), build_id=build_id,
            candidate_version=version, catalogue_count=15,
            catalogue_digest=digest,
        )
