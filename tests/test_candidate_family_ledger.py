"""Immutable alternatives and independent validation, not native live evidence."""
import json

import pytest

from cortex_runtime import candidate_family as families, graph_ledger as ledger
from cortex_runtime.execution_graph import GraphError
from test_candidate_family import alternative
from test_graph_ledger import case, claim, publication, observation


def create(case, count=2):
    store, task, _ = case
    definitions = [alternative(f"option-{index}", acceptance=f"Behavior {index}") for index in range(count)]
    graph_id = store._write(lambda c: families.create_family(c, task_id=task,
        plan_report_id="family-plan", planner_assignment_id="family-planner", candidates=definitions))
    return (store, task, graph_id), definitions


@pytest.mark.parametrize("count", [1, 2, 4])
def test_every_alternative_is_validated_without_installing_its_nodes(case, count):
    family_case, definitions = create(case, count)
    store, task, graph_id = family_case
    names = store._read(lambda c: [row[0] for row in c.execute("SELECT node_key FROM execution_nodes WHERE graph_id=?", (graph_id,))])
    assert set(names) == {"baseline-candidate", "validate-candidate"}
    assert not store._read(lambda c: ledger.closure_evidence(c, task)["ready"])
    with pytest.raises(GraphError, match="candidate_family_not_validated"):
        store._read(lambda c: families.selection_evidence(c, graph_id=graph_id, branch_key="option-0"))
    with pytest.raises(GraphError, match="graph_validation_not_independent"):
        claim(family_case, ["validate-candidate"], "family-planner")
    scope = claim(family_case, ["validate-candidate"], "independent")
    assert len(scope["nodes"][0]["checks"]) == 5 * count
    readback = store._read(lambda c: ledger.assignment_scope(c, "independent"))
    assert "candidate_graph" not in readback
    assert [item["definition"] for item in readback["candidate_family"]["candidates"]] == definitions
    publication(family_case, "independent", scope["nodes"])
    assert store._read(lambda c: ledger._graph(c, graph_id)[0]["activation"]) == "validated"
    assert store._read(lambda c: ledger.task_projection(c, task)["outcomes"]) == [{"outcome": "Product", "status": "unverified"}]
    assert not store._read(lambda c: ledger.closure_evidence(c, task)["ready"])
    selected = store._read(lambda c: families.selection_evidence(c, graph_id=graph_id, branch_key="option-0"))
    assert selected["selected"]["contract"][0]["acceptance"] == ["Behavior 0"]
    assert selected["validation_assignment_id"] == "independent"
    assert store._read(lambda c: families.current_contract(c, task))[0]["acceptance"] == ["Product works"]


def test_one_missing_alternative_check_cannot_publish_complete_family(case):
    family_case, _ = create(case)
    store, _, graph_id = family_case
    scope = claim(family_case, ["validate-candidate"], "independent")
    scope["nodes"][0]["checks"].pop()
    with pytest.raises(GraphError):
        publication(family_case, "independent", scope["nodes"])
    assert store._read(lambda c: ledger._graph(c, graph_id)[0]["activation"]) == "candidate"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]) == 0


def test_failed_family_validation_remains_inactive(case):
    family_case, _ = create(case)
    store, _, graph_id = family_case
    scope = claim(family_case, ["validate-candidate"], "independent")
    publication(family_case, "independent", scope["nodes"], fail=True)
    with pytest.raises(GraphError, match="candidate_family_not_validated"):
        store._read(lambda c: families.selection_evidence(c, graph_id=graph_id, branch_key="option-0"))
    assert store._read(lambda c: ledger._graph(c, graph_id)[0]["activation"]) == "rejected"


@pytest.mark.parametrize("change", ["artifact", "contract", "corruption"])
def test_selection_rechecks_current_immutable_relations(case, change):
    family_case, _ = create(case)
    store, task, graph_id = family_case
    scope = claim(family_case, ["validate-candidate"], "independent")
    publication(family_case, "independent", scope["nodes"])
    if change == "artifact":
        store._write(lambda c: ledger._seal(c, task_id=task, revision=1,
            observation=observation("b" * 64, "b" * 64), source_assignment_id=None))
        reason = "candidate_family_evidence_stale"
    elif change == "contract":
        def corrupt_contract(c):
            # Deliberate storage-corruption fixture, not a permitted mutation.
            # Ordinary writes are now rejected by the immutable-criteria guard;
            # still verify that candidate digest checks detect bypassed guards.
            guard = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='obligation_details_no_update'").fetchone()[0]
            c.execute("DROP TRIGGER obligation_details_no_update")
            row = c.execute("SELECT item_id,details_json FROM effective_contract_item_details LIMIT 1").fetchone()
            details = json.loads(row[1])
            details["acceptance_criteria"] = ["Changed outside the family"]
            c.execute("UPDATE effective_contract_item_details SET details_json=? WHERE item_id=?", (json.dumps(details), row[0]))
            c.execute(guard)
        store._write(corrupt_contract)
        reason = "candidate_base_contract_changed"
    else:
        store._write(lambda c: c.execute("UPDATE plan_candidate_families SET content_json='{}' WHERE graph_id=?", (graph_id,)))
        reason = "candidate_family_corrupted"
    with pytest.raises(GraphError, match=reason):
        store._read(lambda c: families.selection_evidence(c, graph_id=graph_id, branch_key="option-0"))


def test_family_storage_failure_rolls_back_holding_graph_and_nodes(case):
    store, _, _ = case
    store._write(lambda c: c.execute("CREATE TRIGGER fail_family BEFORE INSERT ON plan_candidate_families BEGIN SELECT RAISE(ABORT,'fixture'); END"))
    before = store._read(lambda c: list(c.iterdump()))
    from cortex_runtime.v12_store import V12StoreError
    with pytest.raises(V12StoreError):
        create(case)
    assert store._read(lambda c: list(c.iterdump())) == before
