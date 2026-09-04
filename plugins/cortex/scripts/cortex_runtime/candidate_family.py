"""Immutable structural validation of proposed plans, never execution authority.

Each alternative is validated against its own complete proposed contract and
the same base revision. Independent evidence and a user decision are separate
ledger transitions; successful validation here grants neither.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from cortex_runtime.execution_graph import (
    GraphError, _array, _closed, _text, _validate_shape, graph_schema, validate_graph,
)
from cortex_runtime.public_contracts import semantic_outcome_schema
from cortex_runtime.v12_contract import MCP_OPERATION_MAX_BYTES, TASK_CONTRACT_MAX_ITEMS

MAX_ALTERNATIVES = 4


def candidates_schema() -> dict[str, Any]:
    result = _array(_closed({
        "key": _text("Unique semantic branch key for exact user selection; never infer a choice from prose.", key=True),
        "consequences": _array(_text("Material user-visible consequence of this alternative."), minimum=1, maximum=8),
        "delta": _closed({
            "add": _array(semantic_outcome_schema(), maximum=TASK_CONTRACT_MAX_ITEMS),
            "retire": _array(_text("Exact outcome name in the common base contract."), maximum=TASK_CONTRACT_MAX_ITEMS),
        }),
        "graph": graph_schema(),
    }), minimum=1, maximum=MAX_ALTERNATIVES)
    result["description"] = "Complete alternatives, each validated against its own proposed contract. Ordinary planning supplies exactly one candidate with an empty delta. A nonempty delta or multiple alternatives requires independent validation of every branch and one exact user selection; publication alone grants no branch authority."
    return result


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def proposed_contract(base: Sequence[Mapping[str, Any]], delta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Apply a complete proposed replacement without mutating the base."""
    _validate_shape(list(base), _array(semantic_outcome_schema(), minimum=1, maximum=TASK_CONTRACT_MAX_ITEMS))
    _validate_shape(delta, candidates_schema()["items"]["properties"]["delta"])
    names = [item["outcome"] for item in base]
    added = [item["outcome"] for item in delta["add"]]
    retired = delta["retire"]
    if len(set(names)) != len(names) or len(set(added)) != len(added):
        raise GraphError("candidate_outcome_ambiguous")
    if set(retired) - set(names):
        raise GraphError("candidate_retired_outcome_unknown")
    if set(added) & (set(names) - set(retired)):
        raise GraphError("candidate_outcome_collision")
    # Match the current semantic steering transaction: one unambiguous point
    # replacement retains its source position; independent additions append.
    # No field from a retired item is silently merged.
    if len(retired) == 1 and len(delta["add"]) == 1:
        result = [delta["add"][0] if item["outcome"] == retired[0] else item for item in base]
    else:
        result = [item for item in base if item["outcome"] not in retired] + list(delta["add"])
    if not result or len(result) > TASK_CONTRACT_MAX_ITEMS:
        raise GraphError("candidate_outcome_bound")
    return json.loads(_canonical(result))


@dataclass(frozen=True)
class ValidatedFamily:
    """Canonical snapshots are copied on read, never mutable authority."""
    canonical: str
    digest: str

    def data(self) -> dict[str, Any]:
        return json.loads(self.canonical)

    def select(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str):
            raise GraphError("candidate_selection_unknown")
        for candidate in self.data()["candidates"]:
            if candidate["definition"]["key"] == key:
                return candidate
        raise GraphError("candidate_selection_unknown")


def validate_family(candidates: Sequence[Mapping[str, Any]], base: Sequence[Mapping[str, Any]], *, revision: int) -> ValidatedFamily:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise GraphError("candidate_base_revision_invalid")
    _validate_shape(candidates, candidates_schema())
    if len(_canonical(candidates).encode("utf-8")) > MCP_OPERATION_MAX_BYTES:
        raise GraphError("candidate_family_byte_bound")
    keys = [candidate["key"] for candidate in candidates]
    if len(set(keys)) != len(keys):
        raise GraphError("candidate_key_ambiguous")
    snapshots = []
    semantic_variants = set()
    for candidate in candidates:
        contract = proposed_contract(base, candidate["delta"])
        graph = validate_graph(candidate["graph"], [item["outcome"] for item in contract])
        variant = _digest({"contract": contract, "graph": graph.data()})
        if variant in semantic_variants:
            raise GraphError("candidate_alternatives_equivalent")
        semantic_variants.add(variant)
        snapshots.append({"definition": candidate, "contract": contract,
                          "delta_digest": _digest(candidate["delta"]), "graph_digest": graph.digest})
    content = {"base_revision": revision, "base_contract_digest": _digest(base), "candidates": snapshots}
    canonical = _canonical(content)
    return ValidatedFamily(canonical, _digest(content))


