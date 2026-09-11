#!/usr/bin/env python3
"""Offline Phase 5 model-owned adaptive-selection overlay.

The module validates a small, privacy-safe contract around reviewed Phase 4
aggregates.  It never launches a host, selects an executor, writes a pipeline,
or changes runtime policy.  A model-supplied candidate is advisory; missing,
stale, contradictory, incomplete, contaminated, or externally unavailable
evidence produces an explicit static-baseline fallback.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "phase5-adaptive-v1"
CONTEXT_VERSION = "phase5-adaptive-context-v1"
BUNDLE_VERSION = "phase5-evidence-bundle-v1"
MANIFEST_VERSION = "phase5-adaptive-manifest-v1"
RECOMMENDATION_VERSION = "phase5-strategy-recommendation-v1"
DECISION_VERSION = "phase5-decision-record-v1"

MAX_RECORD_BYTES = 64 * 1024
MAX_EVIDENCE = 256
MAX_REPORTS = 64
MAX_DIGESTS = 32
MAX_REASONS = 16
MAX_TEXT = 240

EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
QUALITY_SOURCES = {"independent_oracle", "independent_review"}
AVAILABILITY_KINDS = (
    "codebase_memory",
    "gateway_provider",
    "dependencies",
    "git",
    "host_capabilities",
)
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_PRIVATE = re.compile(
    r"(?:prompt|command|report_body|raw_output|raw_model|credential|authorization|cookie|password|secret|thread_id|transcript|patch)",
    re.IGNORECASE,
)


class AdaptiveError(ValueError):
    """Raised when an offline Phase 5 input violates its privacy contract."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdaptiveError(f"{name} must be an object")
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise AdaptiveError(f"{name} contains unsupported fields: {sorted(unknown)!r}")


