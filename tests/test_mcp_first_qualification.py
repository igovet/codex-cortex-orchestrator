"""Offline proofs for current-host direct MCP bootstrap qualification."""

import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="mcp_first_test")
LIVE = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="mcp_first_test")


def state(tmp_path):
    root = tmp_path / "mcp-first-state"
    root.mkdir(mode=0o700, parents=True)
    # ``runpy`` returns a globals snapshot; mutate the function's real globals
    # so the observer validates this fixture's isolated state directory.
    OBSERVER["_mcp_first_provenance"].__globals__["MCP_FIRST_LAUNCH_STATE"] = root
    data = {
        "bootstrap_route": "current_host_mcp_first", "host_enforcement_state": "unverified",
        "workdir": "/fixture", "original_request_sha256": "a" * 64,
        "bootstrap_expected_child_thread_id": "child-thread",
        "bootstrap_provenance_schema": "cortex-mcp-first-root-v1",
        "bootstrap_native_user_turn_sha256": "d" * 64,
        "tmux_session_id": "$fixture", "tmux_session_created": 1,
        "tmux_pane_id": "%1", "tmux_pane_pid": 2, "tmux_pane_start_ticks": 3,
    }
    receipt = {
        "schema_version": data["bootstrap_provenance_schema"],
        "bootstrap_route": data["bootstrap_route"], "host_enforcement_state": "unverified",
        "thread_id": data["bootstrap_expected_child_thread_id"], "workdir": data["workdir"],
        "original_request_sha256": data["original_request_sha256"],
        "tmux_session_id": data["tmux_session_id"], "tmux_session_created": 1,
        "tmux_pane_id": data["tmux_pane_id"], "tmux_pane_pid": 2,
        "tmux_pane_start_ticks": 3, "native_user_turn_sha256": data["bootstrap_native_user_turn_sha256"],
    }
    identity = hashlib.sha256("\0".join(("$fixture", "%1", "1", "2", "3", "/fixture")).encode()).hexdigest()
    path = root / f"mcp-first-root-{identity}.json"
    raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw); path.chmod(0o600)
    info = path.stat()
    data.update(bootstrap_provenance_path=str(path), bootstrap_provenance_sha256=hashlib.sha256(raw).hexdigest(),
                bootstrap_provenance_device=info.st_dev, bootstrap_provenance_inode=info.st_ino)
    return data


def create(**changes):
    row = {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
           "tool": "mcp__cortex__create_task", "outcome": "success", "host_receipt_outcome": "success",
           "server_observed": True, "host_receipt_observed": True,
           "bootstrap_project_digest": hashlib.sha256(b"/fixture").hexdigest(),
           "original_request_preserved": True, "host_result_digest": "b" * 12, "task_id": "t_child"}
    row.update(changes)
    return row


def invalid(rows, data, reason):
    receipt = OBSERVER["mcp_first_bootstrap_receipt"](rows, data)
    assert receipt["status"] == "unverified" and receipt["reason"] == reason
    findings = OBSERVER["mcp_first_bootstrap_violations"](rows, data)
    audit = OBSERVER["classify_audit_findings"](failures=[], hook_failures=[], host_failures=[],
        policy_violations=findings, open_sessions=[], open_cells=[])
    assert not audit["evidence_valid"] and not audit["score_eligible"]


