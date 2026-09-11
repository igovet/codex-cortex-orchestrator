import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phase4_telemetry", ROOT / "scripts/phase4_telemetry.py")
TELEMETRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TELEMETRY)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def source_record(**updates):
    usage = dict(
        input_tokens=100,
        cached_input_tokens=60,
        cache_write_input_tokens=5,
        output_tokens=20,
        reasoning_output_tokens=4,
        total_tokens=120,
    )
    value = {
        "run_id": "run-01",
        "pair_id": "pair-01",
        "suite_id": "phase4-synthetic-v1",
        "phase": "phase4",
        "strategy_version": "static-v1",
        "host": "cli",
        "payload_digest": digest("payload"),
        "config_digest": digest("config"),
        "dependency_digest": digest("dependencies"),
        "tool_catalogue_digest": digest("tools"),
        "task_family_digest": digest("family"),
        "fixture_digest": digest("fixture"),
        "oracle_digest": digest("oracle"),
        "lifecycle": {
            "start_at": "2026-09-10T10:00:00Z",
            "finish_at": "2026-09-10T10:00:09Z",
            "wall_seconds": 9.0,
            "wall_source": "coordinator_task_lifecycle",
            "terminal_status": "complete",
            "audit_status": "complete",
            "open_sessions": 0,
        },
        "participants": [
            {
                "role": "coordinator",
                "model": "gpt-5.6-luna",
                "effort": "high",
                "responses": [
                    {"response_id": "response-1", "usage": usage},
                    {"response_id": "response-1", "usage": usage},
                ],
            },
            {
                "role": "worker",
                "model": "gpt-5.6-luna",
                "effort": "medium",
                "responses": [
                    {"response_id": "response-2", "usage": usage},
                ],
            },
        ],
        "counts": {"dispatch_count": 1, "read_count": 2, "write_count": 1, "report_count": 1},
        "availability": {
            "codebase_memory": {"status": "unavailable", "reason": "service-unavailable"},
            "gateway_provider": {"status": "available", "reason": None},
            "dependencies": {"status": "available", "reason": None},
            "git": {"status": "available", "reason": None},
            "host_capabilities": {"status": "available", "reason": None},
        },
        "errors": {"external-availability": 1},
    }
    value.update(updates)
    return value


def test_reducer_deduplicates_response_ids_and_keeps_token_dimensions():
    result = TELEMETRY.aggregate_run(source_record())
    assert result["response_count"] == 2
    assert result["tokens"]["input_tokens"] == 200
    assert result["tokens"]["cached_input_tokens"] == 120
    assert result["tokens"]["cache_write_input_tokens"] == 10
    assert [row["role"] for row in result["participants"]] == ["coordinator", "worker"]
    assert all("thread_id" not in row for row in result["participants"])
    assert result["availability"]["codebase_memory"]["status"] == "unavailable"
    assert result["quality"] is None


def test_missing_participants_are_null_not_zero():
    result = TELEMETRY.aggregate_run(source_record(participants=None))
    assert result["participants"] is None
    assert result["tokens"] is None
    assert result["response_count"] is None
    assert result["unavailable_reason"] == "participant-telemetry-unavailable"


def test_missing_response_rows_are_null_safe():
    value = source_record()
    value["participants"][0]["responses"] = None
    result = TELEMETRY.aggregate_run(value)
    assert result["tokens"] is None
    assert result["response_count"] is None
    assert result["unavailable_reason"] == "participant-telemetry-unavailable"
    assert result["participants"][0]["responses"] == 0


def test_quality_requires_independent_source_and_is_not_derived_from_errors():
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(source_record(quality={"status": "measured", "outcome": "pass"}))
    result = TELEMETRY.aggregate_run(
        source_record(quality={"source": "independent_oracle", "status": "measured", "outcome": "pass", "score": 1})
    )
    assert result["quality"]["source"] == "independent_oracle"


def test_unknown_and_private_fields_fail_closed():
    value = source_record()
    value["prompt"] = "private"
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)
    value = source_record()
    value["availability"]["gateway_provider"]["raw_output"] = "private"
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)


def test_cli_and_desktop_normalize_to_the_same_shape():
    cli = TELEMETRY.aggregate_run(source_record(host="cli"))
    desktop = TELEMETRY.aggregate_run(source_record(host="desktop"))
    assert set(cli) == set(desktop)
    assert set(cli["availability"]) == set(TELEMETRY.AVAILABILITY_KINDS)


def test_conflicting_duplicate_response_is_rejected():
    value = source_record()
    value["participants"][1]["responses"][0]["response_id"] = "response-1"
    value["participants"][1]["responses"][0]["usage"] = dict(
        input_tokens=999,
        cached_input_tokens=60,
        cache_write_input_tokens=5,
        output_tokens=20,
        reasoning_output_tokens=4,
        total_tokens=120,
    )
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)


def test_sink_is_owner_private_and_retains_bounded_records(tmp_path):
    sink = tmp_path / "telemetry.jsonl"
    one = TELEMETRY.aggregate_run(source_record(run_id="run-01"))
    two = TELEMETRY.aggregate_run(source_record(run_id="run-02"))
    TELEMETRY.append_aggregate(sink, one, max_records=1)
    TELEMETRY.append_aggregate(sink, two, max_records=1)
    assert sink.stat().st_mode & 0o077 == 0
    rows = [json.loads(line) for line in sink.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["run_id"] == "run-02"


def test_sink_rejects_forged_schema_records_without_retaining_raw_fields(tmp_path):
    sink = tmp_path / "telemetry.jsonl"
    forged = {"schema_version": TELEMETRY.SCHEMA_VERSION, "prompt": "private raw text"}
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.append_aggregate(sink, forged)
    assert not sink.exists()


@pytest.mark.parametrize("wall_seconds", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_lifecycle_wall_time_is_rejected(wall_seconds):
    value = source_record()
    value["lifecycle"]["wall_seconds"] = wall_seconds
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_quality_score_is_rejected(score):
    value = source_record(quality={"source": "independent_oracle", "status": "measured", "outcome": "pass", "score": score})
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)


def test_error_cardinality_is_bounded():
    value = source_record(errors={f"category-{index}": 1 for index in range(33)})
    with pytest.raises(TELEMETRY.TelemetryError):
        TELEMETRY.aggregate_run(value)
