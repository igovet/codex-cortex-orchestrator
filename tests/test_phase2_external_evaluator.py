import ast
import json
import hashlib
import os
import runpy
import shutil
import subprocess
import sys
import copy
from pathlib import Path

import pytest
import phase2_cli_auditor
import phase2_cli_adapter
import phase2_cli_runner


ROOT = Path(__file__).resolve().parents[1]


def _native_turn(thread_id: str = "thread-current", control: str = "1" * 64,
                 receipt: str = "2" * 64, prompt_sha256: str = "a" * 64) -> dict:
    return {
        "thread_id": thread_id, "thread_created_at": 1000,
        "user_turn_id": "msg-current", "user_turn_source_format": "response-item-message-v1",
        "user_turn_line": 6, "user_turn_sha256": "b" * 64,
        "source_path_sha256": "c" * 64,
        "prompt_sha256": prompt_sha256, "control_record_sha256": control,
        "session_receipt": receipt, "owned_codex_pid": 4321,
        "owned_codex_start_ticks": 987,
        "owned_rollout_descriptors": [{"fd": 9, "device": 10, "inode": 11}],
    }


def test_phase2_identity_lock_matches_current_plugin_manifest():
    manifest = json.loads((ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text())
    expected_version = manifest["version"]
    expected_payload_sha256 = "e4f332d43bf380248c5de142b835a62a888f8885ad6577677aa4f394804733d7"
    assert expected_version == "1.15.9+codex.sha256.e4f332d43bf38024"
    assert expected_version == phase2_cli_runner.CANDIDATE_VERSION
    assert expected_version == phase2_cli_auditor.CANDIDATE_VERSION
    assert expected_payload_sha256 == phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256
    assert phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256 == phase2_cli_auditor.CANDIDATE_PAYLOAD_SHA256
    assert phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256.startswith(expected_version.rsplit(".", 1)[-1])


def test_phase2_live_harness_trusted_anchors_match_current_source():
    harness_sha256 = hashlib.sha256((ROOT / "scripts/cortex-live-smoke").read_bytes()).hexdigest()
    adapter_sha256 = hashlib.sha256((ROOT / "scripts/phase2_cli_adapter.py").read_bytes()).hexdigest()
    assert {phase2_cli_runner.TRUSTED_HARNESS_SHA256,
            phase2_cli_auditor.TRUSTED_HARNESS_SHA256,
            phase2_cli_adapter.TRUSTED_HARNESS_SHA256} == {harness_sha256}
    assert {phase2_cli_runner.TRUSTED_ADAPTER_SHA256,
            phase2_cli_auditor.TRUSTED_ADAPTER_SHA256} == {adapter_sha256}
    assert {phase2_cli_runner.TRUSTED_OBSERVER_DEPENDENCIES_SHA256,
            phase2_cli_auditor.TRUSTED_OBSERVER_DEPENDENCIES_SHA256,
            phase2_cli_adapter.TRUSTED_OBSERVER_DEPENDENCIES_SHA256} == {
                "2cd411e289b6e9e8850136d733ae2f873e1702838ad6aef30b7fe68b4b8dbe63"
            }
    assert phase2_cli_runner.EVIDENCE_COLLECTION == phase2_cli_auditor.EVIDENCE_COLLECTION
    assert phase2_cli_runner.EVIDENCE_COLLECTION == phase2_cli_adapter.EVIDENCE_CONTRACT
    assert phase2_cli_adapter.EVIDENCE_CONTRACT["submission_transition"] == {
        "before_transport": "submitting",
        "successful_atomic_fields": [
            "status", "submission_request_sha256", "submitted_at",
            "submission_native_user_turn", "submission_receipt",
        ],
        "uncertain_transport": "submitting",
        "reconciliation": "resolve-send-never-resends",
    }
    assert phase2_cli_adapter.EVIDENCE_CONTRACT["session_lifecycle"] == {
        "order": ["start", "begin-cell", "send"],
        "start_completion": "ready-for-begin-after-trust-or-composer",
        "accept_trust_command": "idempotent-receipt-verification-only",
        "binding_schema": "phase2-cli-session-binding-v1",
        "receipt": "captured-once-after-owned-pane",
        "authorization_scope": ["control_record_sha256", "session_receipt"],
    }


def _manifest(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(1, 7):
        task = f"F-0{index}"
        rows.append({
            "task_id": task,
            "family": f"family-{index}",
            "prompt_sha256": f"{index:064x}",
            "fixture_tree_sha256": f"{index + 10:064x}",
            "oracle_sha256": f"{index + 20:064x}",
            "reset_sha256": f"{index + 30:064x}",
            "oracle_path": "oracle.py",
            "reset_path": "reset.py",
            "oracle_command": "python3 oracle.py TASK_ID WORKTREE",
            "reset_command": "python3 reset.py --task TASK_ID --destination WORKTREE",
            "reset_policy": "Destination must be absent and reset never overwrites.",
            "protected_paths": ["USER-NOTE.txt"],
        })
    path.write_text(json.dumps({"suite_version": "phase2-cli-v1", "families": rows}) + "\n")
    return path


def _args(tmp_path: Path, manifest: Path, output: Path) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock = tmp_path / "requirements.lock"
    lock.write_text("example==1.0 --hash=sha256:" + "a" * 64 + "\n")
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("PATH\n")
    return [
        "--source-repo", str(ROOT), "--candidate-source", str(ROOT),
        "--manifest", str(manifest), "--dependency-lock", str(lock),
        "--environment-allowlist", str(allowlist), "--provider-route", "offline://phase2-provider-v1",
        "--proxy-policy", "blocked", "--output", str(output),
        "--baseline-commit", phase2_cli_runner.BASELINE_COMMIT,
        "--baseline-version", phase2_cli_runner.BASELINE_VERSION,
        "--baseline-payload-sha256", phase2_cli_runner.BASELINE_PAYLOAD_SHA256,
        "--candidate-version", phase2_cli_runner.CANDIDATE_VERSION,
        "--candidate-payload-sha256", phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256,
        "--coordinator-model", "gpt-5.6-luna", "--coordinator-effort", "high",
        "--worker-model", "gpt-5.6-luna", "--worker-effort", "medium",
        "--cell-timeout-seconds", "1800", "--wait-timeout-ms", "600000",
    ]


def _run_prepare(tmp_path: Path) -> Path:
    manifest = _manifest(tmp_path / "manifest.json")
    output = tmp_path / "prepared"
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    command = ["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *_args(tmp_path, manifest, output)]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    record = output / "control-record.json"
    assert record.is_file()
    assert record.stat().st_mode & 0o777 == 0o400
    return record


def _rewrite_control(record: Path, data: dict) -> None:
    record.chmod(0o600)
    record.write_text(json.dumps(data) + "\n")
    record.chmod(0o400)


def _run_adapter(record: Path, arm: str, command: str, *args: str) -> subprocess.CompletedProcess[str]:
    adapter = Path(json.loads(record.read_text())["neutral"]["live_launcher"]["adapter_path"])
    return subprocess.run(
        ["python3", str(adapter), "--control-record", str(record), "--arm", arm, command, *args],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def _fake_evidence_harness(actions: list[tuple[str, tuple[str, ...]]], *, fail_action: str | None = None,
                           composer_workdir: Path | None = None):
    composer = composer_workdir is not None
    terminal = {
        "schema_version": "cortex-live-terminal-snapshot-v1", "session_id": "$1",
        "pane_id": "%1", "pane_pid": 1234, "current_command": "codex" if composer else "bash",
        "dead": False if composer else True, "dead_status": None if composer else 0,
        "descendants": ([{"pid": 1235, "ppid": 1234, "command": "codex"},
                         {"pid": 1236, "ppid": 1235, "command": "python3"}] if composer else []),
    }
    report_id = "r_0123456789ab"
    outputs = {
        "status": "codex dead=0 status=\n" if composer else "bash dead=1 status=0\n",
        "terminal": json.dumps(terminal, sort_keys=True, separators=(",", ":")) + "\n",
        "capture": (f"Created FACTS.md.\n─ Worked for 6m 31s ─────────\n"
                    f"› Ask Codex to do anything\n\n"
                    f"  gpt-5.6-luna high · {composer_workdir} · Investig…\n" if composer
                    else "terminal completion marker\nCortex live-dev exit=0\n"),
        "events": json.dumps({
            "event_kind": "mcp", "operation": "write_report", "outcome": "success",
            "thread_id": "thread-current", "report_id": report_id, "draft_id": "d_0123456789ab",
        }) + "\n",
        "calls": json.dumps({
            "thread_id": "thread-current", "parent_thread_id": None,
            "role": "coordinator", "tool": "mcp__cortex__write_report", "outcome": "success",
            "host_status": "completed", "report_id": report_id, "draft_id": "d_0123456789ab",
        }) + "\n",
        "usage": json.dumps({"status": "observed", "wall_seconds": 2.0, "totals": {"total_tokens": 10}, "participants": [{}]}) + "\n",
        "audit": json.dumps({
            "mcp_events": 1, "hook_actions": 1, "host_calls": 1,
            "failures": [], "hook_failures": [], "host_failures": [],
            "tool_error_history": [], "orchestration_error_history": [],
            "orchestration_host_failures": [], "resolved_host_failures": [],
            "policy_violations": [], "worker_policy_violations": [],
            "orchestration_policy_violations": [], "open_sessions": [], "open_cells": [],
            "schema_version": "cortex-evidence-quality-classification-v1",
            "evidence_valid": True, "score_eligible": True,
            "evidence_integrity_invalidators": [], "quality_findings": [],
        }) + "\n",
    }

    def main():
        action = __import__("sys").argv[1]
        arguments = tuple(__import__("sys").argv[2:])
        actions.append((action, arguments))
        print(outputs[action], end="")
        return 7 if action == fail_action else 0

    return {"main": main}


def _fake_live_namespace(tmp_path: Path, actions: list[tuple[str, tuple[str, ...]]], *,
                         fail_action: str | None = None, composer: bool = False):
    state_root = tmp_path / "live-state"
    state_root.mkdir(mode=0o700)
    events = state_root / "events"
    events.mkdir(mode=0o700)
    workdir = tmp_path / "workdir"
    workdir.mkdir(mode=0o700)
    (workdir / ".codex/cortex").mkdir(parents=True, mode=0o700)
    state = {
        "workdir": str(workdir),
        "store": str(workdir / ".codex/cortex/cortex.sqlite3"),
        "events": str(events),
        "started_at": 1000.0,
        "thread_created_since": 1000.0,
        "original_request_sha256": "a" * 64,
        "first_submission_at": 1001.0,
        "native_user_turn_receipt": _native_turn(),
        "tmux_server_pid": 900,
        "tmux_session_name": "cortex-markdown-smoke",
        "tmux_session_id": "$1",
        "tmux_session_created": 901,
        "tmux_pane_id": "%1",
        "tmux_pane_pid": 1234,
        "tmux_pane_start_ticks": 902,
        "phase2_control_sha256": hashlib.sha256(b"sealed-control").hexdigest(),
        "lifecycle_status": "started",
    }
    receipt_fields = {
        key: state[key] for key in (
            "workdir", "store", "events", "started_at", "thread_created_since",
            "tmux_server_pid", "tmux_session_name", "tmux_session_id", "tmux_session_created",
            "tmux_pane_id", "tmux_pane_pid", "tmux_pane_start_ticks",
        )
    }
    state["session_receipt"] = hashlib.sha256(
        json.dumps(receipt_fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    state["native_user_turn_receipt"] = _native_turn(
        control=state["phase2_control_sha256"], receipt=state["session_receipt"],
    )
    binding = {
        "schema_version": phase2_cli_adapter.SESSION_BINDING_SCHEMA,
        "control_record_sha256": state["phase2_control_sha256"],
        **receipt_fields,
        "session_receipt": state["session_receipt"],
    }
    binding_path = state_root / (
        f"phase2-session-binding-{state['phase2_control_sha256']}-{state['session_receipt']}.json"
    )
    binding_path.write_text(json.dumps(binding) + "\n")
    binding_path.chmod(0o600)
    namespace = _fake_evidence_harness(
        actions, fail_action=fail_action, composer_workdir=workdir if composer else None,
    )
    namespace.update({
        "STATE": state_root, "state": lambda: state,
        "_require_trust_receipt": lambda _state: {"status": "accepted"},
    })
    return namespace, state


def test_run9_global_stale_authorization_does_not_cross_into_fresh_session(tmp_path):
    """Regression for r_e02164811863: Run 8's UID-global file blocked Run 9."""
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _ = _fake_live_namespace(tmp_path, [])
    legacy = namespace["STATE"] / "phase2-cell-authorization.json"
    legacy.write_text(json.dumps({
        "schema_version": phase2_cli_adapter.CELL_AUTH_SCHEMA,
        "status": "submitted",
        "control_record_sha256": "7" * 64,
        "session_receipt": "8" * 64,
    }) + "\n")
    legacy.chmod(0o600)

    issued = phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-001")

    assert issued["status"] == "issued"
    assert phase2_cli_adapter._authorization_path(
        phase2_cli_adapter._session_context(namespace)
    ) != legacy
    assert json.loads(legacy.read_text())["status"] == "submitted"


def _absent_gc_state(tmp_path: Path, *, status: str = "submitted", active: bool = False,
                     foreign: bool = False, two_generations: bool = False):
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, mode=0o700)
    events = state_root / "events"
    events.mkdir(mode=0o700)
    run_root = tmp_path / "run"
    workdir = run_root / "cells/cell-001/work"
    workdir.mkdir(parents=True)
    diagnostic = run_root / "diagnostic/cell-001"
    diagnostic.mkdir(parents=True)
    calls = [{"tool": "functions.exec", "outcome": "running" if active else "success",
              "host_status": None if active else "completed"}]
    (diagnostic / "calls.jsonl").write_text("".join(json.dumps(row) + "\n" for row in calls))
    (diagnostic / "audit.json").write_text(json.dumps({
        "open_sessions": ["s1"] if active else [], "open_cells": [],
    }) + "\n")
    control, receipt, nonce = "1" * 64, "2" * 64, "3" * 64
    submitted_at, request = 1001.0, "4" * 64
    submitted = status in {"submitted", "collected", "consumed"}
    native = _native_turn(control=control, receipt=receipt)
    authorization = {
        "schema_version": phase2_cli_adapter.CELL_AUTH_SCHEMA, "status": status,
        "cell_id": "cell-001", "nonce": nonce, "control_record_sha256": control,
        "arm": "baseline", "workdir": str(workdir),
        "store": str(workdir / ".codex/cortex/cortex.sqlite3"), "started_at": 1000.0,
        "thread_created_since": 1000.0, "session_receipt": receipt,
        "submission_request_sha256": request if submitted else None,
        "submitted_at": submitted_at if submitted else None,
        "submission_native_user_turn": native if submitted else None,
        "submission_receipt": (phase2_cli_adapter._submission_receipt(nonce, request, submitted_at, native)
                               if submitted else None), "thread_id": None,
    }
    binding = {
        "schema_version": phase2_cli_adapter.SESSION_BINDING_SCHEMA,
        "control_record_sha256": control, "session_receipt": receipt,
        "workdir": str(workdir), "store": authorization["store"], "events": str(events),
        "started_at": 1000.0, "thread_created_since": 1000.0,
        "tmux_session_id": "$1", "tmux_pane_id": "%1", "tmux_pane_pid": 424242,
        "tmux_pane_start_ticks": 77,
    }
    transaction = {"schema_version": "phase2-cli-launch-transaction-v1", "status": "launched",
                   "control_record_sha256": control, "canonical_path_digest": "5" * 64}
    files = {
        "phase2-cell-authorization.json": authorization,
        f"phase2-session-binding-{control}-{receipt}.json": binding,
        "last.json": {"lifecycle_status": "started", "phase2_control_sha256": control,
                      "session_receipt": receipt, **{
                          key: value for key, value in binding.items()
                          if key not in {"schema_version", "control_record_sha256", "session_receipt"}
                      }},
        f"phase2-control-transaction-{control}.json": transaction,
        f"phase2-workdir-transaction-{'5' * 64}.json": transaction,
    }
    if foreign:
        files[f"phase2-session-binding-{'9' * 64}.json"] = {
            **binding, "control_record_sha256": "9" * 64,
        }
    if two_generations:
        control2, receipt2 = "6" * 64, "7" * 64
        run2 = tmp_path / "run2"
        workdir2 = run2 / "cells/cell-002/work"
        workdir2.mkdir(parents=True)
        diagnostic2 = run2 / "diagnostic/cell-002"
        diagnostic2.mkdir(parents=True)
        (diagnostic2 / "calls.jsonl").write_text(json.dumps({
            "tool": "functions.exec", "outcome": "success", "host_status": "completed",
        }) + "\n")
        (diagnostic2 / "audit.json").write_text(json.dumps({
            "open_sessions": [], "open_cells": [],
        }) + "\n")
        authorization2 = {**authorization, "control_record_sha256": control2,
                          "session_receipt": receipt2, "cell_id": "cell-002", "workdir": str(workdir2),
                          "store": str(workdir2 / ".codex/cortex/cortex.sqlite3")}
        if submitted:
            native2 = _native_turn(control=control2, receipt=receipt2)
            authorization2["submission_native_user_turn"] = native2
            authorization2["submission_receipt"] = phase2_cli_adapter._submission_receipt(
                nonce, request, submitted_at, native2,
            )
        transaction2 = {**transaction, "control_record_sha256": control2,
                        "canonical_path_digest": "8" * 64}
        files[f"phase2-cell-authorization-{control2}-{receipt2}.json"] = authorization2
        files[f"phase2-control-transaction-{control2}.json"] = transaction2
        files[f"phase2-workdir-transaction-{'8' * 64}.json"] = transaction2
    for name, value in files.items():
        path = state_root / name
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)
    return state_root


def _prebinding_failed_launch_state(tmp_path: Path):
    state_root = tmp_path / "failed-state"
    state_root.mkdir(mode=0o700)
    workdir = tmp_path / "failed-run/cells/cell-001/work"
    cortex = workdir / ".codex/cortex"
    cortex.mkdir(parents=True, mode=0o700)
    control = "1" * 64
    store = cortex / "cortex.sqlite3"
    path_digest = hashlib.sha256(f"{workdir}\0{store}".encode()).hexdigest()
    transaction = {
        "schema_version": phase2_cli_adapter.LAUNCH_TRANSACTION_SCHEMA,
        "status": "failed", "transaction_id": "2" * 32,
        "control_record_sha256": control, "canonical_path_digest": path_digest,
        "timestamp": 1000.0, "updated_at": 1001.0,
    }
    failure = {
        "schema_version": phase2_cli_adapter.POST_COMMIT_FAILURE_SCHEMA,
        "status": "unusable", "outcome": "post_commit_launch_failure",
        "run_id": transaction["transaction_id"], "control_record_sha256": control,
        "canonical_path_digest": path_digest, "failure_stage": "tmux-new-session",
        "error_type": "RuntimeError", "session_cleanup": "stopped", "timestamp": 1001.0,
    }
    files = {
        f"phase2-control-transaction-{control}.json": transaction,
        f"phase2-workdir-transaction-{path_digest}.json": transaction,
        f"phase2-control-failure-{control}.json": failure,
        f"phase2-workdir-failure-{path_digest}.json": failure,
        "session.json": {
            "workdir": str(workdir), "started_at": 1000.5, "thread_created_since": 1000.5,
            "trial_started_at": 1000.5, "resumed": False, "events": str(state_root / "events"),
            "original_request_sha256": None, "first_submission_at": None, "store": str(store),
            "codebase_memory": False, "model": "gpt-5.6-luna", "effort": "high",
            "lifecycle_status": "starting",
        },
    }
    for name, value in files.items():
        path = state_root / name
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)
    for name, value in (("phase2-launch-transaction.json", transaction),
                        ("phase2-launch-failure.json", failure)):
        path = cortex / name
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)
    capture = state_root / "capture.txt"
    capture.write_text("")
    capture.chmod(0o600)
    events = state_root / "events"
    events.mkdir(mode=0o700)
    event = {
        "event_kind": "launcher", "operation": "evaluation_fresh_store", "outcome": "success",
        "run_id": transaction["transaction_id"], "host_class": "cli",
        "project_relative_store": ".codex/cortex/cortex.sqlite3",
        "project_relative_path": ".codex/cortex/cortex.sqlite3",
        "canonical_path_digest": path_digest, "path_digest": path_digest,
        "existed_before": False, "freshness_check": "absent-before-launch", "path_type": "absent",
        "path_check": "canonical-under-workdir", "symlink_check": "pass",
        "workdir_check": "canonical", "privacy_check": "owner-private",
        "parent_creation": "atomic-private-hierarchy", "creation": "parent-directories-only",
        "timestamp": 1000.0, "created_at": 1000.0, "launcher_digest": "3" * 64,
        "observer_digest": "4" * 64, "candidate_digest": "5" * 64,
    }
    event_path = events / f"launcher-{transaction['transaction_id']}.jsonl"
    event_path.write_text(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    event_path.chmod(0o600)
    return state_root, transaction


def test_prebinding_failed_launch_gc_archives_exact_generation_and_replays(monkeypatch, tmp_path):
    state_root, transaction = _prebinding_failed_launch_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    receipt = phase2_cli_adapter._gc_absent_runtime(state_root, "a" * 64)
    assert receipt["schema_version"] == phase2_cli_adapter.PREBINDING_FAILED_LAUNCH_GC_SCHEMA
    assert receipt["status"] == "consumed"
    assert receipt["generation"]["control_record_sha256"] == transaction["control_record_sha256"]
    assert {receipt["generation"][key] for key in
            ("binding", "authorization", "submission", "runtime_events")} == {"absent"}
    archive = state_root / receipt["archive"]
    assert sorted(path.name for path in archive.iterdir()) == sorted(receipt["targets"])
    assert phase2_cli_adapter._gc_absent_runtime(state_root, "a" * 64)["replayed"] is True


@pytest.mark.parametrize("case", ["malformed", "missing", "mixed-control", "extra-file",
                                  "successful", "submitted", "event"])
def test_prebinding_failed_launch_gc_refuses_partial_foreign_or_active_state(monkeypatch, tmp_path, case):
    state_root, transaction = _prebinding_failed_launch_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    control_failure = state_root / f"phase2-control-failure-{transaction['control_record_sha256']}.json"
    if case == "malformed":
        control_failure.write_text("{}\n")
    elif case == "missing":
        control_failure.unlink()
    elif case == "mixed-control":
        value = json.loads(control_failure.read_text())
        value["control_record_sha256"] = "9" * 64
        control_failure.write_text(json.dumps(value) + "\n")
    elif case == "extra-file":
        extra = state_root / "foreign.json"
        extra.write_text("{}\n")
        extra.chmod(0o600)
    elif case == "successful":
        path = state_root / f"phase2-control-transaction-{transaction['control_record_sha256']}.json"
        value = json.loads(path.read_text())
        value["status"] = "launched"
        path.write_text(json.dumps(value) + "\n")
    elif case == "submitted":
        path = state_root / "session.json"
        value = json.loads(path.read_text())
        value["first_submission_at"] = 1002.0
        path.write_text(json.dumps(value) + "\n")
    else:
        event = next((state_root / "events").iterdir())
        event.write_text(event.read_text() + "{}\n")
    with pytest.raises(phase2_cli_adapter.AdapterError):
        phase2_cli_adapter._gc_absent_runtime(state_root, "b" * 64)
    assert not any(state_root.glob("phase2-prebinding-failed-launch-gc-*.json"))


def test_prebinding_failed_launch_gc_revalidates_race_and_resumes(monkeypatch, tmp_path):
    state_root, _transaction = _prebinding_failed_launch_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    real_replace = phase2_cli_adapter.os.replace
    changed = False
    def race_after_first(source, destination):
        nonlocal changed
        result = real_replace(source, destination)
        if Path(source).parent == state_root and not changed:
            changed = True
            capture = state_root / "capture.txt"
            if capture.exists():
                capture.write_text("raced")
        return result
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", race_after_first)
    with pytest.raises(phase2_cli_adapter.AdapterError):
        phase2_cli_adapter._gc_absent_runtime(state_root, "c" * 64)
    receipt_path = next(state_root.glob("phase2-prebinding-failed-launch-gc-*.json"))
    assert json.loads(receipt_path.read_text())["status"] == "prepared"
    (state_root / "capture.txt").write_text("")
    (state_root / "capture.txt").chmod(0o600)
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", real_replace)
    assert phase2_cli_adapter._gc_absent_runtime(state_root, "c" * 64)["status"] == "consumed"


def _install_later_normal_gc_state(tmp_path: Path, state_root: Path) -> Path:
    later = _absent_gc_state(tmp_path / "later")
    for path in later.glob("phase2-session-binding-*.json"):
        value = json.loads(path.read_text())
        value["events"] = str(state_root / "events")
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)
        for saved_name in ("last.json", "session.json"):
            saved_path = later / saved_name
            if saved_path.exists():
                saved = json.loads(saved_path.read_text())
                saved["events"] = str(state_root / "events")
                saved_path.write_text(json.dumps(saved, sort_keys=True) + "\n")
                saved_path.chmod(0o600)
    for path in later.iterdir():
        os.replace(path, state_root / path.name)
    return state_root


def test_consumed_prebinding_history_does_not_capture_later_normal_gc(monkeypatch, tmp_path):
    state_root, _transaction = _prebinding_failed_launch_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    first_nonce, normal_nonce = "d" * 64, "e" * 64
    first = phase2_cli_adapter._gc_absent_runtime(state_root, first_nonce)
    _install_later_normal_gc_state(tmp_path, state_root)
    normal = phase2_cli_adapter._gc_absent_runtime(state_root, normal_nonce)
    assert normal["schema_version"] == phase2_cli_adapter.ABSENT_RUNTIME_GC_SCHEMA
    assert normal["status"] == "consumed"
    assert normal["generations"]
    assert (state_root / first["archive"]).is_dir()
    assert phase2_cli_adapter._gc_absent_runtime(state_root, normal_nonce)["replayed"] is True


def test_exact_prebinding_replay_ignores_later_normal_live_state(monkeypatch, tmp_path):
    state_root, _transaction = _prebinding_failed_launch_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    nonce = "f" * 64
    phase2_cli_adapter._gc_absent_runtime(state_root, nonce)
    _install_later_normal_gc_state(tmp_path, state_root)
    replay = phase2_cli_adapter._gc_absent_runtime(state_root, nonce)
    assert replay["schema_version"] == phase2_cli_adapter.PREBINDING_FAILED_LAUNCH_GC_SCHEMA
    assert replay["replayed"] is True
    assert (state_root / "phase2-cell-authorization.json").is_file()


def test_gc_refuses_ambiguous_simultaneous_failed_and_bound_generations(monkeypatch, tmp_path):
    state_root, _transaction = _prebinding_failed_launch_state(tmp_path)
    later = _absent_gc_state(tmp_path / "later")
    authorization = later / "phase2-cell-authorization.json"
    os.replace(authorization, state_root / authorization.name)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    with pytest.raises(phase2_cli_adapter.AdapterError, match="ambiguous simultaneous"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "9" * 64)
    assert any(state_root.glob("phase2-*-failure-*.json"))
    assert (state_root / "phase2-cell-authorization.json").is_file()


def test_absent_runtime_gc_archives_exact_state_and_replays(monkeypatch, tmp_path):
    state_root = _absent_gc_state(tmp_path, two_generations=True)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    receipt = phase2_cli_adapter._gc_absent_runtime(state_root, "a" * 64)
    assert receipt["status"] == "consumed"
    assert len(receipt["targets"]) == 8
    archive = state_root / receipt["archive"]
    assert all((archive / name).is_file() and not (state_root / name).exists()
               for name in receipt["targets"])
    replay = phase2_cli_adapter._gc_absent_runtime(state_root, "a" * 64)
    assert replay["status"] == "consumed" and replay["replayed"] is True


@pytest.mark.parametrize("name", [
    "phase2-control-transaction-unrecognized.json",
    "phase2-control-transaction-" + "1" * 64 + ".json.backup",
    "Phase2-control-transaction-" + "1" * 64 + ".json",
    ".phase2-control-transaction-" + "1" * 64 + ".json.tmp",
    "extra.json",
    "phase2-cell-authorization.json~",
])
def test_absent_runtime_gc_refuses_every_unrecognized_state_root_filename(monkeypatch, tmp_path, name):
    state_root = _absent_gc_state(tmp_path)
    extra = state_root / name
    extra.write_bytes((state_root / f"phase2-control-transaction-{'1' * 64}.json").read_bytes())
    extra.chmod(0o600)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="unrecognized|noncanonical"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "9" * 64)
    assert (state_root / "phase2-cell-authorization.json").exists()


@pytest.mark.parametrize("variant", [
    "file", "symlink", "mode", "opaque-row", "valid-row", "multiple",
    "hidden", "nested",
])
def test_absent_runtime_gc_refuses_invalid_events_directory(monkeypatch, tmp_path, variant):
    state_root = _absent_gc_state(tmp_path)
    events = state_root / "events"
    if variant == "file":
        events.rmdir(); events.write_text("not a directory\n"); events.chmod(0o600)
    elif variant == "symlink":
        events.rmdir(); target = tmp_path / "foreign-events"; target.mkdir(); events.symlink_to(target)
    elif variant == "mode":
        events.chmod(0o755)
    elif variant == "nested":
        (events / "nested").mkdir()
    else:
        names = (["one.jsonl", "two.jsonl"] if variant == "multiple"
                 else [".hidden.jsonl"] if variant == "hidden" else ["events.jsonl"])
        for name in names:
            child = events / name
            child.write_text("opaque private row\n" if variant == "opaque-row" else "{}\n")
            child.chmod(0o600)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="event|state-root entry"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "0" * 64)
    assert (state_root / "phase2-cell-authorization.json").exists()


