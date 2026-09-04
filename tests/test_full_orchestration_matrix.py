"""All 20 public operations in prerequisite-correct source scenarios.

This is an API/ledger contract test, never a live, transport, hook, or model
qualification. Profiles are independently covered in test_typed_profile_contract.
The source recovery scenario explicitly simulates a fresh connection; real
resume and native lifecycle belong to the separate CLI/Desktop qualification.
"""
from plan_fixtures import ordinary_candidates
import re

from jsonschema import validate
from cortex import PUBLIC_TOOLS
from cortex_runtime import domain_api as api, graph_ledger
from test_domain_public_api_contract import PROVENANCE
from test_graph_ledger import observation
from test_publication_projection_repair import document_graph, content_for
from test_typed_publication_transaction import baseline_content


def test_all_twenty_operations_have_stateful_source_scenarios(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    exercised = set()

    def call(name, **arguments):
        public = {("continue" if key == "continue_" else key): value
            for key, value in arguments.items() if not key.startswith("_")}
        validate(public, PUBLIC_TOOLS[name]["inputSchema"])
        result = getattr(api, name)(**arguments)
        exercised.add(name)
        return result

    def task(name, request):
        project = tmp_path / name
        project.mkdir()
        result = call("open_task", project_root=str(project), request_original=request, user_language="en",
            outcomes=[{"outcome": "Product", "acceptance": ["Product works"], "constraints": [], "verification": []}],
            constraints=["Controlled source fixture, no external services"])
        return result["task_ref"]

    def dispatch(task_ref, nodes=None, bootstrap=None, responsibility="evidence", profile="general"):
        coordinator = {}
        call("read_scope", task_ref=task_ref, responsibility=responsibility, _connection_context=coordinator)
        result = call("open_assignment", task_ref=task_ref, profile_name=profile, model="gpt-5.6-luna",
            reasoning_effort="high", _connection_context=coordinator,
            **({"nodes": nodes} if nodes is not None else {"bootstrap": bootstrap}))
        assert not result["replayed"] and "model" not in result["native_dispatch"]
        worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', result["native_dispatch"]["message"]).group(1)
        context = {}
        page = call("read_task", task_ref=worker_ref, _connection_context=context)
        while page["has_more"]:
            page = call("read_task", task_ref=worker_ref, continue_=True, _connection_context=context)
        return worker_ref, context

    # Normal typed execution, explicit requested review, correct DAG, and
    # post-result closure. No conditional history/recovery reads here.
    current = task("execution", "Build the product, assess its documentation, and show me the plan for approval.")
    call("assess_governance", task_ref=current, mode="light", user_review_requested=True)
    store, identifier = api._task_store(current)
    baseline, worker = dispatch(current, nodes=["baseline"])
    assert call("publish_result", task_ref=baseline, _connection_context=worker, **baseline_content())["published"]
    planner, worker = dispatch(current, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    assert call("publish_plan", task_ref=planner, status="completed", summary="Complete product plan", scope="Product",
        candidates=ordinary_candidates(document_graph()), artifact=observation(), risks=[], unresolved=[], _connection_context=worker)["published"]
    validator, worker = dispatch(current, nodes=["validate-candidate"])
    assert call("publish_result", task_ref=validator, _connection_context=worker, **content_for(store, worker, "a" * 64))["published"]
    evidence = {}
    page = call("read_evidence", task_ref=current, report_policy="active_plan", _connection_context=evidence)
    while page["has_more"]:
        page = call("read_evidence", task_ref=current, report_policy="active_plan", continue_=True, _connection_context=evidence)
    packet = call("open_plan_review", task_ref=current, prompt="Review the product plan and its implementation and documentation checks.", prompt_language="en")
    assert packet["data"]["human_view"]["markdown_link"].startswith("[Open plan revision](")
    call("record_plan_review", task_ref=current, response_original="Approve this plan.", user_language="en", outcome="approve")
    implementer, worker = dispatch(current, nodes=["implementation"], responsibility="delivery")
    content = content_for(store, worker, "a" * 64)
    content["artifact"] = observation("a" * 64, "b" * 64)
    assert call("publish_result", task_ref=implementer, _connection_context=worker, **content)["published"]
    writer, worker = dispatch(current, nodes=["documentation"], profile="technical_writer")
    content = content_for(store, worker, "b" * 64)
    content.pop("outcome")
    content.pop("changes")
    content.update(findings=[], recommendations=[])
    assert call("publish_documentation", task_ref=writer, _connection_context=worker, **content)["published"]
    assert store._read(lambda c: graph_ledger.closure_evidence(c, identifier))["ready"]
    call("read_state", task_ref=current)
    call("open_clarification", task_ref=current, purpose="closure_review", options=["revise", "close"],
        prompt="The product and documentation checks passed. Review the result: revise or close?", prompt_language="en")
    call("record_clarification", task_ref=current, response_original="Close the checked task.", user_language="en", outcome="close")
    assert call("close_task", task_ref=current, verdict="ready")["state"] == "closed"

    # A genuine pre-plan product decision with a point replacement preserving
    # existing acceptance. The same direct answer changes the contract once.
    branch = task("branch", "Build a product; resolve the language choice before planning its content.")
    call("assess_governance", task_ref=branch, mode="light")
    call("open_steering", task_ref=branch, prompt="Which content language should the product use: English or Romanian?", prompt_language="en")
    context = {}
    call("read_scope", task_ref=branch, responsibility="delivery", _connection_context=context)
    existing = call("read_outcome", task_ref=branch, outcome="Product", _connection_context=context)["data"]["outcome"]
    replacement = {**existing, "acceptance": [*existing["acceptance"], "Content is Romanian"]}
    changed = call("record_steering", task_ref=branch, response_original="Use Romanian content.", user_language="en",
        add=[replacement], retire=["Product"], _connection_context=context)
    assert changed["effect"]["effective_revision"] == 2

    # Explicit source-level connection recovery with an unfinished assignment,
    # followed by requested chronology. Neither read is coverage-only polling.
    recovering = task("recovery", "Inspect the product and provide an audit chronology of the inspection.")
    call("assess_governance", task_ref=recovering, mode="minimal")
    dispatch(recovering, nodes=["baseline"])
    restored = {"_role": "unknown"}
    assert call("read_state", task_ref=recovering, _connection_context=restored)["data"]["recovery_required"]
    page = call("read_continuations", task_ref=recovering, _connection_context=restored)
    while page["has_more"]:
        page = call("read_continuations", task_ref=recovering, continue_=True, _connection_context=restored)
    history = {}
    page = call("read_timeline", task_ref=recovering, _connection_context=history)
    while page["has_more"]:
        page = call("read_timeline", task_ref=recovering, continue_=True, _connection_context=history)
    assert exercised == set(PUBLIC_TOOLS)
    assert len(exercised) == 20
