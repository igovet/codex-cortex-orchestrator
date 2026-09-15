"""Offline contract tests for the host-owned functions.exec pre-dispatch seam."""
from pathlib import Path
import runpy

import pytest

from cortex_runtime.execution_boundary import (
    CAPABILITY_HOST_DISPATCH,
    PERMISSION_DENIED,
    WORKER_CAPABILITIES,
    authorize_pre_dispatch,
    authorize_host_dispatch_envelope,
    dispatch_host_envelope,
    host_dispatch_receipt,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('command,allowed', [
    ('cat README.md', True), ('sed -n 1,40p README.md', True),
    ('cat README.md; touch changed', False), ('sed -i 1d README.md', False),
    ('cat /tmp/.codex/cortex/private.md', False), ('cat $(touch changed)', False),
])
def test_coordinator_can_read_evidence_but_cannot_mutate(command, allowed, tmp_path):
    result = authorize_pre_dispatch(
        'exec_command', {'cmd': command}, actor_kind='native_coordinator',
        role='coordinator', task_id='task', assignment_id='bound', route='native_hook',
        capabilities=frozenset(), cwd=str(tmp_path), project_root=str(tmp_path))
    assert result['allowed'] is allowed


def envelope(*, phase="pre_task", calls=None, **changes):
    value = {
        "tool": "functions.exec",
        "actor_kind": "native_coordinator" if phase == "pre_task" else "native_worker",
        "actor_authentication": "host_authenticated",
        "role": "coordinator" if phase == "pre_task" else "worker",
        "phase": phase,
        "task_id": None if phase == "pre_task" else "task-1",
        "assignment_id": None if phase == "pre_task" else "assignment-1",
        "route": "native_functions_exec_pre_dispatch",
        "task_assignment_relation": "no_task_yet" if phase == "pre_task" else "host_bound_assignment",
        "nested_calls": calls if calls is not None else [{
            "tool": "mcp__cortex__create_task",
            "operation_class": "cortex_bootstrap",
            "target_class": "cortex_api",
            "opaque": False,
        }],
    }
    value.update(changes)
    return value


def test_pre_task_host_dispatch_allows_only_typed_task_bootstrap():
    decision = authorize_host_dispatch_envelope(envelope())
    assert decision == {"allowed": True, "code": None,
                        "capability": CAPABILITY_HOST_DISPATCH,
                        "reason": "pre_task_bootstrap"}
    receipt = host_dispatch_receipt(envelope(), decision)
    assert receipt["operation_classes"] == ["cortex_bootstrap"]
    assert "nested_calls" not in receipt and "task-1" not in str(receipt)


def test_pre_task_forbidden_or_mixed_dispatch_is_atomic_and_sentinel_never_runs():
    forbidden = {
        "tool": "exec_command", "operation_class": "workspace_execution",
        "target_class": "plugin_cache", "opaque": False,
    }
    mixed = envelope(calls=[envelope()["nested_calls"][0], forbidden])
    sentinel = []
    result, receipt = dispatch_host_envelope(mixed, lambda: sentinel.append("executed"))
    assert sentinel == []
    assert result["allowed"] is False and result["code"] == PERMISSION_DENIED
    assert result["capability"] == CAPABILITY_HOST_DISPATCH
    assert receipt["reason"] == "pre_task_bootstrap_only"
    assert "plugin_cache" not in str(result) and "exec_command" not in str(result)


def test_host_dispatch_rejects_opaque_malformed_route_and_provenance_before_execution():
    valid = envelope()
    cases = [
        {**valid, "actor_authentication": "claimed"},
        {**valid, "route": "native_hook"},
        {**valid, "task_id": "forged"},
        {**valid, "nested_calls": [{**valid["nested_calls"][0], "opaque": True}]},
        {**valid, "nested_calls": [{"tool": "exec_command", "operation_class": "shell",
                                      "target_class": "workspace", "opaque": False}]},
        {key: value for key, value in valid.items() if key != "phase"},
    ]
    for value in cases:
        called = []
        decision, _ = dispatch_host_envelope(value, lambda: called.append(True))
        assert called == []
        assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED


def test_bound_task_route_is_closed_and_worker_scoped():
    allowed = envelope(phase="bound_task", calls=[{
        "tool": "exec_command", "operation_class": "workspace_execution",
        "target_class": "workspace", "opaque": False,
    }])
    assert authorize_host_dispatch_envelope(allowed)["allowed"] is True
    coordinator = {**allowed, "actor_kind": "native_coordinator", "role": "coordinator"}
    denied = authorize_host_dispatch_envelope(coordinator)
    assert denied["allowed"] is False and denied["reason"] == "role_not_granted"


@pytest.mark.parametrize(("actor_kind", "role"), [
    ("native_coordinator", "worker"),
    ("native_worker", "coordinator"),
])
def test_bound_task_actor_role_mismatch_denies_before_executor_without_operands(actor_kind, role):
    value = envelope(phase="bound_task", actor_kind=actor_kind, role=role, calls=[{
        "tool": "exec_command", "operation_class": "workspace_execution",
        "target_class": "workspace", "opaque": False,
    }])
    sentinel = []
    decision, receipt = dispatch_host_envelope(value, lambda: sentinel.append("executed"))
    assert sentinel == []
    assert decision == {"allowed": False, "code": PERMISSION_DENIED,
                        "capability": CAPABILITY_HOST_DISPATCH,
                        "reason": "actor_role_mismatch"}
    assert receipt["reason"] == "actor_role_mismatch"
    assert "exec_command" not in str(decision)
    assert "workspace" not in str(decision)


def test_launcher_containment_requires_host_capability_and_bypass_invalidates_audit():
    live = runpy.run_path(str(ROOT / "scripts/cortex-live-smoke"), run_name="host_dispatch_test")
    with __import__("pytest").raises(RuntimeError, match="refuses pre-task shell wrappers"):
        live["qualification_pre_task_containment"](None)
    live["qualification_pre_task_containment"](live["HOST_DISPATCH_ENVELOPE_CAPABILITY"])


def test_exact_manifest_bound_active_skill_read_precedes_private_operand_deny(tmp_path):
    root = tmp_path / ".codex" / "plugins" / "cache" / "cortex" / "cortex" / "candidate"
    skill = root / "skills" / "worker-explorer" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("complete worker skill\n")
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"cortex","version":"1.16.0+codex.sha256.0123456789abcdef","skills":"./skills/"}')
    resource = {"path": str(skill)}
    allowed = authorize_pre_dispatch(
        "read_file", resource, actor_kind="native_worker", role="worker",
        task_id="task", assignment_id="bound", route="native_hook",
        capabilities=WORKER_CAPABILITIES, cwd=str(tmp_path), project_root=str(tmp_path),
        expected_skill="skills/worker-explorer/SKILL.md",
    )
    assert allowed["allowed"] is True and allowed["reason"] == "exact_active_skill_read"
    wrong = authorize_pre_dispatch(
        "read_file", resource, actor_kind="native_worker", role="worker",
        task_id="task", assignment_id="bound", route="native_hook",
        capabilities=WORKER_CAPABILITIES, cwd=str(tmp_path), project_root=str(tmp_path),
        expected_skill="skills/worker-debugger/SKILL.md",
    )
    assert wrong["allowed"] is False and wrong["code"] == PERMISSION_DENIED

    opaque_first = authorize_pre_dispatch(
        "read_file", resource, actor_kind="native_worker", role="worker",
        task_id="task", assignment_id="bound", route="native_hook",
        capabilities=WORKER_CAPABILITIES, cwd=str(tmp_path), project_root=str(tmp_path),
        expected_skill=None,
    )
    assert opaque_first["allowed"] is True and opaque_first["reason"] == "exact_active_skill_read"
    unknown = root / "skills" / "worker-unregistered" / "SKILL.md"
    unknown.parent.mkdir(); unknown.write_text("not a registered worker\n")
    arbitrary = authorize_pre_dispatch(
        "read_file", {"path": str(unknown)}, actor_kind="native_worker",
        role="worker", task_id="task", assignment_id="bound", route="native_hook",
        capabilities=WORKER_CAPABILITIES, cwd=str(tmp_path), project_root=str(tmp_path),
        expected_skill=None,
    )
    assert arbitrary["allowed"] is False and arbitrary["code"] == PERMISSION_DENIED

    observer = runpy.run_path(str(ROOT / "scripts/cortex-desktop-dev"), run_name="host_dispatch_test")
    policy = observer["call_policy_violations"]([{
        "thread_id": "root", "role": "coordinator", "tool": "functions.exec", "outcome": "success",
        "host_dispatch_decision": "PERMISSION_DENIED", "host_dispatch_execution": "executed",
    }])
    assert {row["violation"] for row in policy} == {"host_dispatch_bypass"}
    audit = observer["classify_audit_findings"](
        failures=[], hook_failures=[], host_failures=[], policy_violations=policy,
        open_sessions=[], open_cells=[])
    assert audit["evidence_valid"] is False and audit["score_eligible"] is False
    assert audit["evidence_integrity_invalidators"][0]["reason"] == "host_pre_dispatch_bypassed"


