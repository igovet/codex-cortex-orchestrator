"""Offline P1 policy-manifest and evidence-admissibility regressions."""
from copy import deepcopy
import math

import pytest

from cortex_runtime.capability_policy import (
    PERMISSION_DENIED,
    POLICY_DECLARED,
    ADVISORY_EVIDENCE_FIELDS,
    advisory_replanning_metadata,
    authorize_semantic_request,
    canonical_capability,
    duplicate_review_reuse_diagnostic,
    evidence_admissibility,
    load_manifest,
    one_call_label_accounting,
    policy_receipt,
    quiet_wait_delta,
    structured_advisory_evidence,
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
        "failed_canary": None, "review": "pending", "cost": {"tokens": 1},
        "advisory_only": True, "automatic_routing": False, "acceptance_gate": False,
    }


def test_structured_evidence_preserves_legacy_metadata_and_reports_missing_fields_advisory_only():
    # Existing callers retain their exact legacy shape when no new extension is used.
    assert advisory_replanning_metadata(review="pending") == {
        "failed_canary": None, "review": "pending", "cost": None,
        "advisory_only": True, "automatic_routing": False, "acceptance_gate": False,
    }
    result = structured_advisory_evidence({
        "delivery_state": {"status": "observed"},
        "receipt_references": ["r_abc", "r_def"],
        "unknown": "ignored",
    })
    assert tuple(result)[:len(ADVISORY_EVIDENCE_FIELDS)] == ADVISORY_EVIDENCE_FIELDS
    assert result["delivery_state"] == {"status": "observed"}
    assert result["receipt_references"] == ["r_abc", "r_def"]
    assert result["missing_fields"] == [
        "acceptance_boundary", "causal_model_delta", "predecessor_rollout",
        "retry_discriminator",
    ]
    assert [item["field"] for item in result["diagnostics"]] == result["missing_fields"]
    assert result["advisory_only"] is True
    assert result["automatic_routing"] is False and result["acceptance_gate"] is False
    assert structured_advisory_evidence(None)["missing_fields"] == list(ADVISORY_EVIDENCE_FIELDS)


def test_advisory_replanning_metadata_exposes_structured_fields_without_changing_gate_flags():
    result = advisory_replanning_metadata(
        delivery_state="report-received", acceptance_boundary="offline",
        causal_model_delta={"changed": True}, predecessor_rollout="v1",
        retry_discriminator="new-check", receipt_references=["r_abc"],
    )
    assert result["evidence"]["missing_fields"] == []
    assert result["evidence"]["retry_discriminator"] == "new-check"
    assert result["advisory_only"] is True
    assert result["automatic_routing"] is False and result["acceptance_gate"] is False


def test_duplicate_review_reuse_diagnostic_is_exact_bounded_and_non_blocking():
    prior = {"artifact_revision": "rev-1", "acceptance_boundary": "offline",
             "check_identity": "tests-advisory"}
    current = dict(prior, result="new observation")
    diagnostic = duplicate_review_reuse_diagnostic([None, {}, prior], current)
    assert diagnostic == {
        "code": "duplicate_review_reuse",
        "identity": prior,
        "severity": "advisory",
        "advisory_only": True,
        "automatic_routing": False,
        "acceptance_gate": False,
    }
    assert duplicate_review_reuse_diagnostic([prior], current, fresh_evidence={"receipt": "r"}) is None
    assert duplicate_review_reuse_diagnostic([prior], current, fresh_evidence=False) is not None
    assert duplicate_review_reuse_diagnostic([prior], current, rerun_reason="rechecked after dependency update") is None
    assert duplicate_review_reuse_diagnostic([prior], dict(current, check_identity="other")) is None
    assert duplicate_review_reuse_diagnostic([prior], dict(current, artifact_revision=None)) is None
    assert duplicate_review_reuse_diagnostic([prior], current, max_checks=0) is None


def test_evidence_mapping_is_preserved_and_non_none_direct_values_override():
    result = advisory_replanning_metadata(
        evidence={
            "delivery_state": "mapped-delivery",
            "acceptance_boundary": "mapped-boundary",
            "receipt_references": ["mapped-receipt"],
        },
        delivery_state="direct-delivery",
    )["evidence"]
    assert result["delivery_state"] == "direct-delivery"
    assert result["acceptance_boundary"] == "mapped-boundary"
    assert result["receipt_references"] == ["mapped-receipt"]
    direct_helper = structured_advisory_evidence(
        {"delivery_state": "mapped-delivery"}, delivery_state=None,
    )
    assert direct_helper["delivery_state"] == "mapped-delivery"


def test_advisory_value_traversal_is_hard_bounded_before_materialization():
    class CountingMapping(dict):
        def __init__(self):
            self.seen = 0

        def items(self):
            for index in range(1_000):
                self.seen += 1
                yield (f"field-{index}", index)

    class CountingSequence(list):
        def __init__(self):
            self.seen = 0

        def __iter__(self):
            for index in range(1_000):
                self.seen += 1
                yield index

    mapping = CountingMapping()
    sequence = CountingSequence()
    structured_advisory_evidence({"delivery_state": mapping, "receipt_references": sequence})
    assert mapping.seen == 32 and sequence.seen == 32


