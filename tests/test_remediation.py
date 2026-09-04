"""Conditional repair/regression evidence on the real transactional graph."""
import json
import pytest

from cortex_runtime import graph_ledger as ledger
from cortex_runtime.remediation import chains, reviews, strategy_reviews
from test_graph_ledger import case, implement, claim, publication, states, observation


def test_confirmed_audit_defect_generates_one_repair_and_independent_regression(case):
    implement(case)
    audit = claim(case, ["architecture"], "audit")
    result = publication(case, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)
    store, _, graph_id = case
    chain = store._read(lambda c: chains(c, graph_id))[0]
    assert states(case)[chain["repair"]] == "ready"
    assert states(case)[chain["regression"]] == "waiting"
    assert states(case)["architecture"] == "failed"
    assert publication(case, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)["replayed"]
    assert len(store._read(lambda c: chains(c, graph_id))) == 1
    repair = claim(case, [chain["repair"]], "repair")
    assert repair["predecessor_reports"] == [result["report_id"]]
    publication(case, "repair", repair["nodes"], start="d" * 64, end="e" * 64)
    assert states(case)["architecture"] == "failed"
    assert states(case)[chain["regression"]] == "ready"
    regression = claim(case, [chain["regression"]], "regression")
    assert {check["key"] for check in regression["nodes"][0]["checks"]} == {"tests", "regression"}
    assert set(regression["predecessor_reports"]) == {"report-audit", "report-repair"}
    publication(case, "regression", regression["nodes"], start="e" * 64, end="e" * 64)
    assert states(case)["architecture"] == "resolved"
    original = store._read(lambda c: c.execute("SELECT state,facts_json FROM execution_nodes WHERE graph_id=? AND node_key='architecture'", (graph_id,)).fetchone())
    assert original[0] == "failed"
    assert any(fact["state"] == "failed" for fact in json.loads(original[1]))


@pytest.mark.parametrize("decision", ["validated", "unavailable", "disagreement", "unchanged"])
def test_strategy_switch_requires_bounded_causal_diagnosis_and_independent_validation(case, decision):
    from test_execution_graph_integrity import graph, check
    store, task, _ = case
    value = graph()
    policy = value["nodes"][3]["remediation"]
    policy.update(generation_budget=1, strategy_budget=2)
    policy["strategies"].append({"key": "alternate", "work": ["Use the preauthorized alternate repair approach."],
                                 "diagnostic_checks": [check("alternative-cause")]})
    candidate = store._write(lambda c: ledger.create_candidate(c, task_id=task, revision=1,
        plan_report_id="strategy-plan", planner_assignment_id="strategy-planner", graph=value,
        outcomes=["Product"], review_required=False))
    scoped = store, task, candidate
    implement(scoped)
    audit = claim(scoped, ["architecture"], "audit")
    publication(scoped, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)
    initial = store._read(lambda c: chains(c, candidate))[0]
    repair = claim(scoped, [initial["repair"]], "repair")
    fingerprint = "d" if decision == "unchanged" else "e"
    publication(scoped, "repair", repair["nodes"], start="d" * 64, end=fingerprint * 64)
    regression = claim(scoped, [initial["regression"]], "regression")
    publication(scoped, "regression", regression["nodes"], start=fingerprint * 64, end=fingerprint * 64, fail=True)
    assert len(store._read(lambda c: chains(c, candidate))) == 1
    diagnostic = store._read(lambda c: strategy_reviews(c, candidate))[-1]
    assert states(scoped)[diagnostic["node"]] == "ready"

    def assess(review, owner, selected):
        assigned = claim(scoped, [review["node"]], owner)
        assert "report-regression" in assigned["predecessor_reports"]
        rows = [{**subject, "status": "complete", "verification": [
            {"check_key": check["key"], "state": "executed", "summary": "Causal diagnostic observation",
             **({"strategy_assessment": selected} if "strategy_options" in check else {})}
            for check in assigned["nodes"][0]["checks"]]} for subject in assigned["nodes"][0]["verifies"]]
        result = store._write(lambda c: ledger.publish_nodes(c, assignment_id=owner, report_id="report-" + owner,
            terminal_kind="result", node_coverage=[{"node": review["node"], "coverage": rows}],
            artifact=observation(fingerprint * 64, fingerprint * 64)))
        assert not result["replayed"]

    assess(diagnostic, "diagnostic", "unavailable" if decision == "unavailable" else "alternate")
    if decision == "unavailable":
        assert states(scoped)["architecture"] == "exhausted"
        assert len(store._read(lambda c: strategy_reviews(c, candidate))) == 1
        return
    validation = store._read(lambda c: strategy_reviews(c, candidate))[-1]
    assert validation["phase"] == "validation"
    assert len(store._read(lambda c: chains(c, candidate))) == 1
    assess(validation, "validation", "unavailable" if decision == "disagreement" else "alternate")
    if decision in {"disagreement", "unchanged"}:
        assert states(scoped)["architecture"] == "exhausted"
        assert len(store._read(lambda c: chains(c, candidate))) == 1
        return
    next_chain = store._read(lambda c: chains(c, candidate))[-1]
    assert next_chain["strategy"] == 2 and next_chain["generation"] == 1
    assert next_chain["strategy_key"] == "alternate"
    assert next_chain["predecessor_regression"] == initial["regression"]
    repair = claim(scoped, [next_chain["repair"]], "alternate-repair")
    assert {"report-regression", "report-diagnostic", "report-validation", "report-audit"}.issubset(repair["predecessor_reports"])
    assert policy["strategies"][1]["work"][0] in repair["nodes"][0]["work"]
    publication(scoped, "alternate-repair", repair["nodes"], start="e" * 64, end="f" * 64)
    regression = claim(scoped, [next_chain["regression"]], "alternate-regression")
    publication(scoped, "alternate-regression", regression["nodes"], start="f" * 64, end="f" * 64, fail=True)
    assert states(scoped)["architecture"] == "exhausted"
    assert len(store._read(lambda c: chains(c, candidate))) == 2
    assert len(store._read(lambda c: strategy_reviews(c, candidate))) == 2


