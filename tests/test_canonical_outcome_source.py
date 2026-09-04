"""Source provenance is derived without storing duplicate criterion text."""
import json
import pytest

from cortex_runtime.domain_api import open_task, read_outcome, read_scope, record_steering, _task_store


@pytest.mark.parametrize("route", ["initial", "addition", "replacement"])
def test_equal_text_retains_both_acceptance_and_verification_roles(tmp_path, monkeypatch, route):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    exact = {"outcome": "Product", "acceptance": ["Run the product check."],
             "verification": ["Run the product check."], "constraints": []}
    original = exact if route == "initial" else {**exact, "outcome": "Original"}
    task = open_task(project_root=str(project), request_original="Verify the product.", user_language="en",
                     outcomes=[original], constraints=["Fixture only"])["task_ref"]
    if route != "initial":
        context = {}
        read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
        record_steering(task_ref=task, response_original="Add Product." if route == "addition" else "Replace Original with Product.",
                        user_language="en", add=[exact], retire=[] if route == "addition" else ["Original"],
                        _connection_context=context)
    assert read_outcome(task_ref=task, outcome="Product")["data"]["outcome"] == exact
    store, canonical = _task_store(task)
    item = next(item for item in store._read(lambda c: store._effective_contract(c, canonical))["items"] if item["text"] == "Product")
    fragments = item["source_fragments"]
    assert any(".acceptance[" in fragment["path"] and fragment["text"] == exact["acceptance"][0] for fragment in fragments)
    assert any(".verification[" in fragment["path"] and fragment["text"] == exact["verification"][0] for fragment in fragments)


def test_large_accepted_contract_has_one_persisted_criterion_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    acceptance = [f"Criterion {index}: " + "bounded source evidence " * 75 for index in range(24)]
    outcome = {"outcome": "Product", "acceptance": acceptance, "constraints": ["Keep files scoped."],
               "verification": ["Observe the completed product."]}
    task = open_task(project_root=str(project), request_original="Implement the bounded product.",
                     user_language="en", outcomes=[outcome], constraints=["Fixture only"])["task_ref"]
    store, canonical = _task_store(task)
    def inspect(c):
        task_row = c.execute("SELECT acceptance_criteria_json FROM tasks WHERE task_id=?", (canonical,)).fetchone()
        detail = c.execute("SELECT details_json FROM effective_contract_item_details").fetchone()[0]
        return task_row[0], detail
    duplicated, detail = store._read(inspect)
    assert json.loads(duplicated) == []
    assert "source_fragments" not in json.loads(detail)
    assert json.loads(detail)["acceptance_criteria"] == acceptance
    current = store._read(lambda c: store._effective_contract(c, canonical))["items"][0]
    fragments = current["source_fragments"]
    assert [r["text"] for r in fragments if ".acceptance[" in r["path"]] == acceptance
    assert any(r["text"] == "Keep files scoped." for r in fragments)
    assert {r["source_type"] for r in fragments} == {"user_request"}
    context = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    replacement = {**outcome, "acceptance": ["The revised product is ready."]}
    record_steering(task_ref=task, response_original="Replace the acceptance with the revised product readiness.",
                    user_language="en", add=[replacement], retire=["Product"], _connection_context=context)
    current = store._read(lambda c: store._effective_contract(c, canonical))["items"][0]
    assert {r["source_type"] for r in current["source_fragments"]} == {"user_steer"}
    assert current["acceptance_criteria"] == replacement["acceptance"]
    assert all("source_fragments" not in json.loads(row[0]) for row in store._read(
        lambda c: c.execute("SELECT details_json FROM effective_contract_item_details").fetchall()))