def test_top_level_evidence_mappings_select_only_allowlisted_keys_without_copying():
    class CountingMapping(dict):
        def __init__(self):
            super().__init__({f"extra-{index}": index for index in range(1_000)})
            self.lookups = 0

        def __getitem__(self, key):
            self.lookups += 1
            return super().__getitem__(key)

    mapping = CountingMapping()
    structured_advisory_evidence(mapping)
    assert mapping.lookups == len(ADVISORY_EVIDENCE_FIELDS)
    mapping.lookups = 0
    advisory_replanning_metadata(evidence=mapping)
    assert mapping.lookups == len(ADVISORY_EVIDENCE_FIELDS)


def test_scalar_evidence_is_finite_and_json_safe_with_explicit_limits():
    result = structured_advisory_evidence({
        "delivery_state": 1 << 53,
        "acceptance_boundary": math.inf,
        "causal_model_delta": math.nan,
        "predecessor_rollout": (1 << 53) - 1,
        "retry_discriminator": 1e309,
        "receipt_references": -((1 << 53) + 1),
    })
    assert result["delivery_state"] is None
    assert result["acceptance_boundary"] is None
    assert result["causal_model_delta"] is None
    assert result["predecessor_rollout"] == (1 << 53) - 1
    assert result["retry_discriminator"] is None
    assert result["receipt_references"] is None
    assert set(result["missing_fields"]) == {
        "delivery_state", "acceptance_boundary", "causal_model_delta",
        "retry_discriminator", "receipt_references",
    }
    # Returned metadata can be serialized by strict JSON consumers.
    import json
    json.dumps(result, allow_nan=False)


def test_legacy_advisory_fields_use_bounded_nested_strict_json_normalization():
    result = advisory_replanning_metadata(
        failed_canary="x" * 100_000,
        review=10 ** 100,
        cost={
            "long_text": "y" * 10_000,
            "not_finite": [math.nan, math.inf, -math.inf],
            "nested": {"safe": True, "items": (0, (1 << 53) - 1)},
        },
    )

    assert len(result["failed_canary"]) == 512
    assert result["review"] is None
    assert len(result["cost"]["long_text"]) == 512
    assert result["cost"]["not_finite"] == [None, None, None]
    assert result["cost"]["nested"] == {
        "safe": True, "items": [0, (1 << 53) - 1],
    }
    import json
    json.dumps(result, allow_nan=False)

    for non_finite in (math.nan, math.inf, -math.inf):
        scalar_result = advisory_replanning_metadata(
            failed_canary=non_finite, review=non_finite, cost=non_finite,
        )
        assert scalar_result["failed_canary"] is None
        assert scalar_result["review"] is None
        assert scalar_result["cost"] is None


def test_review_identity_requires_exact_bounded_non_empty_strings():
    current = {"artifact_revision": "rev-1", "acceptance_boundary": "offline",
               "check_identity": "strict-check"}
    for bad in (True, 1, 1.0, ""):
        malformed = dict(current, check_identity=bad)
        assert duplicate_review_reuse_diagnostic([malformed], malformed) is None
    assert duplicate_review_reuse_diagnostic(
        [dict(current, check_identity="strict-check")],
        dict(current, check_identity="strict-check"),
    )["code"] == "duplicate_review_reuse"


def test_duplicate_review_scan_is_capped_and_null_safe():
    current = {"artifact_revision": "rev-1", "acceptance_boundary": "offline",
               "check_identity": "bounded-check"}
    beyond_bound = [None] * 4 + [current]
    assert duplicate_review_reuse_diagnostic(beyond_bound, current, max_checks=4) is None
    assert duplicate_review_reuse_diagnostic(beyond_bound, current, max_checks=5) is not None
    # A caller cannot expand the implementation's hard safety bound.
    assert duplicate_review_reuse_diagnostic([None] * 128 + [current], current, max_checks=129) is None
    assert duplicate_review_reuse_diagnostic("not-a-sequence", current) is None
    assert duplicate_review_reuse_diagnostic([current], None) is None
    oversized = dict(current, check_identity="x" * 513)
    assert duplicate_review_reuse_diagnostic([oversized], oversized) is None


def test_duplicate_diagnostic_can_be_attached_without_acceptance_or_exit_regression():
    prior = {"artifact_revision": "rev-1", "acceptance_boundary": "offline",
             "check_identity": "tests-advisory"}
    result = advisory_replanning_metadata(previous_checks=[prior], current_check=prior)
    assert result["diagnostics"][0]["code"] == "duplicate_review_reuse"
    assert result["acceptance_gate"] is False and result["automatic_routing"] is False
    # The helper returns a value; it does not raise or signal a failed command.
    assert result["advisory_only"] is True