@pytest.mark.parametrize("progress", [True, False])
def test_regression_retry_requires_artifact_change_and_resolved_finding(case, progress):
    from test_execution_graph_integrity import graph, check
    store, task, _ = case
    value = graph()
    source = value["nodes"][3]
    source["checks"] = [check("first"), check("second")]
    source["remediation"]["regression_checks"] = source["checks"]
    candidate = store._write(lambda c: ledger.create_candidate(c, task_id=task, revision=1,
        plan_report_id="progress-plan", planner_assignment_id="progress-planner", graph=value,
        outcomes=["Product"], review_required=False))
    scoped = store, task, candidate
    implement(scoped)
    audit = claim(scoped, ["architecture"], "audit")
    publication(scoped, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)
    initial = store._read(lambda c: chains(c, candidate))[0]
    repair = claim(scoped, [initial["repair"]], "repair")
    publication(scoped, "repair", repair["nodes"], start="d" * 64, end="e" * 64)
    regression = claim(scoped, [initial["regression"]], "regression")
    facts = [{"check_key": "first", "state": "executed", "summary": "First finding resolved"} if progress else
        {"check_key": "first", "state": "failed", "summary": "Different prose is not progress", "classification": "defect_within_contract"},
        {"check_key": "second", "state": "failed", "summary": "Second finding remains", "classification": "defect_within_contract"}]
    coverage = [{"node": initial["regression"], "coverage": [{"kind": "outcome", "name": "Product", "status": "failed", "verification": facts}]}]
    store._write(lambda c: ledger.publish_nodes(c, assignment_id="regression", report_id="failed-regression",
        terminal_kind="result", node_coverage=coverage, artifact=observation("e" * 64, "e" * 64)))
    history = store._read(lambda c: chains(c, candidate))
    if not progress:
        assert len(history) == 1
        assert states(scoped)["architecture"] == "exhausted"
        return
    assert len(history) == 2 and history[-1]["generation"] == 2
    assert history[-1]["predecessor_regression"] == initial["regression"]
    next_repair = claim(scoped, [history[-1]["repair"]], "next-repair")
    assert set(next_repair["predecessor_reports"]) == {"report-audit", "failed-regression"}
    publication(scoped, "next-repair", next_repair["nodes"], start="e" * 64, end="f" * 64)
    next_regression = claim(scoped, [history[-1]["regression"]], "next-regression")
    publication(scoped, "next-regression", next_regression["nodes"], start="f" * 64, end="f" * 64)
    assert states(scoped)["architecture"] == "resolved"
    assert states(scoped)[initial["regression"]] == "resolved"
    observed = store._read(lambda c: c.execute("SELECT state FROM execution_nodes WHERE graph_id=? AND node_key=?", (candidate, initial["regression"])).fetchone()[0])
    assert observed == "failed"


