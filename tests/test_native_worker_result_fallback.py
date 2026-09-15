"""Fail-closed proofs for the current-host native worker result fallback."""

import hashlib
import json
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="native_result_test")


def body(**changes):
    value = {
        "schema": "cortex-native-worker-result-v1",
        "worker_thread_id": "worker", "parent_thread_id": "root", "task_id": "t_child",
        "profile": "general", "status": "success", "worker_mcp": "unavailable",
        "assignment_digest": "a" * 64,
        "artifacts": [{"reference": "README.md", "sha256": "b" * 64}],
        "checks": [{"command": "pytest focused", "exit_code": 0, "receipt_digest": "c" * 64}],
        "limits": ["artifact hash asserted"],
    }
    value.update(changes)
    value["result_digest"] = OBSERVER["canonical_native_worker_result"](value)
    return value


def final(value):
    return {"type": "agent_message", "author": "/root/general", "content": [{"text":
        "Message Type: FINAL_ANSWER\nCortex native worker result:\n```json\n"
        + json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n```"}]}


def rows(value):
    return [
        {"thread_id": "root", "role": "coordinator", "tool": "spawn_agent", "outcome": "success",
         "spawned_thread_id": "worker", "assigned_profile": "general", "assignment_digest": "a" * 64},
        {"thread_id": "root", "role": "coordinator", "tool": "wait_agent", "outcome": "success",
         "wait_target_thread_ids": ["worker"]},
        {"thread_id": "root", "role": "coordinator", "tool": "mcp__cortex__create_task", "outcome": "success",
         "task_id": "t_child"},
        {"thread_id": "worker", "parent_thread_id": "root", "role": "general",
         "tool": "native_agent_result", "outcome": "success", "native_worker_result": value},
        {"thread_id": "root", "role": "coordinator", "tool": "mcp__cortex__write_report", "outcome": "success",
         "delegation_evidence_report": True, "report_id": "r_0123456789ab"},
    ]


def test_closed_native_worker_result_schema_and_digest_are_required():
    value = body()
    parsed, error = OBSERVER["parse_native_worker_result"](
        "Cortex native worker result:\n```json\n" + json.dumps(value) + "\n```")
    assert error is None and parsed == value
    extra = dict(value, unexpected=True)
    extra["result_digest"] = OBSERVER["canonical_native_worker_result"](extra)
    assert OBSERVER["parse_native_worker_result"](
        "Cortex native worker result:\n```json\n" + json.dumps(extra) + "\n```")[1] == "native_worker_result_schema_invalid"
    bad = dict(value, result_digest="0" * 64)
    assert OBSERVER["parse_native_worker_result"](
        "Cortex native worker result:\n```json\n" + json.dumps(bad) + "\n```")[1] == "native_worker_result_schema_invalid"


def test_native_result_metadata_and_closed_correlation_accept_only_one_complete_route():
    value = body()
    parsed = OBSERVER["native_agent_result_metadata"](final(value))
    assert parsed["native_worker_result"] == value
    assert OBSERVER["native_worker_result_fallback_violations"](rows(value)) == []
    observation = rows(value)[3]
    assert observation.get("host_enforcement_state") is None


def test_native_result_fallback_rejects_missing_wait_assignment_duplicate_and_report():
    value = body()
    for mutate, expected in (
        (lambda entries: entries.pop(1), "native_worker_result_wait_mismatch"),
        (lambda entries: entries[0].update(assignment_digest="d" * 64), "native_worker_result_assignment_mismatch"),
        (lambda entries: entries[3].update(native_worker_result=body(profile="debugger")),
         "native_worker_result_correlation_invalid"),
        (lambda entries: entries.append(dict(entries[3])), "native_worker_result_duplicate"),
        (lambda entries: entries.pop(), "native_worker_result_delegation_evidence_missing"),
    ):
        entries = rows(value); mutate(entries)
        assert {row["violation"] for row in OBSERVER["native_worker_result_fallback_violations"](entries)} == {expected}


def test_native_worker_result_final_parser_refuses_prose_and_worker_mcp_available():
    value = body(worker_mcp="available")
    assert OBSERVER["native_agent_result_metadata"](final(value))["native_worker_result"]["worker_mcp"] == "available"
    malformed = final(body())
    malformed["content"][0]["text"] += "\nextra"
    assert OBSERVER["native_agent_result_metadata"](malformed)["native_worker_result_error"] == "native_worker_result_missing"


def test_observational_mode_retains_only_unavailable_host_labels_without_proof():
    """Unavailable host attestations are diagnostic; other receipt gaps are not."""
    value = body()
    entries = rows(value)
    entries[2].update(host_receipt_observed=True, host_receipt_outcome="success",
                      result_receipt_observed=True, server_observed=False,
                      direct_public_mcp=True, parent_thread_id=None,
                      replayed=False, truncated=False)
    entries[3].update(report_id="r_0123456789ab", host_receipt_observed=True,
                      host_receipt_outcome="success", replayed=False, truncated=False)
    entries[4].update(host_receipt_observed=True, host_receipt_outcome="success",
                      result_receipt_observed=True, replayed=False, truncated=False,
                      wrapper_outcome="success")
    artifact_binding = (hashlib.sha256(b"README.md").hexdigest()[:12], "b" * 64)
    entries.extend([
        {"thread_id": "worker", "parent_thread_id": "root", "role": "general",
         "tool": "mcp__cortex__write_report", "report_id": "r_0123456789ab",
         "outcome": "success", "host_receipt_observed": True,
         "host_receipt_outcome": "success", "replayed": False, "truncated": False},
        {"thread_id": "root", "parent_thread_id": None, "role": "coordinator",
         "tool": "mcp__cortex__read_report", "report_id": "r_0123456789ab",
         "outcome": "success", "host_receipt_observed": True,
         "host_receipt_outcome": "success", "replayed": False, "truncated": False,
         "reported_artifact_bindings": [artifact_binding]},
    ])
    policy = [
        {"thread_id": "root", "role": "coordinator", "tool": "spawn_agent",
         "violation": "worker_assignment_policy_unverified"},
        {"thread_id": "worker", "role": "general", "tool": "mcp__cortex__read_report",
         "violation": "worker_project_action_before_skill_receipt"},
        {"thread_id": "worker", "role": "general", "tool": "exec_command",
         "violation": "worker_skill_route_mismatch"},
        {"thread_id": "root", "role": "coordinator", "tool": "mcp__cortex__create_task",
         "violation": "cortex_call_missing_server_event"},
        {"thread_id": "root", "role": "coordinator", "tool": "read_file",
         "violation": "pre_binding_host_action"},
    ]
    state = {"bootstrap_route": "current_host_mcp_first",
             "bootstrap_expected_child_thread_id": "root"}

    # The assignment-policy capability is not emitted by the current host, so
    # that exact label is diagnostic. Worker receipt gaps remain hard unless a
    # separate exact successful assigned-SKILL receipt proves completion.
    blocking, diagnostics = OBSERVER["observational_policy_partition"](entries, state, policy)
    assert {row["violation"] for row in diagnostics} == {
        "worker_assignment_policy_unverified", "cortex_call_missing_server_event"}
    assert {row["violation"] for row in blocking} == {
        "worker_project_action_before_skill_receipt", "worker_skill_route_mismatch",
        "pre_binding_host_action"}

    entries.append(
        {"thread_id": "worker", "parent_thread_id": "root", "role": "general",
         "tool": "exec_command", "outcome": "error", "worker_skill_receipt_required": True,
         "skill_instruction_read": True, "worker_skill_profile": "general",
         "path_policy_provenance": "observer_literal_marker",
         "path_target_class": "approved_instruction_or_static_mention",
         "path_access_kind": "approved_instruction_read", "path_policy_decision": "allowed",
         "execution_status": "denied_before_dispatch", "truncated": False,
         "replayed": False, "private_target_access": False,
         "project_target_access": False, "mutation_observed": False,
         "result_digest": "d" * 12})
    blocking, diagnostics = OBSERVER["observational_policy_partition"](entries, state, policy)
    assert {row["violation"] for row in diagnostics} == {
        "worker_assignment_policy_unverified", "cortex_call_missing_server_event"}
    assert {row["violation"] for row in blocking} == {
        "worker_project_action_before_skill_receipt", "worker_skill_route_mismatch",
        "pre_binding_host_action"}
    assert {row["host_enforcement_state"] for row in diagnostics} == {"unverified"}

    # Truncation still blocks the real worker receipt-gap findings, while the
    # unsupported assignment-policy label remains a printed diagnostic.
    entries[-1]["truncated"] = True
    blocking, diagnostics = OBSERVER["observational_policy_partition"](
        entries, state, policy)
    assert {row["violation"] for row in diagnostics} == {
        "worker_assignment_policy_unverified", "cortex_call_missing_server_event"}
    assert {row["violation"] for row in blocking} == {
        "worker_project_action_before_skill_receipt", "worker_skill_route_mismatch",
        "pre_binding_host_action"}
