"""Focused structural properties; these tests are not host/live evidence."""
from copy import deepcopy

import pytest

from cortex_runtime.execution_graph import (
    GraphError, assignment_compatible, dependency_satisfied, project_readiness,
    publication_kind, validate_coverage, validate_graph,
)


def check(key="tests"):
    return {"key": key, "description": "Run the declared focused tests.", "required": True}


def node(key, *, contribution=None, requires=(), provides=(), dependencies=(), audit=False):
    value = {
        "key": key, "kind": "audit" if audit else "implementation",
        "responsibility": "evidence" if audit else "delivery",
        "execution_mode": "read_only" if audit else "mutating",
        "owner": key, "contributions": [] if contribution is None else [contribution],
        "verifies": [{"kind": "outcome", "name": "Product"}] if audit else [],
        "mutation_domains": [] if audit else [f"src/{key}"],
        "requires": list(requires), "provides": list(provides),
        "dependencies": [{
            "node": parent, "capabilities": list(capabilities),
            "optional": False, "allow_not_applicable": False,
        } for parent, capabilities in dependencies],
        "work": ["Perform the bounded work."], "acceptance": ["The declared scope is complete."],
        "checks": [check()], "activation": "always",
        "remediation": {
            "generation_budget": 2, "strategy_budget": 1,
            "strategies": [{"key": "focused-repair", "work": ["Repair the demonstrated defect."],
                            "diagnostic_checks": [check("cause")]}],
            "mutation_domains": ["src"], "restores": list(provides),
            "regression_checks": [check("regression")], "classification_verification": False,
        },
    }
    return value


def graph():
    return {
        "nodes": [
            node("frontend", contribution="ui", provides=["ui_ready"]),
            node("backend", contribution="api", provides=["api_ready"]),
            node("integration", contribution="integrated", requires=["ui_ready", "api_ready"],
                 provides=["artifact"], dependencies=[("frontend", ["ui_ready"]), ("backend", ["api_ready"])]),
            node("architecture", requires=["artifact"], provides=["architecture_checked"],
                 dependencies=[("integration", ["artifact"])], audit=True),
            node("database", requires=["artifact"], provides=["database_checked"],
                 dependencies=[("integration", ["artifact"])], audit=True),
        ],
        "outcomes": [{"outcome": "Product", "all_of": ["ui", "api", "integrated"]}],
        "fingerprint_method": "git_content_v1", "artifact_paths": ["."],
        "budgets": {"planning": 2, "additional_evidence": 2, "reconciliation": 2, "recovery": 2},
    }


def test_multiple_contributions_and_many_verifiers_share_one_outcome():
    validated = validate_graph(graph(), ["Product"])
    assert validated.order.index("frontend") < validated.order.index("integration")
    assert validated.order.index("backend") < validated.order.index("integration")
    assert validated.order.index("integration") < validated.order.index("architecture")
    copy = validated.data()
    copy["nodes"].clear()
    assert len(validated.data()["nodes"]) == 5


@pytest.mark.parametrize("key", ["baseline", "baseline-candidate", "validate-candidate", "plan-1", "discovery-2", "reconcile-3", "repair-reserved", "regression-reserved", "classify-reserved", "strategy-reserved"])
def test_generated_node_names_are_reserved_in_the_advertised_contract(key):
    value = graph()
    value["nodes"][0]["key"] = key
    with pytest.raises(GraphError, match="reserved_bootstrap_key"):
        validate_graph(value, ["Product"])


def test_independent_audits_wait_for_implementation_and_become_ready_together():
    validated = validate_graph(graph(), ["Product"])
    observations = {
        "frontend": {"state": "complete", "artifact_generation": "g1"},
        "backend": {"state": "complete", "artifact_generation": "g1"},
        "integration": {"state": "active", "artifact_generation": "g1"},
    }
    def states():
        return {row["node"]: row for row in project_readiness(
            validated, observations, activated=True, approved=True, review_required=False,
            current_generation="g1",
        )}
    assert states()["architecture"]["state"] == "waiting"
    assert states()["database"]["state"] == "waiting"
    observations["integration"]["state"] = "complete"
    assert states()["architecture"]["state"] == "ready"
    assert states()["database"]["state"] == "ready"


@pytest.mark.parametrize("disposition", ["active", "partial", "blocked", "failed", "exhausted", "stale", "waiting", "ready"])
def test_unsatisfactory_predecessor_matrix(disposition):
    assert not dependency_satisfied(disposition)


def test_satisfactory_predecessor_matrix():
    assert dependency_satisfied("complete")
    assert dependency_satisfied("resolved")
    assert not dependency_satisfied("complete", has_failed=True)
    assert not dependency_satisfied("complete", has_not_run=True)
    assert dependency_satisfied("complete", has_not_run=True, allow_not_applicable=True)
    assert not dependency_satisfied("skipped")
    assert dependency_satisfied("skipped", optional=True)