def test_absent_runtime_gc_accepts_absent_events_path(monkeypatch, tmp_path):
    state_root = _absent_gc_state(tmp_path, two_generations=True)
    (state_root / "events").rmdir()
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    receipt = phase2_cli_adapter._gc_absent_runtime(state_root, "b" * 64)
    assert receipt["status"] == "consumed" and len(receipt["targets"]) == 8


def test_absent_runtime_gc_refuses_wrong_owner_events_directory(monkeypatch, tmp_path):
    state_root = _absent_gc_state(tmp_path)
    events = state_root / "events"
    real_lstat = Path.lstat
    def wrong_owner(path):
        info = real_lstat(path)
        if path == events:
            class WrongOwner:
                st_mode, st_uid, st_dev, st_ino = info.st_mode, info.st_uid + 1, info.st_dev, info.st_ino
            return WrongOwner()
        return info
    monkeypatch.setattr(Path, "lstat", wrong_owner)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="event directory"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "a" * 64)


@pytest.mark.parametrize("case", ["tmux", "active", "foreign"])
def test_absent_runtime_gc_refuses_existing_active_or_foreign(monkeypatch, tmp_path, case):
    state_root = _absent_gc_state(tmp_path, active=case == "active", foreign=case == "foreign")
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    if case == "tmux":
        monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                            lambda: (_ for _ in ()).throw(phase2_cli_adapter.AdapterError("existing tmux server")))
    else:
        monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                            lambda: {"status": "absent", "exit_code": 1})
    with pytest.raises(phase2_cli_adapter.AdapterError):
        phase2_cli_adapter._gc_absent_runtime(state_root, "b" * 64)
    assert (state_root / "phase2-cell-authorization.json").exists()


def test_absent_runtime_gc_refuses_pid_reuse_and_resumes_partial_failure(monkeypatch, tmp_path):
    state_root = _absent_gc_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: 77)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="live or ambiguous"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "d" * 64)
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    real_replace = phase2_cli_adapter.os.replace
    count = 0
    def fail_once(source, destination):
        nonlocal count
        if Path(source).parent == state_root:
            count += 1
            if count == 2:
                raise OSError("partial archive")
        return real_replace(source, destination)
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", fail_once)
    with pytest.raises(OSError, match="partial archive"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "e" * 64)
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", real_replace)
    assert phase2_cli_adapter._gc_absent_runtime(state_root, "e" * 64)["status"] == "consumed"


def test_absent_runtime_gc_refuses_simultaneous_unknown_saved_state(monkeypatch, tmp_path):
    state_root = _absent_gc_state(tmp_path)
    session = state_root / "session.json"
    session.write_text(json.dumps({"foreign": True}) + "\n")
    session.chmod(0o600)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="simultaneous saved session"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "f" * 64)


@pytest.mark.parametrize("open_state", [{}, {"open_sessions": None, "open_cells": []},
                                         {"open_sessions": [], "open_cells": "none"}])
def test_absent_runtime_gc_refuses_unknown_audit_open_state(monkeypatch, tmp_path, open_state):
    state_root = _absent_gc_state(tmp_path)
    audit = tmp_path / "run/diagnostic/cell-001/audit.json"
    audit.write_text(json.dumps(open_state) + "\n")
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="submitted active"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "6" * 64)


@pytest.mark.parametrize("race", ["active", "pid", "new-file", "receipt"])
def test_absent_runtime_gc_revalidates_every_prepared_resume(monkeypatch, tmp_path, race):
    state_root = _absent_gc_state(tmp_path)
    monkeypatch.setattr(phase2_cli_adapter, "_require_absent_tmux",
                        lambda: {"status": "absent", "exit_code": 1})
    monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: None)
    real_replace = phase2_cli_adapter.os.replace
    def fail_first_archive(source, destination):
        if Path(source).parent == state_root and Path(destination).parent.name.startswith("phase2-absent-runtime-archive-"):
            raise OSError("prepared interruption")
        return real_replace(source, destination)
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", fail_first_archive)
    with pytest.raises(OSError, match="prepared interruption"):
        phase2_cli_adapter._gc_absent_runtime(state_root, "7" * 64)
    monkeypatch.setattr(phase2_cli_adapter.os, "replace", real_replace)
    receipt_path = next(state_root.glob("phase2-absent-runtime-gc-*.json"))
    if race == "active":
        calls = tmp_path / "run/diagnostic/cell-001/calls.jsonl"
        calls.write_text(json.dumps({"tool": "functions.exec", "outcome": "running",
                                     "host_status": None}) + "\n")
    elif race == "pid":
        monkeypatch.setattr(phase2_cli_adapter, "_process_start_ticks_if_present", lambda _pid: 77)
    elif race == "new-file":
        new_state = state_root / f"phase2-session-binding-{'8' * 64}.json"
        new_state.write_text("{}\n")
        new_state.chmod(0o600)
    else:
        receipt = json.loads(receipt_path.read_text())
        receipt["generations"] = {}
        receipt_path.write_text(json.dumps(receipt) + "\n")
        receipt_path.chmod(0o600)
    with pytest.raises(phase2_cli_adapter.AdapterError):
        phase2_cli_adapter._gc_absent_runtime(state_root, "7" * 64)
    assert json.loads(receipt_path.read_text())["status"] == "prepared"


