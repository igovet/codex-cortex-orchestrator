"""Fresh-host bootstrap availability matches atomic admission, not existence."""
import pytest

from cortex_runtime.domain_api import open_assignment, read_scope
from cortex_runtime.mcp_api import _service_failure
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_replan_review_lineage import finish_observation


@pytest.mark.parametrize("responsibility,kind", [("planning", "planning"), ("evidence", "discovery")])
def test_fresh_scope_reports_baseline_before_bootstrap(node_case, monkeypatch, responsibility, kind):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    context = {}
    result = read_scope(task_ref=task, responsibility=responsibility, _connection_context=context)["data"]
    assert result["bootstrap_available"] is False
    assert {"kind": "predecessor_unsatisfied", "node": "baseline", "responsibility": "evidence"} in result["bootstrap_reasons"]
    intent = {"kind": kind, **({"question": "Inspect the current product structure."} if kind == "discovery" else {})}
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12ServiceError) as failure:
        open_assignment(task_ref=task, bootstrap=intent, profile_name="planner" if kind == "planning" else "explorer",
            model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
    public = _service_failure(failure.value)
    assert public["code"] == "assignment_not_ready"
    assert public["details"]["reason"] == "bootstrap_prerequisites_unsatisfied"
    assert "scope" in public["action"] and "model" in public["action"]
    assert store._read(lambda c: list(c.iterdump())) == before
    finish_observation(store, task, "baseline")
    ready = read_scope(task_ref=task, responsibility=responsibility, _connection_context={})["data"]
    assert ready["bootstrap_available"] is True and ready["bootstrap_reasons"] == []


def test_delivery_scope_never_offers_bootstrap(node_case):
    _, args = node_case
    result = read_scope(task_ref=public_task_ref(args["task_id"]), responsibility="delivery", _connection_context={})["data"]
    assert result["bootstrap_available"] is False


def test_bootstrap_field_advertises_its_actual_readiness_precondition():
    from cortex import PUBLIC_TOOLS
    description = PUBLIC_TOOLS["open_assignment"]["inputSchema"]["properties"]["bootstrap"]["description"]
    assert "bootstrap_available=true" in description
    assert "never an already ready baseline" in description


def test_waiting_bootstrap_has_typed_source_transport_error(tmp_path):
    """Real stdio transport preserves safe admission diagnostics, not ledger_error."""
    from test_command_receipts import _source_stdio_session

    project = tmp_path / "project"
    project.mkdir()
    with _source_stdio_session(str(tmp_path / "runtime")) as coordinator:
        def call(name, **args):
            return coordinator(name, args)["result"]

        task = call("open_task", project_root=str(project), request_original="Build a counter.",
            user_language="en", constraints=["Offline only"],
            outcomes=[{"outcome": "Counter", "acceptance": ["Controls work"],
                       "constraints": [], "verification": []}])["structuredContent"]["task_ref"]
        assert not call("assess_governance", task_ref=task, mode="minimal").get("isError")
        for responsibility, kind in (("planning", "planning"), ("evidence", "discovery")):
            scope = call("read_scope", task_ref=task, responsibility=responsibility)
            assert not scope.get("isError")
            assert scope["structuredContent"]["data"]["bootstrap_available"] is False
            result = call("open_assignment", task_ref=task,
                bootstrap={"kind": kind, **({"question": "Inspect structure."} if kind == "discovery" else {})},
                profile_name="planner" if kind == "planning" else "explorer",
                model="gpt-5.6-luna", reasoning_effort="high")
            assert result["isError"] is True
            error = result["structuredContent"]["error"]
            assert error["code"] == "assignment_not_ready"
            assert error["details"]["reason"] == "bootstrap_prerequisites_unsatisfied"
            assert error["retryable"] is False


def test_terminal_empty_arrays_remain_required_and_explicit_in_live_contract():
    from cortex import PUBLIC_TOOLS
    from cortex_runtime.mcp_api import _validate_schema, _SchemaError
    from test_typed_publication_transaction import baseline_content

    for name in ("publish_plan", "publish_result", "publish_documentation"):
        schema = PUBLIC_TOOLS[name]["inputSchema"]
        artifact_description = schema["properties"]["artifact"]["description"]
        assert "artifact" in schema["required"]
        if name != "publish_plan":
            assert all(word in artifact_description for word in ("Required", "read_only", "mutating", "baseline", "artifact_independent"))
        for key in ("risks", "unresolved"):
            assert key in schema["required"]
            field = schema["properties"][key]
            assert field["minItems"] == 0
            assert "Required" in field["description"] and "[]" in field["description"]
            assert "omit" in field["description"]
    payload = dict(task_ref="t_0123456789ab_" + "a" * 32, **baseline_content())
    _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], payload)
    verification = payload["node_coverage"][0]["coverage"][0]["verification"][0]
    verification["check_key"] = "Establish capability and matching start/end observations without mutations."
    with pytest.raises(_SchemaError):
        _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], payload)
    verification["check_key"] = "baseline"
    del payload["unresolved"]
    with pytest.raises(_SchemaError, match="unresolved"):
        _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"], payload)
