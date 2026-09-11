"""Offline Phase 3 consultation-effect fixture and protocol regressions."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "phase3_consultation_v1"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
WORKLOADS = json.loads((ROOT / "workloads-v1.json").read_text())
ORACLE = ROOT / "oracle.py"
RESET = ROOT / "reset.py"
sys.path.insert(0, str(ROOT))
from manifest_tools import validate_manifest


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def run_reset(destination):
    return subprocess.run([sys.executable, str(RESET), "--destination", str(destination)], capture_output=True, text=True, check=False)


def run_oracle(path):
    return subprocess.run([sys.executable, str(ORACLE), str(path)], capture_output=True, text=True, check=False)


def frozen_identity(case_id, payload_identity):
    workload = next(row for row in WORKLOADS["cases"] if row["case_id"] == case_id)
    return {"suite_version": MANIFEST["suite_version"], "case_id": case_id,
            "prompt_sha256": digest(workload["prompt"]),
            "fixture_sha256": MANIFEST["workloads_sha256"],
            "oracle_sha256": hashlib.sha256(ORACLE.read_bytes()).hexdigest(),
            "reset_sha256": hashlib.sha256(RESET.read_bytes()).hexdigest(),
            "dependency_envelope_sha256": digest(MANIFEST["dependency_envelope"]),
            "payload_identity": digest("payload-" + payload_identity)}


def record(case_id, repeat, suffix, consultation_count=0):
    family, kind = next((row["family"], row["kind"]) for row in WORKLOADS["cases"] if row["case_id"] == case_id)
    identity = frozen_identity(case_id, suffix)
    return {"run_id": "run-" + (digest(case_id + str(repeat) + suffix)[:16]),
            "blind_pair_id": "pair-" + digest(case_id + str(repeat))[:16], "case_id": case_id,
            "family": family, "kind": kind, "repeat": repeat, "task_identity": identity,
            "terminal_receipt": {"status": "complete", "audit_status": "pass", "exit_code": 0},
            "quality": {"critical_decision_defects": 0, "requirement_coverage": 1.0,
                        "discriminating_check_use": 1.0, "false_consultation_activation": 0,
                        "missed_qualifying_transition": 0, "protocol_pass": True,
                        "acceptance_regression": False, "protected_content_preserved": True},
            "consultation": {"count": consultation_count, "applicable": kind == "qualifying"},
            "cost": {"input_tokens": 10, "cached_input_tokens": 4, "cache_write_input_tokens": 1,
                     "output_tokens": 3, "reasoning_output_tokens": 1, "total_tokens": 13},
            "latency": {"wall_seconds": 2.0},
            "diagnostics": {"availability": "available", "codebase_memory": "available", "provider": "available", "gateway": "available", "dependency": "available", "git": "available"}}


def complete_payload():
    rows = []
    for case_id in ("T-01", "T-02", "T-03", "N-01", "N-02"):
        for repeat in (1, 2, 3):
            rows.extend([record(case_id, repeat, "a"), record(case_id, repeat, "b", 1 if case_id.startswith("T-") else 0)])
    return {"suite_version": "phase3-consultation-v1", "records": rows}


def test_manifest_freezes_three_qualifying_families_and_negative_controls():
    assert MANIFEST["suite_version"] == "phase3-consultation-v1"
    assert [row["case_id"] for row in MANIFEST["cases"]] == ["T-01", "T-02", "T-03", "N-01", "N-02"]
    assert [row["kind"] for row in MANIFEST["cases"]] == ["qualifying"] * 3 + ["negative-control"] * 2
    assert MANIFEST["paired_repeats"] == 3
    assert MANIFEST["negative_controls"]["specificity_target"] == 0.80


def test_manifest_freezes_luna_only_coordinator_route_and_no_runtime_gate():
    envelope = MANIFEST["model_envelope"]
    assert envelope["coordinator"] == {"model": "gpt-5.6-luna", "reasoning_effort": "high"}
    assert envelope["workers"]["allowed_models"] == ["gpt-5.6-luna"]
    assert envelope["astra_allowed"] is False
    assert MANIFEST["promotion"]["coordinator_owned"] is True
    assert MANIFEST["promotion"]["runtime_gate"] is False
    assert MANIFEST["promotion"]["automatic_acceptance"] is False


def test_manifest_hashes_are_frozen_and_valid():
    result = subprocess.run([sys.executable, str(ROOT / "manifest_tools.py")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"errors": [], "valid": True}


def test_reset_is_deterministic_and_non_overwriting(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    receipt = run_reset(first)
    assert receipt.returncode == 0
    assert json.loads(receipt.stdout)["exit_code"] == 0
    again = run_reset(first)
    assert again.returncode == 1
    assert json.loads(again.stdout)["exit_code"] == 1
    assert run_reset(second).returncode == 0
    files = lambda path: {item.relative_to(path): item.read_bytes() for item in path.rglob("*") if item.is_file()}
    assert files(first) == files(second)
    empty = json.loads((first / "ledger.json").read_text())
    assert empty == {"suite_version": "phase3-consultation-v1", "records": []}


def test_oracle_accepts_exact_blinded_matrix_and_rejects_wrong_cardinality(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(complete_payload()))
    result = run_oracle(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"] is True
    payload = complete_payload()
    payload["records"].pop()
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert json.loads(result.stdout)["passed"] is False


def test_oracle_rejects_arm_or_private_content_leakage():
    payload = complete_payload()
    payload["records"][0]["arm"] = "baseline"
    result = subprocess.run([sys.executable, str(ORACLE), "/dev/stdin"], input=json.dumps(payload), capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "arm_or_private_leak" in result.stdout


def test_oracle_rejects_consultation_on_negative_control():
    payload = complete_payload()
    payload["records"][24]["consultation"]["count"] = 1
    result_path = ROOT / "_phase3_bad_negative_control.json"
    try:
        result_path.write_text(json.dumps(payload))
        result = run_oracle(result_path)
    finally:
        result_path.unlink(missing_ok=True)
    assert result.returncode == 1
    assert "negative_control" in result.stdout


def test_oracle_rejects_condition_independent_identity_asymmetry(tmp_path):
    payload = complete_payload()
    payload["records"][1]["task_identity"]["fixture_sha256"] = digest("different-fixture")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "identity_asymmetry" in result.stdout


def test_oracle_keeps_unavailable_cell_out_of_quality(tmp_path):
    payload = complete_payload()
    row = payload["records"][0]
    row["diagnostics"]["availability"] = "provider_unavailable"
    for key in row["quality"]:
        row["quality"][key] = None
    for key in row["cost"]:
        row["cost"][key] = None
    row["latency"]["wall_seconds"] = None
    row["consultation"] = {"count": 0, "applicable": None, "unavailable_reason": "provider_unavailable"}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("cost_field", [
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
])
def test_oracle_rejects_partial_cost_on_unavailable_cell(tmp_path, cost_field):
    payload = complete_payload()
    row = payload["records"][0]
    row["diagnostics"]["availability"] = "provider_unavailable"
    for key in row["quality"]:
        row["quality"][key] = None
    for key in row["cost"]:
        row["cost"][key] = None
    row["cost"][cost_field] = 1
    row["latency"]["wall_seconds"] = None
    row["consultation"] = {"count": 0, "applicable": None, "unavailable_reason": "provider_unavailable"}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "availability_scored" in result.stdout


@pytest.mark.parametrize(("section", "field", "value", "check"), [
    ("record", "repeat", True, "repeat"),
    ("terminal_receipt", "exit_code", False, "terminal_receipt"),
    ("quality", "critical_decision_defects", True, "quality_counts"),
    ("quality", "requirement_coverage", True, "quality_rates"),
    ("consultation", "count", True, "consultation"),
    ("cost", "input_tokens", True, "cost"),
    ("latency", "wall_seconds", True, "latency"),
])
def test_oracle_rejects_boolean_numeric_fields(tmp_path, section, field, value, check):
    payload = complete_payload()
    if section == "record":
        payload["records"][0][field] = value
    else:
        payload["records"][0][section][field] = value
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert check in result.stdout


@pytest.mark.parametrize("mutation", [
    ("record", "scalar"),
    ("terminal_receipt", None),
    ("quality", None),
    ("consultation", None),
    ("cost", None),
    ("latency", None),
])
def test_oracle_fail_closed_for_malformed_records(tmp_path, mutation):
    payload = complete_payload()
    field, value = mutation
    if field == "record":
        payload["records"][0] = value
    else:
        payload["records"][0][field] = value
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["passed"] is False
    assert receipt["checks"]


def test_mutating_quality_or_scoring_boundary_is_caught():
    payload = complete_payload()
    payload["records"][0]["quality"]["requirement_coverage"] = 2.0
    path = ROOT / "_phase3_bad_rate.json"
    try:
        path.write_text(json.dumps(payload))
        result = run_oracle(path)
    finally:
        path.unlink(missing_ok=True)
    assert result.returncode == 1
    assert "quality_rates" in result.stdout


def test_oracle_rejects_missing_case_repeat_cell(tmp_path):
    payload = complete_payload()
    moved = [row for row in payload["records"] if row["case_id"] == "N-02" and row["repeat"] == 3]
    for row in moved:
        row["case_id"] = "N-01"
        row["family"] = "routine-reversible-work"
        row["kind"] = "negative-control"
        payload_identity = row["task_identity"]["payload_identity"]
        row["task_identity"] = frozen_identity("N-01", "a")
        row["task_identity"]["payload_identity"] = payload_identity
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "\"check\": \"coverage\"" in result.stdout


def test_oracle_rejects_reused_pair_join_key(tmp_path):
    payload = complete_payload()
    first = next(row["blind_pair_id"] for row in payload["records"] if row["case_id"] == "T-01" and row["repeat"] == 1)
    for row in payload["records"]:
        if row["case_id"] == "T-01" and row["repeat"] == 2:
            row["blind_pair_id"] = first
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "\"check\": \"pair_key_unique\"" in result.stdout


def test_oracle_rejects_missing_required_diagnostics(tmp_path):
    payload = complete_payload()
    del payload["records"][0]["diagnostics"]
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "diagnostics" in result.stdout


def test_oracle_rejects_not_observed_cell_with_scores(tmp_path):
    payload = complete_payload()
    payload["records"][0]["diagnostics"]["availability"] = "not_observed"
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "availability_scored" in result.stdout


def test_oracle_rejects_component_unavailability_hidden_by_available_aggregate(tmp_path):
    payload = complete_payload()
    payload["records"][0]["diagnostics"]["provider"] = "provider_unavailable"
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "availability_consistency" in result.stdout
    assert "availability_scored" in result.stdout


@pytest.mark.parametrize("diagnostic", ["codebase_memory", "provider", "gateway", "dependency", "git"])
def test_oracle_rejects_unknown_component_diagnostic_values(tmp_path, diagnostic):
    payload = complete_payload()
    payload["records"][0]["diagnostics"][diagnostic] = "made_up"
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "diagnostic_values" in result.stdout
    assert "Traceback" not in result.stderr


def test_oracle_rejects_missing_overhead_on_available_row(tmp_path):
    payload = complete_payload()
    row = payload["records"][0]
    row["cost"]["total_tokens"] = None
    row["latency"]["wall_seconds"] = None
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "missing_observation" in result.stdout


@pytest.mark.parametrize("reason", [1, "made_up"])
def test_oracle_rejects_unavailable_reason_with_wrong_type_or_vocabulary(tmp_path, reason):
    payload = complete_payload()
    row = payload["records"][0]
    row["diagnostics"]["availability"] = "provider_unavailable"
    for key in row["quality"]:
        row["quality"][key] = None
    for key in row["cost"]:
        row["cost"][key] = None
    row["latency"]["wall_seconds"] = None
    row["consultation"] = {"count": 0, "applicable": None, "unavailable_reason": reason}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "consultation_unavailable_reason" in result.stdout
    assert "Traceback" not in result.stderr


def test_oracle_rejects_unavailable_reason_that_does_not_match_diagnostics(tmp_path):
    payload = complete_payload()
    row = payload["records"][0]
    row["diagnostics"]["availability"] = "provider_unavailable"
    for key in row["quality"]:
        row["quality"][key] = None
    for key in row["cost"]:
        row["cost"][key] = None
    row["latency"]["wall_seconds"] = None
    row["consultation"] = {"count": 0, "applicable": None, "unavailable_reason": "gateway_unavailable"}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "consultation_reason_mismatch" in result.stdout


@pytest.mark.parametrize(("field", "value"), [
    ("case_id", []),
    ("case_id", {}),
    ("blind_pair_id", []),
    ("diagnostics", {"availability": []}),
])
def test_oracle_returns_structured_receipt_for_malformed_scalar_boundaries(tmp_path, field, value):
    payload = complete_payload()
    if field == "diagnostics":
        payload["records"][0]["diagnostics"] = value
    else:
        payload["records"][0][field] = value
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["passed"] is False
    assert receipt["checks"]


def test_oracle_rejects_identity_hash_not_bound_to_frozen_files(tmp_path):
    payload = complete_payload()
    payload["records"][0]["task_identity"]["oracle_sha256"] = digest("arbitrary-oracle")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    result = run_oracle(path)
    assert result.returncode == 1
    assert "task_identity" in result.stdout


def test_manifest_rejects_automatic_acceptance_bypass():
    mutated = copy.deepcopy(MANIFEST)
    mutated["promotion"]["automatic_acceptance"] = True
    assert "automatic_acceptance" in validate_manifest(mutated)


@pytest.mark.parametrize("mutation", [
    ("coordinator", "model", "gpt-5.6-terra"),
    ("coordinator", "reasoning_effort", "low"),
    ("workers", "allowed_models", ["gpt-5.6-terra"]),
    ("workers", "allowed_reasoning_efforts", ["low"]),
    ("inherited_conversation", True),
    ("astra_allowed", True),
])
def test_manifest_rejects_model_envelope_mutations(mutation):
    if len(mutation) == 3:
        section, field, value = mutation
    else:
        section, field, value = "model_envelope", mutation[0], mutation[1]
    mutated = copy.deepcopy(MANIFEST)
    if section == "model_envelope":
        mutated["model_envelope"][field] = value
    else:
        mutated["model_envelope"][section][field] = value
    assert "model_envelope" in validate_manifest(mutated)


def test_manifest_rejects_empty_or_non_object_candidate():
    assert validate_manifest({})
    assert validate_manifest([]) == ["manifest_shape"]


@pytest.mark.parametrize("section, value, check", [
    ("cases", None, "case_order"),
    ("blind_scoring", None, "blind_scoring_shape"),
    ("promotion", [], "promotion_shape"),
])
def test_manifest_rejects_malformed_nested_candidates(section, value, check):
    mutated = copy.deepcopy(MANIFEST)
    mutated[section] = value
    errors = validate_manifest(mutated)
    assert check in errors


@pytest.mark.parametrize("section, field, value, check", [
    ("cases", "family", "changed-family", "case_policy"),
    ("randomization", "seed", "changed-seed", "randomization"),
    ("blind_scoring", "scorer_inputs", [], "blind_scoring_policy"),
    ("scorecard", "quality", [], "scorecard"),
    ("stop_conditions", 0, "changed-stop", "stop_conditions"),
    ("promotion", "criteria", [], "promotion_policy"),
    ("negative_controls", "specificity_target", 0.1, "negative_controls"),
    ("dependency_envelope", None, "changed-dependencies", "dependency_envelope"),
])
def test_manifest_rejects_adjacent_frozen_policy_mutations(section, field, value, check):
    mutated = copy.deepcopy(MANIFEST)
    if section == "cases":
        mutated[section][0][field] = value
    elif section == "stop_conditions":
        mutated[section][field] = value
    elif section == "dependency_envelope":
        mutated[section] = value
    else:
        mutated[section][field] = value
    assert check in validate_manifest(mutated)