@pytest.mark.parametrize("repaired", [True, False])
def test_failed_repair_attempt_requires_independent_observation_not_a_dead_end(case, repaired):
    implement(case)
    store, _, graph_id = case
    audit = claim(case, ["architecture"], "audit")
    publication(case, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)
    chain = store._read(lambda c: chains(c, graph_id))[0]
    repair = claim(case, [chain["repair"]], "repair")
    assert states(case)[chain["regression"]] == "waiting"
    publication(case, "repair", repair["nodes"], start="d" * 64, end="e" * 64, fail=True)
    assert states(case)["architecture"] == "failed"
    assert states(case)[chain["regression"]] == "ready"
    regression = claim(case, [chain["regression"]], "regression")
    assert "report-repair" in regression["predecessor_reports"]
    publication(case, "regression", regression["nodes"], start="e" * 64, end="e" * 64, fail=not repaired)
    assert states(case)["architecture"] == ("resolved" if repaired else "exhausted")
    if repaired:
        assert states(case)[chain["repair"]] == "resolved"
    assert store._read(lambda c: c.execute("SELECT state FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, chain["repair"])).fetchone()[0]) == "failed"


@pytest.mark.parametrize("assessment", ["defect_within_contract", "contract_change_required", "inconclusive"])
def test_required_classification_confirmation_does_not_silently_authorize_repair(case, assessment):
    # Install this distinct prevalidated policy before candidate activation.
    from test_execution_graph_integrity import graph
    store, task, _ = case
    value = graph()
    value["budgets"]["additional_evidence"] = 4
    value["nodes"][3]["remediation"]["classification_verification"] = True
    candidate = store._write(lambda c: ledger.create_candidate(c, task_id=task, revision=1,
        plan_report_id="high-risk-plan", planner_assignment_id="another-planner", graph=value,
        outcomes=["Product"], review_required=False))
    scoped = store, task, candidate
    implement(scoped)
    audit = claim(scoped, ["architecture"], "audit")
    publication(scoped, "audit", audit["nodes"], start="d" * 64, end="d" * 64, fail=True)
    assert store._read(lambda c: chains(c, candidate)) == []
    assert states(scoped)["architecture"] == "failed"
    review = store._read(lambda c: reviews(c, candidate))[0]
    assert states(scoped)[review["node"]] == "ready"
    classifier = claim(scoped, [review["node"]], "classifier")
    assert classifier["predecessor_reports"] == ["report-audit"]
    coverage = [{"node": review["node"], "coverage": [{**subject, "status": "complete",
        "verification": [{"check_key": check["key"], "state": "executed", "summary": "Independent source finding classification", "classification_assessment": assessment}
            for check in classifier["nodes"][0]["checks"]]} for subject in classifier["nodes"][0]["verifies"]]}]
    store._write(lambda c: ledger.publish_nodes(c, assignment_id="classifier", report_id="classification-report",
        terminal_kind="result", node_coverage=coverage, artifact=observation("d" * 64, "d" * 64)))
    generated = store._read(lambda c: chains(c, candidate))
    if assessment == "defect_within_contract":
        assert len(generated) == 1
        repair = claim(scoped, [generated[0]["repair"]], "repair")
        assert set(repair["predecessor_reports"]) == {"report-audit", "classification-report"}
    else:
        assert generated == []
        assert states(scoped)["architecture"] == "failed"
        second = store._read(lambda c: reviews(c, candidate))[-1]
        assert second["generation"] == 2
        assert second["node"] != review["node"]
        followup = claim(scoped, [second["node"]], "classifier-second")
        assert set(followup["predecessor_reports"]) == {"report-audit", "classification-report"}
        coverage[0]["node"] = second["node"]
        coverage[0]["coverage"][0]["verification"][0]["summary"] = "Different prose is not new evidence."
        store._write(lambda c: ledger.publish_nodes(c, assignment_id="classifier-second", report_id="second-classification-report",
            terminal_kind="result", node_coverage=coverage, artifact=observation("d" * 64, "d" * 64)))
        assert len(store._read(lambda c: reviews(c, candidate))) == 2
        assert states(scoped)["architecture"] == "exhausted"
        assert store._read(lambda c: json.loads(c.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='classification_exhausted' ORDER BY sequence DESC LIMIT 1", (candidate,)).fetchone()[0]))["reason"] == "non_progress"
        assert store._read(lambda c: chains(c, candidate)) == []
