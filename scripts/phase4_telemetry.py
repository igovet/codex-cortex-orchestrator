#!/usr/bin/env python3
"""Privacy-safe, offline Phase 4 telemetry reducer.

This module accepts reviewed event summaries, never host transcripts.  It emits
one bounded aggregate per run/cell and deliberately keeps availability,
diagnostics, and independently supplied quality evidence in separate fields.
It has no runtime, routing, model-selection, or acceptance responsibilities.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


SCHEMA_VERSION = "phase4-telemetry-v1"
MAX_RECORD_BYTES = 64 * 1024
MAX_PARTICIPANT_BUCKETS = 32
MAX_ERROR_CATEGORIES = 32
MAX_REASON_LENGTH = 120
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
COUNT_FIELDS = (
    "dispatch_count",
    "read_count",
    "write_count",
    "report_count",
    "consultation_count",
    "retry_count",
    "wait_count",
    "recovery_count",
)
AVAILABILITY_KINDS = (
    "codebase_memory",
    "gateway_provider",
    "dependencies",
    "git",
    "host_capabilities",
)
AVAILABILITY_STATUSES = ("available", "unavailable", "unknown", "not_checked")
HOST_CLASSES = ("cli", "desktop", "offline")
WALL_SOURCES = ("coordinator_task_lifecycle", "response_span", "reviewed", "unavailable")
TERMINAL_STATUSES = ("complete", "incomplete", "unavailable", "unknown")
QUALITY_STATUSES = ("measured", "unavailable", "unknown")
QUALITY_SOURCES = ("independent_oracle", "independent_review")

_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_ISO8601 = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s]{1,48}$")
_PRIVATE_WORDS = re.compile(
    r"(?:prompt|command|report_body|raw_output|raw_model|credential|authorization|cookie|password|secret|thread_id|transcript|patch)",
    re.IGNORECASE,
)


class TelemetryError(ValueError):
    """Raised when a source row is not safe or does not match the contract."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelemetryError(f"{name} must be an object")
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"{name} contains unsupported fields: {sorted(unknown)!r}")


