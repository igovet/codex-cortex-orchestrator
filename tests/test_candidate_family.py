"""Candidate structure is not approval or native/live qualification."""
from copy import deepcopy

import pytest

from cortex_runtime.candidate_family import candidates_schema, proposed_contract, validate_family
from cortex_runtime.execution_graph import GraphError
from cortex_runtime.mcp_api import _validate_schema
from test_execution_graph_integrity import graph


def outcome(name="Product", acceptance="Works"):
    return {"outcome": name, "acceptance": [acceptance], "constraints": [], "verification": ["Run checks"]}


def alternative(key, *, acceptance):
    return {"key": key, "consequences": [f"Use the {key} product behavior."],
            "delta": {"add": [outcome(acceptance=acceptance)], "retire": ["Product"]}, "graph": graph()}


def test_every_alternative_has_exact_base_delta_and_graph_commitments():
    base = [outcome()]
    candidates = [alternative("local", acceptance="Works offline"), alternative("remote", acceptance="Works remotely")]
    original = deepcopy((base, candidates))
    family = validate_family(candidates, base, revision=3)
    _validate_schema(candidates_schema(), candidates)
    assert (base, candidates) == original
    selected = family.select("local")
    assert selected["contract"] == [outcome(acceptance="Works offline")]
    assert selected["delta_digest"] != family.select("remote")["delta_digest"]
    assert selected["graph_digest"] == family.select("remote")["graph_digest"]
    assert family.data()["base_revision"] == 3
    selected["contract"][0]["acceptance"].clear()
    assert family.select("local")["contract"][0]["acceptance"] == ["Works offline"]
    assert family.digest != validate_family(candidates, base, revision=4).digest
    assert family.digest != validate_family(candidates, [outcome(acceptance="Changed baseline")], revision=3).digest


@pytest.mark.parametrize("key", [None, "LOCAL", "local ", "I approve local", "missing"])
def test_branch_selection_is_exact_and_never_parsed_from_prose(key):
    family = validate_family([alternative("local", acceptance="Offline")], [outcome()], revision=1)
    with pytest.raises(GraphError, match="candidate_selection_unknown"):
        family.select(key)


def test_one_ordinary_candidate_does_not_require_a_fake_semantic_delta():
    candidate = alternative("implementation", acceptance="unused")
    candidate["delta"] = {"add": [], "retire": []}
    family = validate_family([candidate], [outcome()], revision=1)
    assert family.select("implementation")["contract"] == [outcome()]
    assert not hasattr(family, "approved")


def test_single_point_replacement_keeps_position_and_discards_retired_details():
    base = [outcome("First"), outcome("Old", "Retired acceptance"), outcome("Last")]
    replacement = outcome("New", "New acceptance")
    result = proposed_contract(base, {"add": [replacement], "retire": ["Old"]})
    assert [item["outcome"] for item in result] == ["First", "New", "Last"]
    assert result[1] == replacement
    assert base[1]["acceptance"] == ["Retired acceptance"]


@pytest.mark.parametrize("mutation,error", [
    ("unknown_retirement", "candidate_retired_outcome_unknown"),
    ("collision", "candidate_outcome_collision"),
    ("duplicate_key", "candidate_key_ambiguous"),
    ("equivalent", "candidate_alternatives_equivalent"),
    ("empty_contract", "candidate_outcome_bound"),
])
def test_invalid_family_is_rejected_without_mutating_source(mutation, error):
    candidates = [alternative("local", acceptance="Offline"), alternative("remote", acceptance="Online")]
    if mutation == "unknown_retirement":
        candidates[1]["delta"]["retire"] = ["Unknown"]
    elif mutation == "collision":
        candidates[1]["delta"]["retire"] = []
    elif mutation == "duplicate_key":
        candidates[1]["key"] = "local"
    elif mutation == "equivalent":
        candidates[1]["delta"] = deepcopy(candidates[0]["delta"])
    elif mutation == "empty_contract":
        candidates[1]["delta"]["add"] = []
    original = deepcopy(candidates)
    with pytest.raises(GraphError, match=error):
        validate_family(candidates, [outcome()], revision=1)
    assert candidates == original