def test_mcp_first_exact_child_receipt_is_complete_without_host_enforcement(tmp_path):
    receipt = OBSERVER["mcp_first_bootstrap_receipt"]([
        {"role": "runtime", "tool": "launcher_transport", "outcome": "success"}, create(),
        {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator", "tool": "exec_command"},
    ], state(tmp_path))
    assert receipt["status"] == "complete", receipt
    assert receipt["host_enforcement_state"] == "unverified"


def test_mcp_first_prebinding_and_private_probe_are_audit_invalidators(tmp_path):
    for row in (
        {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator", "tool": "functions.exec"},
        {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator", "tool": "read_file", "path_target_class": "plugin_cache"},
    ):
        findings = OBSERVER["mcp_first_bootstrap_violations"]([row, create()], state(tmp_path / row["tool"].replace(".", "_")))
        assert findings[0]["violation"] == "pre_binding_host_action"
        assert findings[0]["tool"] == row["tool"]


def test_mcp_first_authenticates_all_provenance_bindings(tmp_path):
    mutations = [
        ("bootstrap_provenance_path", "/wrong/path", "bootstrap_provenance_path_mismatch"),
        ("bootstrap_provenance_sha256", "c" * 64, "bootstrap_provenance_digest_mismatch"),
        ("bootstrap_expected_child_thread_id", "other", "bootstrap_provenance_binding_mismatch"),
        ("workdir", "/other", "bootstrap_provenance_path_mismatch"),
        ("original_request_sha256", "e" * 64, "bootstrap_provenance_binding_mismatch"),
        ("tmux_session_id", "$other", "bootstrap_provenance_path_mismatch"),
        ("tmux_pane_id", "%2", "bootstrap_provenance_path_mismatch"),
        ("bootstrap_native_user_turn_sha256", "f" * 64, "bootstrap_provenance_binding_mismatch"),
    ]
    for key, value, reason in mutations:
        data = state(tmp_path / key); data[key] = value
        invalid([create()], data, reason)
    missing = state(tmp_path / "missing"); Path(missing["bootstrap_provenance_path"]).unlink()
    invalid([create()], missing, "bootstrap_provenance_missing")
    malformed = state(tmp_path / "malformed"); Path(malformed["bootstrap_provenance_path"]).write_text("not json")
    invalid([create()], malformed, "bootstrap_provenance_digest_mismatch")
    symlink = state(tmp_path / "symlink"); path = Path(symlink["bootstrap_provenance_path"]); target = path.with_name("target")
    target.write_text("{}"); path.unlink(); path.symlink_to(target)
    invalid([create()], symlink, "bootstrap_provenance_untrusted")
    replacement = state(tmp_path / "replacement"); path = Path(replacement["bootstrap_provenance_path"]); raw = path.read_bytes()
    alternate = path.with_name("replacement.json"); alternate.write_bytes(raw); alternate.chmod(0o600); alternate.replace(path)
    invalid([create()], replacement, "bootstrap_provenance_replaced")


def test_mcp_first_rejects_unrelated_competing_and_duplicate_receipts(tmp_path):
    unrelated = create(thread_id="other-root", task_id="t_other")
    invalid([unrelated], state(tmp_path / "foreign"), "bootstrap_expected_root_absent")
    invalid([unrelated, create()], state(tmp_path / "before"), "bootstrap_competing_root")
    invalid([create(), unrelated], state(tmp_path / "after"), "bootstrap_competing_root")
    invalid([create(), create()], state(tmp_path / "same"), "bootstrap_duplicate_receipt")
    invalid([create(), create(task_id="other", host_result_digest="c" * 12)], state(tmp_path / "conflict"), "bootstrap_duplicate_receipt")
    invalid([create(replayed=True)], state(tmp_path / "replay"), "bootstrap_replayed_receipt")
    invalid([create(replay_marker=True)], state(tmp_path / "marker"), "bootstrap_replayed_receipt")


def test_mcp_first_requires_complete_context_and_preserves_launcher_transport_exclusion(tmp_path):
    data = state(tmp_path / "missing")
    del data["bootstrap_expected_child_thread_id"]
    invalid([create()], data, "bootstrap_context_missing")
    receipt = OBSERVER["mcp_first_bootstrap_receipt"]([
        {"role": "runtime", "tool": "functions.exec", "launcher_transport": True}, create()], state(tmp_path / "transport"))
    assert receipt["status"] == "complete"
    assert LIVE["qualification_mcp_first_bootstrap"]("mcp-first") == {
        "bootstrap_route": "current_host_mcp_first", "host_enforcement_state": "unverified",
        "qualification_contract": "observational_accept"}


def test_mcp_first_observational_contract_allows_only_complete_bounded_discovery(tmp_path):
    setup = {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
             "tool": "functions.exec", "skill_instruction_read": True, "bounded_wrapper": True,
             "pre_binding_static_read": True,
             "active_coordinator_skill_read": True,
             "project_target_access": False, "private_target_access": False,
             "mutation_observed": False, "truncated": False, "outcome": "success",
             "path_target_class": "approved_instruction_or_static_mention",
             "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
             "host_receipt_observed": True}
    receipt = OBSERVER["mcp_first_bootstrap_receipt"]([setup, create()], state(tmp_path / "good"))
    assert receipt["status"] == "complete"
    generic = dict(setup); generic["project_target_access"] = None
    invalid([generic, create()], state(tmp_path / "generic"), "pre_binding_host_action")
    for name, change in (
        ("neighbor", {"path_target_class": "plugin_cache"}),
        ("glob", {"path_access_kind": "static_mention", "pre_binding_static_read": False}),
        ("mutation", {"mutation_observed": True}),
    ):
        rejected = {**setup, **change}
        invalid([rejected, create()], state(tmp_path / name), "pre_binding_host_action")
    complete = OBSERVER["current_host_observational_qualification"](
        [create()], state(tmp_path / "accept"), {"evidence_valid": True},
        OBSERVER["mcp_first_bootstrap_receipt"]([create()], state(tmp_path / "accept-receipt")),
        capture_complete=True, events_complete=True, terminal_complete=True, exit_complete=True)
    assert complete == {"status": "observational_accept", "qualification_contract": "observational_accept",
                        "host_enforcement_state": "unverified", "assignment_enforcement_state": "unverified",
                        "bootstrap_thread_id": "child-thread"}
    blocked = OBSERVER["current_host_observational_qualification"](
        [create()], state(tmp_path / "block"), {"evidence_valid": True},
        {"status": "complete", "thread_id": "child-thread"},
        capture_complete=True, events_complete=True, terminal_complete=False, exit_complete=False)
    assert blocked["status"] == "BLOCK" and blocked["reason"] == "observational_evidence_incomplete"


def _completed_current_host_rows():
    """One terminal native result, its worker report, and exact reconciliation."""
    artifact_sha = "c" * 64
    report_id = "r_0123456789ab"
    artifact_binding = (hashlib.sha256(b"RESULT.md").hexdigest()[:12], artifact_sha)
    worker_result = {
        "schema": "cortex-native-worker-result-v1", "worker_thread_id": "worker-thread",
        "parent_thread_id": "child-thread", "task_id": "t_child", "profile": "general",
        "status": "success", "worker_mcp": "unavailable", "assignment_digest": "d" * 64,
        "artifacts": [{"reference": "RESULT.md", "sha256": artifact_sha}],
        "checks": [{"command": "pytest focused", "exit_code": 0, "receipt_digest": "e" * 64}],
        "limits": ["artifact hash asserted"],
    }
    worker_result["result_digest"] = OBSERVER["canonical_native_worker_result"](worker_result)
    return [
        create(direct_public_mcp=True, server_observed=False, result_receipt_observed=True),
        {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
         "tool": "native_agent_result", "outcome": "success", "host_receipt_observed": True,
         "host_receipt_outcome": "success", "replayed": False, "truncated": False,
         "report_ids": [report_id], "native_worker_result": worker_result},
        {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
         "tool": "mcp__cortex__write_report", "report_id": report_id, "outcome": "success",
         "host_receipt_observed": True, "host_receipt_outcome": "success", "replayed": False,
         "truncated": False},
        {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
         "tool": "mcp__cortex__read_report", "report_id": report_id, "outcome": "success",
         "host_receipt_observed": True, "host_receipt_outcome": "success", "replayed": False,
         "truncated": False, "reported_artifact_bindings": [artifact_binding]},
    ]


def _strict_worker_skill_group_rows():
    """One native spawn edge plus the closed denied worker-SKILL read group."""
    return [
        {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
         "tool": "spawn_agent", "spawned_thread_id": "worker-thread", "outcome": "success"},
        {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
         "tool": "exec_command", "outcome": "error", "worker_skill_receipt_required": True,
         "skill_instruction_read": True, "worker_skill_profile": "general",
         "path_policy_provenance": "observer_literal_marker",
         "path_target_class": "approved_instruction_or_static_mention",
         "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
         "execution_status": "denied_before_dispatch", "truncated": False,
         "replayed": False, "private_target_access": False,
         "project_target_access": False, "mutation_observed": False,
         "result_digest": "f" * 12},
    ]


def test_desktop_current_host_final_outcome_uses_supported_receipts_only(tmp_path):
    """Desktop may use its durable root/thread/report/artifact surface only."""
    workdir = tmp_path / "desktop"; workdir.mkdir()
    artifact = workdir / "RESULT.md"; artifact.write_bytes(b"current-host desktop result")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    data = {
        "qualification_route": "current_host_desktop", "workdir": str(workdir),
        "thread_id": "desktop-root", "desktop_transport_receipt": "success",
        "desktop_expected_artifact": {"path": "RESULT.md", "sha256": digest},
    }
    rows = [
        {"thread_id": "desktop-root", "parent_thread_id": None, "role": "coordinator",
         "tool": "mcp__cortex__create_task", "direct_public_mcp": True, "outcome": "success",
         "host_receipt_observed": True, "host_receipt_outcome": "success"},
        {"thread_id": "desktop-root", "parent_thread_id": None, "role": "coordinator",
         "tool": "mcp__cortex__write_report", "report_id": "r_0123456789ab", "outcome": "success",
         "host_receipt_observed": True, "host_receipt_outcome": "success"},
        {"thread_id": "desktop-worker", "parent_thread_id": "desktop-root", "role": "general",
         "tool": "apply_patch", "outcome": "success", "path_target_class": "worker_workspace_or_external"},
    ]
    outcome, reason = OBSERVER["desktop_supported_outcome_receipt"](
        rows, data, open_sessions=[], open_cells=[])
    assert reason is None and outcome["status"] == "complete"
    editions=[dict(row) for row in rows]
    editions[1]['draft_id']='d_initial'
    editions.append({**editions[1],'draft_id':'d_final'})
    check=OBSERVER["desktop_supported_outcome_receipt"]
    assert check(editions,data,open_sessions=[],open_cells=[])[0] is not None
    for final in ({**editions[-1],'draft_id':'d_initial'},
                  {**editions[-1],'report_id':'r_999999999999'},
                  {**editions[-1],'draft_id':None}):
        assert check([*editions[:-1],final],data,open_sessions=[],open_cells=[])[0] is None
    assert check([editions[0],editions[1],editions[-1],editions[2]],data,
                 open_sessions=[],open_cells=[])[0] is None
    policy = [
        {"thread_id": "desktop-root", "tool": "spawn_agent", "violation": "worker_assignment_policy_unverified"},
        {"thread_id": "desktop-worker", "tool": "exec_command", "violation": "worker_skill_load_failed"},
        {"thread_id": "desktop-worker", "tool": "apply_patch", "violation": "worker_project_action_before_skill_receipt"},
    ]
    historical = [
        {"thread_id": "desktop-root", "tool": "wait_agent", "outcome": "pending"},
        {"thread_id": "desktop-worker", "tool": "command_execution", "outcome": "error",
         "error_code": "command_exit_1", "command_family": "wc"},
        {"thread_id": "desktop-worker", "tool": "command_execution", "outcome": "error",
         "error_code": "command_exit_1", "command_family": "truncate"},
    ]
    retained_host, retained_policy, diagnostics, final, reason = OBSERVER["desktop_current_host_partition"](
        rows, data, historical, policy, open_sessions=[], open_cells=[])
    assert not retained_host and not retained_policy and reason is None and final == outcome
    assert len(diagnostics) == 6
    classification = OBSERVER["classify_audit_findings"](
        failures=[], hook_failures=[], host_failures=retained_host,
        policy_violations=retained_policy, open_sessions=[], open_cells=[])
    assert classification["evidence_valid"] and classification["score_eligible"]
    for name, changed in (
        ("artifact", lambda: artifact.write_bytes(b"wrong")),
        ("open", lambda: None),
        ("unsafe", lambda: rows.append({"thread_id": "desktop-worker", "private_target_access": True})),
        ("duplicate", lambda: rows.append(dict(rows[0]))),
        ("missing-worker", lambda: rows.__setitem__(2, {"thread_id": "desktop-root", "role": "coordinator", "tool": "noop"})),
        ("truncated", lambda: rows.append({"thread_id": "desktop-worker", "truncated": True})),
    ):
        candidate = [dict(row) for row in rows]
        target = artifact.read_bytes()
        if name == "artifact":
            artifact.write_bytes(b"wrong")
        elif name == "open":
            blocked, blocked_reason = OBSERVER["desktop_supported_outcome_receipt"](
                candidate, data, open_sessions=[{"id": "open"}], open_cells=[])
            assert blocked is None and blocked_reason == "desktop_open_work_remaining"; continue
        else:
            # Apply the mutation to the independent fixture copy.
            if name == "unsafe": candidate.append({"thread_id": "desktop-worker", "private_target_access": True})
            elif name == "duplicate": candidate.append(dict(candidate[0]))
            elif name == "missing-worker": candidate[2] = {"thread_id": "desktop-root", "role": "coordinator", "tool": "noop"}
            elif name == "truncated": candidate.append({"thread_id": "desktop-worker", "truncated": True})
        blocked, blocked_reason = OBSERVER["desktop_supported_outcome_receipt"](
            candidate, data, open_sessions=[], open_cells=[])
        assert blocked is None, name
        artifact.write_bytes(target)


def test_current_host_live_shape_accepts_opaque_task_and_native_result_receipts(tmp_path):
    """Current Codex correlates this completed workflow without exposing task bodies."""
    artifact_sha = "9" * 64
    report_id = "r_abcdef012345"
    artifact_binding = (hashlib.sha256(b"RESULT.md").hexdigest()[:12], artifact_sha)
    static_read = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "functions.exec", "outcome": "covered_by_nested", "wrapper_outcome": "success",
        "pre_binding_static_read": True, "skill_instruction_read": True,
        "active_coordinator_skill_read": True, "bounded_wrapper": True,
        "bounded_metadata_observed": True, "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
        "truncated": False,
    }
    opaque_create = create(task_id=None, direct_public_mcp=True, result_receipt_observed=True,
                           server_observed=True)
    opaque_worker = {
        "thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
        "tool": "native_agent_result", "outcome": "success", "report_id": report_id,
        "agent_path": "/root/fixture-worker", "replayed": False, "truncated": False,
    }
    rows = [static_read, opaque_create, *_strict_worker_skill_group_rows(), opaque_worker,
            {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
             "tool": "mcp__cortex__write_report", "report_id": report_id, "outcome": "success",
             "host_receipt_observed": True, "host_receipt_outcome": "success", "replayed": False,
             "truncated": False},
            {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
             "tool": "mcp__cortex__read_report", "report_id": report_id, "outcome": "success",
             "host_receipt_observed": True, "host_receipt_outcome": "success", "replayed": False,
             "truncated": False, "reported_artifact_bindings": [artifact_binding]}]
    data = state(tmp_path / "live-shape")
    bootstrap = OBSERVER["mcp_first_bootstrap_receipt"](rows, data)
    assert bootstrap["status"] == "complete"
    outcome, reason = OBSERVER["current_host_outcome_receipt"](rows, data)
    assert reason is None and outcome["status"] == "complete"
    policy = [
        {"thread_id": "child-thread", "role": "coordinator", "tool": "spawn_agent",
         "violation": "worker_assignment_policy_unverified"},
        {"thread_id": "child-thread", "role": "coordinator", "tool": "mcp__cortex__create_task",
         "violation": "mcp_first_bootstrap_unverified"},
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, policy)
    assert blocking == [] and all(item["not_applicable"] for item in diagnostics)
    classification = OBSERVER["classify_audit_findings"](
        failures=[], hook_failures=[], host_failures=[], policy_violations=blocking,
        open_sessions=[], open_cells=[])
    qualified = OBSERVER["current_host_observational_qualification"](
        rows, data, classification, bootstrap, capture_complete=True, events_complete=True,
        terminal_complete=True, exit_complete=True)
    assert qualified["status"] == "observational_accept"

    failed_skill = {
        "thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
        "tool": "exec_command", "outcome": "error", "worker_skill_receipt_required": True,
        "skill_instruction_read": True, "path_policy_provenance": "observer_literal_marker",
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
        "result_digest": "a" * 12,
    }
    # The earlier strict group is deliberately excluded from this unit's
    # identity comparison; a different denied read must not be coalesced with
    # the established group merely because it has the same worker thread.
    rows_without_group = [row for row in rows if row.get("worker_skill_receipt_required") is not True]
    rows_with_failed_skill = [*rows_without_group, failed_skill]
    assert OBSERVER["observational_worker_skill_read_failure_diagnostic"](
        rows_with_failed_skill, failed_skill, data)
    for changed in (
        {"path_target_class": "worker_workspace_or_external"},
        {"project_target_access": True},
        {"mutation_observed": True},
    ):
        rejected = {**failed_skill, **changed}
        assert not OBSERVER["observational_worker_skill_read_failure_diagnostic"](
            [*rows_without_group, rejected], rejected, data)

    duplicate = {**failed_skill, "timestamp": "later", "completed_timestamp": "later-complete"}
    equivalent_rows = [*rows_without_group, failed_skill, duplicate]
    assert OBSERVER["observational_worker_skill_read_failure_diagnostic"](
        equivalent_rows, failed_skill, data)
    assert OBSERVER["observational_worker_skill_read_failure_diagnostic"](
        equivalent_rows, duplicate, data)
    for changed in (
        {"path_argument_digest": "b" * 12},
        {"tool": "functions.exec"},
        {"worker_skill_profile": "other-profile"},
        {"mutation_observed": True},
        {"execution_status": "executed"},
        {"result_digest": "b" * 12},
    ):
        unsafe = {**duplicate, **changed}
        candidate_rows = [*rows_without_group, failed_skill, unsafe]
        assert not OBSERVER["observational_worker_skill_read_failure_diagnostic"](
            candidate_rows, failed_skill, data)
        assert not OBSERVER["observational_worker_skill_read_failure_diagnostic"](
            candidate_rows, unsafe, data)
    foreign = {**duplicate, "thread_id": "other-worker"}
    assert not OBSERVER["observational_worker_skill_read_failure_diagnostic"](
        [*rows_without_group, failed_skill, foreign], foreign, data)
    incomplete_rows = [*rows_without_group, failed_skill, duplicate, {"tool": "wait_agent", "outcome": "pending"}]
    assert not OBSERVER["observational_worker_skill_read_failure_diagnostic"](
        incomplete_rows, failed_skill, data)


def test_current_host_unsupported_labels_are_diagnostics_without_outcome_shortcuts(tmp_path):
    rows = _completed_current_host_rows()
    data = state(tmp_path / "completed")
    outcome, reason = OBSERVER["current_host_outcome_receipt"](rows, data)
    assert reason is None and outcome["status"] == "complete"
    policy = [
        {"violation": name, "thread_id": "child-thread", "role": "coordinator",
         "tool": "mcp__cortex__create_task"}
        for name in (
            "mcp_first_bootstrap_unverified", "worker_assignment_policy_unverified",
            "worker_skill_load_failed", "worker_project_action_before_skill_receipt",
            "cortex_call_missing_server_event",
        )
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, policy)
    assert {row["violation"] for row in diagnostics} == {
        "mcp_first_bootstrap_unverified", "worker_assignment_policy_unverified"}
    assert all(row["unsupported_by_current_host"] is True and row["not_applicable"] is True
               for row in diagnostics)
    assert {row["violation"] for row in blocking} == {
        "worker_skill_load_failed", "worker_project_action_before_skill_receipt",
        "cortex_call_missing_server_event"}
    qualified = OBSERVER["current_host_observational_qualification"](
        rows, data, {"evidence_valid": True}, {"status": "unverified"},
        capture_complete=True, events_complete=True, terminal_complete=True, exit_complete=True)
    assert qualified["status"] == "observational_accept"
    assert qualified["host_enforcement_state"] == "unverified"


def test_current_host_exact_denied_worker_skill_group_retains_only_safe_receipt_gaps(tmp_path):
    """A verified host capability gap cannot hide a later unsafe operation."""
    rows = [*_completed_current_host_rows(), *_strict_worker_skill_group_rows()]
    data = state(tmp_path / "worker-receipt-gap")
    policy = [
        {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
         "tool": "exec_command", "outcome": "error", "skill_instruction_read": True,
         "path_policy_decision": "allowed", "violation": "worker_skill_load_failed"},
        {"thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
         "tool": "mcp__cortex__read_report", "outcome": "success",
         "path_policy_decision": "diagnostic_only",
         "violation": "worker_project_action_before_skill_receipt"},
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, policy)
    assert blocking == []
    assert {item["violation"] for item in diagnostics} == {
        "worker_skill_load_failed", "worker_project_action_before_skill_receipt"}
    assert {item["diagnostic_reason"] for item in diagnostics} == {
        "missing_current_host_worker_skill_receipt"}

    for name, change in (
        ("mutation", {"mutation_observed": True}),
        ("private", {"private_target_access": True}),
        ("project", {"project_target_access": True}),
        ("unauthorized", {"path_policy_decision": "unauthorized_access"}),
        ("executed", {"execution_status": "executed"}),
        ("replayed", {"replayed": True}),
        ("truncated", {"truncated": True}),
    ):
        candidate = [{**policy[1], **change}]
        blocking, diagnostics = OBSERVER["observational_policy_partition"](
            rows, state(tmp_path / name), candidate)
        assert blocking == candidate and diagnostics == []

    incomplete = [*rows, {"tool": "wait_agent", "outcome": "pending"}]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        incomplete, state(tmp_path / "incomplete"), policy)
    assert blocking == policy and diagnostics == []


def test_current_host_completed_outcome_retains_actual_incomplete_or_mismatched_facts_as_blocks(tmp_path):
    for name, mutate, expected in (
        ("missing-report", lambda rows: rows.pop(2), "outcome_worker_report_missing_or_ambiguous"),
        ("wrong-hash", lambda rows: rows[-1].update(reported_artifact_bindings=[]),
         "outcome_artifact_reconciliation_missing"),
        ("pending", lambda rows: rows.append({"tool": "wait_agent", "outcome": "pending"}),
         "outcome_pending_or_incomplete_call"),
    ):
        rows = _completed_current_host_rows(); mutate(rows)
        outcome, reason = OBSERVER["current_host_outcome_receipt"](rows, state(tmp_path / name))
        assert outcome is None and reason == expected
    rows = _completed_current_host_rows()
    qualified = OBSERVER["current_host_observational_qualification"](
        rows, state(tmp_path / "not-idle"), {"evidence_valid": True}, {"status": "unverified"},
        capture_complete=True, events_complete=True, terminal_complete=False, exit_complete=False)
    assert qualified["status"] == "BLOCK" and qualified["reason"] == "observational_evidence_incomplete"


def test_completed_current_host_outcome_accepts_redundant_missing_final_id_and_harmless_lookup(tmp_path):
    """Match the completed interactive workload without waiving outcome proof."""
    rows = _completed_current_host_rows()
    # The native final omitted a redundant report ID, but exactly one
    # task-bound worker publication and exact coordinator reconciliation remain.
    rows[1].pop("report_ids")
    lookup = {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
              "tool": "mcp__cortex__read_report", "outcome": "error",
              "error_code": "invalid_arguments", "mutation_observed": False,
              "private_target_access": False, "project_target_access": False,
              "truncated": False, "replayed": False}
    rows.insert(1, lookup)
    data = state(tmp_path / "redundant-final-id")
    outcome, reason = OBSERVER["current_host_outcome_receipt"](rows, data)
    assert reason is None and outcome["report_id"] == "r_0123456789ab"
    assert OBSERVER["observational_read_report_failure_diagnostic"](rows, lookup, data)
    policy = [
        {"thread_id": "worker-thread", "role": "general", "tool": "native_agent_result",
         "violation": "worker_final_without_report_id"},
        {"thread_id": "child-thread", "role": "coordinator", "tool": "mcp__cortex__read_report",
         "violation": "mcp_tool_error_observed", "error_code": "invalid_arguments",
         "outcome": "error", "mutation_observed": False},
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, policy)
    assert blocking == []
    assert {row["violation"] for row in diagnostics} == {
        "worker_final_without_report_id", "mcp_tool_error_observed"}
    qualified = OBSERVER["current_host_observational_qualification"](
        rows, data, {"evidence_valid": True}, {"status": "unverified"},
        capture_complete=True, events_complete=True, terminal_complete=True, exit_complete=True)
    assert qualified["status"] == "observational_accept"


def test_redundant_final_id_and_lookup_relaxation_never_hides_unsafe_or_ambiguous_evidence(tmp_path):
    rows = _completed_current_host_rows(); rows[1].pop("report_ids")
    rows.insert(1, {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
                    "tool": "mcp__cortex__read_report", "outcome": "error",
                    "error_code": "invalid_arguments", "mutation_observed": True,
                    "private_target_access": False, "project_target_access": False,
                    "truncated": False, "replayed": False})
    data = state(tmp_path / "unsafe-lookup")
    lookup = rows[1]
    assert not OBSERVER["observational_read_report_failure_diagnostic"](rows, lookup, data)
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, [{
        "thread_id": "child-thread", "role": "coordinator", "tool": "mcp__cortex__read_report",
        "violation": "mcp_tool_error_observed", "error_code": "invalid_arguments",
        "outcome": "error", "mutation_observed": True,
    }])
    assert diagnostics == [] and blocking[0]["violation"] == "mcp_tool_error_observed"

    ambiguous = _completed_current_host_rows(); ambiguous[1].pop("report_ids")
    ambiguous.append(dict(ambiguous[2]))
    outcome, reason = OBSERVER["current_host_outcome_receipt"](ambiguous, state(tmp_path / "ambiguous-publication"))
    assert outcome is None and reason == "outcome_worker_report_missing_or_ambiguous"


