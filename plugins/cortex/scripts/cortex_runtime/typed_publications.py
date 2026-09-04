"""One schema source for typed report transport and transaction validation."""
from collections.abc import Mapping
from typing import Any

from cortex_runtime.execution_graph import (
    CLASSIFICATIONS, GraphError, _array, _closed, _text, _validate_shape,
)


def artifact_schema() -> dict[str, Any]:
    fingerprint = {"type": "string", "minLength": 64, "maxLength": 64, "pattern": "^[0-9a-f]{64}$"}
    changes = _closed({
        "count": {"type": "integer", "minimum": 0, "maximum": 100_000},
        "digest": dict(fingerprint, description="Commitment to the complete sorted changed-path set."),
        "samples": _array(_text(), maximum=16),
        "within_domains": {"type": "boolean"},
    })
    return dict(_closed({
        "method": {"type": "string", "enum": ["git_content_v1", "path_manifest_v1"]},
        "start": dict(fingerprint, description="Worker-observed fingerprint before bounded work."),
        "end": dict(fingerprint, description="Fingerprint immediately before publication after all mutating children stop."),
        "changes": dict(changes, description="Changes during this assignment, between its start and end observations."),
        "boundary": dict(_closed({
            "method": {"type": "string", "enum": ["git_content_v1", "path_manifest_v1"]},
            "start": fingerprint, "end": fingerprint, "changes": changes,
        }), description="Only for a declared new baseline boundary: required target-method/path observations enclosed by the outer interval proving the old boundary unchanged. Forbidden otherwise."),
        "baseline_changes": dict(changes, description="Required only for reconciliation with a sealed predecessor: predecessor-to-start changes, not worker authorship. Omit otherwise."),
    }, ["method", "start", "end", "changes"]), description="Ordinary work: copy the final procedure's terminal_observation. Conditional metadata requires an explicit assignment declaration.")


def coverage_schema() -> dict[str, Any]:
    fact = _closed({
        "check_key": _text("Copy key from this consumed node's checks entry, not its description or outcome verification prose. Put observed evidence in summary.", key=True),
        "state": {"type": "string", "enum": ["executed", "not_run", "failed"]},
        "summary": _text("Observable evidence, never an inferred success."),
        "classification": {"type": "string", "enum": list(CLASSIFICATIONS),
            "description": "Required for failed or unrun applicable checks; forbidden for successful or legitimately non-applicable checks."},
        "classification_assessment": {"type": "string", "enum": list(CLASSIFICATIONS),
            "description": "Required only for successful assigned classification review: assess its source finding, not the review itself. Omit on ordinary or failed/unrun checks."},
        "not_applicable": {"type": "boolean", "description": "True only for an assigned optional check that does not apply."},
        "strategy_assessment": _text("Successful strategy-selection only: required offered key or unavailable. Independent validation checks that same strategy against causal checks and unchanged authority. Forbidden on ordinary or failed/unrun checks.", key=True),
    }, ["check_key", "state", "summary"])
    return _array(_closed({
        "node": _text("Exact semantic node key from this consumed assignment.", key=True),
        "coverage": _array(_closed({
            "kind": {"type": "string", "enum": ["contribution", "outcome"]},
            "name": _text("Exact assigned contribution or verified outcome name."),
            "status": {"type": "string", "enum": ["complete", "partial", "unverified", "blocked", "failed"]},
            "verification": _array(fact, minimum=1, maximum=32),
        }), minimum=1),
    }), minimum=1)


def report_schema(kind: str) -> dict[str, Any]:
    common = {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"], "description": "Assigned scope only, not downstream product work."},
        "summary": _text("Concise terminal conclusion matching the detailed evidence."),
        "risks": dict(_array(_text()), description="Required assigned-scope risks; [] if none. Never omit."),
        "unresolved": dict(_array(_text()), description="Required unresolved assigned work/checks only, not later product stages. Completed scope requires []; never omit."),
    }
    if kind == "plan":
        from cortex_runtime.candidate_family import candidates_schema
        return _closed({**common, "scope": _text(), "candidates": candidates_schema(), "artifact": artifact_schema()})
    if kind not in {"result", "documentation"}:
        raise GraphError("publication_kind_not_permitted")
    artifact = {"anyOf": [artifact_schema(), {"type": "null"}],
        "description": "Required for baseline and all work: observations for read_only/mutating; null only for artifact_independent."}
    properties = {**common, "documentation_impact": _text(), "node_coverage": coverage_schema(), "artifact": artifact}
    if kind == "result":
        properties.update(outcome=_text(), changes=_array(_closed({"path": _text(), "summary": _text()})))
    else:
        properties.update(findings=_array(_closed({"area": _text(), "summary": _text()})), recommendations=_array(_text()))
    return _closed(properties)


def validate_report(kind: str, content: Mapping[str, Any]) -> None:
    _validate_shape(content, report_schema(kind))
    if kind == "plan":
        return
    rows = [row for node in content["node_coverage"] for row in node["coverage"]]
    if content["status"] == "completed" and (any(row["status"] != "complete" for row in rows) or content["unresolved"]):
        raise GraphError("completed_report_has_unfinished_evidence")
    if content["status"] == "failed" and not any(row["status"] == "failed" for row in rows):
        raise GraphError("failed_report_without_failed_coverage")
