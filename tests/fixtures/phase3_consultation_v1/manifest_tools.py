#!/usr/bin/env python3
"""Validate the frozen Phase 3 fixture identities and policy boundaries."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_EXECUTION_STATUS = (
    "offline-only; live cells, reveal, consultation-effect claims and adaptive activation "
    "are prohibited until coordinator-owned prerequisites are accepted"
)
EXPECTED_NULL_REASONS = [
    "host_unavailable", "provider_unavailable", "gateway_unavailable",
    "dependency_unavailable", "codebase_memory_unavailable",
    "required_receipt_unavailable", "not_observed",
]
EXPECTED_AVAILABILITY_POLICY = (
    "Unavailable observations are excluded cells with null quality, cost and latency; "
    "availability is never a consultation-quality score, adaptive outcome, rank, gate or "
    "acceptance decision."
)
EXPECTED_MODEL_ENVELOPE = {
    "coordinator": {"model": "gpt-5.6-luna", "reasoning_effort": "high"},
    "workers": {
        "allowed_models": ["gpt-5.6-luna"],
        "allowed_reasoning_efforts": ["medium", "high"],
    },
    "inherited_conversation": False,
    "astra_allowed": False,
}

EXPECTED_CASES = [
    {"case_id": "T-01", "family": "material-consequential-decision", "kind": "qualifying"},
    {"case_id": "T-02", "family": "contradictory-hypothesis-bound-evidence", "kind": "qualifying"},
    {"case_id": "T-03", "family": "repeated-failure-surprise-replanning", "kind": "qualifying"},
    {"case_id": "N-01", "family": "routine-reversible-work", "kind": "negative-control"},
    {"case_id": "N-02", "family": "active-incident-recovery", "kind": "negative-control"},
]
EXPECTED_RANDOMIZATION = {
    "assignment_custody": "coordinator",
    "mapping_storage": "coordinator-only; never in blinded rows or scorer input",
    "seed": "phase3-consultation-v1-fixed-seed-20260910",
}
EXPECTED_BLIND_SCORING = {
    "excluded_from_scorer_inputs": [
        "run_id", "arm", "condition", "payload_mapping", "diagnostics", "cost", "latency",
    ],
    "join_key": "blind_pair_id",
    "no_condition_mapping": True,
    "reveal_authority": "coordinator",
    "scorer_blind": True,
    "scorer_inputs": [
        "suite_version", "case_id", "family", "kind", "repeat", "task_identity",
        "terminal_receipt", "quality", "consultation",
    ],
}
EXPECTED_NEGATIVE_CONTROLS = {
    "case_ids": ["N-01", "N-02"],
    "expected_consultation_count": 0,
    "specificity_target": 0.8,
}
EXPECTED_SCORECARD = {
    "quality": [
        "critical_decision_defects", "requirement_coverage", "discriminating_check_use",
        "false_consultation_activation", "missed_qualifying_transition", "protocol_pass",
        "acceptance_regression", "protected_content_preserved",
    ],
    "overhead": [
        "consultations_per_transition", "input_tokens", "cached_input_tokens",
        "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens",
        "wall_seconds", "report_bytes", "duplicate_evidence", "recovery_delay_seconds",
    ],
}
EXPECTED_PROMOTION = {
    "automatic_acceptance": False,
    "coordinator_owned": True,
    "criteria": [
        "fewer critical decision defects than unchanged baseline",
        "no acceptance or protocol regression",
        "at least 80% specificity on negative controls",
        "median no more than one consultation per qualifying transition",
    ],
    "decision_values": ["promote_candidate", "revise_or_replicate", "unverified"],
    "runtime_gate": False,
}
EXPECTED_STOP_CONDITIONS = [
    "identity_or_control_asymmetry", "arm_leakage", "missing_terminal_or_audit_evidence",
    "raw_or_private_content_exposure", "required_receipt_unavailable",
    "external_availability_prevents_comparable_cell",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(path: Path) -> str:
    return hashlib.sha256(json.dumps(json.loads(path.read_text(encoding="utf-8")), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_manifest(manifest=None):
    if manifest is None:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        return ["manifest_shape"]
    errors = []
    if manifest.get("suite_version") != "phase3-consultation-v1":
        errors.append("suite_version")
    if manifest.get("case_count") != 5 or manifest.get("paired_repeats") != 3:
        errors.append("case/repeat_count")
    cases = manifest.get("cases")
    if (not isinstance(cases, list)
            or any(not isinstance(row, Mapping) for row in cases)
            or [row.get("case_id") for row in cases] != ["T-01", "T-02", "T-03", "N-01", "N-02"]):
        errors.append("case_order")
    if (not isinstance(cases, list)
            or any(not isinstance(row, Mapping) for row in cases)
            or [row.get("kind") for row in cases] != ["qualifying"] * 3 + ["negative-control"] * 2):
        errors.append("case_kind")
    if manifest.get("cases") != EXPECTED_CASES:
        errors.append("case_policy")
    blind_scoring = manifest.get("blind_scoring")
    if not isinstance(blind_scoring, Mapping):
        blind_scoring = {}
        errors.append("blind_scoring_shape")
    if blind_scoring.get("no_condition_mapping") is not True:
        errors.append("blind_mapping")
    if blind_scoring.get("scorer_blind") is not True:
        errors.append("scorer_blind")
    if blind_scoring.get("reveal_authority") != "coordinator":
        errors.append("reveal_authority")
    if manifest.get("blind_scoring") != EXPECTED_BLIND_SCORING:
        errors.append("blind_scoring_policy")
    promotion = manifest.get("promotion")
    if not isinstance(promotion, Mapping):
        promotion = {}
        errors.append("promotion_shape")
    if promotion.get("coordinator_owned") is not True:
        errors.append("promotion_owner")
    if promotion.get("runtime_gate") is not False:
        errors.append("runtime_gate")
    if promotion.get("automatic_acceptance") is not False:
        errors.append("automatic_acceptance")
    if manifest.get("promotion") != EXPECTED_PROMOTION:
        errors.append("promotion_policy")
    if manifest.get("execution_status") != EXPECTED_EXECUTION_STATUS:
        errors.append("execution_status")
    if manifest.get("null_reasons") != EXPECTED_NULL_REASONS:
        errors.append("null_reasons")
    if manifest.get("availability_policy") != EXPECTED_AVAILABILITY_POLICY:
        errors.append("availability_policy")
    if manifest.get("model_envelope") != EXPECTED_MODEL_ENVELOPE:
        errors.append("model_envelope")
    if manifest.get("dependency_envelope") != (
            "python3-standard-library-only; host, gateway, provider, Codebase Memory and dependency availability are recorded separately"):
        errors.append("dependency_envelope")
    if manifest.get("randomization") != EXPECTED_RANDOMIZATION:
        errors.append("randomization")
    if manifest.get("negative_controls") != EXPECTED_NEGATIVE_CONTROLS:
        errors.append("negative_controls")
    if manifest.get("scorecard") != EXPECTED_SCORECARD:
        errors.append("scorecard")
    if manifest.get("stop_conditions") != EXPECTED_STOP_CONDITIONS:
        errors.append("stop_conditions")
    if manifest.get("condition_independent_identity") != [
            "suite_version", "case_id", "repeat", "prompt_sha256", "fixture_sha256",
            "oracle_sha256", "reset_sha256", "dependency_envelope_sha256", "payload_identity"]:
        errors.append("condition_independent_identity")
    if "Astra" in json.dumps(manifest) or "gpt-6-astra" in json.dumps(manifest):
        errors.append("prohibited_model")
    for name, key in (("workloads-v1.json", "workloads_sha256"), ("ledger_schema.json", "ledger_schema_sha256"), ("reset.py", "reset_sha256"), ("oracle.py", "oracle_sha256")):
        if manifest.get(key) != sha256_file(ROOT / name):
            errors.append(key)
    if manifest.get("workloads_canonical_sha256") != canonical_json(ROOT / "workloads-v1.json"):
        errors.append("workloads_canonical_sha256")
    return errors


if __name__ == "__main__":
    errors = validate_manifest()
    print(json.dumps({"errors": errors, "valid": not errors}, sort_keys=True))
    raise SystemExit(0 if not errors else 1)