def _opaque(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        raise TelemetryError(f"{name} must be an opaque bounded identifier")
    return value


def _digest(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise TelemetryError(f"{name} must be a lowercase SHA-256 digest or null")
    return value


def _bounded_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise TelemetryError(f"{name} must be a non-negative bounded integer")
    return value


def _reason(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_REASON_LENGTH:
        raise TelemetryError(f"{name} must be a short reason code")
    if _PRIVATE_WORDS.search(value) or any(ord(char) < 32 for char in value):
        raise TelemetryError(f"{name} contains private content")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise TelemetryError(f"{name} must be a reason code")
    return value


def _timestamp(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _ISO8601.fullmatch(value):
        raise TelemetryError(f"{name} must be a bounded ISO-8601 timestamp or null")
    return value


def _availability(value: Any) -> dict[str, dict[str, str | None]]:
    source = _mapping(value, "availability")
    _keys(source, set(AVAILABILITY_KINDS), "availability")
    result: dict[str, dict[str, str | None]] = {}
    for kind in AVAILABILITY_KINDS:
        item = source.get(kind)
        if item is None:
            result[kind] = {"status": "not_checked", "reason": "not-recorded"}
            continue
        item = _mapping(item, f"availability.{kind}")
        _keys(item, {"status", "reason"}, f"availability.{kind}")
        status = item.get("status", "unknown")
        if status not in AVAILABILITY_STATUSES:
            raise TelemetryError(f"availability.{kind}.status is invalid")
        reason = _reason(item.get("reason"), f"availability.{kind}.reason")
        if status == "available" and reason is not None:
            raise TelemetryError(f"available {kind} cannot carry an exclusion reason")
        result[kind] = {"status": status, "reason": reason}
    return result


def _usage(value: Any, name: str) -> dict[str, int]:
    usage = _mapping(value, name)
    _keys(usage, set(TOKEN_FIELDS), name)
    return {field: _bounded_int(usage.get(field), f"{name}.{field}") for field in TOKEN_FIELDS}


def _participants(value: Any) -> tuple[list[dict[str, Any]] | None, dict[str, int], dict[str, int] | None, int | None]:
    if value is None:
        return None, {}, None, None
    if not isinstance(value, list) or len(value) > MAX_PARTICIPANT_BUCKETS:
        raise TelemetryError("participants must be a bounded list")
    buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    responses: dict[str, dict[str, int]] = {}
    for index, participant in enumerate(value):
        participant = _mapping(participant, f"participants[{index}]")
        _keys(participant, {"role", "model", "effort", "responses"}, f"participants[{index}]")
        role = participant.get("role")
        if role not in {"coordinator", "worker"}:
            raise TelemetryError(f"participants[{index}].role is invalid")
        model = participant.get("model")
        effort = participant.get("effort")
        if not isinstance(model, str) or not _MODEL.fullmatch(model):
            raise TelemetryError(f"participants[{index}].model is invalid")
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise TelemetryError(f"participants[{index}].effort is invalid")
        bucket = buckets.setdefault((role, model, effort), {"participants": 0, "responses": 0})
        bucket["participants"] += 1
        row_responses = participant.get("responses")
        if row_responses is None:
            bucket["responses"] = -1
            continue
        if not isinstance(row_responses, list) or len(row_responses) > 256:
            raise TelemetryError(f"participants[{index}].responses must be bounded")
        for response_index, response in enumerate(row_responses):
            response = _mapping(response, f"participants[{index}].responses[{response_index}]")
            _keys(response, {"response_id", "usage"}, "response")
            response_id = _opaque(response.get("response_id"), "response_id")
            usage = _usage(response.get("usage"), "response.usage")
            previous = responses.get(response_id)
            if previous is not None and previous != usage:
                raise TelemetryError("a response ID has conflicting usage")
            if previous is None:
                responses[response_id] = usage
                if bucket["responses"] >= 0:
                    bucket["responses"] += 1
    if len(buckets) > MAX_PARTICIPANT_BUCKETS:
        raise TelemetryError("participant cardinality exceeds the bound")
    if not value or any(bucket["responses"] < 0 for bucket in buckets.values()):
        for bucket in buckets.values():
            bucket["responses"] = 0
        normalized = [
            {"role": role, "model": model, "effort": effort, **buckets[(role, model, effort)]}
            for role, model, effort in sorted(buckets)
        ]
        return normalized, {"response_count": 0}, None, None
    normalized = [
        {"role": role, "model": model, "effort": effort, **buckets[(role, model, effort)]}
        for role, model, effort in sorted(buckets)
    ]
    totals = {field: sum(usage[field] for usage in responses.values()) for field in TOKEN_FIELDS}
    return normalized, {"response_count": len(responses)}, totals, len(responses)


def _lifecycle(value: Any) -> dict[str, Any]:
    source = _mapping(value, "lifecycle")
    _keys(source, {"start_at", "finish_at", "wall_seconds", "wall_source", "terminal_status", "audit_status", "open_sessions"}, "lifecycle")
    wall = source.get("wall_seconds")
    if wall is not None and (
        type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0 or wall > 10**9
    ):
        raise TelemetryError("lifecycle.wall_seconds must be non-negative or null")
    wall_source = source.get("wall_source", "unavailable")
    if wall_source not in WALL_SOURCES:
        raise TelemetryError("lifecycle.wall_source is invalid")
    terminal = source.get("terminal_status", "unknown")
    audit = source.get("audit_status", "unknown")
    if terminal not in TERMINAL_STATUSES or audit not in TERMINAL_STATUSES:
        raise TelemetryError("lifecycle status is invalid")
    open_sessions = source.get("open_sessions")
    if type(open_sessions) is not int or open_sessions < 0 or open_sessions > 10000:
        raise TelemetryError("lifecycle.open_sessions must be a bounded integer")
    return {
        "start_at": _timestamp(source.get("start_at"), "lifecycle.start_at"),
        "finish_at": _timestamp(source.get("finish_at"), "lifecycle.finish_at"),
        "wall_seconds": wall,
        "wall_source": wall_source,
        "terminal_status": terminal,
        "audit_status": audit,
        "open_sessions": open_sessions,
    }


def _counts(value: Any) -> dict[str, int]:
    source = {} if value is None else _mapping(value, "counts")
    _keys(source, set(COUNT_FIELDS), "counts")
    return {field: _bounded_int(source.get(field, 0), f"counts.{field}") for field in COUNT_FIELDS}


def _errors(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    source = _mapping(value, "errors")
    if len(source) > MAX_ERROR_CATEGORIES:
        raise TelemetryError("error category cardinality exceeds the bound")
    result = {}
    for category, count in source.items():
        category = _reason(category, "error category")
        result[category] = _bounded_int(count, f"errors.{category}")
    return dict(sorted(result.items()))


def _quality(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    source = _mapping(value, "quality")
    _keys(source, {"source", "status", "outcome", "score"}, "quality")
    status = source.get("status", "unknown")
    quality_source = source.get("source")
    if status not in QUALITY_STATUSES or quality_source not in QUALITY_SOURCES:
        raise TelemetryError("quality requires an independent oracle or review source")
    outcome = source.get("outcome")
    if outcome is not None and (not isinstance(outcome, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,48}", outcome)):
        raise TelemetryError("quality.outcome must be a bounded label")
    score = source.get("score")
    if score is not None and (
        type(score) not in (int, float) or not math.isfinite(score) or score < 0 or score > 1
    ):
        raise TelemetryError("quality.score must be between zero and one")
    return {"source": quality_source, "status": status, "outcome": outcome, "score": score}


def _normalized_participants(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_PARTICIPANT_BUCKETS:
        raise TelemetryError("aggregate.participants must be a bounded list")
    result = []
    seen: set[tuple[str, str, str]] = set()
    for index, participant in enumerate(value):
        participant = _mapping(participant, f"aggregate.participants[{index}]")
        _keys(
            participant,
            {"role", "model", "effort", "participants", "responses"},
            f"aggregate.participants[{index}]",
        )
        role = participant.get("role")
        model = participant.get("model")
        effort = participant.get("effort")
        if role not in {"coordinator", "worker"}:
            raise TelemetryError(f"aggregate.participants[{index}].role is invalid")
        if not isinstance(model, str) or not _MODEL.fullmatch(model):
            raise TelemetryError(f"aggregate.participants[{index}].model is invalid")
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise TelemetryError(f"aggregate.participants[{index}].effort is invalid")
        key = (role, model, effort)
        if key in seen:
            raise TelemetryError("aggregate.participants contains duplicate buckets")
        seen.add(key)
        result.append(
            {
                "role": role,
                "model": model,
                "effort": effort,
                "participants": _bounded_int(
                    participant.get("participants"), f"aggregate.participants[{index}].participants"
                ),
                "responses": _bounded_int(
                    participant.get("responses"), f"aggregate.participants[{index}].responses"
                ),
            }
        )
    if result != sorted(result, key=lambda item: (item["role"], item["model"], item["effort"])):
        raise TelemetryError("aggregate.participants must be sorted")
    return result


def _validate_aggregate(value: Any) -> dict[str, Any]:
    """Validate and normalize a previously reduced record at the sink boundary."""
    source = _mapping(value, "aggregate")
    allowed = {
        "schema_version", "run_id", "pair_id", "suite_id", "phase", "strategy_version", "host",
        "digests", "lifecycle", "participants", "participant_counts", "tokens", "response_count",
        "counts", "availability", "quality", "errors", "unavailable_reason",
    }
    _keys(source, allowed, "aggregate")
    missing = allowed - set(source)
    if missing:
        raise TelemetryError(f"aggregate is missing required fields: {sorted(missing)!r}")
    if source["schema_version"] != SCHEMA_VERSION:
        raise TelemetryError("aggregate schema_version is invalid")
    host = source["host"]
    if host not in HOST_CLASSES:
        raise TelemetryError("aggregate host must be cli, desktop, or offline")
    digest_source = _mapping(source["digests"], "aggregate.digests")
    digest_keys = {"payload", "config", "dependency", "tool_catalogue", "task_family", "fixture", "oracle"}
    _keys(digest_source, digest_keys, "aggregate.digests")
    digests = {key: _digest(digest_source.get(key), f"aggregate.digests.{key}") for key in digest_keys}
    participants = _normalized_participants(source["participants"])
    participant_counts = _mapping(source["participant_counts"], "aggregate.participant_counts")
    _keys(participant_counts, {"response_count"}, "aggregate.participant_counts")
    if participant_counts and set(participant_counts) != {"response_count"}:
        raise TelemetryError("aggregate.participant_counts is invalid")
    normalized_counts = {
        key: _bounded_int(value, f"aggregate.participant_counts.{key}") for key, value in participant_counts.items()
    }
    tokens = source["tokens"]
    normalized_tokens = None if tokens is None else _usage(tokens, "aggregate.tokens")
    response_count = source["response_count"]
    normalized_response_count = None if response_count is None else _bounded_int(response_count, "aggregate.response_count")
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _opaque(source["run_id"], "aggregate.run_id"),
        "pair_id": _opaque(source["pair_id"], "aggregate.pair_id"),
        "suite_id": _opaque(source["suite_id"], "aggregate.suite_id"),
        "phase": _opaque(source["phase"], "aggregate.phase"),
        "strategy_version": _opaque(source["strategy_version"], "aggregate.strategy_version"),
        "host": host,
        "digests": digests,
        "lifecycle": _lifecycle(source["lifecycle"]),
        "participants": participants,
        "participant_counts": normalized_counts,
        "tokens": normalized_tokens,
        "response_count": normalized_response_count,
        "counts": _counts(source["counts"]),
        "availability": _availability(source["availability"]),
        "quality": _quality(source["quality"]),
        "errors": _errors(source["errors"]),
        "unavailable_reason": _reason(source["unavailable_reason"], "aggregate.unavailable_reason"),
    }
    try:
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise TelemetryError("aggregate contains non-JSON-safe values") from exc
    if len(encoded) > MAX_RECORD_BYTES:
        raise TelemetryError("aggregate exceeds the privacy size bound")
    return result


def aggregate_run(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and reduce one reviewed synthetic run/cell into safe telemetry."""
    source = _mapping(source, "run")
    allowed = {
        "run_id", "pair_id", "suite_id", "phase", "strategy_version", "host",
        "payload_digest", "config_digest", "dependency_digest", "tool_catalogue_digest",
        "task_family_digest", "fixture_digest", "oracle_digest", "lifecycle", "participants",
        "counts", "availability", "quality", "errors", "unavailable_reason",
    }
    _keys(source, allowed, "run")
    required = ("run_id", "pair_id", "suite_id", "phase", "strategy_version", "host", "availability", "lifecycle")
    missing = [key for key in required if key not in source]
    if missing:
        raise TelemetryError(f"run is missing required fields: {missing!r}")
    host = source["host"]
    if host not in HOST_CLASSES:
        raise TelemetryError("host must be cli, desktop, or offline")
    participants, participant_counts, token_totals, response_count = _participants(source.get("participants"))
    quality = _quality(source.get("quality"))
    unavailable_reason = _reason(source.get("unavailable_reason"), "unavailable_reason")
    if token_totals is None and unavailable_reason is None:
        unavailable_reason = "participant-telemetry-unavailable"
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _opaque(source["run_id"], "run_id"),
        "pair_id": _opaque(source["pair_id"], "pair_id"),
        "suite_id": _opaque(source["suite_id"], "suite_id"),
        "phase": _opaque(source["phase"], "phase"),
        "strategy_version": _opaque(source["strategy_version"], "strategy_version"),
        "host": host,
        "digests": {
            "payload": _digest(source.get("payload_digest"), "payload_digest"),
            "config": _digest(source.get("config_digest"), "config_digest"),
            "dependency": _digest(source.get("dependency_digest"), "dependency_digest"),
            "tool_catalogue": _digest(source.get("tool_catalogue_digest"), "tool_catalogue_digest"),
            "task_family": _digest(source.get("task_family_digest"), "task_family_digest"),
            "fixture": _digest(source.get("fixture_digest"), "fixture_digest"),
            "oracle": _digest(source.get("oracle_digest"), "oracle_digest"),
        },
        "lifecycle": _lifecycle(source["lifecycle"]),
        "participants": participants,
        "participant_counts": participant_counts,
        "tokens": token_totals,
        "response_count": response_count,
        "counts": _counts(source.get("counts")),
        "availability": _availability(source["availability"]),
        "quality": quality,
        "errors": _errors(source.get("errors")),
        "unavailable_reason": unavailable_reason,
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_RECORD_BYTES:
        raise TelemetryError("aggregate exceeds the privacy size bound")
    return result


normalize_run = aggregate_run
reduce_run = aggregate_run


def append_aggregate(path: Path, aggregate: Mapping[str, Any], *, max_records: int = 1000) -> None:
    """Append an aggregate to an owner-private JSONL sink with bounded retention."""
    if type(max_records) is not int or not 1 <= max_records <= 10000:
        raise TelemetryError("max_records must be between one and 10000")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise TelemetryError("aggregate sink must be owner-private")
    candidate = _mapping(aggregate, "aggregate")
    record = aggregate_run(candidate) if "schema_version" not in candidate else _validate_aggregate(candidate)
    lines = []
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                existing = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TelemetryError(f"aggregate sink line {index + 1} is invalid JSON") from exc
            lines.append(json.dumps(_validate_aggregate(existing), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    lines = (lines + [json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)])[-max_records:]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON source record")
    parser.add_argument("--output", type=Path, help="optional owner-private JSONL sink")
    args = parser.parse_args()
    aggregate = aggregate_run(json.loads(args.input.read_text(encoding="utf-8")))
    if args.output:
        append_aggregate(args.output, aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