def test_cell_authorization_is_scoped_to_control_and_session_receipt(tmp_path):
    first_record = tmp_path / "control.json"
    first_record.write_text("sealed-control")
    namespace, state = _fake_live_namespace(tmp_path, [])
    first = phase2_cli_adapter._begin_cell(namespace, first_record, "baseline", "cell-001")
    first_path = phase2_cli_adapter._authorization_path(phase2_cli_adapter._session_context(namespace))

    second_record = tmp_path / "control-2.json"
    second_record.write_text("second-control")
    second_digest = hashlib.sha256(second_record.read_bytes()).hexdigest()
    state.update(
        started_at=2000.0, thread_created_since=2000.0,
        tmux_session_id="$2", tmux_session_created=1901,
        tmux_pane_id="%2", tmux_pane_pid=2234, tmux_pane_start_ticks=1902,
        phase2_control_sha256=second_digest,
    )
    fields = {key: state[key] for key in (
        "workdir", "store", "events", "started_at", "thread_created_since",
        "tmux_server_pid", "tmux_session_name", "tmux_session_id", "tmux_session_created",
        "tmux_pane_id", "tmux_pane_pid", "tmux_pane_start_ticks",
    )}
    state["session_receipt"] = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    second_binding = namespace["STATE"] / (
        f"phase2-session-binding-{second_digest}-{state['session_receipt']}.json"
    )
    second_binding.write_text(json.dumps({
        "schema_version": phase2_cli_adapter.SESSION_BINDING_SCHEMA,
        "control_record_sha256": second_digest,
        **fields,
        "session_receipt": state["session_receipt"],
    }) + "\n")
    second_binding.chmod(0o600)

    second = phase2_cli_adapter._begin_cell(namespace, second_record, "candidate", "cell-002")

    second_path = phase2_cli_adapter._authorization_path(phase2_cli_adapter._session_context(namespace))
    assert second_path != first_path
    assert first["control_record_sha256"] != second["control_record_sha256"]
    assert json.loads(first_path.read_text())["status"] == "issued"


def test_session_receipt_is_anchored_once_and_rotation_is_refused(tmp_path):
    namespace, state = _fake_live_namespace(tmp_path, [])
    original = phase2_cli_adapter._session_context(namespace)["session_receipt"]
    state["session_receipt"] = "f" * 64

    with pytest.raises(phase2_cli_adapter.AdapterError, match="receipt changed after start"):
        phase2_cli_adapter._session_context(namespace)

    assert original != state["session_receipt"]


def test_begin_requires_completed_start_and_current_authorization_stays_exclusive(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, state = _fake_live_namespace(tmp_path, [])
    state["lifecycle_status"] = "starting"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="start transition"):
        phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-001")
    state["lifecycle_status"] = "started"
    phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-001")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="unconsumed"):
        phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-002")


def test_normal_start_begin_send_lifecycle_captures_one_stable_receipt(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    control_digest = hashlib.sha256(record.read_bytes()).hexdigest()
    namespace, state = _adapter_flow_namespace(tmp_path, [])
    initial_receipt = state["session_receipt"]
    state.pop("session_receipt")
    state.pop("phase2_control_sha256")
    state["lifecycle_status"] = "starting"
    binding = namespace["STATE"] / (
        f"phase2-session-binding-{control_digest}-{initial_receipt}.json"
    )
    binding.unlink()
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_start_lifecycle")
    capture = harness["_capture_phase2_session_binding"]
    globals_ = capture.__globals__
    monkeypatch.setitem(globals_, "STATE", namespace["STATE"])
    monkeypatch.setitem(globals_, "PHASE2_CONTROL_SHA256", control_digest)

    capture(state)
    started_receipt = state["session_receipt"]
    assert state["lifecycle_status"] == "started"
    with pytest.raises(RuntimeError, match="already exists"):
        capture(state)

    issued = phase2_cli_adapter._begin_cell(namespace, record, "candidate", "cell-009")
    assert issued["session_receipt"] == started_receipt
    phase2_cli_adapter._mark_submitting(namespace, record, "candidate")
    state["original_request_sha256"] = "a" * 64
    state["first_submission_at"] = 1001.25
    state["native_user_turn_receipt"] = _native_turn(
        control=state["phase2_control_sha256"], receipt=state["session_receipt"],
    )
    phase2_cli_adapter._mark_submitted(namespace, record, "candidate")
    submitted, context = phase2_cli_adapter._require_authorization(
        namespace, record, "candidate", "submitted",
    )
    assert submitted["session_receipt"] == context["session_receipt"] == started_receipt


def _pre_submit_abort_namespace(tmp_path: Path, *, task_call: bool = False,
                                foreign_pane: bool = False, race: bool = False):
    actions = []
    namespace, state = _fake_live_namespace(tmp_path, actions)
    state["original_request_sha256"] = None
    state["first_submission_at"] = None
    state["native_user_turn_receipt"] = None
    terminal = {
        "schema_version": "cortex-live-terminal-snapshot-v1",
        "session_id": "$9" if foreign_pane else "$1", "pane_id": "%1", "pane_pid": 1234,
        "current_command": "codex", "dead": False, "dead_status": None,
        "descendants": [{"pid": 1235, "ppid": 1234, "command": "codex"}],
    }
    calls = [{
        "timestamp_ns": 0, "thread_id": None, "parent_thread_id": None,
        "role": "runtime", "tool": "mcp__cortex__evaluation_fresh_store",
        "outcome": "success", "host_status": None,
    }]
    if task_call:
        calls.append({
            "thread_id": "task-1", "parent_thread_id": None, "role": "coordinator",
            "tool": "mcp__cortex__create_task", "outcome": "success", "host_status": "completed",
        })
    event = {"event_kind": "launcher", "operation": "evaluation_fresh_store", "outcome": "success"}
    audit = {
        "mcp_events": 1, "hook_actions": 0, "host_calls": len(calls),
        "failures": [], "hook_failures": [], "host_failures": [],
        "orchestration_error_history": [], "orchestration_host_failures": [],
        "policy_violations": [], "worker_policy_violations": [],
        "orchestration_policy_violations": [], "open_sessions": [], "open_cells": [],
        "schema_version": "cortex-evidence-quality-classification-v1",
        "evidence_valid": True, "score_eligible": True,
        "evidence_integrity_invalidators": [], "quality_findings": [],
    }
    counts = {"capture": 0}

    def main():
        action = __import__("sys").argv[1]
        actions.append((action, tuple(__import__("sys").argv[2:])))
        if action.startswith("stop"):
            return 0
        outputs = {
            "status": "codex dead=0 status=\n",
            "terminal": json.dumps(terminal) + "\n",
            "capture": "Do you trust the contents of this directory?\nPress enter to continue\n",
            "calls": "".join(json.dumps(row) + "\n" for row in calls),
            "events": json.dumps(event) + "\n",
            "audit": json.dumps(audit) + "\n",
        }
        if action == "capture":
            counts["capture"] += 1
            if race and counts["capture"] == 2:
                outputs["capture"] += "changed\n"
        print(outputs[action], end="")
        return 0

    namespace["main"] = main
    namespace["tmux"] = lambda *_args, **_kwargs: type(
        "Result", (), {"stdout": "Do you trust the contents of this directory?\nPress enter to continue\n"}
    )()
    return namespace, state, actions


def test_pre_submit_abort_allows_only_stable_owned_trust_prompt(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _pre_submit_abort_namespace(tmp_path)
    abort_dir = (tmp_path / "abort").resolve()

    receipt = phase2_cli_adapter._abort_pre_submit(
        namespace, tmp_path / "harness", record, "baseline", str(abort_dir),
    )

    assert receipt["status"] == "consumed"
    assert actions[-1][0] == "stop-exact" and actions[-1][1][0] == "--interrupt"
    assert json.loads((abort_dir / "pre-submit-abort.json").read_text())["status"] == "consumed"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="already been used"):
        phase2_cli_adapter._abort_pre_submit(
            namespace, tmp_path / "harness", record, "baseline", str(tmp_path / "again"),
        )


def test_submitting_no_submit_empty_composer_can_be_resolved_without_resend(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, state, actions = _pre_submit_abort_namespace(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("exact prompt\n")
    observer = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="phase2_test_digest")
    state["original_request_sha256"] = observer["original_request_digest"]("exact prompt")
    phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-001")
    phase2_cli_adapter._mark_submitting(namespace, record, "baseline")
    namespace["native_user_turn_receipts"] = lambda *_: []
    namespace["tmux"] = lambda *_args, **_kwargs: type(
        "Result", (), {"stdout": "› Ask Codex to do anything\n"}
    )()
    receipt = phase2_cli_adapter._abort_pre_submit(
        namespace, tmp_path / "harness", record, "baseline",
        str((tmp_path / "resolved").resolve()), str(prompt.resolve()),
    )
    assert receipt["status"] == "consumed"
    assert receipt["authorization_status"] == "submitting"
    assert receipt["activity"]["pane_state"] == "live-empty-composer-after-no-submit"
    assert all(action != "send" for action, _ in actions)


@pytest.mark.parametrize("failure", ["request", "submission", "task", "foreign", "race"])
def test_pre_submit_abort_refuses_any_submit_task_foreign_or_raced_state(tmp_path, failure):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, state, actions = _pre_submit_abort_namespace(
        tmp_path, task_call=failure == "task", foreign_pane=failure == "foreign",
        race=failure == "race",
    )
    if failure == "request":
        state["original_request_sha256"] = "a" * 64
    if failure == "submission":
        state["first_submission_at"] = 1001.0

    with pytest.raises(phase2_cli_adapter.AdapterError):
        phase2_cli_adapter._abort_pre_submit(
            namespace, tmp_path / "harness", record, "baseline", str(tmp_path / "abort"),
        )

    assert all(not action.startswith("stop") for action, _ in actions)


def _submitted_authorization(namespace: dict, record: Path, arm: str, cell_id: str = "cell-001"):
    phase2_cli_adapter._begin_cell(namespace, record, arm, cell_id)
    phase2_cli_adapter._mark_submitting(namespace, record, arm)
    state = namespace["state"]()
    state["native_user_turn_receipt"] = _native_turn(
        control=state["phase2_control_sha256"], receipt=state["session_receipt"],
    )
    phase2_cli_adapter._mark_submitted(namespace, record, arm)
    authorization, context = phase2_cli_adapter._require_authorization(
        namespace, record, arm, "submitted",
    )
    return authorization, context


def _adapter_flow_namespace(tmp_path: Path, actions: list[tuple[str, tuple[str, ...]]], *,
                            send_exit: int = 0, crash_after_receipt: bool = False,
                            stop_failure: bool = False):
    namespace, state = _fake_live_namespace(tmp_path, actions)
    state["original_request_sha256"] = None
    state["first_submission_at"] = None
    evidence_main = namespace["main"]

    def main():
        command = __import__("sys").argv[1]
        if command == "send-preflight":
            actions.append((command, tuple(__import__("sys").argv[2:])))
            return 0
        if command == "send":
            actions.append((command, tuple(__import__("sys").argv[2:])))
            state["original_request_sha256"] = "a" * 64
            state["first_submission_at"] = 1001.25
            state["native_user_turn_receipt"] = _native_turn(
                control=state["phase2_control_sha256"], receipt=state["session_receipt"],
            )
            if crash_after_receipt:
                raise OSError("simulated adapter crash window")
            return send_exit
        if command.startswith("stop"):
            actions.append((command, tuple(__import__("sys").argv[2:])))
            if stop_failure:
                raise RuntimeError("simulated exact-target substitution")
            return 0
        return evidence_main()

    namespace["main"] = main
    return namespace, state


def _orphan_namespace(tmp_path: Path, *, active: bool = False, live_pane: bool = False,
                      race_live: bool = False, race_capture: bool = False,
                      race_terminal: bool = False):
    actions = []
    namespace, state = _fake_live_namespace(tmp_path, actions)
    state["original_request_sha256"] = None
    state["first_submission_at"] = None
    outputs = {
        "status": "codex dead=0 status=\n" if live_pane else "bash dead=1 status=0\n",
        "terminal": json.dumps({
            "schema_version": "cortex-live-terminal-snapshot-v1",
            "session_id": "$1", "pane_id": "%1", "pane_pid": 1234,
            "current_command": "codex" if live_pane else "bash",
            "dead": False if live_pane else True,
            "dead_status": None if live_pane else 0,
            "descendants": ([{"pid": 1235, "ppid": 1234, "command": "codex"}]
                            if live_pane else []),
        }, sort_keys=True, separators=(",", ":")) + "\n",
        "capture": "terminal output\nCortex live-dev exit=0\n",
        "calls": json.dumps({
            "thread_id": "thread-current", "parent_thread_id": None,
            "role": "coordinator", "tool": "functions.exec",
            "outcome": "running" if active else "success",
            **({} if active else {"completed_timestamp": "2026-01-01T00:00:01Z"}),
        }) + "\n",
        "events": json.dumps({"event_kind": "mcp", "operation": "initialize", "outcome": "success"}) + "\n",
        "audit": json.dumps({"open_sessions": ["s1"] if active else [], "open_cells": []}) + "\n",
    }
    status_calls = 0
    capture_calls = 0
    terminal_calls = 0

    def main():
        nonlocal status_calls, capture_calls, terminal_calls
        action = __import__("sys").argv[1]
        actions.append((action, tuple(__import__("sys").argv[2:])))
        if action.startswith("stop") or action == "start":
            return 0
        if action == "status":
            status_calls += 1
            if race_live and status_calls > 1:
                print("codex dead=0 status=")
                return 0
        if action == "capture":
            capture_calls += 1
            if race_capture and capture_calls > 1:
                print("terminal output\nCortex live-dev exit=1")
                return 0
        if action == "terminal":
            terminal_calls += 1
            if race_terminal and terminal_calls > 1:
                changed = json.loads(outputs["terminal"])
                changed["pane_pid"] = 9999
                print(json.dumps(changed, sort_keys=True, separators=(",", ":")))
                return 0
        print(outputs[action], end="")
        return 1 if action == "audit" else 0

    namespace["main"] = main
    return namespace, state, actions


def _issued_orphan(namespace: dict, record: Path, arm: str = "baseline"):
    authorization = phase2_cli_adapter._begin_cell(namespace, record, arm, "cell-001")
    return authorization, phase2_cli_adapter._session_context(namespace)


def test_stale_pre_submit_orphan_recovery_captures_before_exact_cleanup(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path)
    authorization, _ = _issued_orphan(namespace, record)
    recovery = tmp_path / "recovery"
    receipt = phase2_cli_adapter._recover_orphan(
        namespace, tmp_path / "harness", record, "baseline", "cell-001",
        authorization["session_receipt"], "failed-pre-submit", "b" * 64, str(recovery.resolve()),
    )
    assert actions == [
        ("status", ()), ("terminal", ()), ("capture", ("--lines", "500")),
        ("calls", ("--limit", "10000")), ("events", ("--lines", "500")),
        ("audit", ()), ("status", ()), ("terminal", ()),
        ("capture", ("--lines", "500")),
        ("stop-exact", phase2_cli_adapter._exact_stop_arguments(
            phase2_cli_adapter._session_context(namespace), interrupt=False,
        )),
    ]
    assert receipt["status"] == "consumed"
    assert receipt["activity"] == {"calls": 1, "events": 1, "audit_exit_code": 1}
    assert set(receipt["artifacts"]) == {
        "status", "terminal", "capture", "calls", "events", "audit",
        "status-final", "terminal-final", "capture-final",
    }
    assert (recovery / "orphan-recovery.json").stat().st_mode & 0o777 == 0o600


def test_orphan_recovery_refuses_active_task_before_cleanup(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path, active=True)
    authorization, _ = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="active"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "c" * 64,
            str((tmp_path / "active-recovery").resolve()),
        )
    assert actions[-1][0] == "audit"
    assert all(not action.startswith("stop") for action, _ in actions)


def test_orphan_recovery_refuses_every_live_codex_pane(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path, live_pane=True)
    authorization, _ = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="every live pane is refused"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "2" * 64,
            str((tmp_path / "live-codex").resolve()),
        )
    assert actions[-1][0] == "audit"
    assert all(action != "stop" for action, _ in actions)


def test_orphan_recovery_refuses_idle_live_bash_even_with_no_active_calls(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path)
    authorization, _ = _issued_orphan(namespace, record)
    original = namespace["main"]
    def live_bash():
        if __import__("sys").argv[1] == "status":
            actions.append(("status", ()))
            print("bash dead=0 status=")
            return 0
        return original()
    namespace["main"] = live_bash
    with pytest.raises(phase2_cli_adapter.AdapterError, match="every live pane is refused"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "3" * 64,
            str((tmp_path / "live-bash").resolve()),
        )
    assert all(action != "stop" for action, _ in actions)


def test_orphan_recovery_revalidates_terminal_pane_before_stop(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path, race_live=True)
    authorization, _ = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="changed before cleanup"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "4" * 64,
            str((tmp_path / "race").resolve()),
        )
    assert actions[-1] == ("status", ())
    assert all(action != "stop" for action, _ in actions)


def test_orphan_recovery_rejects_changed_exit_marker_before_stop(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path, race_capture=True)
    authorization, _ = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exit-marker evidence changed"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "7" * 64,
            str((tmp_path / "marker-race").resolve()),
        )
    assert actions[-1] == ("capture", ("--lines", "500"))
    assert all(action != "stop" for action, _ in actions)


def test_orphan_recovery_rejects_changed_process_snapshot_before_stop(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path, race_terminal=True)
    authorization, _ = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="process, or exit-marker evidence changed"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "8" * 64,
            str((tmp_path / "process-race").resolve()),
        )
    assert actions[-1] == ("terminal", ())
    assert all(action != "stop" for action, _ in actions)