def test_live_shaped_audit_exit_uses_only_the_finalized_classification():
    outcome = {"status": "observational_accept", "host_enforcement_state": "unverified",
               "outcome_receipt": {"task_id": "t_child", "report_id": "r_0123456789ab"}}
    classification = {"evidence_valid": True, "score_eligible": True,
                      "evidence_integrity_invalidators": [], "quality_findings": [{
        "classification": "prevented_attempt_diagnostic",
        "reason": "authorization_denied_before_dispatch",
    }]}
    diagnostics = [{"observational_diagnostic": True,
                    "diagnostic_reason": "nonmutating_invalid_report_lookup"}]
    audit_exit = LIVE["current_host_audit_exit_code"]
    assert audit_exit(classification, outcome, failures=[], host_failures=[], audit_policy=[],
                      observational_diagnostics=diagnostics) == 0
    # Raw collections are pre-final diagnostics/history, not a second exit
    # decision. The exact printed classification is authoritative after its
    # partition and final-boundary normalization.
    assert audit_exit(classification, outcome, failures=[{"outcome": "error"}],
                      host_failures=[], audit_policy=[{"violation": "legacy"}],
                      observational_diagnostics=[]) == 0
    # A real supported blocker must have survived into the final classifier and
    # therefore still returns a nonzero CLI status.
    dirty = {**classification, "evidence_valid": False, "score_eligible": False,
             "evidence_integrity_invalidators": [{"reason": "supported_task_failure"}]}
    assert audit_exit(dirty, outcome, failures=[], host_failures=[],
                      audit_policy=[],
                      observational_diagnostics=diagnostics) == 1
    assert audit_exit({"evidence_valid": True, "evidence_integrity_invalidators": [],
                       "score_eligible": True,
                       "quality_findings": [{"classification": "product_quality_outcome"}]},
                      outcome, failures=[], host_failures=[], audit_policy=[],
                      observational_diagnostics=diagnostics) == 1
    assert audit_exit({"evidence_valid": True, "score_eligible": False,
                       "evidence_integrity_invalidators": [{"reason": "truncated"}],
                       "quality_findings": []}, outcome, failures=[], host_failures=[], audit_policy=[],
                      observational_diagnostics=diagnostics) == 1
    assert audit_exit({"evidence_valid": True, "score_eligible": True, "quality_findings": []}, outcome,
                      failures=[], host_failures=[], audit_policy=[],
                      observational_diagnostics=diagnostics) == 1
    assert audit_exit({"evidence_valid": True, "score_eligible": True,
                       "evidence_integrity_invalidators": "not-a-list",
                       "quality_findings": []}, outcome, failures=[], host_failures=[], audit_policy=[],
                      observational_diagnostics=diagnostics) == 1
    assert audit_exit(classification, {"status": "BLOCK"}, failures=[], host_failures=[],
                      audit_policy=[], observational_diagnostics=diagnostics) == 1


