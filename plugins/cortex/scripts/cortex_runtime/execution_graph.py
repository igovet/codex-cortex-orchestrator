"""Deterministic graph validation and readiness; no filesystem or host access.

The graph is planner-authored intent. This module proves structural properties
and computes neutral projections only; it never chooses a profile or dispatches
work. The store must call these checks inside its mutation transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


MAX_NODES = 64
MAX_EDGES = 256
MAX_GRAPH_BYTES = 49_152
MAX_CHECKS = 32
MAX_GENERATIONS = 8
MAX_STRATEGIES = 4
KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
KEY_RE = re.compile(KEY_PATTERN)
NODE_KINDS = (
    "planning", "graph_validation", "discovery", "implementation", "audit",
    "remediation", "verification", "documentation",
)
EXECUTION_MODES = ("artifact_independent", "read_only", "mutating")
CLASSIFICATIONS = (
    "defect_within_contract", "contract_change_required", "authority_required",
    "risk_change_required", "inconclusive",
)
NODE_STATES = (
    "waiting", "ready", "active", "complete", "partial", "failed", "blocked",
    "resolved", "exhausted", "skipped", "stale",
)


class GraphError(ValueError):
    def __init__(self, reason: str, *, node: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.node = node


def _closed(properties: dict[str, Any], required: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties,
        "required": list(properties if required is None else required),
        "additionalProperties": False,
    }


def _text(description: str = "", *, key: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": 2048}
    if key:
        result.update(maxLength=64, pattern=KEY_PATTERN)
    if description:
        result["description"] = description
    return result


def _array(items: dict[str, Any], *, minimum: int = 0, maximum: int = MAX_NODES) -> dict[str, Any]:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


def graph_schema() -> dict[str, Any]:
    """The static advertised shape is shared by transport and unit tests."""
    check = _closed({
        "key": _text("Node-local check key.", key=True),
        "description": _text("Expected check, never an observed execution claim."),
        "required": {"type": "boolean"},
    })
    subject = _closed({
        "kind": {"type": "string", "enum": ["outcome", "contribution"]},
        "name": _text("Exact outcome name or graph contribution key."),
    })
    edge = _closed({
        "node": _text("Predecessor semantic node key.", key=True),
        "capabilities": _array(_text(key=True), minimum=1),
        "optional": {"type": "boolean"},
        "allow_not_applicable": {"type": "boolean"},
    })
    template = _closed({
        "generation_budget": {"type": "integer", "minimum": 1, "maximum": MAX_GENERATIONS},
        "strategy_budget": {"type": "integer", "minimum": 1, "maximum": MAX_STRATEGIES},
        "strategies": _array(_closed({
            "key": _text("Preauthorized repair strategy; first is initial. The reserved key unavailable is forbidden.", key=True),
            "work": _array(_text("Bounded repair approach within this template's existing authority."), minimum=1),
            "diagnostic_checks": _array(check, minimum=1, maximum=MAX_CHECKS),
        }), minimum=1, maximum=MAX_STRATEGIES),
        "mutation_domains": _array(_text(), minimum=1),
        "restores": _array(_text(key=True), minimum=1),
        "regression_checks": _array(check, minimum=1, maximum=MAX_CHECKS),
        "classification_verification": {"type": "boolean"},
    })
    node = _closed({
        "key": _text("Unique graph-revision-local node selector. Reserved for generated nodes: baseline, baseline-candidate, validate-candidate, numbered plan-/discovery-/reconcile- names, and repair-/regression-/classify-/strategy- prefixes.", key=True),
        "kind": {"type": "string", "enum": list(NODE_KINDS)},
        "responsibility": {"type": "string", "enum": ["planning", "delivery", "evidence"]},
        "execution_mode": {"type": "string", "enum": list(EXECUTION_MODES)},
        "owner": _text("Human ownership label, not a profile or native agent type."),
        "contributions": _array(_text(key=True)),
        "verifies": _array(subject),
        "mutation_domains": _array(_text()),
        "requires": _array(_text(key=True)),
        "provides": _array(_text(key=True)),
        "dependencies": _array(edge),
        "work": _array(_text(), minimum=1),
        "acceptance": _array(_text(), minimum=1),
        "checks": _array(check, minimum=1, maximum=MAX_CHECKS),
        "activation": {"type": "string", "enum": ["always", "remediation"]},
        "remediation": template,
    }, [
        "key", "kind", "responsibility", "execution_mode", "owner", "contributions",
        "verifies", "mutation_domains", "requires", "provides", "dependencies", "work",
        "acceptance", "checks", "activation",
    ])
    expression = _closed({
        "outcome": _text("Exact current semantic outcome name."),
        "all_of": _array(_text(key=True)),
        "non_execution": _text("Reason this is a decision or constraint rather than an executable outcome."),
    }, ["outcome", "all_of"])
    budgets = _closed({
        name: {"type": "integer", "minimum": 1, "maximum": MAX_GENERATIONS}
        for name in ("planning", "additional_evidence", "reconciliation", "recovery")
    })
    return _closed({
        "nodes": _array(node, minimum=1),
        "outcomes": _array(expression, minimum=1),
        "fingerprint_method": {"type": "string", "enum": ["git_content_v1", "path_manifest_v1"]},
        "artifact_paths": _array(_text(), minimum=1),
        "budgets": budgets,
    })


def _validate_shape(value: Any, schema: Mapping[str, Any]) -> None:
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate_shape(value, branch)
                return
            except GraphError:
                pass
        raise GraphError("shape_invalid")
    kind = schema.get("type")
    if kind == "null":
        if value is not None:
            raise GraphError("shape_invalid")
    elif kind == "object":
        if not isinstance(value, Mapping):
            raise GraphError("shape_invalid")
        props = schema["properties"]
        if set(value) - set(props) or set(schema["required"]) - set(value):
            raise GraphError("shape_invalid")
        for key, item in value.items():
            _validate_shape(item, props[key])
    elif kind == "array":
        if not isinstance(value, list) or not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise GraphError("array_bound_invalid")
        serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
        if schema.get("uniqueItems") and len(set(serialized)) != len(value):
            raise GraphError("duplicate_item")
        for item in value:
            _validate_shape(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or not value.strip():
            raise GraphError("text_invalid")
        if len(value) > schema.get("maxLength", 2048) or ("pattern" in schema and not re.fullmatch(schema["pattern"], value)):
            raise GraphError("text_bound_invalid")
    elif kind == "integer":
        if type(value) is not int or not schema["minimum"] <= value <= schema["maximum"]:
            raise GraphError("budget_invalid")
    elif kind == "boolean" and type(value) is not bool:
        raise GraphError("shape_invalid")
    if "enum" in schema and value not in schema["enum"]:
        raise GraphError("enum_invalid")


def publication_kind(node: Mapping[str, Any]) -> str:
    return "plan" if node["kind"] == "planning" else "documentation" if node["kind"] == "documentation" else "result"


@dataclass(frozen=True)
class ValidatedGraph:
    canonical_json: str
    digest: str
    order: tuple[str, ...]

    def data(self) -> dict[str, Any]:
        # Callers receive a copy, never mutable authority owned by this value.
        return json.loads(self.canonical_json)


def validate_graph(value: Mapping[str, Any], outcomes: Sequence[str]) -> ValidatedGraph:
    _validate_shape(value, graph_schema())
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(canonical.encode("utf-8")) > MAX_GRAPH_BYTES:
        raise GraphError("graph_byte_bound")
    nodes = {node["key"]: node for node in value["nodes"]}
    if len(nodes) != len(value["nodes"]):
        raise GraphError("duplicate_node")
    if sum(len(node["dependencies"]) for node in nodes.values()) > MAX_EDGES:
        raise GraphError("edge_bound")
    producers: dict[str, str] = {}
    def bounded_path(path: str) -> None:
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or ".git" in parsed.parts or "\x00" in path or str(parsed) != path:
            raise GraphError("artifact_boundary_invalid")
    for path in value["artifact_paths"]:
        bounded_path(path)
    for key, node in nodes.items():
        if key in {"baseline", "baseline-candidate", "validate-candidate"} or re.fullmatch(r"(?:plan|discovery|reconcile)-[0-9]+", key) or key.startswith(("repair-", "regression-", "classify-", "strategy-")):
            raise GraphError("reserved_bootstrap_key", node=key)
        for path in node["mutation_domains"]:
            bounded_path(path)
        if node["kind"] in {"planning", "graph_validation"}:
            raise GraphError("bootstrap_node_in_candidate", node=key)
        if node["responsibility"] == "planning":
            raise GraphError("node_responsibility_invalid", node=key)
        if node["execution_mode"] == "mutating":
            if node["responsibility"] != "delivery" or not node["mutation_domains"]:
                raise GraphError("mutation_boundary_missing", node=key)
        elif node["mutation_domains"]:
            raise GraphError("read_only_mutation_claim", node=key)
        if node["kind"] in {"audit", "verification", "discovery"} and node["responsibility"] != "evidence":
            raise GraphError("node_responsibility_invalid", node=key)
        if node["kind"] in {"audit", "verification", "discovery"} and node["execution_mode"] == "mutating":
            raise GraphError("audit_mutation_forbidden", node=key)
        if node["kind"] in {"audit", "verification", "remediation", "documentation"}:
            if node["execution_mode"] == "artifact_independent" or "artifact" not in node["requires"]:
                raise GraphError("artifact_dependency_missing", node=key)
        if node["execution_mode"] == "artifact_independent" and "artifact" in node["requires"]:
            raise GraphError("artifact_independence_false", node=key)
        if node["activation"] == "remediation" and node["kind"] != "remediation":
            raise GraphError("activation_invalid", node=key)
        if node["activation"] == "remediation":
            raise GraphError("remediation_must_use_template", node=key)
        if len({check["key"] for check in node["checks"]}) != len(node["checks"]):
            raise GraphError("duplicate_check", node=key)
        if not node["contributions"] and not node["verifies"]:
            raise GraphError("node_scope_empty", node=key)
        if node["kind"] in {"implementation", "audit", "remediation", "verification"} and "remediation" not in node:
            raise GraphError("remediation_policy_missing", node=key)
        if "remediation" in node:
            policy = node["remediation"]
            strategies = policy["strategies"]
            if (len(strategies) != policy["strategy_budget"]
                    or len({item["key"] for item in strategies}) != len(strategies)
                    or any(item["key"] == "unavailable" for item in strategies)):
                raise GraphError("strategy_menu_invalid", node=key)
            if 1 + sum(len(item["diagnostic_checks"]) for item in strategies[1:]) > MAX_CHECKS:
                raise GraphError("strategy_diagnostic_bound", node=key)
            for strategy in strategies:
                checks = strategy["diagnostic_checks"]
                if (len({item["key"] for item in checks}) != len(checks)
                        or not any(item["required"] for item in checks)):
                    raise GraphError("strategy_diagnostic_invalid", node=key)
            for path in policy["mutation_domains"]:
                bounded_path(path)
            if not set(policy["restores"]).issubset(node["provides"]):
                raise GraphError("remediation_capability_expansion", node=key)
            if not any(check["required"] for check in policy["regression_checks"]):
                raise GraphError("regression_check_missing", node=key)
            if len({check["key"] for check in [*node["checks"], *policy["regression_checks"]]}) > MAX_CHECKS:
                raise GraphError("regression_check_bound", node=key)
        for contribution in node["contributions"]:
            if contribution in producers:
                raise GraphError("duplicate_contribution_owner", node=key)
            producers[contribution] = key
    known_outcomes = set(outcomes)
    expressions = {item["outcome"]: item for item in value["outcomes"]}
    if len(expressions) != len(value["outcomes"]) or set(expressions) != known_outcomes:
        raise GraphError("outcome_coverage_invalid")
    used_contributions: set[str] = set()
    for expression in expressions.values():
        if bool(expression["all_of"]) == bool(expression.get("non_execution")):
            raise GraphError("outcome_expression_invalid")
        if not set(expression["all_of"]).issubset(producers):
            raise GraphError("outcome_contribution_missing")
        used_contributions.update(expression["all_of"])
    if set(producers) != used_contributions:
        raise GraphError("orphan_contribution")
    for key, node in nodes.items():
        for subject in node["verifies"]:
            if subject["kind"] == "outcome":
                if subject["name"] not in expressions:
                    raise GraphError("verification_subject_missing", node=key)
                owners = {producers[item] for item in expressions[subject["name"]]["all_of"]}
            else:
                if subject["name"] not in producers:
                    raise GraphError("verification_subject_missing", node=key)
                owners = {producers[subject["name"]]}
            if key in owners:
                raise GraphError("verification_not_independent", node=key)
        dependencies = node["dependencies"]
        if len({edge["node"] for edge in dependencies}) != len(dependencies):
            raise GraphError("duplicate_dependency", node=key)
        supplied: list[str] = []
        for edge in dependencies:
            parent = nodes.get(edge["node"])
            if parent is None or edge["node"] == key:
                raise GraphError("dependency_invalid", node=key)
            if not set(edge["capabilities"]).issubset(parent["provides"]):
                raise GraphError("capability_provider_invalid", node=key)
            if not set(edge["capabilities"]).issubset(node["requires"]):
                raise GraphError("ordering_only_edge", node=key)
            supplied.extend(edge["capabilities"])
        if len(supplied) != len(set(supplied)) or set(supplied) != set(node["requires"]):
            raise GraphError("capability_provider_ambiguous_or_missing", node=key)
    # Kahn ordering is deterministic and permits forward declarations.
    remaining = set(nodes)
    order: list[str] = []
    while remaining:
        ready = sorted(key for key in remaining if all(edge["node"] in order for edge in nodes[key]["dependencies"]))
        if not ready:
            raise GraphError("dependency_cycle")
        order.extend(ready)
        remaining.difference_update(ready)
    ancestors: dict[str, set[str]] = {}
    for key in order:
        ancestors[key] = set()
        for edge in nodes[key]["dependencies"]:
            ancestors[key].add(edge["node"])
            ancestors[key].update(ancestors[edge["node"]])
        for subject in nodes[key]["verifies"]:
            owners = (
                {producers[item] for item in expressions[subject["name"]]["all_of"]}
                if subject["kind"] == "outcome" else {producers[subject["name"]]}
            )
            if not owners.issubset(ancestors[key]):
                raise GraphError("verification_dependency_missing", node=key)
    return ValidatedGraph(canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest(), tuple(order))


def dependency_satisfied(state: str, *, optional: bool = False, allow_not_applicable: bool = False,
                         has_not_run: bool = False, has_failed: bool = False) -> bool:
    if state == "skipped":
        return optional
    return state in {"complete", "resolved"} and not has_failed and (not has_not_run or allow_not_applicable)


def project_readiness(graph: ValidatedGraph, observations: Mapping[str, Mapping[str, Any]], *,
                      activated: bool, approved: bool, review_required: bool,
                      current_generation: str | None, reconciliation_required: bool = False) -> list[dict[str, Any]]:
    """Compute node facts. Capacity is intentionally not an input."""
    value = graph.data()
    nodes = {node["key"]: node for node in value["nodes"]}
    result: list[dict[str, Any]] = []
    projected_states: dict[str, str] = {}
    for key in graph.order:
        node = nodes[key]
        observed = observations.get(key, {})
        state = observed.get("state")
        reasons: list[dict[str, str]] = []
        if state is not None and state not in NODE_STATES:
            raise GraphError("node_state_invalid", node=key)
        if state in {"active", "partial", "failed", "blocked", "exhausted", "stale", "skipped"}:
            projected = state
        else:
            if not activated:
                reasons.append({"kind": "candidate_inactive"})
            if review_required and not approved:
                reasons.append({"kind": "approval_required"})
            if reconciliation_required:
                reasons.append({"kind": "reconciliation_required"})
            if node["execution_mode"] != "artifact_independent" and current_generation is None:
                reasons.append({"kind": "artifact_generation_missing"})
            for edge in node["dependencies"]:
                parent = observations.get(edge["node"], {})
                if not dependency_satisfied(
                    projected_states.get(edge["node"], "waiting"), optional=edge["optional"],
                    allow_not_applicable=edge["allow_not_applicable"],
                    has_not_run=bool(parent.get("has_not_run")), has_failed=bool(parent.get("has_failed")),
                ):
                    reasons.append({"kind": "predecessor_unsatisfied", "node": edge["node"]})
            if state in {"complete", "resolved"}:
                # Producer contributions survive successor generations;
                # generation-bound read-only evidence does not.
                generation_matches = node["execution_mode"] != "read_only" or observed.get("artifact_generation") == current_generation
                if not generation_matches:
                    reasons.append({"kind": "artifact_generation_stale"})
                if observed.get("has_failed"):
                    reasons.append({"kind": "failed_verification_fact"})
                # Historical evidence does not satisfy dependants, but a
                # repeat check is ready when its current prerequisites hold.
                # Leaving it waiting solely on its own stale evidence would
                # create a graph dead end with no admissible verifier.
                if not generation_matches and all(reason["kind"] == "artifact_generation_stale" for reason in reasons):
                    projected = "ready"
                else:
                    projected = state if not reasons else "waiting"
            else:
                projected = "waiting" if reasons else "ready"
        projected_states[key] = str(projected)
        result.append({
            "node": key, "state": projected, "reasons": reasons,
            "contributions": node["contributions"], "verifies": node["verifies"],
            "artifact_generation": current_generation if node["execution_mode"] != "artifact_independent" else None,
        })
    return result


def validate_coverage(node: Mapping[str, Any], coverage: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate exactly the assigned contribution/verifier subjects and checks.

    Plans do not use this function: their checks are expectations, not facts.
    No string-to-fact coercion or disposition-derived state is permitted.
    """
    if publication_kind(node) == "plan":
        raise GraphError("plan_observed_coverage_forbidden")
    subjects = {("contribution", name) for name in node["contributions"]}
    subjects.update((subject["kind"], subject["name"]) for subject in node["verifies"])
    checks = {check["key"]: check for check in node["checks"]}
    observed: set[tuple[str, str]] = set()
    facts: list[dict[str, Any]] = []
    for row in coverage:
        if not isinstance(row, Mapping) or set(row) != {"kind", "name", "status", "verification"}:
            raise GraphError("coverage_shape_invalid")
        if not isinstance(row["kind"], str) or not isinstance(row["name"], str):
            raise GraphError("coverage_subject_invalid")
        subject = (row["kind"], row["name"])
        if subject not in subjects or subject in observed:
            raise GraphError("coverage_subject_invalid")
        observed.add(subject)
        if row["status"] not in {"complete", "partial", "unverified", "blocked", "failed"}:
            raise GraphError("coverage_disposition_invalid")
        verification = row["verification"]
        if not isinstance(verification, list) or not verification or len(verification) > len(checks):
            raise GraphError("coverage_facts_missing")
        seen_checks: set[str] = set()
        for fact in verification:
            if not isinstance(fact, Mapping) or not {"check_key", "state", "summary"}.issubset(fact):
                raise GraphError("verification_fact_invalid")
            if set(fact) - {"check_key", "state", "summary", "classification", "not_applicable", "classification_assessment", "strategy_assessment"}:
                raise GraphError("verification_fact_invalid")
            if not isinstance(fact["check_key"], str) or fact["check_key"] not in checks or fact["check_key"] in seen_checks:
                raise GraphError("verification_check_invalid")
            seen_checks.add(fact["check_key"])
            if not isinstance(fact["state"], str) or fact["state"] not in {"executed", "not_run", "failed"} or not isinstance(fact["summary"], str) or not fact["summary"].strip() or len(fact["summary"]) > 2048:
                raise GraphError("verification_fact_invalid")
            if fact["state"] == "failed":
                if fact.get("classification") not in CLASSIFICATIONS:
                    raise GraphError("finding_classification_missing")
                if row["status"] == "complete":
                    raise GraphError("complete_with_failed_fact")
            elif fact["state"] == "not_run" and fact.get("not_applicable") is not True:
                if fact.get("classification") not in CLASSIFICATIONS:
                    raise GraphError("finding_classification_missing")
            elif "classification" in fact:
                raise GraphError("classification_without_finding")
            if "not_applicable" in fact and (fact["not_applicable"] is not True or fact["state"] != "not_run" or checks[fact["check_key"]]["required"]):
                raise GraphError("non_applicability_not_authorized")
            assessment_required = (checks[fact["check_key"]].get("classification_review") is True
                and {"kind": row["kind"], "name": row["name"]} in checks[fact["check_key"]].get("classification_subjects", [])
                and fact["state"] == "executed")
            if assessment_required:
                if fact.get("classification_assessment") not in CLASSIFICATIONS:
                    raise GraphError("classification_assessment_required")
            elif "classification_assessment" in fact:
                raise GraphError("classification_assessment_not_permitted")
            strategy_options = checks[fact["check_key"]].get("strategy_options")
            if strategy_options is not None and fact["state"] == "executed":
                if fact.get("strategy_assessment") not in [*strategy_options, "unavailable"]:
                    raise GraphError("strategy_assessment_required")
            elif "strategy_assessment" in fact:
                raise GraphError("strategy_assessment_not_permitted")
            if fact["state"] == "not_run" and row["status"] == "complete":
                if checks[fact["check_key"]]["required"] or fact.get("not_applicable") is not True:
                    raise GraphError("complete_with_missing_check")
            facts.append({**fact, "subject": {"kind": row["kind"], "name": row["name"]}})
        if seen_checks != set(checks):
            raise GraphError("verification_check_coverage_incomplete")
        has_failure = any(fact["state"] == "failed" for fact in verification)
        has_missing = any(fact["state"] == "not_run" and fact.get("not_applicable") is not True for fact in verification)
        if row["status"] == "failed" and not has_failure:
            raise GraphError("failed_without_failed_fact")
        if row["status"] != "complete" and not has_failure and not has_missing:
            raise GraphError("incomplete_without_finding")
    if observed != subjects:
        raise GraphError("coverage_incomplete")
    return facts


def assignment_compatible(nodes: Sequence[Mapping[str, Any]], active_modes: Sequence[str]) -> None:
    if not nodes:
        raise GraphError("assignment_nodes_empty")
    if len({publication_kind(node) for node in nodes}) != 1:
        raise GraphError("assignment_publication_kind_conflict")
    modes = {node["execution_mode"] for node in nodes}
    if len(modes) != 1:
        raise GraphError("assignment_execution_mode_conflict")
    mode = next(iter(modes))
    if mode == "mutating" and any(active in {"mutating", "read_only"} for active in active_modes):
        raise GraphError("project_mutation_barrier")
    if mode == "read_only" and "mutating" in active_modes:
        raise GraphError("project_mutation_barrier")