def test_orphan_recovery_absent_session_is_safe_refusal(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace = {"STATE": tmp_path / "absent", "state": lambda: {}}
    with pytest.raises(phase2_cli_adapter.AdapterError, match="live session state"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001", "5" * 64,
            "failed-pre-submit", "6" * 64, str((tmp_path / "absent-recovery").resolve()),
        )
    assert not (tmp_path / "absent-recovery").exists()


def test_orphan_recovery_refuses_submitted_transport_receipt(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, state, actions = _orphan_namespace(tmp_path)
    authorization, _ = _issued_orphan(namespace, record)
    state["first_submission_at"] = 1001.0
    with pytest.raises(phase2_cli_adapter.AdapterError, match="submitted session"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "invalidated", "a" * 64,
            str((tmp_path / "submitted").resolve()),
        )
    assert not actions


def test_orphan_recovery_refuses_foreign_session_or_missing_receipt(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _, actions = _orphan_namespace(tmp_path)
    authorization, context = _issued_orphan(namespace, record)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="ownership receipt mismatch"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001", "d" * 64,
            "failed-pre-submit", "e" * 64, str((tmp_path / "foreign").resolve()),
        )
    assert not actions
    phase2_cli_adapter._authorization_path(context).unlink()
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact prior cell authorization receipt"):
        phase2_cli_adapter._recover_orphan(
            namespace, tmp_path / "harness", record, "baseline", "cell-001",
            authorization["session_receipt"], "failed-pre-submit", "f" * 64,
            str((tmp_path / "missing").resolve()),
        )


def test_orphan_recovery_refuses_repeat_and_allows_clean_start_dispatch(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    harness = tmp_path / "harness"
    harness.write_text("stub")
    namespace, _, actions = _orphan_namespace(tmp_path)
    authorization, _ = _issued_orphan(namespace, record)
    phase2_cli_adapter._recover_orphan(
        namespace, harness, record, "baseline", "cell-001", authorization["session_receipt"],
        "failed-pre-submit", "1" * 64, str((tmp_path / "first-recovery").resolve()),
    )
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact prior cell authorization receipt"):
        phase2_cli_adapter._recover_orphan(
            namespace, harness, record, "baseline", "cell-001", authorization["session_receipt"],
            "failed-pre-submit", "1" * 64, str((tmp_path / "repeat-recovery").resolve()),
        )
    clean = tmp_path / "clean"
    clean.mkdir()
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_args, **_kwargs: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: namespace)
    monkeypatch.setattr(phase2_cli_adapter, "_complete_phase2_startup", lambda *_: (
        phase2_cli_adapter._session_context(namespace), {"status": "accepted"}))
    assert phase2_cli_adapter.main([
        "--control-record", str(record), "--arm", "baseline", "start",
        "--workdir", str(clean.resolve()), "--evaluation-fresh-store",
        "--model", "gpt-5.6-luna", "--effort", "high",
    ]) == 0
    assert actions[-1][0] == "start"


def test_evidence_collection_rejects_swapped_or_duplicated_action_flags(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("{}")
    harness = tmp_path / "harness"
    harness.write_text("fixture")
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: _fake_evidence_harness([]))
    for malformed in (
        ["--lines", "--lines"],
        ["--evidence-dir", str(tmp_path / "one"), "--evidence-dir", str(tmp_path / "two")],
        [str(tmp_path / "three"), "--evidence-dir"],
    ):
        with pytest.raises(phase2_cli_adapter.AdapterError, match="accepts exactly"):
            phase2_cli_adapter.main([
                "--control-record", str(record), "--arm", "baseline", "collect-evidence", *malformed,
            ])


def test_evidence_collection_retains_partial_bundle_and_refuses_premature_stop(tmp_path):
    directory = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "evidence").resolve()))
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    actions = []
    namespace, _ = _fake_live_namespace(tmp_path, actions, fail_action="events")
    authorization, context = _submitted_authorization(namespace, record, "baseline")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="events failed with exit 7"):
        phase2_cli_adapter._collect_evidence(
            namespace, tmp_path / "harness", directory, record, "baseline",
            authorization, context,
        )
    assert actions == [
        ("status", ()), ("terminal", ()), ("capture", ("--lines", "500")),
        ("events", ("--lines", "500")),
    ]
    assert (directory / "status.txt").is_file()
    assert (directory / "capture.txt").is_file()
    failure = json.loads((directory / "collection-failure.json").read_text())
    assert failure["status"] == "incomplete"
    assert failure["completed_actions"] == ["status", "terminal", "capture"]
    assert not (directory / "evidence-bundle.json").exists()
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be collected|complete sealed evidence bundle"):
        phase2_cli_adapter._validate_evidence_bundle(str(directory), record, "baseline", namespace)


def test_successful_evidence_bundle_is_complete_atomic_and_has_no_submission(tmp_path):
    directory = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "evidence").resolve()))
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    actions = []
    namespace, _ = _fake_live_namespace(tmp_path, actions)
    authorization, context = _submitted_authorization(namespace, record, "candidate")
    phase2_cli_adapter._collect_evidence(
        namespace, tmp_path / "harness", directory, record, "candidate", authorization, context,
    )
    assert actions == (
        [(action, arguments) for action, arguments, _ in phase2_cli_adapter.EVIDENCE_ACTIONS]
        + [(action, arguments) for _, action, arguments, _ in phase2_cli_adapter.TERMINAL_RECHECKS]
    )
    assert all(action not in {"send", "enter", "stop"} for action, _ in actions)
    manifest = json.loads((directory / "evidence-bundle.json").read_text())
    assert manifest["schema_version"] == "phase2-cli-evidence-bundle-v1"
    assert manifest["status"] == "complete"
    assert set(manifest["actions"]) == {
        *(action for action, _, _ in phase2_cli_adapter.EVIDENCE_ACTIONS),
        *(name for name, _, _, _ in phase2_cli_adapter.TERMINAL_RECHECKS),
    }
    for entry in manifest["actions"].values():
        artifact = directory / entry["filename"]
        assert artifact.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == entry["sha256"]
    assert not list(directory.glob(".*.*"))
    assert phase2_cli_adapter._validate_evidence_bundle(str(directory), record, "candidate", namespace)[0] == directory


def test_evidence_bundle_rejects_failed_schema_and_tampered_artifact(tmp_path):
    assert phase2_cli_adapter._validate_evidence("status", "bash dead=0 status=\n") == {
        "dead": False, "exit_status": None,
    }
    with pytest.raises(phase2_cli_adapter.AdapterError, match="events evidence is empty"):
        phase2_cli_adapter._validate_evidence("events", "\n")
    directory = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "evidence").resolve()))
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _ = _fake_live_namespace(tmp_path, [])
    authorization, context = _submitted_authorization(namespace, record, "baseline")
    phase2_cli_adapter._collect_evidence(
        namespace, tmp_path / "harness", directory, record, "baseline", authorization, context,
    )
    (directory / "calls.jsonl").write_text("{}\n")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="digest mismatch"):
        phase2_cli_adapter._validate_evidence_bundle(str(directory), record, "baseline", namespace)


def _terminal_proof_bodies(*, command="bash", dead=False, dead_status=None, descendants=None,
                           marker="Cortex live-dev exit=0", active=False):
    status_value = "" if dead_status is None else str(dead_status)
    status = f"{command} dead={int(dead)} status={status_value}\n"
    terminal = json.dumps({
        "schema_version": "cortex-live-terminal-snapshot-v1", "session_id": "$1",
        "pane_id": "%1", "pane_pid": 1234, "current_command": command,
        "dead": dead, "dead_status": dead_status, "descendants": descendants or [],
    }, sort_keys=True, separators=(",", ":")) + "\n"
    calls = json.dumps({
        "thread_id": "thread-current", "parent_thread_id": None, "role": "coordinator",
        "tool": "mcp__cortex__write_report", "outcome": "running" if active else "success",
    }) + "\n"
    audit = json.dumps({"open_sessions": ["s1"] if active else [], "open_cells": []}) + "\n"
    bodies = {
        "status": status, "status-final": status,
        "terminal": terminal, "terminal-final": terminal,
        "capture": f"task output\n{marker}\n", "calls": calls, "audit": audit,
        "events": json.dumps({"operation": "write_report", "outcome": "success"}) + "\n",
    }
    bodies.update({"capture-final": bodies["capture"], "calls-final": bodies["calls"],
                   "events-final": bodies["events"], "audit-final": bodies["audit"]})
    return bodies


def _terminal_context():
    return {
        "tmux_session_id": "$1", "tmux_pane_id": "%1", "tmux_pane_pid": 1234,
        "session_receipt": "a" * 64, "workdir": "/tmp/owned-workdir",
    }


def _idle_composer_bodies(*, active=False, completed_worker=True, spoofed=False):
    workdir = "/tmp/foreign" if spoofed else "/tmp/owned-workdir"
    terminal = json.dumps({
        "schema_version": "cortex-live-terminal-snapshot-v1", "session_id": "$1",
        "pane_id": "%1", "pane_pid": 1234, "current_command": "codex",
        "dead": False, "dead_status": None,
        "descendants": [
            {"pid": 1235, "ppid": 1234, "command": "codex"},
            {"pid": 1236, "ppid": 1235, "command": "python3"},
        ],
    }, sort_keys=True, separators=(",", ":")) + "\n"
    rows = [
        {"thread_id": "root", "parent_thread_id": None, "role": "coordinator",
         "tool": "wait_agent", "outcome": "running" if active else "success",
         "host_status": "running" if active else "completed",
         **({} if active else {"completed_timestamp": "2026-09-10T23:04:37Z"})},
        {"thread_id": "worker", "parent_thread_id": "root", "role": "build_verification",
         "tool": "native_agent_result" if completed_worker else "functions.exec",
         "outcome": "success"},
        {"thread_id": "root", "parent_thread_id": None, "role": "coordinator",
         "tool": "mcp__cortex__write_report", "outcome": "success",
         "host_status": "completed", "report_id": "r_0123456789ab", "draft_id": "d_0123456789ab"},
    ]
    calls = "".join(json.dumps(row) + "\n" for row in rows)
    events = json.dumps({"thread_id": "root", "operation": "write_report",
                         "outcome": "success", "report_id": "r_0123456789ab",
                         "draft_id": "d_0123456789ab"}) + "\n"
    audit = json.dumps({"open_sessions": ["wait-1"] if active else [], "open_cells": []}) + "\n"
    capture = ("Created FACTS.md.\n─ Worked for 6m 31s ─────────\n"
               "› Ask Codex to do anything\n\n"
               f"  gpt-5.6-luna high · {workdir} · Investig…\n")
    return {
        "status": "codex dead=0 status=\n", "status-final": "codex dead=0 status=\n",
        "terminal": terminal, "terminal-final": terminal,
        "capture": capture, "capture-final": capture,
        "calls": calls, "calls-final": calls,
        "events": events, "events-final": events,
        "audit": audit, "audit-final": audit,
    }


def test_terminal_proof_accepts_owned_idle_live_bash_with_empty_status_and_exit_marker():
    assert phase2_cli_adapter._validate_terminal_proof(_terminal_proof_bodies(), _terminal_context()) == {
        "pane_state": "idle-live-bash", "exit_status": 0, "session_receipt": "a" * 64,
    }


def test_terminal_proof_rejects_active_pane_and_active_model_receipt():
    with pytest.raises(phase2_cli_adapter.AdapterError, match="owned bash pane or idle Codex composer"):
        phase2_cli_adapter._validate_terminal_proof(
            _terminal_proof_bodies(command="python3"), _terminal_context(),
        )
    with pytest.raises(phase2_cli_adapter.AdapterError, match="is active"):
        phase2_cli_adapter._validate_terminal_proof(
            _terminal_proof_bodies(active=True), _terminal_context(),
        )


@pytest.mark.parametrize("marker", ["no marker", "Cortex live-dev exit=1", "Cortex live-dev exit=0\nCortex live-dev exit=0"])
def test_terminal_proof_rejects_missing_failed_or_forged_duplicate_marker(marker):
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exactly one current successful"):
        phase2_cli_adapter._validate_terminal_proof(
            _terminal_proof_bodies(marker=marker), _terminal_context(),
        )


def test_terminal_proof_rejects_stale_marker_from_foreign_session_receipt():
    context = _terminal_context()
    context["tmux_session_id"] = "$2"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="owned session receipt"):
        phase2_cli_adapter._validate_terminal_proof(_terminal_proof_bodies(), context)


def test_terminal_proof_rejects_child_process_and_status_process_race():
    child = [{"pid": 1235, "ppid": 1234, "command": "codex"}]
    with pytest.raises(phase2_cli_adapter.AdapterError, match="child process remains"):
        phase2_cli_adapter._validate_terminal_proof(
            _terminal_proof_bodies(descendants=child), _terminal_context(),
        )
    raced = _terminal_proof_bodies()
    raced["status-final"] = "codex dead=0 status=\n"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="changed during evidence collection"):
        phase2_cli_adapter._validate_terminal_proof(raced, _terminal_context())


def test_run8_idle_composer_is_terminal_without_exit_marker():
    assert phase2_cli_adapter._validate_terminal_proof(
        _idle_composer_bodies(), _terminal_context(),
    ) == {
        "pane_state": "idle-live-codex-composer", "exit_status": None,
        "session_receipt": "a" * 64,
    }


def test_idle_composer_rejects_active_model_and_open_wait():
    with pytest.raises(phase2_cli_adapter.AdapterError, match="is active|exec session or cell"):
        phase2_cli_adapter._validate_terminal_proof(
            _idle_composer_bodies(active=True), _terminal_context(),
        )


@pytest.mark.parametrize("field", ["outcome", "host_status"])
def test_idle_composer_rejects_explicit_active_wait_state(field):
    bodies = _idle_composer_bodies()
    rows = [json.loads(line) for line in bodies["calls"].splitlines()]
    rows[0][field] = "active"
    calls = "".join(json.dumps(row) + "\n" for row in rows)
    bodies["calls"] = bodies["calls-final"] = calls
    with pytest.raises(phase2_cli_adapter.AdapterError, match="activity is active"):
        phase2_cli_adapter._validate_terminal_proof(bodies, _terminal_context())


def test_idle_composer_rejects_wait_without_explicit_completion_receipt():
    bodies = _idle_composer_bodies()
    rows = [json.loads(line) for line in bodies["calls"].splitlines()]
    rows[0].pop("completed_timestamp")
    calls = "".join(json.dumps(row) + "\n" for row in rows)
    bodies["calls"] = bodies["calls-final"] = calls
    with pytest.raises(phase2_cli_adapter.AdapterError, match="explicitly completed terminal wait"):
        phase2_cli_adapter._validate_terminal_proof(bodies, _terminal_context())


def test_idle_composer_rejects_terminal_text_with_open_worker():
    with pytest.raises(phase2_cli_adapter.AdapterError, match="worker lacks a terminal result"):
        phase2_cli_adapter._validate_terminal_proof(
            _idle_composer_bodies(completed_worker=False), _terminal_context(),
        )


def test_idle_composer_rejects_foreign_or_duplicate_final_report_events():
    foreign = _idle_composer_bodies()
    event = json.loads(foreign["events"])
    event["report_id"] = "r_bbbbbbbbbbbb"
    foreign["events"] = foreign["events-final"] = json.dumps(event) + "\n"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact owned coordinator final report event"):
        phase2_cli_adapter._validate_terminal_proof(foreign, _terminal_context())

    duplicate = _idle_composer_bodies()
    duplicate["events"] += duplicate["events"]
    duplicate["events-final"] = duplicate["events"]
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact owned coordinator final report event"):
        phase2_cli_adapter._validate_terminal_proof(duplicate, _terminal_context())


@pytest.mark.parametrize(("target", "deletes", "replacements"), [
    ("call", ("report_id",), {}),
    ("call", ("draft_id",), {}),
    ("call", ("report_id", "draft_id"), {}),
    ("call", (), {"report_id": ""}),
    ("call", (), {"draft_id": ""}),
    ("call", (), {"report_id": 7}),
    ("call", (), {"draft_id": ["d_0123456789ab"]}),
    ("call", (), {"report_id": "r_not-hex"}),
    ("call", (), {"draft_id": "d_short"}),
    ("event", ("report_id",), {}),
    ("event", ("draft_id",), {}),
    ("event", ("report_id", "draft_id"), {}),
    ("event", (), {"report_id": ""}),
    ("event", (), {"draft_id": ""}),
    ("event", (), {"report_id": 7}),
    ("event", (), {"draft_id": ["d_0123456789ab"]}),
    ("event", (), {"report_id": "r_not-hex"}),
    ("event", (), {"draft_id": "d_short"}),
    ("event", (), {"report_id": "r_bbbbbbbbbbbb"}),
    ("event", (), {"draft_id": "d_bbbbbbbbbbbb"}),
])
def test_idle_composer_rejects_missing_empty_wrong_type_or_mismatched_report_identity(
        target, deletes, replacements):
    bodies = _idle_composer_bodies()
    if target == "call":
        rows = [json.loads(line) for line in bodies["calls"].splitlines()]
        row = rows[-1]
        for key in deletes:
            row.pop(key)
        row.update(replacements)
        value = "".join(json.dumps(item) + "\n" for item in rows)
        bodies["calls"] = bodies["calls-final"] = value
    else:
        row = json.loads(bodies["events"])
        for key in deletes:
            row.pop(key)
        row.update(replacements)
        value = json.dumps(row) + "\n"
        bodies["events"] = bodies["events-final"] = value
    with pytest.raises(
        phase2_cli_adapter.AdapterError,
        match="coordinator's final report receipt|exact owned coordinator final report event",
    ):
        phase2_cli_adapter._validate_terminal_proof(bodies, _terminal_context())