def test_audit_cli_subprocess_prints_its_finalized_clean_or_dirty_result(tmp_path):
    """The actual CLI parser returns the status included in its final JSON."""
    capture = tmp_path / "capture.txt"
    capture.write_text("")
    runner = (
        "import json,runpy,sys\n"
        "from pathlib import Path\n"
        "root,capture,kind=map(Path,sys.argv[1:4]);dirty=kind.name=='dirty'\n"
        "live=runpy.run_path(str(root/'scripts/cortex-live-smoke'),run_name='audit_cli_regression')\n"
        "c=({'evidence_valid':False,'score_eligible':False,'evidence_integrity_invalidators':[{'reason':'supported_task_failure'}],'quality_findings':[]} if dirty else {'evidence_valid':True,'score_eligible':True,'evidence_integrity_invalidators':[],'quality_findings':[]})\n"
        "o=({'status':'BLOCK'} if dirty else {'status':'observational_accept','host_enforcement_state':'unverified'})\n"
        "z=lambda *a,**k:[]; observer={'observed_tool_calls':z,'classify_host_failures':lambda *a:([],[]),'tool_error_history':z,'orchestration_error_history':z,'call_policy_violations':z,'mcp_first_bootstrap_receipt':lambda *a:{'status':'complete'},'mcp_first_bootstrap_violations':z,'observational_policy_partition':lambda *a:([],[]),'observational_read_report_failure_diagnostic':lambda *a:False,'observational_worker_skill_read_failure_diagnostic':lambda *a:False,'open_command_sessions':z,'open_exec_cells':z,'classify_audit_findings':lambda **k:c,'current_host_observational_qualification':lambda *a,**k:o,'orchestration_policy_violations':z,'worker_policy_violations':z,'is_orchestration_call':lambda *a:False}\n"
        "g=live['main'].__globals__;g['state']=lambda:{'bootstrap_route':'current_host_mcp_first','events':str(capture.parent/'events'),'host_enforcement_state':'unverified'};g['_observer_namespace']=lambda _n:observer;g['normalized_observer_events']=lambda *a:([],[]);g['live_exit_marker_evidence']=lambda _d:{'status':'complete','exit_status':0};g['STATE']=capture.parent\n"
        "sys.argv=['cortex-live-smoke','audit'];raise SystemExit(live['main']())\n"
    )
    for name, expected_exit, expected_status in (
        ("clean", 0, "observational_accept"),
        ("dirty", 1, "BLOCK"),
    ):
        result = subprocess.run(
            [sys.executable, "-c", runner, str(ROOT), str(capture), name],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert result.returncode == expected_exit, result.stderr
        printed = json.loads(result.stdout)
        assert printed["audit_exit_code"] == expected_exit
        assert printed["observational_qualification"]["status"] == expected_status
        if expected_exit == 0:
            assert printed["evidence_valid"] is True
            assert printed["score_eligible"] is True
            assert printed["evidence_integrity_invalidators"] == []
        else:
            assert printed["evidence_integrity_invalidators"] == [{"reason": "supported_task_failure"}]


def test_current_host_final5_supported_outcome_accepts_with_unsupported_labels(tmp_path):
    rows = _completed_current_host_rows(); data = state(tmp_path / "unsupported-host-labels")
    labels = [
        {"thread_id": "child-thread", "role": "coordinator", "tool": "spawn_agent",
         "violation": "worker_assignment_policy_unverified"},
        {"thread_id": "child-thread", "role": "coordinator", "tool": "mcp__cortex__create_task",
         "violation": "mcp_first_bootstrap_unverified"},
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, labels)
    assert blocking == []
    assert {row["violation"] for row in diagnostics} == {row["violation"] for row in labels}
    assert all(row["unsupported_by_current_host"] is True and row["not_applicable"] is True
               for row in diagnostics)
    prevented = [{"hook_event": "PreToolUse", "action_status": "denied",
                  "authorization_outcome": "PERMISSION_DENIED", "execution_status": "denied_before_dispatch",
                  "task_id": "t_child", "thread_id": "child-thread", "role": "coordinator",
                  "binding_confidence": "receipt", "binding_origin": "native_hook",
                  "authorization_capability": "skill_read", "authorization_reason": "policy"}] * 2
    classification = OBSERVER["classify_audit_findings"](
        failures=[], hook_failures=prevented, host_failures=[], policy_violations=blocking,
        open_sessions=[], open_cells=[])
    qualified = OBSERVER["current_host_observational_qualification"](
        rows, data, classification, {"status": "unverified"},
        capture_complete=True, events_complete=True, terminal_complete=True, exit_complete=True)
    assert qualified["status"] == "observational_accept"
    assert classification["evidence_valid"] and classification["score_eligible"]
    assert LIVE["current_host_audit_exit_code"](
        classification, qualified, failures=[], host_failures=[], audit_policy=[],
        observational_diagnostics=diagnostics) == 0
    for mutate in (
        lambda items: items.pop(2),
        lambda items: items[-1].update(reported_artifact_bindings=[]),
        lambda items: items.append({"tool": "wait_agent", "outcome": "pending"}),
    ):
        rejected = _completed_current_host_rows(); mutate(rejected)
        rejected_outcome = OBSERVER["current_host_observational_qualification"](
            rejected, state(tmp_path / str(len(rejected))), classification, {"status": "unverified"},
            capture_complete=True, events_complete=True, terminal_complete=True, exit_complete=True)
        assert rejected_outcome["status"] == "BLOCK"
        assert LIVE["current_host_audit_exit_code"](
            classification, rejected_outcome, failures=[], host_failures=[], audit_policy=[],
            observational_diagnostics=diagnostics) == 1


def test_final_audit_boundary_removes_late_current_host_attestations_only(tmp_path):
    """A parser cannot resurrect an unavailable host attestation after partition."""
    rows = _completed_current_host_rows(); data = state(tmp_path / "final-boundary")
    # Simulate the latest live ordering: partition has completed, then the
    # generic bootstrap classifier rewrites its pre-binding manifestation to
    # the exact unavailable-host label while assignment remains a direct row.
    late_policy = [
        {"thread_id": "child-thread", "role": "coordinator", "tool": "functions.exec",
         "violation": "pre_binding_host_action", "argument_digest": "a" * 12,
         "result_digest": "b" * 12},
        {"thread_id": "child-thread", "role": "coordinator", "tool": "spawn_agent",
         "violation": "worker_assignment_policy_unverified", "argument_digest": "c" * 12,
         "result_digest": "d" * 12},
    ]
    classification = OBSERVER["classify_audit_findings"](
        failures=[], hook_failures=[], host_failures=[], policy_violations=late_policy,
        open_sessions=[], open_cells=[])
    assert {item["reason"] for item in classification["evidence_integrity_invalidators"]} == {
        "mcp_first_bootstrap_unverified", "unverified_worker_assignment"}
    normalized, audit_policy, diagnostics = LIVE["current_host_final_audit_boundary"](
        data, classification, late_policy, [])
    assert audit_policy == []
    assert normalized["evidence_integrity_invalidators"] == []
    assert normalized["evidence_valid"] is True and normalized["score_eligible"] is True
    assert {item["violation"] for item in diagnostics} == {
        "mcp_first_bootstrap_unverified", "worker_assignment_policy_unverified"}
    assert all(item["unsupported_by_current_host"] and item["not_applicable"]
               and item["host_enforcement_state"] == "unverified" for item in diagnostics)
    accepted = OBSERVER["current_host_observational_qualification"](
        rows, data, normalized, {"status": "unverified"}, capture_complete=True,
        events_complete=True, terminal_complete=True, exit_complete=True)
    assert accepted["status"] == "observational_accept"
    assert LIVE["current_host_audit_exit_code"](
        normalized, accepted, failures=[], host_failures=[], audit_policy=audit_policy,
        observational_diagnostics=diagnostics) == 0

    # The unavailable labels add no outcome proof: supported outcome absence
    # still blocks after exactly the same final normalization.
    missing_rows = _completed_current_host_rows(); missing_rows.pop(2)
    missing = OBSERVER["current_host_observational_qualification"](
        missing_rows, data, normalized, {"status": "unverified"}, capture_complete=True,
        events_complete=True, terminal_complete=True, exit_complete=True)
    assert missing["status"] == "BLOCK"
    assert LIVE["current_host_audit_exit_code"](
        normalized, missing, failures=[], host_failures=[], audit_policy=audit_policy,
        observational_diagnostics=diagnostics) == 1

    # A genuine integrity failure is neither a supported unavailable label nor
    # a diagnostic and therefore survives the final boundary.
    hard = OBSERVER["classify_audit_findings"](
        failures=[{"thread_id": "child-thread", "outcome": "truncated"}],
        hook_failures=[], host_failures=[], policy_violations=late_policy,
        open_sessions=[], open_cells=[])
    hard_normalized, hard_policy, hard_diagnostics = LIVE["current_host_final_audit_boundary"](
        data, hard, late_policy, [])
    assert hard_normalized["evidence_valid"] is False
    assert hard_normalized["evidence_integrity_invalidators"][0]["reason"] == "truncated_or_unverified_observation"
    assert LIVE["current_host_audit_exit_code"](
        hard_normalized, accepted, failures=[{"thread_id": "child-thread", "outcome": "truncated"}],
        host_failures=[], audit_policy=hard_policy, observational_diagnostics=hard_diagnostics) == 1

    outside = {**data, "bootstrap_route": "other"}
    unchanged, outside_policy, outside_diagnostics = LIVE["current_host_final_audit_boundary"](
        outside, classification, late_policy, [])
    assert unchanged is classification and outside_policy is late_policy and outside_diagnostics == []


def test_normalized_bootstrap_labels_are_unconditionally_current_host_diagnostics(tmp_path):
    """Unsupported host attestations never become blockers in this route."""
    coordinator = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "functions.exec", "outcome": "covered_by_nested",
        "skill_instruction_read": True, "pre_binding_static_read": True,
        "active_coordinator_skill_read": True, "bounded_wrapper": True,
        "bounded_metadata_observed": True,
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
        "argument_digest": "a" * 12, "result_digest": "b" * 12,
    }
    mcp_label = {"thread_id": "child-thread", "role": "coordinator",
                 "tool": "mcp__cortex__create_task",
                 "violation": "mcp_first_bootstrap_unverified"}
    worker_label = {"thread_id": "child-thread", "role": "coordinator",
                    "tool": "spawn_agent",
                    "violation": "worker_assignment_policy_unverified"}
    rows = [coordinator, *_completed_current_host_rows(), *_strict_worker_skill_group_rows()]
    data = state(tmp_path / "strict-positive")
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        rows, data, [mcp_label, worker_label])
    assert blocking == []
    assert {item["violation"] for item in diagnostics} == {
        "mcp_first_bootstrap_unverified", "worker_assignment_policy_unverified"}

    # The labels stay diagnostic even beside hostile-shaped records; the actual
    # unsafe record itself must be reported as its own hard policy finding.
    for name, changed in (
        ("cache", {"path_target_class": "plugin_cache"}),
        ("mutation", {"mutation_observed": True}),
        ("executed", {"execution_status": "executed"}),
        ("enumeration", {"directory_enumeration": True}),
        ("mixed", {"catalogue_mixed": True}),
        ("ambiguous", {"actor_attribution_ambiguous": True}),
        ("truncated", {"truncated": True}),
        ("replay", {"replayed": True}),
        ("unknown-actor", {"role": "unknown"}),
        ("wrong-path", {"active_coordinator_skill_read": False}),
    ):
        bad_rows = [{**coordinator, **changed}, *_completed_current_host_rows(),
                    *_strict_worker_skill_group_rows()]
        blocking, diagnostics = OBSERVER["observational_policy_partition"](
            bad_rows, state(tmp_path / name), [mcp_label])
        assert blocking == [] and diagnostics[0]["violation"] == "mcp_first_bootstrap_unverified"
        assert diagnostics[0]["unsupported_by_current_host"] is True

    # The same applies to the unavailable assignment policy label. This does
    # not classify the unsafe worker call as safe; its own violation stays hard.
    for name, changed in (
        ("foreign-worker", {"thread_id": "foreign-worker"}),
        ("wrong-worker-path", {"path_target_class": "plugin_cache"}),
        ("wrong-profile", {"worker_skill_profile": "other-profile"}),
        ("worker-executed", {"execution_status": "executed"}),
        ("worker-mutation", {"mutation_observed": True}),
        ("worker-truncated", {"truncated": True}),
        ("worker-replay", {"replayed": True}),
    ):
        worker_rows = _strict_worker_skill_group_rows()
        worker_rows[-1] = {**worker_rows[-1], **changed}
        blocking, diagnostics = OBSERVER["observational_policy_partition"](
            [coordinator, *_completed_current_host_rows(), *worker_rows],
            state(tmp_path / name), [worker_label])
        assert blocking == [] and diagnostics[0]["violation"] == "worker_assignment_policy_unverified"
        assert diagnostics[0]["unsupported_by_current_host"] is True

    incomplete = [coordinator, *_completed_current_host_rows(), *_strict_worker_skill_group_rows(),
                  {"tool": "wait_agent", "outcome": "pending"}]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        incomplete, state(tmp_path / "incomplete"), [mcp_label, worker_label])
    assert blocking == [] and {item["violation"] for item in diagnostics} == {
        "mcp_first_bootstrap_unverified", "worker_assignment_policy_unverified"}