def test_one_invalid_alternative_cannot_hide_behind_a_valid_first_graph():
    candidates = [alternative("local", acceptance="Offline"), alternative("remote", acceptance="Online")]
    candidates[1]["graph"]["nodes"][3]["dependencies"] = []
    with pytest.raises(GraphError):
        validate_family(candidates, [outcome()], revision=1)


@pytest.mark.parametrize("revision", [0, -1, True, "1"])
def test_base_revision_must_be_exact_positive_integer(revision):
    with pytest.raises(GraphError, match="candidate_base_revision_invalid"):
        validate_family([alternative("local", acceptance="Offline")], [outcome()], revision=revision)


def test_family_bound_counts_all_alternatives_not_each_one_separately():
    candidates = [alternative(f"choice-{index}", acceptance=f"Behavior {index}") for index in range(4)]
    for candidate in candidates:
        candidate["consequences"] = [f"Impact {index}: " + "x" * 1900 for index in range(8)]
    with pytest.raises(GraphError, match="candidate_family_byte_bound"):
        validate_family(candidates, [outcome()], revision=1)


def test_variant_count_is_finite():
    with pytest.raises(GraphError, match="array_bound_invalid"):
        validate_family([alternative(f"choice-{index}", acceptance=f"Behavior {index}") for index in range(5)],
                        [outcome()], revision=1)


@pytest.mark.parametrize("delta", [
    {"add": [outcome("Added")], "retire": []},
    {"add": [outcome("Renamed", "New")], "retire": ["Middle"]},
    {"add": [outcome("Middle", "Replacement")], "retire": ["Middle"]},
    {"add": [], "retire": ["Middle"]},
    {"add": [outcome("First", "New first"), outcome("Added")], "retire": ["First", "Middle"]},
])
def test_proposed_contract_matches_the_real_atomic_steering_result(tmp_path, monkeypatch, delta):
    from cortex_runtime.domain_api import open_task, read_scope, read_outcome, record_steering
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    base = [outcome("First"), outcome("Middle"), outcome("Last")]
    expected = proposed_contract(base, delta)
    task = open_task(project_root=str(project), request_original="Implement the three product surfaces.",
                     user_language="en", outcomes=base, constraints=["Fixture only"])["task_ref"]
    context = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    record_steering(task_ref=task, response_original="Apply the specified requirement change.",
                    user_language="en", **delta, _connection_context=context)
    current = read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    actual = [read_outcome(task_ref=task, outcome=item["outcome"])["data"]["outcome"]
              for item in current["data"]["outcomes"]]
    assert actual == expected


@pytest.mark.parametrize("failure", ["unchanged", "empty", "collision", "injected"])
def test_direct_steering_rolls_back_binding_decision_and_revision(tmp_path, monkeypatch, failure):
    from cortex_runtime.domain_api import open_task, read_scope, record_steering, _task_store
    from cortex_runtime.v12_service import V12ServiceError
    from cortex_runtime.v12_store import V12Store, V12StoreError
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    original = outcome()
    task = open_task(project_root=str(project), request_original="Build the product.", user_language="en",
                     outcomes=[original], constraints=["Fixture only"])["task_ref"]
    store, _ = _task_store(task)
    context = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    before = store._read(lambda c: list(c.iterdump()))
    delta = {"add": [original], "retire": ["Product"]}
    if failure == "empty":
        delta["add"] = []
    elif failure == "collision":
        delta["retire"] = []
    elif failure == "injected":
        delta = {"add": [outcome("Added")], "retire": []}
        def reject(*args, **kwargs):
            raise V12StoreError("Injected transaction failure", code="ledger_error")
        monkeypatch.setattr(V12Store, "_commit_contract_delta", reject)
    with pytest.raises(V12ServiceError):
        record_steering(task_ref=task, response_original="Apply the described product change.",
                        user_language="en", **delta, _connection_context=context)
    assert store._read(lambda c: list(c.iterdump())) == before