def test_idle_composer_rejects_changing_capture_and_spoofed_composer():
    changed = _idle_composer_bodies()
    changed["capture-final"] += "new progress\n"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="changed during evidence collection"):
        phase2_cli_adapter._validate_terminal_proof(changed, _terminal_context())
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact idle Codex composer"):
        phase2_cli_adapter._validate_terminal_proof(
            _idle_composer_bodies(spoofed=True), _terminal_context(),
        )


def test_idle_composer_rejects_pid_ownership_race():
    raced = _idle_composer_bodies()
    final = json.loads(raced["terminal-final"])
    final["pane_pid"] = 9999
    raced["terminal-final"] = json.dumps(final, sort_keys=True, separators=(",", ":")) + "\n"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="changed during evidence collection"):
        phase2_cli_adapter._validate_terminal_proof(raced, _terminal_context())


def test_idle_composer_collects_then_bundle_authorized_interrupt_stop(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    harness = tmp_path / "harness"
    harness.write_text("stub")
    actions = []
    namespace, state = _fake_live_namespace(tmp_path, actions, composer=True)
    original = namespace["main"]

    def main():
        action = __import__("sys").argv[1]
        if action.startswith("stop"):
            actions.append((action, tuple(__import__("sys").argv[2:])))
            return 0
        return original()

    namespace["main"] = main
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: namespace)
    prefix = ["--control-record", str(record), "--arm", "candidate"]
    phase2_cli_adapter._begin_cell(namespace, record, "candidate", "cell-008")
    phase2_cli_adapter._mark_submitting(namespace, record, "candidate")
    phase2_cli_adapter._mark_submitted(namespace, record, "candidate")
    evidence = (tmp_path / "composer-evidence").resolve()
    assert phase2_cli_adapter.main([*prefix, "collect-evidence", "--evidence-dir", str(evidence)]) == 0
    assert phase2_cli_adapter.main([
        *prefix, "stop", "--evidence-dir", str(evidence), "--interrupt",
    ]) == 0
    assert actions[-1][0] == "stop-exact" and actions[-1][1][0] == "--interrupt"
    authorization = phase2_cli_adapter._read_authorization(
        phase2_cli_adapter._session_context(namespace),
    )
    assert authorization["status"] == "consumed"
    assert state["workdir"] in (evidence / "capture.txt").read_text()


def test_terminal_proof_is_rechecked_against_bundle_immediately_before_stop(tmp_path):
    directory = tmp_path / "evidence"
    directory.mkdir()
    bodies = _terminal_proof_bodies()
    (directory / "status-final.txt").write_text(bodies["status-final"])
    (directory / "terminal-final.json").write_text(bodies["terminal-final"])
    actions = []
    namespace, _ = _fake_live_namespace(tmp_path, actions)
    original = namespace["main"]

    def raced_main():
        if __import__("sys").argv[1] == "status":
            print("codex dead=0 status=")
            return 0
        return original()

    namespace["main"] = raced_main
    with pytest.raises(phase2_cli_adapter.AdapterError, match="changed before stop"):
        phase2_cli_adapter._revalidate_terminal_before_stop(
            namespace, tmp_path / "harness", directory,
            phase2_cli_adapter._session_context(namespace),
        )
    assert all(action != "stop" for action, _ in actions)


def test_cross_cell_bundle_replay_is_refused_for_same_control_and_arm(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    actions = []
    namespace, state = _fake_live_namespace(tmp_path, actions)
    authorization, context = _submitted_authorization(namespace, record, "baseline", "cell-001")
    evidence = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "cell-001-evidence").resolve()))
    phase2_cli_adapter._collect_evidence(
        namespace, tmp_path / "harness", evidence, record, "baseline", authorization, context,
    )
    old_authorization = phase2_cli_adapter._read_authorization(context)
    old_authorization["status"] = "consumed"
    phase2_cli_adapter._atomic_write(
        phase2_cli_adapter._authorization_path(context),
        json.dumps(old_authorization, indent=2, sort_keys=True) + "\n",
    )

    second_workdir = tmp_path / "workdir-002"
    second_workdir.mkdir(mode=0o700)
    (second_workdir / ".codex/cortex").mkdir(parents=True, mode=0o700)
    state.update({
        "workdir": str(second_workdir),
        "store": str(second_workdir / ".codex/cortex/cortex.sqlite3"),
        "started_at": 2000.0,
        "thread_created_since": 2000.0,
        "first_submission_at": 2001.0,
    })
    second_fields = {key: state[key] for key in (
        "workdir", "store", "events", "started_at", "thread_created_since",
        "tmux_server_pid", "tmux_session_name", "tmux_session_id", "tmux_session_created",
        "tmux_pane_id", "tmux_pane_pid", "tmux_pane_start_ticks",
    )}
    state["session_receipt"] = hashlib.sha256(
        json.dumps(second_fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    second_binding = namespace["STATE"] / (
        f"phase2-session-binding-{state['phase2_control_sha256']}-{state['session_receipt']}.json"
    )
    second_binding.write_text(json.dumps({
        "schema_version": phase2_cli_adapter.SESSION_BINDING_SCHEMA,
        "control_record_sha256": state["phase2_control_sha256"],
        **second_fields,
        "session_receipt": state["session_receipt"],
    }) + "\n")
    second_binding.chmod(0o600)
    _submitted_authorization(namespace, record, "baseline", "cell-002")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="current cell authorization|authorization digest"):
        phase2_cli_adapter._validate_evidence_bundle(str(evidence), record, "baseline", namespace)


def test_cell_authorization_allows_one_submission_collection_and_stop(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    actions = []
    namespace, _ = _fake_live_namespace(tmp_path, actions)
    issued = phase2_cli_adapter._begin_cell(namespace, record, "candidate", "cell-012")
    assert issued["status"] == "issued"
    assert len(issued["nonce"]) == 64
    phase2_cli_adapter._mark_submitting(namespace, record, "candidate")
    phase2_cli_adapter._mark_submitted(namespace, record, "candidate")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be issued"):
        phase2_cli_adapter._mark_submitting(namespace, record, "candidate")
    authorization, context = phase2_cli_adapter._require_authorization(
        namespace, record, "candidate", "submitted",
    )
    evidence = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "evidence").resolve()))
    phase2_cli_adapter._collect_evidence(
        namespace, tmp_path / "harness", evidence, record, "candidate", authorization, context,
    )
    _, collected, collected_context = phase2_cli_adapter._validate_evidence_bundle(
        str(evidence), record, "candidate", namespace,
    )
    collected["status"] = "consumed"
    phase2_cli_adapter._atomic_write(
        phase2_cli_adapter._authorization_path(collected_context),
        json.dumps(collected, indent=2, sort_keys=True) + "\n",
    )
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be collected"):
        phase2_cli_adapter._validate_evidence_bundle(str(evidence), record, "candidate", namespace)


def test_adapter_end_to_end_records_exact_send_receipt_then_collects_and_stops(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    harness = tmp_path / "harness"
    harness.write_text("stub")
    actions = []
    namespace, state = _adapter_flow_namespace(tmp_path, actions)
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: namespace)
    prefix = ["--control-record", str(record), "--arm", "candidate"]

    assert phase2_cli_adapter.main([*prefix, "begin-cell", "--cell-id", "cell-007"]) == 0
    issued = phase2_cli_adapter._read_authorization(phase2_cli_adapter._session_context(namespace))
    assert issued["status"] == "issued"
    assert issued["submission_request_sha256"] is None
    assert issued["submitted_at"] is None
    assert issued["submission_receipt"] is None

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("exact prompt\n")
    assert phase2_cli_adapter.main([*prefix, "send", "--prompt-file", str(prompt)]) == 0
    submitted, _ = phase2_cli_adapter._require_authorization(namespace, record.resolve(), "candidate", "submitted")
    assert submitted["submission_request_sha256"] == state["original_request_sha256"] == "a" * 64
    assert submitted["submitted_at"] == state["first_submission_at"] == 1001.25
    assert submitted["submission_receipt"] == phase2_cli_adapter._submission_receipt(
        submitted["nonce"], submitted["submission_request_sha256"], submitted["submitted_at"],
        submitted["submission_native_user_turn"],
    )

    evidence = (tmp_path / "evidence").resolve()
    assert phase2_cli_adapter.main([*prefix, "collect-evidence", "--evidence-dir", str(evidence)]) == 0
    assert phase2_cli_adapter.main([*prefix, "stop", "--evidence-dir", str(evidence)]) == 0
    consumed = phase2_cli_adapter._read_authorization(phase2_cli_adapter._session_context(namespace))
    assert consumed["status"] == "consumed"
    assert actions[0] == ("send-preflight", ("--prompt-file", str(prompt)))
    assert actions[1] == ("send", ("--prompt-file", str(prompt)))
    assert actions[-1][0] == "stop-exact"


def test_adapter_exact_stop_substitution_failure_does_not_consume_authorization(monkeypatch, tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    harness = tmp_path / "harness"
    harness.write_text("stub")
    actions = []
    namespace, state = _adapter_flow_namespace(tmp_path, actions, stop_failure=True)
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: namespace)
    prefix = ["--control-record", str(record), "--arm", "candidate"]
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("exact prompt\n")
    evidence = (tmp_path / "evidence").resolve()

    assert phase2_cli_adapter.main([*prefix, "begin-cell", "--cell-id", "cell-007"]) == 0
    assert phase2_cli_adapter.main([*prefix, "send", "--prompt-file", str(prompt)]) == 0
    assert phase2_cli_adapter.main([*prefix, "collect-evidence", "--evidence-dir", str(evidence)]) == 0
    with pytest.raises(RuntimeError, match="exact-target substitution"):
        phase2_cli_adapter.main([*prefix, "stop", "--evidence-dir", str(evidence)])

    authorization = phase2_cli_adapter._read_authorization(phase2_cli_adapter._session_context(namespace))
    assert authorization["status"] == "collected"
    assert actions[-1][0] == "stop-exact"


def test_common_harness_successful_send_replaces_null_submission_timestamp(monkeypatch, tmp_path):
    import sys
    import types

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_send_regression")
    main = harness["main"]
    namespace = main.__globals__
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("exact prompt\n")
    state = {
        "resumed": False,
        "original_request_sha256": "a" * 64,
        "first_submission_at": None,
        "phase2_control_sha256": "c" * 64,
        "session_receipt": "d" * 64,
        "workdir": str(tmp_path),
        "tmux_session_id": "$1",
        "tmux_pane_id": "%1",
    }
    saved = []
    receipts = iter(([], [_native_turn()]))
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr(sys, "argv", ["cortex-live-smoke", "send", "--prompt-file", str(prompt)])
    monkeypatch.setitem(namespace, "PHASE2_CONTROL_SHA256", "c" * 64)
    monkeypatch.setitem(namespace, "state", lambda: state)
    monkeypatch.setitem(namespace, "save", lambda value: saved.append(copy.deepcopy(value)))
    monkeypatch.setitem(namespace, "STATE", state_root)
    monkeypatch.setitem(namespace, "native_user_turn_receipts", lambda *_: next(receipts))
    monkeypatch.setitem(namespace, "_require_trust_receipt", lambda *_: {"status": "accepted"})
    monkeypatch.setitem(namespace, "_require_empty_composer", lambda *_: "empty")
    monkeypatch.setitem(namespace, "tmux", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setitem(namespace, "time", types.SimpleNamespace(sleep=lambda _: None, time=lambda: 1001.25))

    assert main() is None
    assert state["first_submission_at"] == 1001.25
    assert state["native_user_turn_receipt"] == _native_turn()
    assert saved[-1]["first_submission_at"] == 1001.25


def test_phase2_send_with_missing_control_session_fields_still_rejects(monkeypatch, tmp_path):
    import sys

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_missing_identity")
    main = harness["main"]; namespace = main.__globals__
    prompt = tmp_path / "prompt.txt"; prompt.write_text("exact prompt\n")
    state = {
        "resumed": False, "original_request_sha256": "a" * 64,
        "first_submission_at": None, "workdir": str(tmp_path),
        "tmux_pane_id": "%1", "started_at": 1000.0,
    }
    sent = []
    monkeypatch.setattr(sys, "argv", ["cortex-live-smoke", "send", "--prompt-file", str(prompt)])
    monkeypatch.setitem(namespace, "PHASE2_CONTROL_SHA256", "c" * 64)
    monkeypatch.setitem(namespace, "state", lambda: state)
    monkeypatch.setitem(namespace, "tmux", lambda *args, **kwargs: sent.append(args))
    with pytest.raises(RuntimeError, match="requires exact control and session identities"):
        main()
    assert sent == []


def test_native_user_turn_parser_accepts_current_and_old_formats():
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_turn_formats")
    parse = harness["_user_turn_text"]
    assert parse({"payload": {"type": "message", "role": "user", "id": "m1",
                              "content": [{"type": "input_text", "text": "exact"}]}}) == (
        "exact", "response-item-message-v1", "m1",
    )
    assert parse({"payload": {"type": "user_message", "id": "u1", "message": "exact"}}) == (
        "exact", "event-user-message-v1", "u1",
    )


def test_native_user_turn_rejects_unrelated_same_cwd_root_without_owned_process_link(monkeypatch, tmp_path):
    import sqlite3

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".cortex-dev/.codex"
    sessions = home / "sessions/2026/09/11"
    sessions.mkdir(parents=True)
    bound = sessions / "rollout-bound.jsonl"
    unrelated = sessions / "rollout-unrelated.jsonl"

    def lines(thread_id, text):
        return "\n".join((
            json.dumps({"type": "session_meta", "payload": {
                "id": thread_id, "cwd": str(tmp_path), "parent_thread_id": None,
            }}),
            json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "user", "id": f"msg-{thread_id}",
                "content": [{"type": "input_text", "text": text}],
            }}),
        )) + "\n"

    bound.write_text(lines("thread-bound", "different prompt"))
    unrelated.write_text(lines("thread-unrelated", "exact prompt"))
    with sqlite3.connect(home / "state_5.sqlite") as database:
        database.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, created_at INTEGER, cwd TEXT)")
        database.executemany("INSERT INTO threads VALUES (?, ?, ?, ?)", (
            ("thread-bound", str(bound), 1001, str(tmp_path)),
            ("thread-unrelated", str(unrelated), 1001, str(tmp_path)),
        ))

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_owned_turn")
    reader = harness["native_user_turn_receipts"]
    bound_info = bound.stat()
    linkage = {bound.resolve(): {
        "owned_codex_pid": 4321, "owned_codex_start_ticks": 987,
        "owned_rollout_descriptors": [{
            "fd": 9, "device": bound_info.st_dev, "inode": bound_info.st_ino,
        }],
    }}
    monkeypatch.setitem(reader.__globals__, "_owned_native_rollout_sources", lambda _data: linkage)
    data = {"workdir": str(tmp_path), "started_at": 1000, "thread_created_since": 1000,
            "phase2_control_sha256": "c" * 64, "session_receipt": "d" * 64}
    assert reader(data, "exact prompt") == []

    bound.write_text(lines("thread-bound", "exact prompt"))
    receipt = reader(data, "exact prompt")
    assert len(receipt) == 1
    assert receipt[0]["thread_id"] == "thread-bound"
    assert receipt[0]["control_record_sha256"] == "c" * 64
    assert receipt[0]["session_receipt"] == "d" * 64
    assert receipt[0]["prompt_sha256"] == hashlib.sha256(b"exact prompt").hexdigest()


@pytest.mark.parametrize("churn", ["added", "removed", "retargeted"])
def test_native_user_turn_rejects_post_enumeration_descriptor_topology_churn(
        monkeypatch, tmp_path, churn):
    import sqlite3

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".cortex-dev/.codex"
    sessions = home / "sessions/2026/09/11"
    sessions.mkdir(parents=True)
    bound = sessions / "rollout-bound.jsonl"
    second = sessions / "rollout-second.jsonl"
    body = "\n".join((
        json.dumps({"type": "session_meta", "payload": {
            "id": "thread-bound", "cwd": str(tmp_path), "parent_thread_id": None,
        }}),
        json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user", "id": "msg-bound",
            "content": [{"type": "input_text", "text": "exact prompt"}],
        }}),
    )) + "\n"
    bound.write_text(body); second.write_text(body)
    with sqlite3.connect(home / "state_5.sqlite") as database:
        database.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, created_at INTEGER, cwd TEXT)")
        database.execute("INSERT INTO threads VALUES (?, ?, ?, ?)",
                         ("thread-bound", str(bound), 1001, str(tmp_path)))
    info = bound.stat()
    initial = {bound.resolve(): {
        "owned_codex_pid": 4321, "owned_codex_start_ticks": 987,
        "owned_rollout_descriptors": [{"fd": 9, "device": info.st_dev, "inode": info.st_ino}],
    }}
    if churn == "added":
        other = second.stat()
        final = {**initial, second.resolve(): {
            "owned_codex_pid": 4321, "owned_codex_start_ticks": 987,
            "owned_rollout_descriptors": [{"fd": 10, "device": other.st_dev, "inode": other.st_ino}],
        }}
    elif churn == "removed":
        final = {}
    else:
        final = {bound.resolve(): {
            **initial[bound.resolve()],
            "owned_rollout_descriptors": [{"fd": 9, "device": info.st_dev, "inode": info.st_ino + 1}],
        }}
    snapshots = iter((initial, final))
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name=f"phase2_fd_{churn}")
    reader = harness["native_user_turn_receipts"]
    monkeypatch.setitem(reader.__globals__, "_owned_native_rollout_sources", lambda _data: next(snapshots))
    data = {"workdir": str(tmp_path), "started_at": 1000, "thread_created_since": 1000,
            "phase2_control_sha256": "c" * 64, "session_receipt": "d" * 64}
    with pytest.raises(RuntimeError, match="descriptor topology changed"):
        reader(data, "exact prompt")