def test_current_host_final_partition_keeps_generic_prebinding_manifestations_blocking(tmp_path):
    """Generic pre-binding labels never bypass the strict worker-read matcher."""
    rows = _completed_current_host_rows()
    data = state(tmp_path / "final-live-shape")
    prebinding = {"thread_id": "child-thread", "role": "coordinator",
                  "tool": "functions.exec", "violation": "pre_binding_host_action",
                  "outcome": "success", "argument_digest": "a" * 12,
                  "result_digest": "b" * 12}
    for changed in (
        {}, {"mutation_observed": True}, {"execution_status": "executed"},
        {"path_target_class": "plugin_cache"}, {"private_target_access": True},
        {"project_target_access": True}, {"thread_id": "foreign-worker"},
        {"path_argument_digest": "c" * 12}, {"operation": "write_file"},
        {"worker_skill_profile": "other-profile"}, {"replayed": True},
        {"truncated": True},
    ):
        finding = {**prebinding, **changed}
        blocking, diagnostics = OBSERVER["observational_policy_partition"](rows, data, [finding])
        assert diagnostics == [] and blocking == [finding]

    incomplete = _completed_current_host_rows(); incomplete.append({"tool": "wait_agent", "outcome": "pending"})
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        incomplete, state(tmp_path / "final-incomplete"), [prebinding])
    assert diagnostics == [] and blocking == [prebinding]