def test_worker_skill_read_accepts_declared_leaves_and_rejects_neighbors(tmp_path):
    root = tmp_path / ".codex" / "plugins" / "cache" / "cortex" / "cortex" / "candidate"
    skill = root / "skills" / "worker-backend-dev" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("complete worker skill\n\n[publication](references/report-publication.md)\n")
    reference = skill.parent / "references" / "report-publication.md"
    reference.parent.mkdir()
    reference.write_text("declared publication guidance\n")
    unlinked = reference.with_name("unlinked.md")
    unlinked.write_text("not declared\n")
    reference_link = reference.with_name("linked-by-symlink.md")
    reference_link.symlink_to(reference)
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"cortex","version":"1.16.0+codex.sha256.0123456789abcdef","skills":"./skills/"}')
    context = dict(actor_kind="native_worker", role="worker", task_id="task", assignment_id="bound",
                   route="native_hook", capabilities=WORKER_CAPABILITIES, cwd=str(tmp_path),
                   project_root=str(tmp_path), expected_skill="skills/worker-backend-dev/SKILL.md")
    for path in (skill, reference):
        decision = authorize_pre_dispatch("read_file", {"path": str(path)}, **context)
        assert decision == {"allowed": True, "code": None,
                            "capability": "cortex.skill.read", "reason": "exact_active_skill_read"}
    for tool_input in (
        {"path": str(skill.parent)},
        {"path": str(skill.with_name('README.md'))},
        {"path": str(unlinked)},
        {"path": str(reference_link)},
        {"path": str(skill), "offset": 1},
        {"path": str(skill.relative_to(tmp_path))},
    ):
        decision = authorize_pre_dispatch("read_file", tool_input, **context)
        assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED

    reference_target = reference.with_name("reference-target.md")
    reference_target.write_text(reference.read_text())
    reference.unlink()
    reference.symlink_to(reference_target)
    decision = authorize_pre_dispatch("read_file", {"path": str(reference)}, **context)
    assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED

    other_skill = root / "skills" / "worker-debugger" / "SKILL.md"
    other_skill.parent.mkdir(parents=True)
    other_skill.write_text("other registered worker\n")
    decision = authorize_pre_dispatch("read_file", {"path": str(other_skill)}, **context)
    assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED


def test_worker_execution_is_not_pre_dispatch_denied_for_missing_skill_receipt():
    """Receipt ordering is observed post-action in current-host mode, not a gate."""
    decision = authorize_pre_dispatch(
        "exec_command", {"cmd": "python3 -B -m pytest -q tests/test_package.py"},
        actor_kind="native_worker", role="worker", task_id="task", assignment_id="bound",
        route="native_hook", capabilities=WORKER_CAPABILITIES, cwd=str(ROOT),
        project_root=str(ROOT), expected_skill=None,
    )
    assert decision["allowed"] is True
