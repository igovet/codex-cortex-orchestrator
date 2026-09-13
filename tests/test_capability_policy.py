"""Offline P1 policy-manifest and evidence-admissibility regressions."""
from copy import deepcopy

import pytest

from cortex_runtime.capability_policy import (
    PERMISSION_DENIED,
    POLICY_DECLARED,
    advisory_replanning_metadata,
    authorize_semantic_request,
    canonical_capability,
    evidence_admissibility,
    load_manifest,
    one_call_label_accounting,
    policy_receipt,
    quiet_wait_delta,
)


PAYLOAD = "a" * 64


def request(capability="cortex.runtime.execute", **changes):
    namespace, resource_class, verb = capability.split(".")
    scope = "artifact_bound" if resource_class in {"artifact", "release"} else "task_bound"
    value = {
        "policy_version": 1,
        "actor_kind": "native_worker",
        "profile_id": "build-verification",
        "profile_version": "agent-v2",
        "target": "cli",
        "route": "native_cli_qualification",
        "phase": "bound_task",
        "task_id": "task-1",
        "assignment_receipt": "assignment-1",
        "candidate_payload_sha256": PAYLOAD,
        "artifact_revision": "artifact-1",
        "acceptance_boundary": "offline-boundary",
        "check_identity": "check-1",
        "operation": {
            "namespace": namespace,
            "verb": verb,
            "resource_class": resource_class,
            "scope": scope,
            "effect": "allow" if capability in {
                "cortex.report.read", "cortex.task.read", "cortex.runtime.execute",
                "cortex.runtime.observe", "cortex.artifact.inspect"} else "deny",
            "version": 1,
            "opaque": False,
        },
        "resource": {"resource_class": resource_class, "scope": scope, "opaque": False},
    }
    value.update(changes)
    return value


def test_manifest_is_versioned_closed_and_build_verification_is_least_privilege():
    manifest = load_manifest()
    assert manifest["schema"] == "capability-manifest-v1" and manifest["version"] == 1
    policy = manifest["profiles"]["build-verification"]
    assert set(policy["allow"]) == {
        "cortex.report.read", "cortex.task.read", "cortex.runtime.execute",
        "cortex.runtime.observe", "cortex.artifact.inspect",
    }
    assert {"cortex.task.write", "cortex.orchestrator.mutate", "cortex.release.promote",
            "cortex.internal.*", "host.cache.inspect", "host.registry.inspect",
            "host.plugin.inspect", "host.debug.inspect", "host.state.read"} <= set(policy["deny"])


@pytest.mark.parametrize("capability", [
    "cortex.report.read", "cortex.task.read", "cortex.runtime.execute",
    "cortex.runtime.observe", "cortex.artifact.inspect",
])
def test_build_verification_allowlist_accepts_only_closed_public_semantic_operations(capability):
    decision = authorize_semantic_request(request(capability))
    assert decision["allowed"] is True and decision["capability"] == capability
    assert decision["enforcement_state"] == POLICY_DECLARED


@pytest.mark.parametrize("capability", [
    "cortex.task.write", "cortex.orchestrator.mutate", "cortex.release.promote",
])
def test_build_verification_denies_mutation_and_promotion(capability):
    decision = authorize_semantic_request(request(capability))
    assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED
    assert decision["reason"] == "capability_denied"


@pytest.mark.parametrize("mutate", [
    lambda value: value["operation"].update(namespace="Cortex"),
    lambda value: value["operation"].update(verb="read%2fstate"),
    lambda value: value["operation"].update(resource_class="internal"),
    lambda value: value["operation"].update(opaque=True),
    lambda value: value["resource"].update(opaque=True),
    lambda value: value["resource"].update(scope="runtime"),
    lambda value: value["operation"].update(alias="run"),
])
def test_alias_encoded_internal_opaque_or_mixed_operation_is_fail_closed(mutate):
    value = request()
    mutate(value)
    decision = authorize_semantic_request(value)
    assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED
    assert decision["reason"] == "unknown_or_opaque_operation"
    assert "read%2fstate" not in str(decision)


@pytest.mark.parametrize("field, value", [
    ("profile_id", "unknown-profile"), ("profile_version", "agent-v1"),
    ("route", "alias-route"), ("target", "remote"), ("phase", "pre_task"),
    ("task_id", ""), ("candidate_payload_sha256", "b" * 63),
])
def test_profile_and_required_binding_context_are_verified_or_fail_closed(field, value):
    decision = authorize_semantic_request(request(**{field: value}))
    assert decision["allowed"] is False and decision["code"] == PERMISSION_DENIED
    assert decision["reason"] in {"malformed_or_ambiguous_request", "unverified_profile"}