def test_unavailable_current_host_labels_never_block_incomplete_supported_evidence(tmp_path):
    """Incomplete outcome remains a separate hard failure, not a label failure."""
    labels = (
        "worker_assignment_policy_unverified",
        "mcp_first_bootstrap_unverified",
    )
    for case, mutate in (
        ("missing", lambda items: items.pop(2)),
        ("incomplete", lambda items: items.append({"tool": "wait_agent", "outcome": "pending"})),
    ):
        rows = _completed_current_host_rows(); mutate(rows)
        policy = [{"thread_id": "child-thread", "role": "coordinator",
                   "tool": "mcp__cortex__create_task", "violation": label}
                  for label in labels]
        blocking, diagnostics = OBSERVER["observational_policy_partition"](
            rows, state(tmp_path / case), policy)
        assert blocking == []
        assert {row["violation"] for row in diagnostics} == set(labels)


def test_mcp_first_all_tools_catalogue_is_exact_metadata_only_and_precedes_create(tmp_path):
    """The approved host catalogue envelope is distinct from generic wrappers."""
    catalogue = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "tool_catalogue_search", "catalogue_discovery": True,
        "nested": True,
        "catalogue_value_free": True, "catalogue_bounded": True,
        "catalogue_project_free": True, "catalogue_private_free": True,
        "catalogue_mutation_free": True, "catalogue_mixed": False,
        "truncated": False, "outcome": "success", "host_receipt_observed": True,
    }
    helper = OBSERVER["approved_tool_catalogue_envelope"]
    assert helper("text(ALL_TOOLS)", [])
    assert helper("text(ALL_TOOLS);", [])
    assert helper('text(ALL_TOOLS.filter(x => x.name.startsWith("mcp__cortex__")))', [])
    assert helper('const xs = ALL_TOOLS.filter(x => x.name.startsWith("mcp__cortex__")); text(xs);', [])
    assert not helper('text(ALL_TOOLS.filter(x => x.name.startsWith(secret)))', [])
    filtered='text(ALL_TOOLS.filter(x => /create_task|cortex/i.test(x.name+" "+x.description)))'
    assert helper(filtered, [])
    for source in (filtered+'; tools.exec_command({})',
                   filtered.replace('x.name','x["name"]'),
                   filtered.replace('x.name','x.name()'),
                   filtered.replace('/create_task|cortex/i','/.* /i'),
                   filtered.replace('x.description','x.private'),
                   filtered.replace('x.description','other.description')):
        assert not helper(source, [])
    for source, nested in (
        ("text(ALL_TOOLS.filter(item => item.name))", []),
        ("text(ALL_TOOLS); await tools.mcp__cortex__create_task({})", []),
        ("text(ALL_TOOLS)", [("exec_command", "a" * 12, "{}")]),
        ("text(ALL_TOOLS['/private'])", []),
    ):
        assert not helper(source, nested)
    receipt = OBSERVER["mcp_first_bootstrap_receipt"]([catalogue, create()], state(tmp_path / "catalogue"))
    assert receipt["status"] == "complete"
    for key, value in (
        ("catalogue_value_free", False), ("catalogue_project_free", None),
        ("catalogue_private_free", None), ("catalogue_mutation_free", False),
        ("catalogue_mixed", True), ("truncated", True),
    ):
        rejected = dict(catalogue); rejected[key] = value
        invalid([rejected, create()], state(tmp_path / (key + "-bad")), "pre_binding_host_action")


def test_mcp_event_key_correlation_precedes_order_and_rejects_duplicates():
    candidate = {
        "thread_id": "child-thread", "tool": "mcp__cortex__create_task",
        "task_id": "t_child", "host_receipt_observed": True,
        "host_receipt_outcome": "success", "outcome": "success",
    }
    candidate["canonical_call_key"] = OBSERVER["canonical_mcp_call_key"](candidate)
    event = {"thread_id": "child-thread", "operation": "create_task", "task_id": "t_child"}
    assert OBSERVER["event_call_candidate"]([candidate], 0, event=event) is candidate
    assert OBSERVER["event_call_candidate"]([candidate, dict(candidate)], 0, event=event) is None


def test_mcp_first_live_order_treats_one_direct_public_create_as_binding_transition(tmp_path):
    """Mirror the live order: launcher/native/static setup then create at index 3."""
    static = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "exec_command", "skill_instruction_read": True, "pre_binding_static_read": True,
        "active_coordinator_skill_read": True,
        "bounded_wrapper": True, "project_target_access": False, "private_target_access": False,
        "mutation_observed": False, "truncated": False, "outcome": "success",
        "host_receipt_observed": True,
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
    }
    transport = {"thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
                 "tool": "functions.exec", "direct_public_mcp_transport": True}
    direct = create(nested=True, direct_public_mcp=True)
    receipt = OBSERVER["mcp_first_bootstrap_receipt"](
        [{"role": "runtime", "tool": "launcher_transport"},
         {"role": "coordinator", "tool": "native_user_input"}, static, transport, direct],
        state(tmp_path / "live-order"))
    assert receipt["status"] == "complete"
    invalid([static, create(nested=True)], state(tmp_path / "wrapped"), "bootstrap_wrapped_receipt")
    invalid([static, create(outcome="error", host_receipt_outcome="error")],
            state(tmp_path / "failed"), "bootstrap_mcp_failed")
    invalid([static, create(replayed=True)], state(tmp_path / "replayed"), "bootstrap_replayed_receipt")
    invalid([static, create(), create()], state(tmp_path / "duplicate"), "bootstrap_duplicate_receipt")


def test_mcp_first_current_host_wrapper_preserves_exact_nested_skill_metadata(tmp_path):
    """A covered functions.exec read is not relabelled as a generic probe."""
    nested = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "exec_command", "nested": True, "skill_instruction_read": True,
        "pre_binding_static_read": True, "active_coordinator_skill_read": True,
        "bounded_wrapper": True, "project_target_access": False, "private_target_access": False,
        "mutation_observed": False, "truncated": False, "outcome": "success",
        "host_receipt_observed": True,
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
    }
    wrapper = {**nested, "tool": "functions.exec", "nested": False,
               "outcome": "covered_by_nested", "host_receipt_observed": False,
               "bounded_metadata_observed": True}
    receipt = OBSERVER["mcp_first_bootstrap_receipt"]([wrapper, nested, create()],
                                                       state(tmp_path / "wrapper"))
    assert receipt["status"] == "complete", receipt
    # Dropping the exact bounded metadata restores the ordinary hard block.
    invalid([{key: value for key, value in wrapper.items() if key != "bounded_metadata_observed"},
             nested, create()], state(tmp_path / "wrapper-missing"), "pre_binding_host_action")


def test_mcp_first_current_host_coordinator_skill_wrapper_accepts_only_complete_metadata_shape(tmp_path):
    """Replay the retained final3 wrapper shape without widening generic setup.

    Current Codex emits one covered ``functions.exec`` row and one nested
    ``exec_command`` row for each manifest-bound coordinator orchestrator skill
    read.  It marks both rows as bounded and active, but omits redundant false
    scope/truncation members.  The active marker is produced only after the
    literal candidate-manifest check, so it is the strict proof—not a cache
    path label or the later outcome—that admits this setup record.
    """
    outer = {
        "thread_id": "child-thread", "parent_thread_id": None, "role": "coordinator",
        "tool": "functions.exec", "outcome": "covered_by_nested",
        "skill_instruction_read": True, "pre_binding_static_read": True,
        "active_coordinator_skill_read": True, "bounded_wrapper": True,
        "bounded_metadata_observed": True,
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
        "argument_digest": "a" * 12, "result_digest": "b" * 12,
    }
    nested = {**outer, "tool": "exec_command", "nested": True,
              "outcome": "covered_by_command_execution", "argument_digest": "c" * 12,
              "wrapper_argument_digest": "a" * 12, "intent_digest": "d" * 12}
    receipt = OBSERVER["mcp_first_bootstrap_receipt"](
        [outer, nested, create()], state(tmp_path / "final3-coordinator-skill"))
    assert receipt["status"] == "complete", receipt
    assert OBSERVER["mcp_first_bootstrap_violations"](
        [outer, nested, create()], state(tmp_path / "final3-coordinator-skill-violations")) == []

    # The full retained final3 shape still needs independently complete outcome
    # proof.  Once it has that proof, it is observationally accepted with the
    # unavailable-host labels printed as diagnostics; the static setup does not
    # itself become outcome evidence or host enforcement.
    full_rows = [outer, nested, *_completed_current_host_rows(), *_strict_worker_skill_group_rows()]
    full_state = state(tmp_path / "final3-complete")
    bootstrap = OBSERVER["mcp_first_bootstrap_receipt"](full_rows, full_state)
    assert bootstrap["status"] == "complete"
    policy = [
        {"thread_id": "child-thread", "role": "coordinator", "tool": "spawn_agent",
         "violation": "worker_assignment_policy_unverified"},
        {"thread_id": "child-thread", "role": "coordinator", "tool": "mcp__cortex__create_task",
         "violation": "mcp_first_bootstrap_unverified"},
    ]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](full_rows, full_state, policy)
    assert blocking == [] and len(diagnostics) == 2
    classification = OBSERVER["classify_audit_findings"](
        failures=[], hook_failures=[], host_failures=[], policy_violations=blocking,
        open_sessions=[], open_cells=[])
    qualified = OBSERVER["current_host_observational_qualification"](
        full_rows, full_state, classification, bootstrap, capture_complete=True,
        events_complete=True, terminal_complete=True, exit_complete=True)
    assert qualified["status"] == "observational_accept"
    assert LIVE["current_host_audit_exit_code"](
        classification, qualified, failures=[], host_failures=[], audit_policy=[],
        observational_diagnostics=diagnostics) == 0

    # Nearby cache files, another skill/profile, directory-like records,
    # mutation, unknown provenance, catalogue mixing, and an explicit unknown
    # truncation value cannot borrow the covered-wrapper exception.
    for name, changed in (
        ("nearby-cache", {"active_coordinator_skill_read": False}),
        ("different-skill", {"active_coordinator_skill_read": False,
                             "coordinator_skill_profile": "worker-general"}),
        ("directory", {"path_access_kind": "static_mention"}),
        ("mutation", {"mutation_observed": True}),
        ("execution", {"execution_status": "executed"}),
        ("replay", {"replayed": True}),
        ("ambiguous-actor", {"actor_attribution_ambiguous": True}),
        ("enumeration", {"directory_enumeration": True}),
        ("unknown-actor", {"role": "unknown"}),
        ("unknown-path", {"path_target_class": "plugin_cache"}),
        ("mixed-catalogue", {"catalogue_discovery": True, "catalogue_mixed": True}),
        ("unknown-truncation", {"truncated": None}),
    ):
        invalid([{**outer, **changed}, create()], state(tmp_path / name), "pre_binding_host_action")

    incomplete = [outer, nested, *_completed_current_host_rows(),
                  {"tool": "wait_agent", "outcome": "pending"}]
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        incomplete, state(tmp_path / "incomplete-outcome"), policy)
    assert blocking == [] and {item["violation"] for item in diagnostics} == {
        "worker_assignment_policy_unverified", "mcp_first_bootstrap_unverified"}