def test_native_user_turn_wait_handles_delay_missing_and_ambiguity(monkeypatch):
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_turn_wait")
    wait = harness["_native_send_receipt"]
    globals_ = wait.__globals__
    delayed = iter(([], [], [_native_turn()]))
    monkeypatch.setitem(globals_, "native_user_turn_receipts", lambda *_: next(delayed))
    monkeypatch.setitem(globals_, "time", type("Clock", (), {"sleep": staticmethod(lambda _: None)})())
    assert wait({}, "exact", wait=True) == _native_turn()
    monkeypatch.setitem(globals_, "native_user_turn_receipts", lambda *_: [])
    assert wait({}, "exact", wait=False) is None
    monkeypatch.setitem(globals_, "native_user_turn_receipts", lambda *_: [_native_turn(), _native_turn("other")])
    with pytest.raises(RuntimeError, match="ambiguous or duplicate"):
        wait({}, "exact", wait=False)


def test_empty_composer_rejects_historical_placeholder_before_active_text(monkeypatch):
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_active_composer")
    require = harness["_require_empty_composer"]
    monkeypatch.setitem(require.__globals__, "_rendered_screen", lambda _data: (
        "› Ask Codex to do anything\n  gpt-5.6-luna high · old\n"
        "assistant output\n› unrelated unsent text\n  gpt-5.6-luna high · current\n"
    ))
    with pytest.raises(RuntimeError, match="exact empty Codex composer"):
        require({})


def test_accept_trust_sends_at_most_one_named_enter_then_seals_composer(monkeypatch, tmp_path):
    import types

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_accept_trust")
    accept = harness["_accept_trust"]
    globals_ = accept.__globals__
    state_root = tmp_path / "state"; state_root.mkdir()
    data = {"phase2_control_sha256": "c" * 64, "session_receipt": "d" * 64,
            "workdir": str(tmp_path), "tmux_pane_id": "%1"}
    sent = []
    trusted = {"value": False}

    def fake_tmux(*args, **_kwargs):
        if args[0] == "capture-pane":
            body = ("› Ask Codex to do anything\n" if trusted["value"]
                    else "Do you trust the contents of this directory?\nPress enter to continue\n")
            return subprocess.CompletedProcess(args, 0, body, "")
        sent.append(args)
        trusted["value"] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setitem(globals_, "STATE", state_root)
    monkeypatch.setitem(globals_, "tmux", fake_tmux)
    monkeypatch.setitem(globals_, "PHASE2_COMPOSER_SETTLE_SECONDS", 0)
    monkeypatch.setitem(globals_, "PHASE2_COMPOSER_SETTLE_SAMPLES", 1)
    monkeypatch.setitem(globals_, "time", types.SimpleNamespace(
        monotonic=lambda: 1.0, sleep=lambda _: None, time=lambda: 2.0,
    ))
    receipt = accept(data)
    assert receipt["status"] == "accepted" and receipt["enter_count"] == 1
    assert sent == [("send-keys", "-t", "%1", "Enter")]

    data = {**data, "session_receipt": "e" * 64}
    trusted["value"] = True; sent.clear()
    receipt = accept(data)
    assert receipt["status"] == "not-required" and receipt["enter_count"] == 0
    assert sent == []

    data = {**data, "session_receipt": "f" * 64}
    trusted["value"] = False; sent.clear()
    def stale_trust_tmux(*args, **_kwargs):
        if args[0] == "capture-pane":
            body = ("Do you trust the contents of this directory?\n"
                    "Press enter to continue\n› Ask Codex to do anything\n")
            return subprocess.CompletedProcess(args, 0, body, "")
        sent.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setitem(globals_, "tmux", stale_trust_tmux)
    receipt = accept(data)
    assert receipt["status"] == "not-required" and receipt["enter_count"] == 0
    assert sent == []


def test_phase2_startup_waits_through_transient_composer_for_delayed_trust(monkeypatch, tmp_path):
    import types
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="delayed_trust")
    accept = harness["_accept_trust"]
    globals_ = accept.__globals__
    state_root = tmp_path / "state"; state_root.mkdir()
    data = {"phase2_control_sha256": "1" * 64, "session_receipt": "2" * 64,
            "workdir": str(tmp_path), "tmux_pane_id": "%1"}
    screens = iter(("› Ask Codex to do anything\n", "› Ask Codex to do anything\n",
                    "Do you trust the contents of this directory?\nPress enter to continue\n",
                    "› Ask Codex to do anything\n"))
    sent = []
    def tmux(*args, **_kwargs):
        if args[0] == "capture-pane":
            return subprocess.CompletedProcess(args, 0, next(screens), "")
        sent.append(args); return subprocess.CompletedProcess(args, 0, "", "")
    ticks = iter(x / 10 for x in range(1000))
    monkeypatch.setitem(globals_, "STATE", state_root)
    monkeypatch.setitem(globals_, "tmux", tmux)
    monkeypatch.setitem(globals_, "time", types.SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda _: None, time=lambda: 7.0))
    receipt = accept(data)
    assert receipt["status"] == "accepted" and receipt["enter_count"] == 1
    assert sent == [("send-keys", "-t", "%1", "Enter")]


def test_phase2_startup_timeout_and_duplicate_emit_no_input(monkeypatch, tmp_path):
    import types
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="startup_timeout")
    accept = harness["_accept_trust"]
    globals_ = accept.__globals__
    state_root = tmp_path / "state"; state_root.mkdir()
    data = {"phase2_control_sha256": "3" * 64, "session_receipt": "4" * 64,
            "workdir": str(tmp_path), "tmux_pane_id": "%4"}
    sent = []
    monkeypatch.setitem(globals_, "STATE", state_root)
    monkeypatch.setitem(globals_, "tmux", lambda *args, **_kwargs: (
        subprocess.CompletedProcess(args, 0, "Loading…\n", "") if args[0] == "capture-pane"
        else sent.append(args)))
    ticks = iter(range(1000))
    monkeypatch.setitem(globals_, "time", types.SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda _: None, time=lambda: 8.0))
    with pytest.raises(RuntimeError, match="neither trust prompt nor empty composer"):
        accept(data)
    assert sent == [] and not list(state_root.glob("phase2-trust-receipt-*.json"))
    path = globals_["_phase2_trust_receipt_path"](data)
    path.write_text(json.dumps({"already": True})); path.chmod(0o600)
    with pytest.raises(RuntimeError, match="already exists"):
        accept(data)
    assert sent == []


def test_phase2_startup_rejects_session_replacement(monkeypatch, tmp_path):
    contexts = iter(({"session_receipt": "a" * 64}, {"session_receipt": "b" * 64}))
    monkeypatch.setattr(phase2_cli_adapter, "_session_context", lambda _namespace: next(contexts))
    monkeypatch.setattr(phase2_cli_adapter, "_invoke_harness", lambda *_args: "")
    monkeypatch.setattr(phase2_cli_adapter, "_require_trust_ready",
                        lambda _namespace: {"status": "accepted"})
    with pytest.raises(phase2_cli_adapter.AdapterError, match="identity changed"):
        phase2_cli_adapter._complete_phase2_startup({}, tmp_path / "harness")


def test_resolve_send_recovers_accepted_timeout_without_transport(monkeypatch, tmp_path):
    import sys
    import types

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_resolve_send")
    main = harness["main"]
    namespace = main.__globals__
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("exact prompt\n")
    state_root = tmp_path / "state"; state_root.mkdir()
    state = {
        "resumed": False, "original_request_sha256": "a" * 64,
        "first_submission_at": None, "phase2_control_sha256": "c" * 64,
        "session_receipt": "d" * 64, "workdir": str(tmp_path),
    }
    intent = {
        "schema_version": "phase2-cli-native-user-turn-receipt-v1", "status": "enter-requested",
        "control_record_sha256": "c" * 64, "session_receipt": "d" * 64,
        "workdir": str(tmp_path), "request_sha256": "a" * 64,
        "prompt_sha256": hashlib.sha256(b"exact prompt").hexdigest(),
        "prepared_at": 1000.0, "enter_requested_at": 1001.0, "native_user_turn": None,
    }
    path = state_root / f"phase2-send-receipt-{'c' * 64}-{'d' * 64}.json"
    path.write_text(json.dumps(intent) + "\n"); path.chmod(0o600)
    saved = []
    monkeypatch.setattr(sys, "argv", ["cortex-live-smoke", "resolve-send", "--prompt-file", str(prompt)])
    monkeypatch.setitem(namespace, "PHASE2_CONTROL_SHA256", "c" * 64)
    monkeypatch.setitem(namespace, "STATE", state_root)
    monkeypatch.setitem(namespace, "state", lambda: state)
    monkeypatch.setitem(namespace, "save", lambda value: saved.append(copy.deepcopy(value)))
    monkeypatch.setitem(namespace, "native_user_turn_receipts", lambda *_: [_native_turn()])
    monkeypatch.setitem(namespace, "time", types.SimpleNamespace(sleep=lambda _: None, time=lambda: 1002.0))
    monkeypatch.setitem(namespace, "tmux", lambda *_args, **_kwargs: pytest.fail("resolve-send resent input"))
    assert main() is None
    assert state["first_submission_at"] == 1001.0
    assert state["native_user_turn_receipt"] == _native_turn()
    assert json.loads(path.read_text())["status"] == "accepted"


def test_common_harness_keeps_owned_bash_for_terminal_snapshot(monkeypatch):
    import types

    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_terminal_regression")
    terminal_snapshot = harness["terminal_snapshot"]
    namespace = terminal_snapshot.__globals__
    monkeypatch.setitem(namespace, "tmux", lambda *_args, **_kwargs: types.SimpleNamespace(
        stdout="$1|%1|1234|bash|0|\n",
    ))
    monkeypatch.setitem(namespace, "subprocess", types.SimpleNamespace(run=lambda *_args, **_kwargs: types.SimpleNamespace(
        stdout="1234 1 bash\n",
    )))
    assert terminal_snapshot({"tmux_session_id": "$1", "tmux_pane_id": "%1", "tmux_pane_pid": 1234}) == {
        "schema_version": "cortex-live-terminal-snapshot-v1", "session_id": "$1",
        "pane_id": "%1", "pane_pid": 1234, "current_command": "bash",
        "dead": False, "dead_status": None, "descendants": [],
    }
    source = (ROOT / "scripts/cortex-live-smoke").read_text()
    assert "printf 'Cortex live-dev exit=%s\\\\n'" in source
    assert "; exit \\\"$live_status\\\"" not in source


def test_common_harness_tmux_snapshot_uses_literal_stable_separator():
    source = (ROOT / "scripts/cortex-live-smoke").read_text()
    assert "format_string='#{session_id}|#{pane_id}|#{pane_pid}|" in source
    assert "fields=rows[0].split('|')" in source
    assert "format_string='#{session_id}\\x1f" not in source


def _exact_stop_harness(monkeypatch, tmp_path, *, list_rows, start_ticks=(902,), interrupt=True):
    import sys
    import types

    loaded = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_exact_stop")
    main = loaded["main"]
    globals_ = main.__globals__
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    (state_root / "events").mkdir(mode=0o700)
    (state_root / "capture.txt").write_text("")
    fields = {
        "workdir": str(tmp_path), "store": str(tmp_path / ".codex/cortex/cortex.sqlite3"),
        "events": str(state_root / "events"), "started_at": 1000.0,
        "thread_created_since": 1000.0, "tmux_server_pid": 900,
        "tmux_session_name": "cortex-markdown-smoke", "tmux_session_id": "$1",
        "tmux_session_created": 901, "tmux_pane_id": "%1", "tmux_pane_pid": 1234,
        "tmux_pane_start_ticks": 902,
    }
    receipt = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data = {**fields, "phase2_control_sha256": "c" * 64, "session_receipt": receipt}
    (state_root / "session.json").write_text(json.dumps(data))
    calls = []
    rows = iter(list_rows)

    def fake_tmux(*args, **kwargs):
        calls.append(args)
        if args[0] == "display-message":
            return types.SimpleNamespace(returncode=0, stdout="900\n")
        if args[0] == "list-panes":
            return types.SimpleNamespace(returncode=0, stdout=next(rows) + "\n")
        return types.SimpleNamespace(returncode=0, stdout="")

    ticks = iter(start_ticks)
    monkeypatch.setitem(globals_, "STATE", state_root)
    monkeypatch.setitem(globals_, "tmux", fake_tmux)
    monkeypatch.setitem(globals_, "_proc_start_ticks", lambda _pid: next(ticks))
    exact_context = {**data, "control_record_sha256": data["phase2_control_sha256"]}
    monkeypatch.setattr(sys, "argv", [
        "cortex-live-smoke", "stop-exact",
        *phase2_cli_adapter._exact_stop_arguments(exact_context, interrupt=interrupt),
    ])
    return main, calls, state_root


def test_exact_stop_refuses_session_substitution_before_interrupt(monkeypatch, tmp_path):
    exact = "cortex-markdown-smoke|$1|901|%1|1234"
    replacement = "cortex-markdown-smoke|$2|999|%2|2234"
    main, calls, state_root = _exact_stop_harness(
        monkeypatch, tmp_path, list_rows=(exact, replacement), start_ticks=(902,),
    )
    with pytest.raises(RuntimeError, match="replaced session or pane"):
        main()
    assert not any(call[0] in {"send-keys", "kill-session"} for call in calls)
    assert (state_root / "session.json").exists()


def test_exact_stop_refuses_pane_pid_reuse_before_interrupt(monkeypatch, tmp_path):
    exact = "cortex-markdown-smoke|$1|901|%1|1234"
    main, calls, state_root = _exact_stop_harness(
        monkeypatch, tmp_path, list_rows=(exact,), start_ticks=(999,),
    )
    with pytest.raises(RuntimeError, match="PID reuse"):
        main()
    assert not any(call[0] in {"send-keys", "kill-session"} for call in calls)
    assert (state_root / "session.json").exists()


@pytest.mark.parametrize("replacement", [
    "renamed-smoke|$1|901|%1|1234",
    "cortex-markdown-smoke|$1|999|%1|1234",
])
def test_exact_stop_refuses_renamed_or_recreated_session(monkeypatch, tmp_path, replacement):
    main, calls, _ = _exact_stop_harness(
        monkeypatch, tmp_path, list_rows=(replacement,), start_ticks=(),
    )
    with pytest.raises(RuntimeError, match="replaced session or pane"):
        main()
    assert not any(call[0] in {"send-keys", "kill-session"} for call in calls)


def test_exact_stop_signals_pane_and_kills_session_only_by_sealed_ids(monkeypatch, tmp_path):
    exact = "cortex-markdown-smoke|$1|901|%1|1234"
    main, calls, state_root = _exact_stop_harness(
        monkeypatch, tmp_path, list_rows=(exact, exact, exact, exact),
        start_ticks=(902, 902, 902, 902),
    )
    assert main() is None
    assert ("send-keys", "-t", "%1", "C-c") in calls
    assert ("kill-session", "-t", "=$1") in calls
    assert not any("cortex-markdown-smoke:0.0" in call for call in calls)
    assert not (state_root / "session.json").exists()


def test_normal_exact_stop_skips_interrupt_and_kills_only_sealed_session_id(monkeypatch, tmp_path):
    exact = "cortex-markdown-smoke|$1|901|%1|1234"
    main, calls, state_root = _exact_stop_harness(
        monkeypatch, tmp_path, list_rows=(exact, exact),
        start_ticks=(902, 902), interrupt=False,
    )
    assert main() is None
    assert not any(call[0] == "send-keys" for call in calls)
    assert ("kill-session", "-t", "=$1") in calls
    assert not (state_root / "session.json").exists()


@pytest.mark.parametrize("timestamp", [None, "1001.0", True, float("nan"), 999.0])
def test_submission_timestamp_missing_or_malformed_never_promotes(tmp_path, timestamp):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, state = _fake_live_namespace(tmp_path, [])
    phase2_cli_adapter._begin_cell(namespace, record, "baseline", "cell-003")
    phase2_cli_adapter._mark_submitting(namespace, record, "baseline")
    state["first_submission_at"] = timestamp

    with pytest.raises(phase2_cli_adapter.AdapterError, match="valid submission timestamp"):
        phase2_cli_adapter._mark_submitted(namespace, record, "baseline")
    authorization = phase2_cli_adapter._read_authorization(phase2_cli_adapter._session_context(namespace))
    assert authorization["status"] == "submitting"
    assert authorization["submission_request_sha256"] is None
    assert authorization["submitted_at"] is None
    assert authorization["submission_receipt"] is None


