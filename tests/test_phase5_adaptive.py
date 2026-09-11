import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phase5_adaptive", ROOT / "scripts/phase5_adaptive.py")
PHASE5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PHASE5)
PHASE4 = PHASE5._phase4_validator()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def aggregate(**updates):
    usage = {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "cache_write_input_tokens": 1,
        "output_tokens": 4,
        "reasoning_output_tokens": 2,
        "total_tokens": 14,
    }
    value = {
        "run_id": "run-1",
        "pair_id": "pair-1",
        "suite_id": "suite-1",
        "phase": "phase4",
        "strategy_version": "static-v1",
        "host": "offline",
        "payload_digest": digest("payload"),
        "config_digest": digest("config"),
        "dependency_digest": digest("dependency"),
        "tool_catalogue_digest": digest("tools"),
        "task_family_digest": digest("family"),
        "fixture_digest": digest("fixture"),
        "oracle_digest": digest("oracle"),
        "lifecycle": {
            "start_at": "2026-09-10T10:00:00Z",
            "finish_at": "2026-09-10T10:00:01Z",
            "wall_seconds": 1,
            "wall_source": "reviewed",
            "terminal_status": "complete",
            "audit_status": "complete",
            "open_sessions": 0,
        },
        "participants": [{
            "role": "worker",
            "model": "gpt-5.6-luna",
            "effort": "medium",
            "responses": [{"response_id": "response-1", "usage": usage}],
        }],
        "counts": {},
        "availability": {kind: {"status": "available", "reason": None} for kind in PHASE5.AVAILABILITY_KINDS},
        "quality": {"source": "independent_oracle", "status": "measured", "outcome": "pass", "score": 0.9},
        "errors": {},
    }
    value.update(updates)
    return value


def context(**updates):
    value = {
        "requirements_digest": digest("requirements"),
        "task_family_digest": digest("family"),
        "risk_class": "ordinary",
        "complexity_class": "bounded",
        "security_class": "normal",
        "artifact_type": "code",
        "mutation_surface": "workspace",
        "selected_reports": [{"report_id": "report-1", "source_revision": 33, "artifact_digests": []}],
        "policy_version": "phase5-test-v1",
        "requested_check": "verify route",
        "user_override": None,
    }
    value.update(updates)
    return value


def manifest(**updates):
    value = {
        "version": "phase5-test-v1",
        "baseline": {"profile": "worker-general", "model": "gpt-5.6-luna", "effort": "medium"},
        "candidates": [{"profile": "backend-dev", "model": "gpt-5.6-luna", "effort": "high"}],
        "min_samples": 1,
        "min_score": 0.8,
        "required_digests": {},
    }
    value.update(updates)
    return value


def proposal(**updates):
    value = {
        "route": "candidate",
        "profile": "backend-dev",
        "model": "gpt-5.6-luna",
        "effort": "high",
        "rationale": "Independent score supports this bounded route.",
        "evidence_ids": ["evidence-1"],
        "expected_risk": "low",
        "expected_overhead": {"wall_ratio": 1.1, "token_ratio": 1.2, "consultation_count": 0},
        "confidence": 0.8,
        "unknown_reasons": [],
        "discriminating_check": "verify candidate acceptance",
    }
    value.update(updates)
    return value


def bundle(**aggregate_updates):
    return PHASE5.build_evidence_bundle(
        [{"evidence_id": "evidence-1", "report_id": "report-1", "source_revision": 33, "artifact_digests": [], "aggregate": PHASE4.aggregate_run(aggregate(**aggregate_updates))}],
        manifest_version="phase5-test-v1",
    )


def test_candidate_is_advisory_and_accepted_only_with_complete_independent_evidence():
    result = PHASE5.recommend_strategy(context(), bundle(), manifest(), proposal())
    assert result["route"] == "candidate"
    assert result["selected"]["profile"] == "backend-dev"
    assert result["fallback_reason"] is None