def test_direct_public_create_marker_rejects_aliases_and_mixed_wrappers():
    marker = OBSERVER["direct_public_cortex_create_task"]
    direct = [("mcp__cortex__create_task", "a" * 12, "{}")]
    assert marker("await tools.mcp__cortex__create_task({});", direct)
    assert not marker("const create = tools.mcp__cortex__create_task; await create({});", direct)
    assert not marker("await tools.mcp__cortex__create_task({}); await tools.exec_command({});",
                      direct + [("exec_command", "b" * 12, "{}")])


def test_current_host_static_bundle_reads_allow_only_exact_skill_or_public_declaration(tmp_path, monkeypatch):
    """Model the observed installed-cache reads without widening cache access."""
    home = tmp_path / "home"
    release = home / ".cortex-dev/.codex/plugins/cache/cortex/cortex/1.15.9-test"
    skill = release / "skills/orchestrator/SKILL.md"
    declaration = release / ".mcp.json"
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_bytes())
    declaration.write_bytes((ROOT / "plugins/cortex/.mcp.json").read_bytes())
    candidate = home / ".cortex-dev/.codex/cortex-candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps({"version": "1.15.9-test"})); candidate.chmod(0o600)
    monkeypatch.setattr(Path, "home", lambda: home)
    check = OBSERVER["skill_instruction_read"]
    command = lambda path: json.dumps({"cmd": f"cat {path}"})
    assert check("exec_command", command(skill), coordinator_only=True)
    assert check("exec_command", command(declaration), coordinator_only=True)
    assert OBSERVER["active_coordinator_skill_read"]("exec_command", command(skill))
    assert not OBSERVER["active_coordinator_skill_read"]("exec_command", command(declaration))
    assert not check("exec_command", command(release / "skills/orchestrator"), coordinator_only=True)
    assert not check("exec_command", command(release / "skills/orchestrator/SKILL.md.bak"), coordinator_only=True)
    assert not check("exec_command", command(release / "skills/*/SKILL.md"), coordinator_only=True)
    assert not check("exec_command", json.dumps({"cmd": f"sed -i 1d {skill}"}), coordinator_only=True)
    assert not check("exec_command", command(release / "scripts/cortex_runtime/store.py"), coordinator_only=True)
    reference=release / "skills/orchestrator/references/worker-routing.md"
    reference.parent.mkdir()
    reference.write_bytes((ROOT / "plugins/cortex/skills/orchestrator/references/worker-routing.md").read_bytes())
    assert check("exec_command", command(reference), coordinator_only=True)
    assert OBSERVER["active_coordinator_skill_read"]("exec_command", command(reference))
    assert not OBSERVER["call_policy_flags"]("exec_command", command(reference), "coordinator", str(tmp_path))
    reference.write_text("tampered instructions")
    assert not check("exec_command", command(reference), coordinator_only=True)
    unknown=reference.parent / "private.md"
    unknown.write_text("not linked")
    assert not check("exec_command", command(unknown), coordinator_only=True)


def test_current_host_bounded_shell_wrapper_allows_only_exact_registered_worker_skill(tmp_path, monkeypatch):
    """The host transport wrapper cannot turn a cache probe into a skill read."""
    home = tmp_path / "home"
    release = home / ".cortex-dev/.codex/plugins/cache/cortex/cortex/1.15.9-test"
    skill = release / "skills/worker-general/SKILL.md"
    neighbor = release / "skills/worker-general/references/diagnostic.md"
    registry = release / "agents/general.toml"
    skill.parent.mkdir(parents=True); neighbor.parent.mkdir(parents=True)
    skill.write_bytes((ROOT / "plugins/cortex/skills/worker-general/SKILL.md").read_bytes())
    neighbor.write_text("not a worker instruction leaf")
    registry.parent.mkdir(parents=True); registry.write_text("not a skill")
    monkeypatch.setattr(Path, "home", lambda: home)
    wrapped = lambda inner: json.dumps({"cmd": f"bash -lc {json.dumps(inner)}"})
    assert OBSERVER["worker_skill_read"]("exec_command", wrapped(f"sed -n '1,80p' {skill}"))
    assert OBSERVER["worker_skill_profile"]("exec_command", wrapped(f"cat {skill}")) == "general"
    metadata = OBSERVER["path_policy_metadata"](
        "exec_command", wrapped(f"cat {skill}"), "general", "/fixture")
    assert metadata["path_policy_decision"] == "allowed"
    assert metadata["path_access_kind"] == "approved_instruction_read"
    assert OBSERVER["call_policy_flags"](
        "exec_command", wrapped(f"cat {skill}"), "general", "/fixture") == []
    for command in (
        f"cat {neighbor}",
        f"cat {registry}",
        f"cat {skill.parent}",
        f"cat {skill} && touch {tmp_path / 'mutated'}",
        f"find {release} -name SKILL.md",
        f"bash -lc {json.dumps(f'cat {skill}')}",
    ):
        arguments = wrapped(command)
        assert not OBSERVER["worker_skill_read"]("exec_command", arguments)
        assert OBSERVER["path_policy_metadata"](
            "exec_command", arguments, "general", "/fixture")["path_policy_decision"] == "unauthorized_access"
        assert "forbidden_plugin_or_cache_access" in OBSERVER["call_policy_flags"](
            "exec_command", arguments, "general", "/fixture")