def test_complete_receipt_requires_exact_binding_and_denials_never_become_admissible():
    value = request()
    decision = authorize_semantic_request(value)
    observed = policy_receipt(decision, observed=True)
    assert evidence_admissibility(value, observed) == {
        "admissible": True, "status": "admissible", "reason": "bound_policy_observed",
        "enforcement_state": "policy_observed",
    }
    stale = deepcopy(observed)
    stale["binding"]["artifact_revision"] = "artifact-2"
    assert evidence_admissibility(value, stale) == {
        "admissible": False, "status": "unverified", "reason": "binding_mismatch"}
    denied = policy_receipt(authorize_semantic_request(request("cortex.task.write")), observed=True)
    assert evidence_admissibility(value, denied) == {
        "admissible": False, "status": "unverified", "reason": "incomplete_or_denied_receipt"}


def test_local_policy_receipts_cannot_attest_a_host_or_admit_forged_host_state():
    value = request()
    decision = authorize_semantic_request(value)
    local = policy_receipt(decision, observed=True)
    assert local["enforcement_state"] == "policy_observed"
    with pytest.raises(TypeError):
        policy_receipt(decision, host_attested=True)
    forged = deepcopy(local)
    forged["enforcement_state"] = "host_enforced"
    assert evidence_admissibility(value, forged) == {
        "admissible": False, "status": "unverified", "reason": "host_attestation_unverified"}


@pytest.mark.parametrize("other", [
    "cortex.runtime.execute", "cortex.artifact.inspect", "cortex.task.write",
])
def test_receipt_cannot_replay_across_capability_operation_or_resource(other):
    report_read = request("cortex.report.read")
    report_receipt = policy_receipt(authorize_semantic_request(report_read), observed=True)
    expected = request(other)
    assert authorize_semantic_request(report_read)["decision_id"] != authorize_semantic_request(expected)["decision_id"]
    assert evidence_admissibility(expected, report_receipt) == {
        "admissible": False, "status": "unverified", "reason": "binding_mismatch"}


@pytest.mark.parametrize("mutation", [
    lambda receipt: receipt.update(truncated=True),
    lambda receipt: receipt.update(replaced=True),
    lambda receipt: receipt["binding"].update(capability="cortex.unknown.read"),
    lambda receipt: receipt.pop("capability"),
])
def test_truncated_replaced_unknown_or_incomplete_receipts_are_unverified(mutation):
    value = request()
    receipt = policy_receipt(authorize_semantic_request(value), observed=True)
    mutation(receipt)
    assert evidence_admissibility(value, receipt) == {
        "admissible": False, "status": "unverified", "reason": "incomplete_or_denied_receipt"}


def test_decision_identity_keeps_one_call_identity_when_multiple_labels_describe_it():
    value = request()
    allowed = authorize_semantic_request(value)
    repeated = authorize_semantic_request(value)
    assert allowed["decision_id"] == repeated["decision_id"]
    assert allowed["binding"] == repeated["binding"]
    assert canonical_capability(value["operation"], value["resource"]) == "cortex.runtime.execute"
    assert one_call_label_accounting(allowed, ["semantic_policy", "path_boundary"]) == {
        "call_id": allowed["decision_id"], "call_count": 1,
        "labels": ["path_boundary", "semantic_policy"], "status": "complete",
    }
    assert one_call_label_accounting(allowed, ["semantic_policy", "semantic_policy"]) == {
        "call_id": allowed["decision_id"], "call_count": 1,
        "labels": ["semantic_policy"], "status": "complete",
    }


def test_quiet_wait_and_advisory_metadata_are_non_routing_and_null_safe():
    prior = {"state": "waiting", "evidence_cursor": "cursor-1"}
    assert quiet_wait_delta(prior, prior) is None
    assert quiet_wait_delta(prior, {"state": "waiting", "evidence_cursor": "cursor-2"}) == {
        "state": "waiting", "evidence_cursor": "cursor-2"}
    advisory = advisory_replanning_metadata(failed_canary=None, review="pending", cost={"tokens": 1})
    assert advisory == {
        "failed_canary": None, "review": "pending", "cost": None,
        "advisory_only": True, "automatic_routing": False, "acceptance_gate": False,
    }