def current_contract(connection, task_id: str) -> list[dict[str, Any]]:
    """Read canonical semantic rows, never historical task-text fallbacks."""
    result = []
    for row in connection.execute(
        "SELECT i.text,d.details_json FROM effective_contract_items i "
        "LEFT JOIN effective_contract_item_details d ON d.item_id=i.item_id "
        "WHERE i.task_id=? AND i.retired_revision IS NULL ORDER BY i.ordinal", (task_id,),
    ):
        if row["details_json"] is None:
            raise GraphError("candidate_base_contract_corrupted")
        details = json.loads(row["details_json"])
        result.append({"outcome": row["text"], "acceptance": details["acceptance_criteria"],
                       "constraints": details["constraints"], "verification": details["verification_criteria"]})
    return result


def read_family(connection, graph_id: str) -> ValidatedFamily | None:
    row = connection.execute("SELECT content_digest,content_json FROM plan_candidate_families WHERE graph_id=?", (graph_id,)).fetchone()
    if row is None:
        return None
    if "sha256:" + hashlib.sha256(row["content_json"].encode("utf-8")).hexdigest() != row["content_digest"]:
        raise GraphError("candidate_family_corrupted")
    return ValidatedFamily(row["content_json"], row["content_digest"])


def create_family(connection, *, task_id: str, plan_report_id: str,
                  planner_assignment_id: str, candidates: Sequence[Mapping[str, Any]]) -> str:
    """Store all alternatives without installing any branch's execution nodes.

    The holding graph is system-owned validation intent, not a chosen plan.
    Even complete validation only makes it decision-ready; no alternative has
    execution authority until an atomic selection transition creates its graph.
    """
    from cortex_runtime import graph_ledger as ledger
    ledger._transaction(connection)
    revision = ledger._current_revision(connection, task_id)
    base = current_contract(connection, task_id)
    family = validate_family(candidates, base, revision=revision)
    if len(candidates) == 1 and not candidates[0]["delta"]["add"] and not candidates[0]["delta"]["retire"]:
        raise GraphError("candidate_family_decision_not_required")
    graph_id = ledger._digest([task_id, revision, plan_report_id])
    existing = read_family(connection, graph_id)
    if existing is not None:
        owner = connection.execute("SELECT planner_assignment_id FROM execution_graphs WHERE graph_id=?", (graph_id,)).fetchone()
        if existing != family or owner[0] != planner_assignment_id:
            raise GraphError("graph_publication_conflict")
        return graph_id
    target = connection.execute("SELECT method,paths_json FROM artifact_generations WHERE generation_key=(SELECT generation_key FROM project_integrity)").fetchone()
    if target is None:
        raise GraphError("artifact_generation_missing")
    bootstrap = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='bootstrap'", (task_id, revision)).fetchone()
    if bootstrap is None:
        raise GraphError("planning_outside_bootstrap")
    holding = {"nodes": [], "outcomes": [{"outcome": item["outcome"], "all_of": [],
        "non_execution": "Unselected alternatives grant no execution or completion authority."} for item in base],
        "fingerprint_method": target["method"], "artifact_paths": json.loads(target["paths_json"]),
        "budgets": ledger._graph(connection, bootstrap[0])[1].data()["budgets"]}
    if connection.execute("SELECT 1 FROM execution_graphs WHERE graph_id=?", (graph_id,)).fetchone():
        raise GraphError("graph_publication_conflict")
    if connection.execute("SELECT 1 FROM execution_assignments a JOIN execution_graphs g ON g.graph_id=a.graph_id WHERE a.task_id=? AND a.revision=? AND a.state='active' AND g.graph_kind!='bootstrap' LIMIT 1", (task_id, revision)).fetchone():
        raise GraphError("execution_evidence_pending")
    # This empty system-owned holding topology is not an authored graph. Every
    # actual alternative has already passed the unchanged strict graph schema.
    connection.execute("INSERT INTO execution_graphs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (graph_id, "candidate", task_id, revision, plan_report_id, planner_assignment_id,
         ledger._digest(holding), ledger._json(holding), "[]", "candidate", 1, 0))
    ledger._create_validation_nodes(connection, graph_id=graph_id, graph=holding,
                                    outcomes=[item["outcome"] for item in base])
    connection.execute("INSERT INTO plan_candidate_families VALUES (?,?,?)", (graph_id, family.digest, family.canonical))
    validation = json.loads(connection.execute("SELECT content_json FROM execution_nodes WHERE graph_id=? AND node_key='validate-candidate'", (graph_id,)).fetchone()[0])
    criteria = validation["checks"]
    validation["checks"] = [{**check, "key": f"branch-{position}-{check['key']}",
        "description": f"Alternative {candidate['key']}: {check['description']} Use its complete proposed contract, not another alternative's contract."}
        for position, candidate in enumerate(candidates, 1) for check in criteria]
    validation["work"] = ["Independently validate every alternative against its complete proposed contract and the same base revision. No alternative is selected by this assessment."]
    connection.execute("UPDATE execution_nodes SET content_json=? WHERE graph_id=? AND node_key='validate-candidate'",
                       (ledger._json(validation), graph_id))
    ledger._event(connection, graph_id, "candidate_family_created", {"alternatives": [item["key"] for item in candidates], "digest": family.digest})
    return graph_id