def test_current_host_functions_exec_static_read_transport_is_exact_for_coordinator_and_worker(
        tmp_path, monkeypatch):
    """Require one direct nested read and same-identifier result forwarding."""
    home = tmp_path / "home"
    release = home / ".cortex-dev" / ".codex" / "plugins" / "cache" / "cortex" / "cortex" / "1.15.9-test"
    coordinator_skill = release / "skills" / "orchestrator" / "SKILL.md"
    worker_skill = release / "skills" / "worker-general" / "SKILL.md"
    coordinator_skill.parent.mkdir(parents=True)
    worker_skill.parent.mkdir(parents=True)
    coordinator_skill.write_bytes((ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_bytes())
    worker_skill.write_bytes((ROOT / "plugins/cortex/skills/worker-general/SKILL.md").read_bytes())
    candidate = home / ".cortex-dev" / ".codex" / "cortex-candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps({"version": "1.15.9-test"}))
    candidate.chmod(0o600)
    monkeypatch.setattr(Path, "home", lambda: home)

    def wrapper(command, binding="result"):
        nested_arguments = json.dumps({"cmd": command}, separators=(",", ":"))
        return (f"const {binding} = await tools.exec_command({nested_arguments}); "
                f"text({binding});")

    for path, coordinator_only, worker_only in (
            (coordinator_skill, True, False), (worker_skill, False, True)):
        for binding in ("result", "output", "command_result_1", "_read_result", "$output"):
            source = wrapper(f"cat {path}", binding)
            nested = OBSERVER["nested_tool_invocations"](source)
            assert OBSERVER["bounded_static_read_transport"](source, nested)
            split=source.replace(f'text({binding});',
                f'text({binding}.output); text(`exit_code=${{{binding}.exit_code}}`);')
            assert OBSERVER['bounded_static_read_transport'](split, nested)
            session=split+f' if({binding}.session_id) text(`session_id=${{{binding}.session_id}}`);'
            assert OBSERVER['bounded_static_read_transport'](session, nested)
            nested_arguments = json.dumps({"cmd": f"cat {path}"}, separators=(",", ":"))
            assert OBSERVER["skill_instruction_read"](
                "exec_command", nested_arguments,
                coordinator_only=coordinator_only, worker_only=worker_only)
            if worker_only:
                # The manifest-bound worker profile is value-free metadata and
                # remains recoverable even if the host denies this read before
                # dispatch; it is not a completion/assignment receipt.
                assert OBSERVER["worker_skill_profile"](
                    "exec_command", nested_arguments) == "general"

    coordinator_source = wrapper(f"cat {coordinator_skill}")
    coordinator_nested = OBSERVER["nested_tool_invocations"](coordinator_source)
    for source in (
            coordinator_source.replace("text(result);", "text(result.output);"),
            coordinator_source.replace("text(result);", "return text(result);"),
            coordinator_source + " text(result);",
            coordinator_source.replace("const result", "let result"),
            coordinator_source.replace(
                "); text(result);", "); await tools.exec_command({}); text(result);"),
            "const run = tools.exec_command; const result = await run({}); text(result);",
    ):
        assert not OBSERVER["bounded_static_read_transport"](
            source, OBSERVER["nested_tool_invocations"](source))

    # Every ECMAScript reserved/future-reserved word and strict-invalid binding
    # name is rejected, while the one-call grammar itself remains unchanged.
    for binding in (
            "arguments", "await", "break", "case", "catch", "class", "const",
            "continue", "debugger", "default", "delete", "do", "else", "enum",
            "eval", "export", "extends", "false", "finally", "for", "function",
            "if", "implements", "import", "in", "instanceof", "interface", "let",
            "new", "null", "package", "private", "protected", "public", "return",
            "static", "super", "switch", "this", "throw", "true", "try", "typeof",
            "var", "void", "while", "with", "yield"):
        source = wrapper(f"cat {coordinator_skill}", binding)
        assert not OBSERVER["bounded_static_read_transport"](
            source, OBSERVER["nested_tool_invocations"](source)), binding

    # A denied exact worker read still retains the closed registered profile
    # marker, while the policy layer continues to report receipt acquisition as
    # failed until a complete success receipt is observed.
    denied_worker = {
        "thread_id": "worker-thread", "parent_thread_id": "child-thread", "role": "general",
        "tool": "exec_command", "outcome": "error", "worker_skill_receipt_required": True,
        "skill_instruction_read": True, "worker_skill_profile": "general",
        "path_policy_provenance": "observer_literal_marker",
        "path_target_class": "approved_instruction_or_static_mention",
        "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
        "execution_status": "denied_before_dispatch", "result_digest": "a" * 12,
    }
    assert "worker_skill_load_failed" in {
        item["violation"] for item in OBSERVER["call_policy_violations"]([denied_worker])
    }
    assert not OBSERVER["bounded_static_read_transport"](
        coordinator_source, [(*coordinator_nested[0][:2], "different")])

    # The binding must be a single safe identifier used exactly once for the
    # direct call and once for complete forwarding.  Aliases, property access,
    # returns, extra calls, and non-const/invalid bindings remain fail-closed.
    output_source = wrapper(f"cat {coordinator_skill}", "output")
    for source in (
            output_source.replace("text(output);", "text(result);"),
            output_source.replace("text(output);", "text(output.value);"),
            output_source.replace("text(output);", "return text(output);"),
            output_source.replace("text(output);", "const copy = output; text(copy);"),
            output_source + " await tools.exec_command({});",
            output_source.replace("const output", "let output"),
            output_source.replace("const output", "const output.value"),
            output_source.replace("const output", "const 1output"),
            output_source.replace("const output", "const await"),
    ):
        assert not OBSERVER["bounded_static_read_transport"](
            source, OBSERVER["nested_tool_invocations"](source))


def test_current_host_functions_exec_static_read_transport_marks_observed_coordinator_wrapper(
        tmp_path, monkeypatch):
    """Exercise wrapper promotion only after the exact nested source parses."""
    home = tmp_path / "home"
    release = home / ".cortex-dev" / ".codex" / "plugins" / "cache" / "cortex" / "cortex" / "1.15.9-test"
    skill = release / "skills" / "orchestrator" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT / "plugins/cortex/skills/orchestrator/SKILL.md").read_bytes())
    candidate = home / ".cortex-dev" / ".codex" / "cortex-candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps({"version": "1.15.9-test"}))
    candidate.chmod(0o600)
    monkeypatch.setattr(Path, "home", lambda: home)

    nested_arguments = json.dumps({"cmd": f"cat {skill}"}, separators=(",", ":"))
    source = f"const result = await tools.exec_command({nested_arguments}); text(result);"
    rollout = tmp_path / "coordinator.jsonl"
    events = tmp_path / "events"
    events.mkdir()

    def entry(stamp, payload):
        return json.dumps({"timestamp": stamp, "type": "response_item", "payload": payload})

    rollout.write_text("\n".join([
        entry("2026-09-13T12:00:00.000Z", {
            "type": "custom_tool_call", "call_id": "read", "name": "functions.exec",
            "input": source,
        }),
        entry("2026-09-13T12:00:00.100Z", {
            "type": "custom_tool_call_output", "call_id": "read",
            "output": "skill text\nexit_status=0",
        }),
    ]) + "\n")
    codex = home / ".cortex-dev" / ".codex"
    with sqlite3.connect(codex / "state_5.sqlite") as db:
        db.execute(
            "CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,"
            "reasoning_effort TEXT,created_at INTEGER,cwd TEXT)"
        )
        db.execute(
            "CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)"
        )
        db.execute(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?)",
            ("root", str(rollout), None, "gpt-5.6-luna", "high", 100, "/fixture"),
        )

    rows = OBSERVER["observed_tool_calls"]({
        "workdir": "/fixture", "started_at": 100, "thread_created_since": 100,
        "events": str(events),
    })
    wrapper = [row for row in rows if row.get("tool") == "functions.exec"]
    nested = [row for row in rows if row.get("tool") == "exec_command"]
    assert len(wrapper) == len(nested) == 1
    assert wrapper[0]["bounded_metadata_observed"] is True
    assert wrapper[0]["active_coordinator_skill_read"] is True
    assert nested[0]["pre_binding_static_read"] is True


def test_current_host_worker_wrapper_result_credits_exact_skill_receipt(tmp_path, monkeypatch):
    """A successful permitted worker read must create the normal receipt."""
    home = tmp_path / "home"
    release = home / ".cortex-dev" / ".codex" / "plugins" / "cache" / "cortex" / "cortex" / "1.15.9-test"
    skill = release / "skills" / "worker-general" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes((ROOT / "plugins/cortex/skills/worker-general/SKILL.md").read_bytes())
    monkeypatch.setattr(Path, "home", lambda: home)

    nested_arguments = json.dumps({"cmd": f"bash -lc 'cat {skill}'"}, separators=(",", ":"))
    source = f"const output = await tools.exec_command({nested_arguments}); text(output);"
    child_rollout = tmp_path / "worker.jsonl"
    events = tmp_path / "events"
    events.mkdir()

    def entry(stamp, payload):
        return json.dumps({"timestamp": stamp, "type": "response_item", "payload": payload})

    complete = (ROOT / "plugins/cortex/skills/worker-general/SKILL.md").read_text()
    child_rollout.write_text("\n".join([
        entry("2026-09-13T12:00:00.000Z", {
            "type": "custom_tool_call", "call_id": "read", "name": "functions.exec",
            "input": source,
        }),
        entry("2026-09-13T12:00:00.100Z", {
            "type": "custom_tool_call_output", "call_id": "read",
            "output": complete + "\nexit_status=0",
        }),
    ]) + "\n")
    database = home / ".cortex-dev" / ".codex" / "state_5.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE threads(id TEXT,rollout_path TEXT,agent_role TEXT,model TEXT,reasoning_effort TEXT,created_at INTEGER,cwd TEXT)")
        db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)")
        db.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", [
            ("root", str(tmp_path / "root.jsonl"), None, "gpt-5.6-luna", "high", 100, "/fixture"),
            ("child", str(child_rollout), "general", "gpt-5.6-luna", "medium", 100, "/fixture"),
        ])
        db.execute("INSERT INTO thread_spawn_edges VALUES (?,?)", ("root", "child"))

    rows = OBSERVER["observed_tool_calls"]({
        "workdir": "/fixture", "started_at": 100, "thread_created_since": 100,
        "events": str(events),
    })
    worker_rows = [row for row in rows if row.get("thread_id") == "child"]
    receipts = [row for row in worker_rows if row.get("worker_skill_assignment_receipt") == "complete_exact_assigned"]
    assert len(receipts) == 1
    assert receipts[0]["worker_skill_complete"] is True
    assert receipts[0]["worker_skill_profile"] == "general"
    assert receipts[0]["outcome"] == "success"


def test_live_owner_identity_is_required_for_native_root_binding_and_rejects_replacement(tmp_path):
    live_globals = LIVE["_exact_cleanup_identity"].__globals__
    live_globals["tmux"] = lambda *args, **kwargs: SimpleNamespace(
        stdout=("7\n" if args[0] == "display-message" else "cortex-markdown-smoke|$7|1|%8|9\n"))
    live_globals["_proc_start_ticks"] = lambda pid: 10
    data = {"tmux_server_pid": 7, "tmux_session_name": "cortex-markdown-smoke",
            "tmux_session_id": "$7", "tmux_session_created": 1, "tmux_pane_id": "%8",
            "tmux_pane_pid": 9, "tmux_pane_start_ticks": 10}
    assert LIVE["_exact_cleanup_identity"](data) is None
    data["tmux_server_pid"] = 8
    try:
        LIVE["_exact_cleanup_identity"](data)
    except RuntimeError as exc:
        assert "replaced tmux server" in str(exc)
    else:
        raise AssertionError("replaced server identity was accepted")