def _opaque(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        raise AdaptiveError(f"{name} must be an opaque bounded identifier")
    return value


def _label(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise AdaptiveError(f"{name} must be a bounded label")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise AdaptiveError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _optional_digest(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _digest(value, name)


def _bounded_int(value: Any, name: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AdaptiveError(f"{name} must be a bounded integer")
    return value


def _finite_number(value: Any, name: str, *, minimum: float = 0.0, maximum: float = 1e12) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise AdaptiveError(f"{name} must be a finite bounded number")
    return value


def _reason(value: Any, name: str) -> str:
    result = _label(value, name)
    if _PRIVATE.search(result):
        raise AdaptiveError(f"{name} contains private content")
    return result


def _safe_text(value: Any, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise AdaptiveError(f"{name} must be short bounded text")
    if _PRIVATE.search(value) or any(ord(char) < 32 for char in value) or "/" in value or "\\" in value:
        raise AdaptiveError(f"{name} contains private content")
    return value


def _source_revision(value: Any, name: str) -> int:
    return _bounded_int(value, name, minimum=1, maximum=2**31 - 1)


def _route(value: Any, name: str) -> dict[str, str]:
    source = _mapping(value, name)
    _keys(source, {"profile", "model", "effort"}, name)
    profile = _label(source.get("profile"), f"{name}.profile")
    model = _label(source.get("model"), f"{name}.model")
    effort = source.get("effort")
    if effort not in EFFORTS:
        raise AdaptiveError(f"{name}.effort is invalid")
    if "astra" in model.lower():
        raise AdaptiveError("Astra is prohibited by the Phase 5 contract")
    return {"profile": profile, "model": model, "effort": effort}


def _route_equal(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("profile", "model", "effort"))


def normalize_context(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value, "context")
    allowed = {
        "schema_version", "requirements_digest", "task_family_digest", "risk_class",
        "complexity_class", "security_class", "artifact_type", "mutation_surface",
        "user_override", "selected_reports", "policy_version", "requested_check",
    }
    _keys(source, allowed, "context")
    if source.get("schema_version", CONTEXT_VERSION) != CONTEXT_VERSION:
        raise AdaptiveError("context schema_version is invalid")
    reports = source.get("selected_reports")
    if not isinstance(reports, list) or not 1 <= len(reports) <= MAX_REPORTS:
        raise AdaptiveError("context.selected_reports must be a bounded non-empty list")
    normalized_reports = []
    seen: set[str] = set()
    for index, item in enumerate(reports):
        item = _mapping(item, f"context.selected_reports[{index}]")
        _keys(item, {"report_id", "source_revision", "artifact_digests"}, f"context.selected_reports[{index}]")
        report_id = _opaque(item.get("report_id"), f"context.selected_reports[{index}].report_id")
        if report_id in seen:
            raise AdaptiveError("context.selected_reports contains duplicate report IDs")
        seen.add(report_id)
        artifacts = item.get("artifact_digests", [])
        if not isinstance(artifacts, list) or len(artifacts) > MAX_DIGESTS:
            raise AdaptiveError("context artifact digest list is too large")
        normalized_reports.append(
            {
                "report_id": report_id,
                "source_revision": _source_revision(item.get("source_revision"), f"context.selected_reports[{index}].source_revision"),
                "artifact_digests": [_digest(digest, f"context.selected_reports[{index}].artifact_digests") for digest in artifacts],
            }
        )
    override = source.get("user_override")
    normalized_override = None if override is None else _route(override, "context.user_override")
    return {
        "schema_version": CONTEXT_VERSION,
        "requirements_digest": _digest(source.get("requirements_digest"), "context.requirements_digest"),
        "task_family_digest": _optional_digest(source.get("task_family_digest"), "context.task_family_digest"),
        "risk_class": _label(source.get("risk_class"), "context.risk_class"),
        "complexity_class": _label(source.get("complexity_class"), "context.complexity_class"),
        "security_class": _label(source.get("security_class"), "context.security_class"),
        "artifact_type": _label(source.get("artifact_type"), "context.artifact_type"),
        "mutation_surface": _label(source.get("mutation_surface"), "context.mutation_surface"),
        "user_override": normalized_override,
        "selected_reports": normalized_reports,
        "policy_version": _label(source.get("policy_version"), "context.policy_version"),
        "requested_check": _safe_text(source.get("requested_check"), "context.requested_check"),
    }


def _phase4_validator() -> Any:
    path = Path(__file__).with_name("phase4_telemetry.py")
    spec = importlib.util.spec_from_file_location("phase4_telemetry_for_phase5", path)
    if spec is None or spec.loader is None:
        raise AdaptiveError("Phase 4 validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_phase4_aggregate(value: Any) -> dict[str, Any]:
    try:
        validator = _phase4_validator()
        return validator._validate_aggregate(value)
    except AdaptiveError:
        raise
    except Exception as exc:
        raise AdaptiveError(f"evidence aggregate is invalid: {exc}") from exc


def _all_available(availability: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(availability[kind]["status"] == "available" for kind in AVAILABILITY_KINDS)


def _availability_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    statuses = {"available", "unavailable", "unknown", "not_checked"}
    for kind in AVAILABILITY_KINDS:
        counts = {status: 0 for status in statuses}
        for row in rows:
            status = row["aggregate"]["availability"][kind]["status"]
            counts[status] += 1
        result[kind] = counts
    return result


def _quality_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    samples = []
    sources: set[str] = set()
    for row in rows:
        quality = row["aggregate"].get("quality")
        if not (_all_available(row["aggregate"]["availability"]) and quality and quality["status"] == "measured"):
            continue
        if quality["source"] not in QUALITY_SOURCES:
            continue
        score = quality.get("score")
        samples.append({"evidence_id": row["evidence_id"], "outcome": quality.get("outcome"), "score": score})
        sources.add(quality["source"])
    measured_scores = [sample["score"] for sample in samples if sample["score"] is not None]
    return {
        "status": "measured" if samples else "unavailable",
        "sources": sorted(sources),
        "sample_count": len(samples),
        "scored_sample_count": len(measured_scores),
        "mean_score": sum(measured_scores) / len(measured_scores) if measured_scores else None,
        "samples": samples,
    }


def _operational_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [
        row["aggregate"]
        for row in rows
        if _all_available(row["aggregate"]["availability"])
        and row["aggregate"]["lifecycle"]["terminal_status"] == "complete"
        and row["aggregate"]["lifecycle"]["audit_status"] == "complete"
    ]
    walls = [row["lifecycle"]["wall_seconds"] for row in usable if row["lifecycle"]["wall_seconds"] is not None]
    return {
        "usable_sample_count": len(usable),
        "wall_seconds": {"count": len(walls), "total": sum(walls) if walls else None},
        "response_count": sum(row["response_count"] or 0 for row in usable) if usable else None,
        "token_totals": {
            field: sum((row["tokens"] or {}).get(field, 0) for row in usable) if usable and all(row["tokens"] is not None for row in usable) else None
            for field in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
        },
    }


def build_evidence_bundle(records: Sequence[Mapping[str, Any]], *, manifest_version: str = "phase5-offline-v1") -> dict[str, Any]:
    """Validate reviewed Phase 4 rows and build a safe, bounded evidence bundle."""
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_EVIDENCE:
        raise AdaptiveError("evidence records must be a bounded non-empty list")
    normalized = []
    seen: set[str] = set()
    for index, item in enumerate(records):
        item = _mapping(item, f"evidence[{index}]")
        _keys(item, {"evidence_id", "report_id", "source_revision", "artifact_digests", "aggregate", "stale", "contaminated"}, f"evidence[{index}]")
        evidence_id = _opaque(item.get("evidence_id", item.get("report_id")), f"evidence[{index}].evidence_id")
        report_id = _opaque(item.get("report_id", evidence_id), f"evidence[{index}].report_id")
        if evidence_id in seen:
            raise AdaptiveError("evidence contains duplicate evidence IDs")
        seen.add(evidence_id)
        artifacts = item.get("artifact_digests", [])
        if not isinstance(artifacts, list) or len(artifacts) > MAX_DIGESTS:
            raise AdaptiveError("evidence artifact digest list is too large")
        aggregate = _validate_phase4_aggregate(item.get("aggregate"))
        normalized.append(
            {
                "evidence_id": evidence_id,
                "report_id": report_id,
                "source_revision": _source_revision(item.get("source_revision"), f"evidence[{index}].source_revision"),
                "artifact_digests": [_digest(digest, f"evidence[{index}].artifact_digests") for digest in artifacts],
                "aggregate": aggregate,
                "stale": bool(item.get("stale", False)) if type(item.get("stale", False)) is bool else (_raise("stale must be boolean")),
                "contaminated": bool(item.get("contaminated", False)) if type(item.get("contaminated", False)) is bool else (_raise("contaminated must be boolean")),
            }
        )
    payload_digests = {row["aggregate"]["digests"]["payload"] for row in normalized if row["aggregate"]["digests"]["payload"]}
    config_digests = {row["aggregate"]["digests"]["config"] for row in normalized if row["aggregate"]["digests"]["config"]}
    dependency_digests = {row["aggregate"]["digests"]["dependency"] for row in normalized if row["aggregate"]["digests"]["dependency"]}
    tool_digests = {row["aggregate"]["digests"]["tool_catalogue"] for row in normalized if row["aggregate"]["digests"]["tool_catalogue"]}
    contradictory = any(len(values) > 1 for values in (payload_digests, config_digests, dependency_digests, tool_digests))
    stale = any(row["stale"] for row in normalized)
    contaminated = any(row["contaminated"] for row in normalized)
    quality = _quality_summary(normalized)
    complete_rows = [
        row for row in normalized
        if _all_available(row["aggregate"]["availability"])
        and row["aggregate"]["lifecycle"]["terminal_status"] == "complete"
        and row["aggregate"]["lifecycle"]["audit_status"] == "complete"
        and row["aggregate"].get("quality", {}).get("status") == "measured"
    ]
    complete = bool(complete_rows) and not stale and not contaminated and not contradictory
    unavailable = any(not _all_available(row["aggregate"]["availability"]) for row in normalized)
    bundle = {
        "schema_version": BUNDLE_VERSION,
        "manifest_version": _label(manifest_version, "manifest_version"),
        "evidence": [
            {
                "evidence_id": row["evidence_id"],
                "report_id": row["report_id"],
                "source_revision": row["source_revision"],
                "artifact_digests": row["artifact_digests"],
                "aggregate": row["aggregate"],
                "stale": row["stale"],
                "contaminated": row["contaminated"],
            }
            for row in normalized
        ],
        "completeness": "complete" if complete else ("unavailable" if unavailable else "partial"),
        "sample_count": quality["sample_count"],
        "quality": quality,
        "operational": _operational_summary(normalized),
        "availability": _availability_summary(normalized),
        "flags": {"stale": stale, "contradictory": contradictory, "contaminated": contaminated},
        "excluded_evidence": [
            {
                "evidence_id": row["evidence_id"],
                "reason": "availability-excluded" if not _all_available(row["aggregate"]["availability"]) else "quality-unavailable",
            }
            for row in normalized
            if row not in complete_rows
        ],
    }
    _ensure_json_size(bundle, "evidence bundle")
    return bundle


def _raise(message: str) -> Any:
    raise AdaptiveError(message)


def normalize_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value, "manifest")
    _keys(source, {"schema_version", "version", "baseline", "candidates", "min_samples", "min_score", "required_digests"}, "manifest")
    if source.get("schema_version", MANIFEST_VERSION) != MANIFEST_VERSION:
        raise AdaptiveError("manifest schema_version is invalid")
    candidates = source.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 64:
        raise AdaptiveError("manifest candidates must be bounded")
    normalized_candidates = [_route(item, f"manifest.candidates[{index}]") for index, item in enumerate(candidates)]
    if len({tuple(route.values()) for route in normalized_candidates}) != len(normalized_candidates):
        raise AdaptiveError("manifest contains duplicate candidate routes")
    min_score = source.get("min_score")
    if min_score is not None:
        min_score = _finite_number(min_score, "manifest.min_score", maximum=1)
    required_digests = source.get("required_digests", {})
    required_digests = _mapping(required_digests, "manifest.required_digests")
    _keys(required_digests, {"payload", "config", "dependency", "tool_catalogue"}, "manifest.required_digests")
    return {
        "schema_version": MANIFEST_VERSION,
        "version": _label(source.get("version"), "manifest.version"),
        "baseline": _route(source.get("baseline"), "manifest.baseline"),
        "candidates": normalized_candidates,
        "min_samples": _bounded_int(source.get("min_samples", 1), "manifest.min_samples", minimum=1, maximum=MAX_EVIDENCE),
        "min_score": min_score,
        "required_digests": {key: _digest(value, f"manifest.required_digests.{key}") for key, value in required_digests.items()},
    }


def normalize_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value, "bundle")
    required = {"schema_version", "manifest_version", "evidence", "completeness", "sample_count", "quality", "operational", "availability", "flags", "excluded_evidence"}
    _keys(source, required, "bundle")
    if source.get("schema_version") != BUNDLE_VERSION:
        raise AdaptiveError("bundle schema_version is invalid")
    evidence = source.get("evidence")
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise AdaptiveError("bundle evidence must be bounded")
    # Rebuild through the source validator so callers cannot forge a schema-shaped bundle.
    records = []
    for index, item in enumerate(evidence):
        item = _mapping(item, f"bundle.evidence[{index}]")
        _keys(item, {"evidence_id", "report_id", "source_revision", "artifact_digests", "aggregate", "stale", "contaminated"}, f"bundle.evidence[{index}]")
        records.append(item)
    rebuilt = build_evidence_bundle(records, manifest_version=source["manifest_version"])
    if rebuilt["completeness"] != source["completeness"] or rebuilt["sample_count"] != source["sample_count"] or rebuilt["flags"] != source["flags"]:
        raise AdaptiveError("bundle summary does not match its evidence")
    return rebuilt


def normalize_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value, "proposal")
    _keys(source, {"route", "profile", "model", "effort", "rationale", "evidence_ids", "expected_risk", "expected_overhead", "confidence", "unknown_reasons", "discriminating_check"}, "proposal")
    route = source.get("route")
    if route not in {"baseline", "candidate"}:
        raise AdaptiveError("proposal.route must be baseline or candidate")
    evidence_ids = source.get("evidence_ids", [])
    if not isinstance(evidence_ids, list) or len(evidence_ids) > MAX_EVIDENCE:
        raise AdaptiveError("proposal.evidence_ids must be bounded")
    expected_overhead = source.get("expected_overhead")
    expected_overhead = _mapping(expected_overhead, "proposal.expected_overhead")
    _keys(expected_overhead, {"wall_ratio", "token_ratio", "consultation_count"}, "proposal.expected_overhead")
    unknown_reasons = source.get("unknown_reasons", [])
    if not isinstance(unknown_reasons, list) or len(unknown_reasons) > MAX_REASONS:
        raise AdaptiveError("proposal.unknown_reasons must be a bounded list")
    return {
        "route": route,
        "selected": _route({"profile": source.get("profile"), "model": source.get("model"), "effort": source.get("effort")}, "proposal"),
        "rationale": _safe_text(source.get("rationale"), "proposal.rationale"),
        "evidence_ids": [_opaque(item, "proposal.evidence_ids") for item in evidence_ids],
        "expected_risk": _label(source.get("expected_risk"), "proposal.expected_risk"),
        "expected_overhead": {
            key: (_finite_number(value, f"proposal.expected_overhead.{key}", maximum=1e6) if key != "consultation_count" else _bounded_int(value, f"proposal.expected_overhead.{key}", maximum=2**31 - 1))
            for key, value in expected_overhead.items()
        },
        "confidence": None if source.get("confidence") is None else _finite_number(source.get("confidence"), "proposal.confidence", maximum=1),
        "unknown_reasons": [_reason(item, "proposal.unknown_reasons") for item in unknown_reasons],
        "discriminating_check": _safe_text(source.get("discriminating_check"), "proposal.discriminating_check"),
    }


def _fallback(manifest: Mapping[str, Any], reason: str, *, evidence_ids: Sequence[str] = (), proposal: Mapping[str, Any] | None = None, override: bool = False) -> dict[str, Any]:
    selected = dict(manifest["baseline"])
    result = {
        "schema_version": RECOMMENDATION_VERSION,
        "route": "baseline",
        "selected": selected,
        "rationale": "Static baseline retained pending acceptable evidence.",
        "evidence_ids": list(evidence_ids),
        "expected_risk": "unknown",
        "expected_overhead": {"wall_ratio": None, "token_ratio": None, "consultation_count": None},
        "confidence": None,
        "unknown_reasons": [reason],
        "discriminating_check": "verify-baseline-acceptance",
        "fallback_reason": reason,
        "override_applied": override,
    }
    if proposal is not None:
        result["rationale"] = proposal["rationale"]
        result["expected_risk"] = proposal["expected_risk"]
        result["expected_overhead"] = proposal["expected_overhead"]
        result["confidence"] = proposal["confidence"]
        result["discriminating_check"] = proposal["discriminating_check"]
    return _finalize_recommendation(result)


def _finalize_recommendation(value: dict[str, Any]) -> dict[str, Any]:
    """Apply the final recommendation shape and privacy-size boundary."""
    reasons = value.get("unknown_reasons")
    if not isinstance(reasons, list) or len(reasons) > MAX_REASONS:
        raise AdaptiveError("recommendation.unknown_reasons must be a bounded list")
    for reason in reasons:
        _reason(reason, "recommendation.unknown_reasons")
    _ensure_json_size(value, "strategy recommendation")
    return value


def _selected_report_joined(context: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    """Require every evidence row to match selected report identity metadata."""
    selected = {
        (
            item["report_id"],
            item["source_revision"],
            tuple(sorted(item["artifact_digests"])),
        )
        for item in context["selected_reports"]
    }
    return all(
        (
            row["report_id"],
            row["source_revision"],
            tuple(sorted(row["artifact_digests"])),
        )
        in selected
        for row in evidence["evidence"]
    )


def recommend_strategy(context: Mapping[str, Any], evidence: Mapping[str, Any], manifest: Mapping[str, Any], proposal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate a model proposal without selecting a route on the model's behalf."""
    context = normalize_context(context)
    evidence = normalize_bundle(evidence)
    manifest = normalize_manifest(manifest)
    evidence_ids = {item["evidence_id"] for item in evidence["evidence"]}
    if evidence["manifest_version"] != manifest["version"]:
        return _fallback(manifest, "manifest-version-mismatch", evidence_ids=sorted(evidence_ids))
    if context["policy_version"] != manifest["version"]:
        return _fallback(manifest, "policy-version-mismatch", evidence_ids=sorted(evidence_ids))
    if context["user_override"] is not None:
        override = context["user_override"]
        override_route = "baseline" if _route_equal(override, manifest["baseline"]) else "candidate"
        return _finalize_recommendation({
            "schema_version": RECOMMENDATION_VERSION,
            "route": override_route,
            "selected": override,
            "rationale": "Explicit user route preserved.",
            "evidence_ids": [],
            "expected_risk": "unknown",
            "expected_overhead": {"wall_ratio": None, "token_ratio": None, "consultation_count": None},
            "confidence": None,
            "unknown_reasons": ["user-override-preserved"],
            "discriminating_check": "verify-user-route-acceptance",
            "fallback_reason": "user-override-preserved",
            "override_applied": True,
        })
    if proposal is None:
        return _fallback(manifest, "recommendation-not-provided", evidence_ids=sorted(evidence_ids))
    try:
        proposal = normalize_proposal(proposal)
    except AdaptiveError:
        return _fallback(manifest, "proposal-invalid")
    if not set(proposal["evidence_ids"]).issubset(evidence_ids):
        return _fallback(manifest, "unknown-evidence-reference", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if proposal["route"] == "candidate" and not proposal["evidence_ids"]:
        return _fallback(manifest, "candidate-evidence-required", proposal=proposal)
    if proposal["route"] == "baseline":
        return _finalize_recommendation({
            "schema_version": RECOMMENDATION_VERSION,
            "route": "baseline",
            "selected": manifest["baseline"],
            "rationale": proposal["rationale"],
            "evidence_ids": proposal["evidence_ids"],
            "expected_risk": proposal["expected_risk"],
            "expected_overhead": proposal["expected_overhead"],
            "confidence": proposal["confidence"],
            "unknown_reasons": proposal["unknown_reasons"] or ["model-selected-baseline"],
            "discriminating_check": proposal["discriminating_check"],
            "fallback_reason": "model-selected-baseline",
            "override_applied": False,
        })
    if not any(_route_equal(proposal["selected"], candidate) for candidate in manifest["candidates"]):
        return _fallback(manifest, "candidate-not-in-manifest", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if not _selected_report_joined(context, evidence):
        return _fallback(manifest, "selected-report-join-mismatch", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if evidence["completeness"] != "complete":
        reason = "availability-excluded" if evidence["completeness"] == "unavailable" else "evidence-incomplete"
        return _fallback(manifest, reason, evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if evidence["flags"]["stale"]:
        return _fallback(manifest, "evidence-stale", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if evidence["flags"]["contradictory"]:
        return _fallback(manifest, "evidence-contradictory", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if evidence["flags"]["contaminated"]:
        return _fallback(manifest, "evidence-contaminated", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    if evidence["sample_count"] < manifest["min_samples"]:
        return _fallback(manifest, "evidence-low-sample", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    minimum_score = manifest["min_score"]
    if minimum_score is not None and (evidence["quality"]["mean_score"] is None or evidence["quality"]["mean_score"] < minimum_score):
        return _fallback(manifest, "quality-threshold-unmet", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    digest_map = {key: {row["aggregate"]["digests"].get(key) for row in evidence["evidence"]} for key in ("payload", "config", "dependency", "tool_catalogue")}
    for key, expected in manifest["required_digests"].items():
        if expected not in digest_map[key]:
            return _fallback(manifest, "required-digest-mismatch", evidence_ids=proposal["evidence_ids"], proposal=proposal)
    return _finalize_recommendation({
        "schema_version": RECOMMENDATION_VERSION,
        "route": "candidate",
        "selected": proposal["selected"],
        "rationale": proposal["rationale"],
        "evidence_ids": proposal["evidence_ids"],
        "expected_risk": proposal["expected_risk"],
        "expected_overhead": proposal["expected_overhead"],
        "confidence": proposal["confidence"],
        "unknown_reasons": proposal["unknown_reasons"],
        "discriminating_check": proposal["discriminating_check"],
        "fallback_reason": None,
        "override_applied": False,
    })


def make_decision_record(context: Mapping[str, Any], evidence: Mapping[str, Any], recommendation: Mapping[str, Any], selected: Mapping[str, Any] | None = None, *, decision_id: str = "decision-offline", rollback_reason: str | None = None) -> dict[str, Any]:
    """Create a coordinator-owned decision record; this function performs no write."""
    context = normalize_context(context)
    evidence = normalize_bundle(evidence)
    recommendation = _mapping(recommendation, "recommendation")
    selected_route = _route(selected if selected is not None else recommendation.get("selected"), "selected")
    if context["user_override"] is not None and not _route_equal(selected_route, context["user_override"]):
        raise AdaptiveError("decision must preserve the explicit user override")
    if rollback_reason is not None:
        rollback_reason = _reason(rollback_reason, "rollback_reason")
    evidence_ids = recommendation.get("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        raise AdaptiveError("recommendation.evidence_ids must be a list")
    known = {item["evidence_id"] for item in evidence["evidence"]}
    if not set(evidence_ids).issubset(known):
        raise AdaptiveError("decision contains unknown evidence references")
    result = {
        "schema_version": DECISION_VERSION,
        "decision_id": _opaque(decision_id, "decision_id"),
        "policy_version": context["policy_version"],
        "selected": selected_route,
        "recommendation_route": recommendation.get("route"),
        "user_override": context["user_override"],
        "rationale": _safe_text(recommendation.get("rationale"), "decision.rationale"),
        "evidence_ids": [_opaque(item, "decision.evidence_ids") for item in evidence_ids],
        "fallback_reason": recommendation.get("fallback_reason"),
        "rollback_reason": rollback_reason,
        "planned_check": _safe_text(recommendation.get("discriminating_check"), "decision.planned_check"),
    }
    _ensure_json_size(result, "decision record")
    return result


def rollback_to_baseline(context: Mapping[str, Any], evidence: Mapping[str, Any], recommendation: Mapping[str, Any], manifest: Mapping[str, Any], *, reason: str, decision_id: str = "decision-rollback") -> dict[str, Any]:
    """Return an explicit coordinator rollback record; no automatic mutation occurs."""
    manifest = normalize_manifest(manifest)
    reason = _reason(reason, "rollback reason")
    return make_decision_record(context, evidence, recommendation, manifest["baseline"], decision_id=decision_id, rollback_reason=reason)


def _ensure_json_size(value: Mapping[str, Any], name: str) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise AdaptiveError(f"{name} is not JSON-safe") from exc
    if len(encoded) > MAX_RECORD_BYTES:
        raise AdaptiveError(f"{name} exceeds the privacy size bound")


aggregate_evidence = build_evidence_bundle
evaluate_recommendation = recommend_strategy
record_decision = make_decision_record


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON object containing context, evidence_records, manifest, and optional proposal")
    args = parser.parse_args()
    payload = _mapping(json.loads(args.input.read_text(encoding="utf-8")), "input")
    _keys(payload, {"context", "evidence_records", "manifest", "proposal"}, "input")
    bundle = build_evidence_bundle(payload["evidence_records"], manifest_version=normalize_manifest(payload["manifest"])["version"])
    recommendation = recommend_strategy(payload["context"], bundle, payload["manifest"], payload.get("proposal"))
    print(json.dumps({"evidence": bundle, "recommendation": recommendation}, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