@pytest.mark.parametrize("mutation,reason", [
    (lambda g: g["nodes"][1]["contributions"].append("ui"), "duplicate_contribution_owner"),
    (lambda g: g["outcomes"][0]["all_of"].append("missing"), "outcome_contribution_missing"),
    (lambda g: g["nodes"][2]["dependencies"].pop(), "capability_provider_ambiguous_or_missing"),
    (lambda g: g["nodes"][3]["requires"].clear(), "artifact_dependency_missing"),
    (lambda g: g["nodes"][3]["verifies"].append({"kind": "contribution", "name": "missing"}), "verification_subject_missing"),
    (lambda g: g["nodes"][0]["verifies"].append({"kind": "outcome", "name": "Product"}), "verification_not_independent"),
    (lambda g: g["nodes"][0]["remediation"].update(generation_budget=0), "budget_invalid"),
    (lambda g: g["nodes"][0].pop("remediation"), "remediation_policy_missing"),
    (lambda g: g["nodes"][0]["mutation_domains"].clear(), "mutation_boundary_missing"),
])
def test_invalid_graph_is_rejected_before_activation(mutation, reason):
    value = graph()
    mutation(value)
    with pytest.raises(GraphError) as error:
        validate_graph(value, ["Product"])
    assert error.value.reason == reason


def test_cycles_are_rejected_even_with_valid_capability_edges():
    value = graph()
    value["nodes"][0]["requires"] = ["artifact"]
    value["nodes"][0]["dependencies"] = [{
        "node": "integration", "capabilities": ["artifact"],
        "optional": False, "allow_not_applicable": False,
    }]
    with pytest.raises(GraphError, match="dependency_cycle"):
        validate_graph(value, ["Product"])


def test_structural_validity_does_not_activate_candidate():
    rows = project_readiness(validate_graph(graph(), ["Product"]), {},
        activated=False, approved=True, review_required=False, current_generation="g1")
    assert all(row["state"] == "waiting" for row in rows)
    assert all({"kind": "candidate_inactive"} in row["reasons"] for row in rows)


def test_approval_and_reconciliation_are_independent_gates():
    rows = project_readiness(validate_graph(graph(), ["Product"]), {},
        activated=True, approved=False, review_required=True,
        current_generation="g1", reconciliation_required=True)
    assert all({"kind": "approval_required"} in row["reasons"] for row in rows)
    assert all({"kind": "reconciliation_required"} in row["reasons"] for row in rows)


def test_mutation_barrier_does_not_impose_single_worker_limit():
    nodes = graph()["nodes"]
    assignment_compatible([nodes[3], nodes[4]], ["read_only", "read_only"])
    with pytest.raises(GraphError, match="project_mutation_barrier"):
        assignment_compatible([nodes[0]], ["read_only"])
    with pytest.raises(GraphError, match="project_mutation_barrier"):
        assignment_compatible([nodes[3]], ["mutating"])
    independent = deepcopy(nodes[3])
    independent["execution_mode"] = "artifact_independent"
    assignment_compatible([independent], ["mutating"])


def test_publication_kind_is_node_purpose_not_profile():
    assert publication_kind({"kind": "documentation", "profile_name": "general"}) == "documentation"
    assert publication_kind({"kind": "audit", "profile_name": "technical_writer"}) == "result"
    assert publication_kind({"kind": "planning"}) == "plan"


def test_old_stage_only_graph_is_not_accepted():
    with pytest.raises(GraphError):
        validate_graph({"stages": [{"owner": "developer", "work": ["Implement"], "verification": ["Test"]}]}, ["Product"])


def coverage(*, state="executed", status="complete"):
    return [{
        "kind": "contribution", "name": "ui", "status": status,
        "verification": [{"check_key": "tests", "state": state, "summary": "Observed focused check."}],
    }]


def test_canonical_facts_preserve_state_and_wording():
    value = coverage()
    facts = validate_coverage(graph()["nodes"][0], value)
    assert facts == [{**fact, "subject": {"kind": "contribution", "name": "ui"}} for fact in value[0]["verification"]]
    facts[0]["summary"] = "Changed copy"
    assert value[0]["verification"][0]["summary"] == "Observed focused check."


def test_classification_assessment_is_only_for_an_assigned_review_check():
    value = coverage()
    value[0]["verification"][0]["classification_assessment"] = "defect_within_contract"
    with pytest.raises(GraphError, match="classification_assessment_not_permitted"):
        validate_coverage(graph()["nodes"][0], value)
    subject = graph()["nodes"][0]
    subject["checks"][0].update(classification_review=True, classification_subjects=[{"kind": "contribution", "name": "ui"}])
    assert validate_coverage(subject, value)[0]["classification_assessment"] == "defect_within_contract"
    value[0]["verification"][0].pop("classification_assessment")
    with pytest.raises(GraphError, match="classification_assessment_required"):
        validate_coverage(subject, value)