def test_submitted_authorization_rejects_timestamp_and_receipt_inconsistency(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _ = _fake_live_namespace(tmp_path, [])
    authorization, context = _submitted_authorization(namespace, record, "baseline", "cell-005")

    authorization["submitted_at"] += 1
    phase2_cli_adapter._atomic_write(
        phase2_cli_adapter._authorization_path(context),
        json.dumps(authorization, indent=2, sort_keys=True) + "\n",
    )
    with pytest.raises(phase2_cli_adapter.AdapterError, match="inconsistent submission receipt"):
        phase2_cli_adapter._require_authorization(namespace, record, "baseline", "submitted")


@pytest.mark.parametrize("send_exit,crash", [(9, False), (0, True)])
def test_failed_or_uncertain_send_is_non_resubmittable_and_cannot_collect_or_stop(
        monkeypatch, tmp_path, send_exit, crash):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    harness = tmp_path / "harness"
    harness.write_text("stub")
    namespace, _ = _adapter_flow_namespace(tmp_path, [], send_exit=send_exit, crash_after_receipt=crash)
    monkeypatch.setattr(phase2_cli_adapter, "_load_binding", lambda *_: ({}, harness, harness, tmp_path, {}))
    monkeypatch.setattr(phase2_cli_adapter, "_load_harness", lambda *_: namespace)
    prefix = ["--control-record", str(record), "--arm", "baseline"]
    assert phase2_cli_adapter.main([*prefix, "begin-cell", "--cell-id", "cell-006"]) == 0

    send = [*prefix, "send", "--prompt-file", str(tmp_path / "prompt.txt")]
    if crash:
        with pytest.raises(OSError, match="simulated adapter crash window"):
            phase2_cli_adapter.main(send)
    else:
        assert phase2_cli_adapter.main(send) == send_exit
    context = phase2_cli_adapter._session_context(namespace)
    authorization = phase2_cli_adapter._read_authorization(context)
    assert authorization["status"] == "submitting"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be issued"):
        phase2_cli_adapter.main(send)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be submitted"):
        phase2_cli_adapter.main([
            *prefix, "collect-evidence", "--evidence-dir", str((tmp_path / "evidence").resolve()),
        ])
    with pytest.raises(phase2_cli_adapter.AdapterError, match="must be collected"):
        phase2_cli_adapter.main([
            *prefix, "stop", "--evidence-dir", str((tmp_path / "evidence").resolve()),
        ])


def test_current_cell_bundle_copy_rename_and_path_alias_are_refused(tmp_path):
    record = tmp_path / "control.json"
    record.write_text("sealed-control")
    namespace, _ = _fake_live_namespace(tmp_path, [])
    authorization, context = _submitted_authorization(namespace, record, "baseline", "cell-004")
    evidence = phase2_cli_adapter._prepare_evidence_directory(str((tmp_path / "evidence-original").resolve()))
    phase2_cli_adapter._collect_evidence(
        namespace, tmp_path / "harness", evidence, record, "baseline", authorization, context,
    )
    assert phase2_cli_adapter._validate_evidence_bundle(
        str(evidence), record, "baseline", namespace,
    )[0] == evidence

    copied = tmp_path / "evidence-copy"
    shutil.copytree(evidence, copied)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="authorized directory identity"):
        phase2_cli_adapter._validate_evidence_bundle(str(copied), record, "baseline", namespace)

    alias = tmp_path / "evidence-alias"
    alias.symlink_to(evidence, target_is_directory=True)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="canonical directory"):
        phase2_cli_adapter._validate_evidence_bundle(str(alias), record, "baseline", namespace)
    noncanonical = evidence / ".." / evidence.name
    with pytest.raises(phase2_cli_adapter.AdapterError, match="canonical"):
        phase2_cli_adapter._validate_evidence_bundle(str(noncanonical), record, "baseline", namespace)

    renamed = tmp_path / "evidence-renamed"
    evidence.rename(renamed)
    with pytest.raises(phase2_cli_adapter.AdapterError, match="authorized directory identity"):
        phase2_cli_adapter._validate_evidence_bundle(str(renamed), record, "baseline", namespace)


def test_preparer_rejects_stale_embedded_version_even_when_normalized_hash_matches(tmp_path):
    plugin = tmp_path / "cortex"
    shutil.copytree(ROOT / "plugins/cortex", plugin)
    manifest = plugin / ".codex-plugin/plugin.json"
    value = json.loads(manifest.read_text())
    value["version"] = "1.15.8+codex.sha256.9dfe4c2cf790714c"
    manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    assert phase2_cli_runner._payload_digest(plugin, canonical_version="1.15.9") == phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256
    with pytest.raises(phase2_cli_runner.PreparationError, match="archived payload version"):
        phase2_cli_runner._validate_payload_identity(
            plugin,
            expected_version=phase2_cli_runner.CANDIDATE_VERSION,
            expected_digest=phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256,
        )