def selection_evidence(connection, *, graph_id: str, branch_key: str) -> dict[str, Any]:
    """Resolve one exact validated option, without applying its semantic delta."""
    from cortex_runtime import graph_ledger as ledger
    record, _ = ledger._graph(connection, graph_id)
    family = read_family(connection, graph_id)
    if family is None:
        raise GraphError("candidate_family_missing")
    selected = family.select(branch_key)
    if record["revision"] != ledger._current_revision(connection, record["task_id"]):
        raise GraphError("graph_revision_stale")
    if family.data()["base_contract_digest"] != _digest(current_contract(connection, record["task_id"])):
        raise GraphError("candidate_base_contract_changed")
    latest = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap' ORDER BY rowid DESC LIMIT 1", (record["task_id"], record["revision"])).fetchone()
    validation = connection.execute("SELECT state,assignment_id,artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key='validate-candidate'", (graph_id,)).fetchone()
    integrity = connection.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone()
    if latest[0] != graph_id or record["activation"] != "validated" or validation["state"] != "complete":
        raise GraphError("candidate_family_not_validated")
    if integrity["reconciliation_required"] or integrity["generation_key"] != validation["artifact_generation"]:
        raise GraphError("candidate_family_evidence_stale")
    if connection.execute("SELECT 1 FROM execution_assignments WHERE task_id=? AND (state='active' OR (state IN ('stale','lost','snapshot_conflict') AND quiescent=0))", (record["task_id"],)).fetchone():
        raise GraphError("execution_evidence_pending")
    return {"selected": selected, "family_digest": family.digest,
            "validation_assignment_id": validation["assignment_id"], "artifact_generation": validation["artifact_generation"]}


def activate_selected(connection, *, family_graph_id: str, branch_key: str, decision_id: str) -> str:
    """Finish the same user-decision transaction after its contract revision.

    Resolution points to original independent evidence; it never copies facts,
    invents a worker publication or authorizes an unselected alternative.
    """
    from cortex_runtime import graph_ledger as ledger
    ledger._transaction(connection)
    family = read_family(connection, family_graph_id)
    if family is None:
        raise GraphError("candidate_family_missing")
    selected = family.select(branch_key)
    source, _ = ledger._graph(connection, family_graph_id)
    task = source["task_id"]
    revision = ledger._current_revision(connection, task)
    if revision != family.data()["base_revision"] + 1 or current_contract(connection, task) != selected["contract"]:
        raise GraphError("candidate_selection_contract_mismatch")
    decision = connection.execute("SELECT subject_type,subject_id,decision_type FROM user_decisions WHERE task_id=? AND decision_id=?", (task, decision_id)).fetchone()
    if decision is None or tuple(decision) != ("plan", source["plan_report_id"], "approve"):
        raise GraphError("candidate_selection_decision_missing")
    validation = connection.execute("SELECT state,assignment_id,artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key='validate-candidate'", (family_graph_id,)).fetchone()
    integrity = connection.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone()
    if validation["state"] != "complete" or integrity["reconciliation_required"] or integrity["generation_key"] != validation["artifact_generation"]:
        raise GraphError("candidate_family_evidence_stale")
    graph_id = ledger.create_candidate(connection, task_id=task, revision=revision,
        plan_report_id=source["plan_report_id"], planner_assignment_id=source["planner_assignment_id"],
        graph=selected["definition"]["graph"], outcomes=[item["outcome"] for item in selected["contract"]],
        review_required=True)
    connection.execute("INSERT INTO plan_candidate_selections VALUES (?,?,?,?)",
        (graph_id, family_graph_id, branch_key, validation["assignment_id"]))
    connection.execute("UPDATE execution_graphs SET activation='active',approved=1 WHERE graph_id=?", (graph_id,))
    connection.execute("UPDATE execution_nodes SET state='resolved',artifact_generation=? WHERE graph_id=? AND node_key='validate-candidate'",
        (validation["artifact_generation"], graph_id))
    bootstrap = ledger.ensure_bootstrap(connection, task_id=task, outcomes=[item["outcome"] for item in selected["contract"]])
    connection.execute("UPDATE execution_nodes SET state='resolved',artifact_generation=? WHERE graph_id=? AND node_key='baseline'",
        (validation["artifact_generation"], bootstrap))
    ledger._event(connection, graph_id, "validated_alternative_selected", {
        "family": family_graph_id, "branch": branch_key, "decision": decision_id,
        "validation_assignment": validation["assignment_id"], "family_digest": family.digest})
    return graph_id