def test_complete_cannot_hide_failed_or_unrun_mandatory_checks():
    missing = coverage(state="not_run")
    missing[0]["verification"][0]["classification"] = "inconclusive"
    with pytest.raises(GraphError, match="complete_with_missing_check"):
        validate_coverage(graph()["nodes"][0], missing)
    value = coverage(state="failed")
    value[0]["verification"][0]["classification"] = "defect_within_contract"
    with pytest.raises(GraphError, match="complete_with_failed_fact"):
        validate_coverage(graph()["nodes"][0], value)


def test_optional_non_applicability_must_be_predeclared():
    value = coverage(state="not_run")
    value[0]["verification"][0]["not_applicable"] = True
    subject = graph()["nodes"][0]
    with pytest.raises(GraphError, match="non_applicability_not_authorized"):
        validate_coverage(subject, value)
    subject["checks"][0]["required"] = False
    assert validate_coverage(subject, value)[0]["state"] == "not_run"


def test_failed_fact_requires_exact_remediation_classification():
    value = coverage(state="failed", status="failed")
    with pytest.raises(GraphError, match="finding_classification_missing"):
        validate_coverage(graph()["nodes"][0], value)
    value[0]["verification"][0]["classification"] = "defect_within_contract"
    assert validate_coverage(graph()["nodes"][0], value)[0]["classification"] == "defect_within_contract"


def test_duplicate_subjects_and_plain_strings_are_not_coalesced():
    value = coverage()
    with pytest.raises(GraphError, match="coverage_subject_invalid"):
        validate_coverage(graph()["nodes"][0], value + deepcopy(value))
    value[0]["verification"] = ["It passed."]
    with pytest.raises(GraphError, match="verification_fact_invalid"):
        validate_coverage(graph()["nodes"][0], value)


def test_planning_never_fabricates_observed_not_run_evidence():
    with pytest.raises(GraphError, match="plan_observed_coverage_forbidden"):
        validate_coverage({"kind": "planning"}, coverage(state="not_run", status="unverified"))


@pytest.mark.parametrize("status", ["partial", "unverified", "blocked"])
def test_missing_checks_have_structured_classification(status):
    value = coverage(state="not_run", status=status)
    with pytest.raises(GraphError, match="finding_classification_missing"):
        validate_coverage(graph()["nodes"][0], value)
    value[0]["verification"][0]["classification"] = "inconclusive"
    assert validate_coverage(graph()["nodes"][0], value)[0]["classification"] == "inconclusive"


@pytest.mark.parametrize("status", ["partial", "unverified", "blocked", "failed"])
def test_incomplete_disposition_cannot_contradict_all_success_facts(status):
    with pytest.raises(GraphError, match="failed_without_failed_fact|incomplete_without_finding"):
        validate_coverage(graph()["nodes"][0], coverage(status=status))


@pytest.mark.parametrize("path", ["../outside", "/absolute", "src/../other", "src//nested", "src/.git/config", "src/\x00file"])
@pytest.mark.parametrize("surface", ["artifact", "mutation", "remediation"])
def test_all_artifact_and_mutation_boundaries_are_relative_canonical(path, surface):
    value = graph()
    if surface == "artifact":
        value["artifact_paths"] = [path]
    elif surface == "mutation":
        value["nodes"][0]["mutation_domains"] = [path]
    else:
        value["nodes"][0]["remediation"]["mutation_domains"] = [path]
    with pytest.raises(GraphError, match="artifact_boundary_invalid"):
        validate_graph(value, ["Product"])


def test_stale_generation_audit_does_not_satisfy_downstream_evidence():
    value = graph()
    value["nodes"][4]["requires"].append("architecture_checked")
    value["nodes"][4]["dependencies"].append({
        "node": "architecture", "capabilities": ["architecture_checked"],
        "optional": False, "allow_not_applicable": False,
    })
    observations = {key: {"state": "complete", "artifact_generation": "old"}
                    for key in ("frontend", "backend", "integration", "architecture")}
    rows = {row["node"]: row for row in project_readiness(
        validate_graph(value, ["Product"]), observations,
        activated=True, approved=True, review_required=False, current_generation="new",
    )}
    assert rows["integration"]["state"] == "complete"
    assert rows["architecture"]["state"] == "ready"
    assert rows["database"]["state"] == "waiting"
    assert {"kind": "predecessor_unsatisfied", "node": "architecture"} in rows["database"]["reasons"]