def test_stale_archive_version_fails_audit_and_preflight_with_outer_hash_unchanged(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    candidate = next(item for item in data["arms"] if item["arm"] == "candidate")
    manifest = Path(candidate["workdir"]) / "plugins/cortex/.codex-plugin/plugin.json"
    value = json.loads(manifest.read_text())
    value["version"] = "1.15.8+codex.sha256.9dfe4c2cf790714c"
    manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    assert candidate["identity"]["payload_sha256"] == phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256
    assert phase2_cli_auditor.payload_digest(manifest.parents[1], "1.15.9") == phase2_cli_runner.CANDIDATE_PAYLOAD_SHA256
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("candidate archived payload version mismatch" in error for error in audited["errors"])
    preflight = _run_adapter(record, "candidate", "preflight")
    assert preflight.returncode != 0
    assert "archived payload version mismatch" in preflight.stderr


def test_archive_rebuild_drift_after_preflight_fails_next_cell_before_launch(tmp_path):
    record = _run_prepare(tmp_path)
    assert _run_adapter(record, "candidate", "preflight").returncode == 0
    data = json.loads(record.read_text())
    candidate = next(item for item in data["arms"] if item["arm"] == "candidate")
    drifted = Path(candidate["workdir"]) / "plugins/cortex/skills/orchestrator/SKILL.md"
    drifted.write_bytes(drifted.read_bytes() + b"\n")

    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("candidate payload digest mismatch" in error for error in audited["errors"])
    workdir = tmp_path / "next-cell"
    workdir.mkdir(mode=0o700)
    start = _run_adapter(
        record, "candidate", "start", "--workdir", str(workdir),
        "--evaluation-fresh-store", "--model", "gpt-5.6-luna", "--effort", "high",
    )
    assert start.returncode != 0
    assert "archived payload digest mismatch" in start.stderr
    assert not (workdir / ".codex").exists()


def test_offline_preparation_and_independent_audit_pass(tmp_path):
    record = _run_prepare(tmp_path)
    result = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_auditor.py"), str(record)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "pass"
    assert parsed["checks"]["live_activity"] == "none"
    assert parsed["checks"]["fixture_tree_access"] == "not performed"


def test_both_arms_use_one_preflighted_fresh_store_launcher(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    launcher = data["neutral"]["live_launcher"]
    assert launcher["mechanism"] == "phase2-common-live-harness-v1"
    assert launcher["fresh_store"] == "required-at-start"
    assert launcher["model"] == "gpt-5.6-luna"
    assert launcher["effort"] == "high"
    assert {arm["live_launcher_mechanism"] for arm in data["arms"]} == {launcher["mechanism"]}
    arm_roots = {arm["arm"]: Path(arm["workdir"]) for arm in data["arms"]}
    baseline_transport = subprocess.check_output(
        ["git", "show", f"{phase2_cli_runner.BASELINE_COMMIT}:scripts/cortex-live-smoke"], cwd=ROOT
    )
    candidate_transport = subprocess.check_output(["git", "show", "HEAD:scripts/cortex-live-smoke"], cwd=ROOT)
    assert (arm_roots["baseline"] / "scripts/cortex-live-smoke").read_bytes() == baseline_transport
    assert (arm_roots["candidate"] / "scripts/cortex-live-smoke").read_bytes() == candidate_transport
    adapter = Path(launcher["adapter_path"])
    for arm in ("baseline", "candidate"):
        arm_record = next(item for item in data["arms"] if item["arm"] == arm)
        arm_root = Path(arm_record["workdir"])
        archived_launcher = arm_root / "scripts/cortex-dev"
        assert archived_launcher.stat().st_mode & 0o777 == 0o600
        assert arm_record["transport_files"]["launcher"] == {
            "relative_path": "scripts/cortex-dev",
            "sha256": phase2_cli_auditor.file_sha256(archived_launcher),
            "mode": "0600",
        }
        result = subprocess.run(
            ["python3", str(adapter), "--control-record", str(record), "--arm", arm, "preflight"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt == {
            "arm": arm,
            "effort": "high",
            "fresh_store": "required-at-start",
            "mechanism": "phase2-common-live-harness-v1",
            "model": "gpt-5.6-luna",
            "status": "pass",
        }
        _, harness_path, observer_path, selected_root, selected = __import__("phase2_cli_adapter")._load_binding(record, arm)
        assert observer_path == Path(launcher["observer_path"])
        namespace = __import__("phase2_cli_adapter")._load_harness(harness_path, observer_path, selected_root, selected)
        command = namespace["launch_command"](tmp_path / "events", False, "gpt-5.6-luna", "high", False, False)
        launcher_index = command.index("/bin/bash")
        assert command[launcher_index + 1] == str(archived_launcher)
        help_result = subprocess.run(["/bin/bash", str(archived_launcher), "--help"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert help_result.returncode == 0, help_result.stderr
        assert "Usage: scripts/cortex-dev" in help_result.stdout


def test_archived_baseline_create_task_accepts_bound_fresh_project_store(tmp_path):
    """Exercise the original failing storage boundary without a live Codex host."""
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    baseline = next(item for item in data["arms"] if item["arm"] == "baseline")
    _, harness_path, observer_path, selected_root, selected = phase2_cli_adapter._load_binding(record, "baseline")
    harness = phase2_cli_adapter._load_harness(harness_path, observer_path, selected_root, selected)
    project = (tmp_path / "offline-create-task").resolve()
    project.mkdir(mode=0o700)
    (project / ".codex/cortex").mkdir(parents=True, mode=0o700)
    (project / ".codex").chmod(0o700)
    command = harness["launch_command"](
        tmp_path / "events", False, "gpt-5.6-luna", "high", False, False,
        False, project,
    )
    storage_assignment = next(part for part in command if part.startswith("CORTEX_DATA_DIR="))
    storage = Path(storage_assignment.split("=", 1)[1])
    script = (
        "import json,os,sys,uuid\n"
        "from cortex_runtime.store import Store\n"
        "project=sys.argv[1]\n"
        "store=Store(os.environ['CORTEX_DATA_DIR'])\n"
        "result=store.call('create_task',"
        "{'project_root':project,'request_key':str(uuid.uuid4())},"
        "str(uuid.uuid4()),original_request='offline Phase 2 storage acceptance')\n"
        "print(json.dumps(result,sort_keys=True))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(baseline["workdir"]) / "plugins/cortex/scripts")
    env["CORTEX_DATA_DIR"] = str(storage)
    result = subprocess.run(
        ["python3", "-B", "-c", script, str(project)], cwd=ROOT, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["replayed"] is False
    assert receipt["original_report_id"].startswith("r_")
    import sqlite3
    with sqlite3.connect(storage / "cortex.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 10


def test_common_harness_normalizes_legacy_and_current_hook_event_schemas(tmp_path, capsys):
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_test_harness")
    mcp = {"event_kind": "mcp", "operation": "initialize", "outcome": "success"}
    hook = {"event_kind": "hook", "hook_event": "Stop", "outcome": "success"}

    assert harness["normalized_observer_events"]({"observed_events": lambda _: [mcp, hook]}, tmp_path) == ([mcp], [hook])
    assert harness["normalized_observer_events"]({
        "observed_events": lambda _: [mcp],
        "observed_hook_events": lambda _: [hook],
    }, tmp_path) == ([mcp], [hook])

    with pytest.raises(harness["ObserverEvidenceError"]) as malformed:
        harness["normalized_observer_events"]({
            "observed_events": lambda _: [mcp],
            "observed_hook_events": "not-callable",
        }, tmp_path)
    assert malformed.value.code == "malformed_observer_capability"
    assert malformed.value.capability == "observed_hook_events"

    for invalid_rows, expected_code in (([], "malformed_observer_evidence"),
                                        ([{"outcome": "success"}], "missing_event_stream_discriminator")):
        with pytest.raises(harness["ObserverEvidenceError"]) as invalid:
            harness["normalized_observer_events"]({"observed_events": lambda _, rows=invalid_rows: rows}, tmp_path)
        assert invalid.value.code == expected_code
        assert invalid.value.capability == "observed_events"
        assert harness["_audit_no_go"](invalid.value) == 1
        no_go = json.loads(capsys.readouterr().out)
        assert no_go == {
            "capability": "observed_events",
            "code": expected_code,
            "diagnostic_type": "phase2-observer-evidence-schema",
            "eligible": False,
            "status": "NO-GO",
        }

    with pytest.raises(harness["ObserverEvidenceError"]) as missing:
        harness["normalized_observer_events"]({}, tmp_path)
    diagnostic = {"status": "NO-GO", "eligible": False,
                  "diagnostic_type": "phase2-observer-evidence-schema",
                  "code": missing.value.code, "capability": missing.value.capability}
    assert diagnostic["code"] == "missing_required_observer_capability"


def test_common_harness_usage_is_scoped_to_exact_coordinator_root(monkeypatch, capsys):
    harness = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="phase2_usage_test")
    globals_ = harness["main"].__globals__
    globals_["PHASE2_CONTROL_SHA256"] = "1" * 64
    globals_["COMMON_OBSERVER_PATH"] = ROOT / "scripts/cortex-desktop-dev"
    globals_["state"] = lambda: {"workdir": "/fixture", "started_at": 1}
    seen = {}
    globals_["_observer_namespace"] = lambda _name: {
        "observed_tool_calls": lambda _state: [
            {"role": "coordinator", "parent_thread_id": None, "thread_id": "root-1"},
            {"role": "general", "parent_thread_id": "root-1", "thread_id": "worker-1"},
        ],
        "participant_token_usage": lambda state: seen.setdefault("state", state) or {},
    }
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/cortex-live-smoke"), "usage"])
    assert harness["main"]() is None
    assert seen["state"]["thread_id"] == "root-1"
    assert json.loads(capsys.readouterr().out)["thread_id"] == "root-1"


def test_common_observer_is_sealed_and_tamper_rejected(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    observer = Path(data["neutral"]["live_launcher"]["observer_path"])
    assert observer.read_bytes() == (ROOT / "scripts/cortex-desktop-dev").read_bytes()
    observer.write_bytes(observer.read_bytes() + b"\n")
    result = phase2_cli_auditor.audit(record)
    assert result["status"] == "fail-closed"
    assert any("observer digest mismatch" in error for error in result["errors"])


def test_common_observer_separates_evidence_integrity_from_quality_symmetrically():
    observer = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="audit_classifier_test")
    classify = observer["classify_audit_findings"]
    complete_error = {"thread_id": "root", "role": "coordinator", "tool": "update_goal",
                      "outcome": "error", "result_digest": "a" * 12}
    rejected_access = {"thread_id": "worker", "parent_thread_id": "root", "role": "explorer",
                       "tool": "exec_command", "outcome": "error",
                       "violation": "forbidden_plugin_or_cache_access",
                       "content_delivery_status": "rejected"}
    valid = classify(failures=[], hook_failures=[], host_failures=[complete_error],
                     policy_violations=[rejected_access], open_sessions=[], open_cells=[])
    assert valid["evidence_valid"] is True and valid["score_eligible"] is True
    assert {row["reason"] for row in valid["quality_findings"]} == {
        "complete_attributable_failure", "rejected_forbidden_access",
    }
    for invalid_row in (
        {**complete_error, "outcome": "truncated"},
        {"thread_id": "worker", "role": "general", "tool": "spawn_agent",
         "outcome": "success", "violation": "worker_assignment_policy_unverified"},
    ):
        invalid = classify(failures=[], hook_failures=[], host_failures=[invalid_row],
                           policy_violations=[], open_sessions=[], open_cells=[])
        if invalid_row.get("violation"):
            invalid = classify(failures=[], hook_failures=[], host_failures=[],
                               policy_violations=[invalid_row], open_sessions=[], open_cells=[])
        assert invalid["evidence_valid"] is False and invalid["score_eligible"] is False
        assert invalid["quality_findings"] == []
    contaminated = classify(failures=[], hook_failures=[], host_failures=[], policy_violations=[
        {**rejected_access, "outcome": "success", "content_delivery_status": "protected_content_returned"}],
        open_sessions=[], open_cells=[])
    assert contaminated["evidence_integrity_invalidators"][0]["classification"] == "protocol_contamination"
    # Both arms call this same content-free helper; arm identity is not an input.
    assert classify(failures=[], hook_failures=[], host_failures=[complete_error],
                    policy_violations=[], open_sessions=[], open_cells=[]) == classify(
                        failures=[], hook_failures=[], host_failures=[complete_error],
                        policy_violations=[], open_sessions=[], open_cells=[])


def test_common_observer_dependency_closure_is_complete_and_sealed(tmp_path):
    source_manifest = phase2_cli_runner._observer_dependency_manifest(ROOT)
    assert "plugins/cortex/profiles.json" in source_manifest
    assert {key for key in source_manifest if key.startswith("plugins/cortex/skills/")} == {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "plugins/cortex/skills").rglob("*") if path.is_file()
    }
    tree = ast.parse((ROOT / "scripts/cortex-desktop-dev").read_text())
    direct_root_operands = {
        node.right.value for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        and isinstance(node.left, ast.Name) and node.left.id == "ROOT"
        and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
    }
    assert direct_root_operands == {
        "plugins/cortex/profiles.json", "plugins/cortex/skills", "scripts/cortex-dev",
    }

    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    launcher = data["neutral"]["live_launcher"]
    assert launcher["observer_bundle_root"] == str(record.parent)
    assert launcher["observer_dependencies"] == source_manifest
    for relative, digest in source_manifest.items():
        copied = record.parent / relative
        assert copied.is_file() and not copied.is_symlink()
        assert copied.stat().st_mode & 0o777 == 0o600
        assert phase2_cli_auditor.file_sha256(copied) == digest
    observer = runpy.run_path(launcher["observer_path"], run_name="sealed_dependency_test")
    assert observer["assigned_worker_profile"]("$cortex:worker-general") == "general"


@pytest.mark.parametrize("mutation", ["missing", "tampered", "extra"])
def test_common_observer_dependency_drift_fails_audit_and_preflight(tmp_path, mutation):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    launcher = data["neutral"]["live_launcher"]
    profile = record.parent / "plugins/cortex/profiles.json"
    if mutation == "missing":
        profile.unlink()
    elif mutation == "tampered":
        profile.write_bytes(profile.read_bytes() + b"\n")
    else:
        extra = record.parent / "plugins/cortex/skills/unsealed.md"
        extra.write_text("unsealed\n")
        extra.chmod(0o600)
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    adapter = Path(launcher["adapter_path"])
    preflight = subprocess.run(
        ["python3", str(adapter), "--control-record", str(record), "--arm", "baseline", "preflight"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert preflight.returncode != 0
    assert "observer dependency" in preflight.stderr


def test_invalid_cell_cleanup_refuses_every_other_control(tmp_path):
    control = tmp_path / "control.json"
    control.write_text("foreign")
    with pytest.raises(phase2_cli_adapter.AdapterError, match="exact authorized NO-GO cell"):
        phase2_cli_adapter._cleanup_invalid_cell(
            {}, tmp_path / "harness", control, "baseline", str(tmp_path),
            str(tmp_path / "cleanup"), "1" * 64,
        )


def test_missing_profiles_invalid_cell_cleanup_requires_exact_failure(tmp_path, monkeypatch):
    control = tmp_path / "control/control-record.json"
    control.parent.mkdir()
    control.write_text("sealed-control")
    monkeypatch.setattr(
        phase2_cli_adapter, "_sha256_file",
        lambda path: (phase2_cli_adapter.MISSING_PROFILES_INVALID_CONTROL_SHA256
                      if Path(path) == control else hashlib.sha256(Path(path).read_bytes()).hexdigest()),
    )
    incident = phase2_cli_adapter.INVALID_CELL_CLEANUP_INCIDENTS[
        phase2_cli_adapter.MISSING_PROFILES_INVALID_CONTROL_SHA256
    ]
    exact = {
        "schema_version": phase2_cli_adapter.EVIDENCE_SCHEMA,
        "status": "incomplete",
        "completed_actions": ["status", "terminal", "capture", "events"],
        "error": ("evidence action calls failed: [Errno 2] No such file or directory: '"
                  + str(control.parent / "plugins/cortex/profiles.json") + "'"),
    }
    matches = phase2_cli_adapter._invalid_cleanup_failure_matches
    assert matches(exact, incident, control)
    for changed in (
        {**exact, "completed_actions": [*exact["completed_actions"], "calls"]},
        {**exact, "error": "evidence action calls failed: unrelated"},
        {**exact, "status": "complete"},
    ):
        assert not matches(changed, incident, control)


def test_incomplete_audit_cleanup_is_bound_to_seven_exact_partial_artifacts(tmp_path, monkeypatch):
    control = tmp_path / "control/control-record.json"
    control.parent.mkdir()
    control.write_text("sealed-control")
    monkeypatch.setattr(phase2_cli_adapter, "_sha256_file", lambda path: (
        phase2_cli_adapter.INCOMPLETE_AUDIT_INVALID_CONTROL_SHA256
        if Path(path) == control else hashlib.sha256(Path(path).read_bytes()).hexdigest()))
    incident = phase2_cli_adapter.INVALID_CELL_CLEANUP_INCIDENTS[
        phase2_cli_adapter.INCOMPLETE_AUDIT_INVALID_CONTROL_SHA256]
    exact = {"schema_version": phase2_cli_adapter.EVIDENCE_SCHEMA, "status": "incomplete",
             "completed_actions": ["status", "terminal", "capture", "events", "calls", "usage"],
             "error": "evidence action audit failed: retained diagnostics"}
    assert phase2_cli_adapter._invalid_cleanup_failure_matches(exact, incident, control)
    assert len(incident["partial_artifacts"]) == 7
    assert incident["invalidators"] == ["truncated_worker_output",
                                         "unverified_worker_assignment",
                                         "forbidden_access_delivery_unverified"]


def test_cancelled_cell_cleanup_preserves_exact_four_invalidators_and_report():
    incident = phase2_cli_adapter.INVALID_CELL_CLEANUP_INCIDENTS[
        "507700c06a21aec4cfdcd128b197546a1783121b76419632ccd2b8e03d15266d"]
    assert incident["cancelled_by_user"] is True
    assert incident["cancellation_report"] == {
        "report_id": "r_4df181bc6cce",
        "sha256": "118bbec1876a38f9982b89a1c7da31af5f2473e95e18bd6dd1271611c5dae01c",
    }
    assert len(incident["invalidator_identities"]) == 4
    assert [row[0] for row in incident["invalidator_identities"]].count(
        "worker_assignment_policy_unverified") == 3
    assert [row[0] for row in incident["invalidator_identities"]].count(
        "command_wrapper_missing_receipt") == 1
    assert set(incident["partial_artifacts"]) >= {
        "collection-failure.json", "terminal.json", "audit-cancelled.json",
        "calls-cancelled.jsonl", "events-cancelled.jsonl", "usage-cancelled.json",
    }


def test_invalid_cleanup_preserves_terminal_failures_without_accepting_active_calls():
    audit = json.dumps({"open_sessions": [], "open_cells": []})
    terminal_error = json.dumps({
        "thread_id": "root", "parent_thread_id": None, "role": "coordinator",
        "tool": "functions.exec", "outcome": "truncated",
        "completed_timestamp": "2026-09-11T06:13:53.848Z",
    }) + "\n"
    validate = phase2_cli_adapter._validate_inactive_activity
    with pytest.raises(phase2_cli_adapter.AdapterError, match="activity is active"):
        validate(terminal_error, audit)
    assert validate(terminal_error, audit, allow_terminal_failures=True) == ("root", None)
    active = json.dumps({
        "thread_id": "root", "parent_thread_id": None, "role": "coordinator",
        "tool": "functions.exec", "outcome": "pending",
    }) + "\n"
    with pytest.raises(phase2_cli_adapter.AdapterError, match="activity is active"):
        validate(active, audit, allow_terminal_failures=True)


def test_live_adapter_rejects_dropped_isolation_and_tampered_common_harness(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    adapter = Path(data["neutral"]["live_launcher"]["adapter_path"])
    workdir = tmp_path / "live-workdir"
    workdir.mkdir(mode=0o700)
    missing_isolation = subprocess.run(
        [
            "python3", str(adapter), "--control-record", str(record), "--arm", "baseline",
            "start", "--workdir", str(workdir), "--model", "gpt-5.6-luna", "--effort", "high",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing_isolation.returncode != 0
    assert "requires non-resume --evaluation-fresh-store" in missing_isolation.stderr

    harness = Path(data["neutral"]["live_launcher"]["harness_path"])
    harness.write_bytes(harness.read_bytes() + b"\n")
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("common live harness digest mismatch" in error for error in audited["errors"])
    preflight = subprocess.run(
        ["python3", str(adapter), "--control-record", str(record), "--arm", "candidate", "preflight"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert preflight.returncode != 0
    assert "common live-launcher digest mismatch" in preflight.stderr


def test_self_resealed_forged_harness_cannot_preflight_or_fake_success(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    launcher = data["neutral"]["live_launcher"]
    adapter = Path(launcher["adapter_path"])
    harness = Path(launcher["harness_path"])
    harness.write_text(
        "from pathlib import Path\n"
        "LIVE_MODEL='gpt-5.6-luna'\n"
        "LIVE_COORDINATOR_EFFORT='high'\n"
        "ROOT=Path('.')\n"
        "def prepare_evaluation_fresh_store(*args, **kwargs): return {}\n"
        "def launch_command(*args, **kwargs): return [str(ROOT/'scripts/cortex-dev')]\n"
        "def main(): return 0\n"
    )
    launcher["harness_sha256"] = phase2_cli_auditor.file_sha256(harness)
    forged_fingerprint = phase2_cli_auditor.neutral_fingerprint(data["neutral"])
    for arm in data["arms"]:
        arm["control_neutral_fingerprint"] = forged_fingerprint
    _rewrite_control(record, data)

    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("trusted digest mismatch" in error for error in audited["errors"])
    preflight = subprocess.run(
        ["python3", str(adapter), "--control-record", str(record), "--arm", "baseline", "preflight"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert preflight.returncode != 0
    assert "digest mismatch" in preflight.stderr
    workdir = tmp_path / "forged-start-workdir"
    workdir.mkdir(mode=0o700)
    start = subprocess.run(
        [
            "python3", str(adapter), "--control-record", str(record), "--arm", "baseline",
            "start", "--workdir", str(workdir), "--evaluation-fresh-store",
            "--model", "gpt-5.6-luna", "--effort", "high",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert start.returncode != 0
    assert not (workdir / ".codex").exists()


@pytest.mark.parametrize("arm_name", ["baseline", "candidate"])
def test_relocated_arm_root_fails_auditor_and_adapter(tmp_path, arm_name):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    arm = next(item for item in data["arms"] if item["arm"] == arm_name)
    relocated = tmp_path / f"relocated-{arm_name}"
    shutil.copytree(Path(arm["workdir"]), relocated)
    arm["workdir"] = str(relocated)
    arm["workdir_sha256"] = phase2_cli_auditor.sha256(str(relocated).encode())
    remapped_fingerprint = phase2_cli_auditor.neutral_fingerprint(data["neutral"])
    for item in data["arms"]:
        item["control_neutral_fingerprint"] = remapped_fingerprint
    _rewrite_control(record, data)

    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any(f"{arm_name} exact arm-root binding mismatch" in error for error in audited["errors"])
    adapter = Path(data["neutral"]["live_launcher"]["adapter_path"])
    preflight = subprocess.run(
        ["python3", str(adapter), "--control-record", str(record), "--arm", arm_name, "preflight"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert preflight.returncode != 0
    assert "selected arm root binding mismatch" in preflight.stderr


def test_runner_fails_closed_on_identity_manifest_and_environment_tampering(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json")
    args = _args(tmp_path, manifest, tmp_path / "prepared")
    args[args.index("--candidate-payload-sha256") + 1] = "0" * 64
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    refused = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert refused.returncode == 2
    assert "identity" in refused.stderr

    record = _run_prepare(tmp_path / "good")
    data = json.loads(record.read_text())
    data["neutral"]["provider"]["route_sha256"] = "0" * 64
    _rewrite_control(record, data)
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("provider route" in error for error in audited["errors"])


def test_auditor_rejects_duplicate_or_open_terminal_receipts(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    data["arms"][0]["terminal_receipt"]["open_receipt"] = True
    data["arms"][1]["control_neutral_fingerprint"] = "0" * 64
    _rewrite_control(record, data)
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("terminal_receipt" in error for error in audited["errors"])
    assert any("symmetric" in error for error in audited["errors"])


def test_runner_and_auditor_fail_closed_on_missing_reset_dependency_or_manifest_digest(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json")
    manifest_data = json.loads(manifest.read_text())
    del manifest_data["families"][0]["reset_sha256"]
    manifest.write_text(json.dumps(manifest_data) + "\n")
    args = _args(tmp_path, manifest, tmp_path / "missing-reset")
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    refused_reset = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert refused_reset.returncode == 2
    assert "reset_sha256" in refused_reset.stderr

    args = _args(tmp_path / "missing-dependency", _manifest(tmp_path / "missing-dependency" / "manifest.json"), tmp_path / "missing-dependency" / "prepared")
    args[args.index("--dependency-lock") + 1] = str(tmp_path / "does-not-exist.lock")
    refused_dependency = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert refused_dependency.returncode == 2
    assert "dependency lock" in refused_dependency.stderr

    record = _run_prepare(tmp_path / "digest")
    manifest_path = Path(json.loads(record.read_text())["manifest_path"])
    manifest_path.write_text(manifest_path.read_text() + "\n")
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("manifest digest" in error for error in audited["errors"])


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("oracle_path", None, "oracle/reset path"),
        ("reset_path", 123, "oracle/reset path"),
        ("oracle_path", "other.py", "oracle/reset path"),
        ("reset_path", "../reset.py", "oracle/reset path"),
        ("oracle_command", "python3 other.py TASK_ID WORKTREE", "oracle command"),
        ("reset_command", "python3 reset.py", "reset command"),
        ("reset_policy", "reset may overwrite the destination", "unsafe reset policy"),
        ("protected_paths", ["../USER-NOTE.txt"], "protected paths"),
    ],
)
def test_runner_rejects_invalid_or_remapped_oracle_reset_contracts(tmp_path, field, value, needle):
    manifest = _manifest(tmp_path / "manifest.json")
    manifest_data = json.loads(manifest.read_text())
    manifest_data["families"][0][field] = value
    manifest.write_text(json.dumps(manifest_data) + "\n")
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    result = subprocess.run(
        ["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *_args(tmp_path, manifest, tmp_path / "prepared")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert needle in result.stderr


def test_runner_rejects_uncontrolled_proxy_policy_and_ambient_proxy(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json")
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    args = _args(tmp_path, manifest, tmp_path / "bad-policy")
    args[args.index("--proxy-policy") + 1] = "allow-network-and-plugins"
    result = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert "proxy policy" in result.stderr

    env["HTTP_PROXY"] = "http://uncontrolled.invalid"
    args = _args(tmp_path / "ambient", _manifest(tmp_path / "ambient" / "manifest.json"), tmp_path / "ambient" / "prepared")
    result = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert "HTTP_PROXY" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["neutral"].__setitem__("provider", []),
        lambda data: data["neutral"].__setitem__("model", None),
        lambda data: data["identities"].__setitem__("candidate", []),
        lambda data: data["arms"][0].__setitem__("identity", None),
        lambda data: data["arms"][0].__setitem__("terminal_receipt", []),
        lambda data: data["arms"][1].__setitem__("reset", None),
    ],
)
def test_auditor_returns_structured_nonzero_for_malformed_nested_records(tmp_path, mutation):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    data = copy.deepcopy(data)
    mutation(data)
    mutated = tmp_path / "mutated-record.json"
    mutated.write_text(json.dumps(data) + "\n")
    # Direct callers get a JSON-compatible verdict instead of AttributeError.
    audited = phase2_cli_auditor.audit(mutated)
    assert audited["status"] == "fail-closed"
    assert isinstance(audited["errors"], list) and audited["errors"]
    # The CLI boundary also emits structured JSON and a nonzero status.
    result = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_auditor.py"), str(mutated)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "fail-closed"
    assert "Traceback" not in result.stderr


def test_auditor_requires_explicit_environment_controls(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    data["neutral"]["environment"]["proxy_policy"] = "unavailable"
    _rewrite_control(record, data)
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("sanitized environment" in error for error in audited["errors"])


def test_auditor_rejects_candidate_identity_extras_and_paired_commit_remap(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    data["identities"]["candidate"]["unexpected"] = "attacker-controlled"
    mutated = tmp_path / "identity-extra.json"
    mutated.write_text(json.dumps(data) + "\n")
    audited = phase2_cli_auditor.audit(mutated)
    assert audited["status"] == "fail-closed"
    assert any("identities.candidate has unknown fields" in error for error in audited["errors"])

    data = json.loads(record.read_text())
    remapped = "f" * 40
    data["identities"]["candidate"]["source_commit"] = remapped
    data["arms"][1]["identity"]["source_commit"] = remapped
    mutated.write_text(json.dumps(data) + "\n")
    audited = phase2_cli_auditor.audit(mutated)
    assert audited["status"] == "fail-closed"
    assert any("full identity lock mismatch" in error for error in audited["errors"])


def test_auditor_rejects_missing_or_malformed_structured_reasons(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    del data["arms"][0]["reset"]["reason"]
    data["arms"][1]["terminal_receipt"]["reason"] = ["not-a-reason"]
    mutated = tmp_path / "reason-shape.json"
    mutated.write_text(json.dumps(data) + "\n")
    audited = phase2_cli_auditor.audit(mutated)
    assert audited["status"] == "fail-closed"
    assert any("baseline.reset missing fields" in error for error in audited["errors"])
    assert any("candidate.terminal_receipt.reason has invalid type" in error for error in audited["errors"])


def test_runner_and_auditor_reject_denylisted_allowlist_key(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json")
    allowlist = tmp_path / "denylisted.allowlist"
    allowlist.write_text("HTTP_PROXY\n")
    args = _args(tmp_path, manifest, tmp_path / "denylisted-prepared")
    args[args.index("--environment-allowlist") + 1] = str(allowlist)
    env = os.environ.copy()
    for key in phase2_cli_runner.UNSAFE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    result = subprocess.run(["python3", str(ROOT / "scripts/phase2_cli_runner.py"), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert "denied key" in result.stderr

    record = _run_prepare(tmp_path / "audit")
    data = json.loads(record.read_text())
    data["neutral"]["environment"]["allowlist_keys"] = ["HTTP_PROXY"]
    fingerprint = phase2_cli_auditor.neutral_fingerprint(data["neutral"])
    for arm in data["arms"]:
        arm["control_neutral_fingerprint"] = fingerprint
    _rewrite_control(record, data)
    audited = phase2_cli_auditor.audit(record)
    assert audited["status"] == "fail-closed"
    assert any("allowlist overlaps denied keys" in error for error in audited["errors"])


def test_offline_host_receipts_do_not_probe_live_tools(tmp_path):
    record = _run_prepare(tmp_path)
    data = json.loads(record.read_text())
    host = data["neutral"]["host"]
    assert host["tmux"] == {"status": "unavailable", "reason": "offline preparation does not invoke tmux", "version": None}
    assert host["codex"] == {"status": "unavailable", "reason": "offline preparation does not invoke codex", "version": None}
    source = (ROOT / "scripts/phase2_cli_runner.py").read_text()
    assert '["tmux", "-V"]' not in source
    assert '["codex", "--version"]' not in source