def test_availability_is_an_exclusion_not_a_quality_signal():
    value = aggregate()
    value["availability"]["gateway_provider"] = {"status": "unavailable", "reason": "service-unavailable"}
    evidence = PHASE5.build_evidence_bundle(
        [{"evidence_id": "evidence-1", "report_id": "report-1", "source_revision": 33, "artifact_digests": [], "aggregate": PHASE4.aggregate_run(value)}],
        manifest_version="phase5-test-v1",
    )
    assert evidence["quality"]["sample_count"] == 0
    assert evidence["completeness"] == "unavailable"
    result = PHASE5.recommend_strategy(context(), evidence, manifest(), proposal())
    assert result["route"] == "baseline"
    assert result["fallback_reason"] == "availability-excluded"


def test_stale_evidence_falls_back_without_ranking_on_it():
    evidence = PHASE5.build_evidence_bundle(
        [{"evidence_id": "evidence-1", "report_id": "report-1", "source_revision": 33, "artifact_digests": [], "stale": True, "aggregate": PHASE4.aggregate_run(aggregate())}],
        manifest_version="phase5-test-v1",
    )
    result = PHASE5.recommend_strategy(context(), evidence, manifest(), proposal())
    assert result["fallback_reason"] == "evidence-incomplete"


def test_user_override_survives_recommendation_and_decision():
    override = {"profile": "worker-general", "model": "gpt-5.6-luna", "effort": "high"}
    ctx = context(user_override=override)
    evidence = bundle()
    result = PHASE5.recommend_strategy(ctx, evidence, manifest(), proposal())
    assert result["override_applied"] is True
    decision = PHASE5.make_decision_record(ctx, evidence, result)
    assert decision["selected"] == override
    assert decision["user_override"] == override


def test_unknown_and_private_fields_fail_closed():
    value = PHASE4.aggregate_run(aggregate())
    value["prompt"] = "private"
    with pytest.raises(PHASE5.AdaptiveError):
        PHASE5.build_evidence_bundle([{"evidence_id": "evidence-1", "report_id": "report-1", "source_revision": 33, "aggregate": value}], manifest_version="phase5-test-v1")
    result = PHASE5.recommend_strategy(context(), bundle(), manifest(), proposal(rationale="prompt private details"))
    assert result["route"] == "baseline"
    assert result["fallback_reason"] == "proposal-invalid"


def test_rollback_is_explicit_and_returns_the_static_baseline():
    evidence = bundle()
    recommendation = PHASE5.recommend_strategy(context(), evidence, manifest(), proposal())
    record = PHASE5.rollback_to_baseline(context(), evidence, recommendation, manifest(), reason="quality-regression")
    assert record["selected"] == manifest()["baseline"]
    assert record["rollback_reason"] == "quality-regression"


def test_astra_routes_are_rejected():
    with pytest.raises(PHASE5.AdaptiveError):
        PHASE5.normalize_manifest({**manifest(), "candidates": [{"profile": "backend-dev", "model": "gpt-6-astra", "effort": "high"}]})


def test_selected_report_join_mismatch_falls_back():
    result = PHASE5.recommend_strategy(
        context(selected_reports=[{"report_id": "report-1", "source_revision": 32, "artifact_digests": []}]),
        bundle(),
        manifest(),
        proposal(),
    )
    assert result["route"] == "baseline"
    assert result["fallback_reason"] == "selected-report-join-mismatch"


def test_unknown_reasons_are_strictly_bounded_and_invalid_proposal_falls_back():
    invalid = proposal(unknown_reasons=["reason"] * (PHASE5.MAX_REASONS + 1))
    result = PHASE5.recommend_strategy(context(), bundle(), manifest(), invalid)
    assert result["route"] == "baseline"
    assert result["fallback_reason"] == "proposal-invalid"
    assert result["unknown_reasons"] == ["proposal-invalid"]


def test_invalid_proposal_shape_is_typed_baseline_fallback():
    result = PHASE5.recommend_strategy(context(), bundle(), manifest(), {"route": "candidate"})
    assert result["route"] == "baseline"
    assert result["selected"] == manifest()["baseline"]
    assert result["fallback_reason"] == "proposal-invalid"


def test_candidate_requires_explicit_evidence_references():
    result = PHASE5.recommend_strategy(context(), bundle(), manifest(), proposal(evidence_ids=[]))
    assert result["route"] == "baseline"
    assert result["selected"] == manifest()["baseline"]
    assert result["fallback_reason"] == "candidate-evidence-required"
