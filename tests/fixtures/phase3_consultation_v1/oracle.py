#!/usr/bin/env python3
"""Independent offline protocol oracle for phase3-consultation-v1."""

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CASES = {"T-01": ("material-consequential-decision", "qualifying"), "T-02": ("contradictory-hypothesis-bound-evidence", "qualifying"), "T-03": ("repeated-failure-surprise-replanning", "qualifying"), "N-01": ("routine-reversible-work", "negative-control"), "N-02": ("active-incident-recovery", "negative-control")}
REASONS = {"host_unavailable", "provider_unavailable", "gateway_unavailable", "dependency_unavailable", "codebase_memory_unavailable", "required_receipt_unavailable", "not_observed"}
DIAGNOSTICS = {"availability", "codebase_memory", "provider", "gateway", "dependency", "git"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def fail(check, reason):
    return {"check": check, "passed": False, "reason": reason}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _frozen_identity(case_id):
    """Return the condition-independent identity frozen by the manifest."""
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    workloads = json.loads((ROOT / "workloads-v1.json").read_text(encoding="utf-8"))
    workload = next((row for row in workloads.get("cases", []) if row.get("case_id") == case_id), None)
    if workload is None:
        return None
    return {
        "suite_version": manifest.get("suite_version"),
        "case_id": case_id,
        "prompt_sha256": digest(workload.get("prompt", "")),
        "fixture_sha256": manifest.get("workloads_sha256"),
        "oracle_sha256": hashlib.sha256((ROOT / "oracle.py").read_bytes()).hexdigest(),
        "reset_sha256": hashlib.sha256((ROOT / "reset.py").read_bytes()).hexdigest(),
        "dependency_envelope_sha256": digest(manifest.get("dependency_envelope", "")),
    }


def task_identity_ok(identity, case_id):
    required = ("suite_version", "case_id", "prompt_sha256", "fixture_sha256", "oracle_sha256", "reset_sha256", "dependency_envelope_sha256", "payload_identity")
    if not isinstance(case_id, str) or not isinstance(identity, dict) or not set(identity) >= set(required):
        return False
    expected = _frozen_identity(case_id)
    return (expected is not None and identity["suite_version"] == expected["suite_version"] and
            identity["case_id"] == case_id and
            all(identity[key] == expected[key] for key in required[2:-1]) and
            HEX64.fullmatch(str(identity["payload_identity"])) is not None)


def _strict_int(value):
    """Return true for JSON integer values, excluding Python booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


def _strict_number(value):
    """Return true for finite JSON numbers, excluding Python booleans."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _opaque_id(value, prefix):
    """Return true only for the scalar opaque identifiers used by the ledger."""
    return isinstance(value, str) and re.fullmatch(rf"{re.escape(prefix)}[a-z0-9]{{16}}", value) is not None


def _diagnostic_value_ok(value):
    """Diagnostic cells use the frozen availability vocabulary, never arbitrary values."""
    return isinstance(value, str) and (value == "available" or value in REASONS)


def record_checks(record):
    if not isinstance(record, Mapping):
        return ["record_shape"]
    errors = []
    case_id = record.get("case_id")
    diagnostics = record.get("diagnostics")
    if not isinstance(diagnostics, dict) or not DIAGNOSTICS <= set(diagnostics):
        errors.append("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    availability = diagnostics.get("availability")
    if not _diagnostic_value_ok(availability):
        errors.append("availability")
    if not all(_diagnostic_value_ok(diagnostics.get(name)) for name in DIAGNOSTICS):
        errors.append("diagnostic_values")
    component_reasons = {
        value for name, value in diagnostics.items()
        if name in DIAGNOSTICS and isinstance(value, str) and value in REASONS
    }
    unavailable_reasons = set(component_reasons)
    if isinstance(availability, str) and availability in REASONS:
        unavailable_reasons.add(availability)
    unavailable = bool(unavailable_reasons)
    if availability == "available" and component_reasons:
        errors.append("availability_consistency")
    if (not isinstance(case_id, str) or case_id not in CASES
            or record.get("family") != CASES.get(case_id, (None, None))[0]
            or record.get("kind") != CASES.get(case_id, (None, None))[1]):
        errors.append("case_identity")
    if not _strict_int(record.get("repeat")) or record["repeat"] not in (1, 2, 3):
        errors.append("repeat")
    if not _opaque_id(record.get("run_id"), "run-"):
        errors.append("run_id")
    if not _opaque_id(record.get("blind_pair_id"), "pair-"):
        errors.append("blind_pair_id")
    if not task_identity_ok(record.get("task_identity", {}), case_id):
        errors.append("task_identity")
    terminal = record.get("terminal_receipt", {})
    if not isinstance(terminal, Mapping):
        errors.append("terminal_receipt_shape")
        terminal = {}
    complete = (terminal.get("status") == "complete"
                and terminal.get("audit_status") == "pass"
                and _strict_int(terminal.get("exit_code"))
                and terminal.get("exit_code") == 0)
    excluded = (unavailable
                and isinstance(terminal.get("status"), str)
                and terminal.get("status") in {"unavailable", "excluded"}
                and isinstance(terminal.get("audit_status"), str)
                and terminal.get("audit_status") in {"unavailable", "not_run"}
                and (terminal.get("exit_code") is None
                     or (_strict_int(terminal.get("exit_code"))
                         and terminal.get("exit_code") == 1)))
    if not (complete or excluded):
        errors.append("terminal_receipt")
    quality = record.get("quality", {})
    if not isinstance(quality, Mapping):
        errors.append("quality_shape")
        quality = {}
    required_quality = ("critical_decision_defects", "requirement_coverage", "discriminating_check_use", "false_consultation_activation", "missed_qualifying_transition", "protocol_pass", "acceptance_regression", "protected_content_preserved")
    if not set(quality) >= set(required_quality):
        errors.append("quality_fields")
    elif unavailable:
        if any(value is not None for value in quality.values()):
            errors.append("availability_scored")
    else:
        if (not _strict_number(quality["requirement_coverage"])
                or not _strict_number(quality["discriminating_check_use"])
                or not (0 <= quality["requirement_coverage"] <= 1
                        and 0 <= quality["discriminating_check_use"] <= 1)):
            errors.append("quality_rates")
        if any(not _strict_int(quality[key]) or quality[key] < 0 for key in ("critical_decision_defects", "false_consultation_activation", "missed_qualifying_transition")):
            errors.append("quality_counts")
        if not all(isinstance(quality[key], bool) for key in ("protocol_pass", "acceptance_regression", "protected_content_preserved")):
            errors.append("quality_flags")
    consultation = record.get("consultation", {})
    if not isinstance(consultation, Mapping):
        errors.append("consultation_shape")
        consultation = {}
    applicable = consultation.get("applicable")
    if (not _strict_int(consultation.get("count"))
            or consultation["count"] < 0
            or not (applicable is True or applicable is False or applicable is None)):
        errors.append("consultation")
    consultation_reason = consultation.get("unavailable_reason")
    if applicable is None:
        if not isinstance(consultation_reason, str) or consultation_reason not in REASONS:
            errors.append("consultation_unavailable_reason")
        else:
            if not unavailable:
                errors.append("consultation_unavailable_state")
            elif consultation_reason not in unavailable_reasons:
                errors.append("consultation_reason_mismatch")
    elif consultation_reason is not None:
        errors.append("consultation_unavailable_reason")
    if unavailable and applicable is not None:
        errors.append("availability_consultation")
    if consultation.get("applicable") is False and consultation.get("count") != 0:
        errors.append("negative_consultation_count")
    cost = record.get("cost", {})
    if not isinstance(cost, Mapping):
        errors.append("cost_shape")
        cost = {}
    cost_fields = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
    for key in cost_fields:
        if cost.get(key) is not None and (not _strict_int(cost[key]) or cost[key] < 0):
            errors.append("cost")
    latency = record.get("latency", {})
    if not isinstance(latency, Mapping):
        errors.append("latency_shape")
        latency = {}
    if (latency.get("wall_seconds") is not None
            and (not _strict_number(latency["wall_seconds"])
                 or latency["wall_seconds"] < 0)):
        errors.append("latency")
    if not unavailable and (
            any(cost.get(key) is None for key in cost_fields)
            or latency.get("wall_seconds") is None):
        errors.append("missing_observation")
    if DIAGNOSTICS <= set(diagnostics) and unavailable:
        if (any(quality.get(key) is not None for key in required_quality)
                or any(cost.get(key) is not None for key in cost_fields)
                or latency.get("wall_seconds") is not None):
            errors.append("availability_scored")
    forbidden = {"arm", "condition", "baseline", "candidate", "payload_mapping", "private_prompt", "raw_prompt"}
    def leaked(value):
        if isinstance(value, dict):
            return any(str(key).lower() in forbidden or leaked(item) for key, item in value.items())
        if isinstance(value, list):
            return any(leaked(item) for item in value)
        return False
    if leaked(record):
        errors.append("arm_or_private_leak")
    return errors


def validate(payload):
    if not isinstance(payload, Mapping):
        return [fail("payload_shape", "payload must be a JSON object")]
    errors = []
    if payload.get("suite_version") != "phase3-consultation-v1":
        errors.append(fail("suite_version", "wrong suite version"))
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 30:
        errors.append(fail("record_count", "expected exactly 30 records"))
        return errors
    pair_groups = {}
    pair_cells = {}
    cell_pairs = {}
    seen_runs = set()
    for record in records:
        errors.extend(fail("record", reason) for reason in record_checks(record))
        if not isinstance(record, Mapping):
            continue
        run_id = record.get("run_id")
        if _opaque_id(run_id, "run-") and run_id in seen_runs:
            errors.append(fail("run_id_unique", "run_id repeated"))
        if _opaque_id(run_id, "run-"):
            seen_runs.add(run_id)
        pair_id = record.get("blind_pair_id")
        case_id = record.get("case_id")
        repeat = record.get("repeat")
        if not (_opaque_id(pair_id, "pair-") and isinstance(case_id, str)
                and _strict_int(repeat)):
            continue
        cell = (case_id, repeat)
        pair_groups.setdefault((pair_id, *cell), []).append(record)
        pair_cells.setdefault(pair_id, set()).add(cell)
        cell_pairs.setdefault(cell, set()).add(pair_id)
    expected_cells = {(case_id, repeat) for case_id in CASES for repeat in (1, 2, 3)}
    observed_cells = set(cell_pairs)
    if observed_cells != expected_cells:
        missing = sorted(expected_cells - observed_cells)
        extra = sorted(observed_cells - expected_cells)
        errors.append(fail("coverage", f"expected exact case/repeat matrix; missing={missing}, extra={extra}"))
    if any(len(cells) != 1 for cells in pair_cells.values()) or any(len(pairs) != 1 for pairs in cell_pairs.values()):
        errors.append(fail("pair_key_unique", "each blind_pair_id must identify exactly one case/repeat cell"))
    if len(pair_groups) != 15 or any(len(rows) != 2 for rows in pair_groups.values()):
        errors.append(fail("paired_repeats", "each case/repeat must have exactly two blinded rows"))
    for (_pair, case_id, _repeat), rows in pair_groups.items():
        if len(rows) == 2:
            left, right = (row.get("task_identity", {}) for row in rows)
            common = ("suite_version", "case_id", "prompt_sha256", "fixture_sha256", "oracle_sha256", "reset_sha256", "dependency_envelope_sha256")
            if any(left.get(key) != right.get(key) for key in common):
                errors.append(fail("identity_asymmetry", "paired rows disagree on condition-independent identity"))
        if (case_id in CASES and CASES[case_id][1] == "negative-control"
                and any((row.get("consultation") or {}).get("count") != 0
                        if isinstance(row.get("consultation"), Mapping) else True
                        for row in rows)):
            errors.append(fail("negative_control", "negative controls must not activate consultation"))
        if (case_id in ("T-01", "T-02", "T-03")
                and any((row.get("consultation") or {}).get("count") not in (0, 1)
                        if isinstance(row.get("consultation"), Mapping) else True
                        for row in rows)):
            errors.append(fail("consultation_bound", "at most one consultation per qualifying transition"))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.ledger.read_text(encoding="utf-8"))
        errors = validate(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError, KeyError) as exc:
        errors = [fail("input", str(exc))]
    output = {"suite_version": "phase3-consultation-v1", "passed": not errors, "exit_code": 0 if not errors else 1, "checks": errors}
    print(json.dumps(output, sort_keys=True))
    return output["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
