"""Artifact presence is explicit at every public boundary, never inferred."""
from copy import deepcopy

import pytest

from cortex import PUBLIC_TOOLS
from cortex_runtime import domain_api
from cortex_runtime.execution_graph import GraphError
from cortex_runtime.mcp_api import _SchemaError, _validate_schema
from cortex_runtime.typed_publications import validate_report
from test_typed_publication_transaction import baseline_content


def test_check_key_is_the_only_selector_and_rejects_legacy_check():
    content = baseline_content()
    fact = content["node_coverage"][0]["coverage"][0]["verification"][0]
    assert fact["check_key"] == "baseline"
    validate_report("result", content)
    fact["check"] = fact.pop("check_key")
    with pytest.raises(GraphError):
        validate_report("result", content)
    with pytest.raises(_SchemaError):
        _validate_schema(PUBLIC_TOOLS["publish_result"]["inputSchema"],
                         dict(task_ref="t_0123456789ab_" + "a" * 32, **content))


@pytest.mark.parametrize("reason", ["completed_report_has_unfinished_evidence",
    "artifact_observation_invalid", "verification_check_invalid", "classification_without_finding"])
def test_publication_failure_keeps_safe_invariant_without_private_evidence(reason):
    from cortex_runtime.mcp_api import _service_failure, _tool_error_result
    from cortex_runtime.v12_service import V12ServiceError
    failure = _service_failure(V12ServiceError("private diagnostic", code="report_incomplete",
        details={"reason": reason, "raw_report": "private evidence"}))
    result = _tool_error_result(failure, mutation="publish_result")
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["details"] == {"reason": reason}
    assert reason in result["content"][0]["text"]
    assert "private" not in str(result)


@pytest.mark.parametrize("kind", ["result", "documentation"])
@pytest.mark.parametrize("artifact", [None, "observed"])
def test_explicit_artifact_survives_public_adapter(monkeypatch, kind, artifact):
    content = baseline_content()
    if kind == "documentation":
        content.pop("outcome")
        content.pop("changes")
        content.update(findings=[], recommendations=[])
    if artifact is None:
        content["artifact"] = None
    schema = PUBLIC_TOOLS[f"publish_{kind}"]["inputSchema"]
    arguments = dict(task_ref="t_0123456789ab_" + "a" * 32, **content)
    _validate_schema(schema, arguments)
    validate_report(kind, content)
    captured = []
    monkeypatch.setattr(domain_api, "_publish_typed", lambda **kw: captured.append(kw["content"]))
    getattr(domain_api, f"publish_{kind}")(**arguments)
    assert captured == [content]
    missing = deepcopy(content)
    del missing["artifact"]
    with pytest.raises(GraphError):
        validate_report(kind, missing)
    with pytest.raises(_SchemaError):
        _validate_schema(schema, dict(task_ref=arguments["task_ref"], **missing))
    with pytest.raises(TypeError):
        getattr(domain_api, f"publish_{kind}")(task_ref=arguments["task_ref"], **missing)
