"""Append-only conditional repair evidence, never a scheduler.

The baseline graph remains immutable. Generated work derives only from an
independently validated policy and an exact finalized finding. Classification
requiring a decision or independent confirmation never authorizes a repair.
"""
from __future__ import annotations

import json
from typing import Any

from cortex_runtime.execution_graph import GraphError, MAX_NODES


def chains(connection, graph_id: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='remediation_created' ORDER BY sequence", (graph_id,))]


def reviews(connection, graph_id: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='classification_review_created' ORDER BY sequence", (graph_id,))]


def strategy_reviews(connection, graph_id: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='strategy_review_created' ORDER BY sequence", (graph_id,))]


def _strategy_exhausted(connection, graph_id, source, report, reason):
    from cortex_runtime import graph_ledger as ledger
    ledger._event(connection, graph_id, "strategy_exhausted", {
        "source_node": source["key"], "report": report, "reason": reason})


def _strategy_review(connection, *, assignment, source, report_id, chain, selected=None,
                     diagnostic_report=None):
    """One bounded diagnosis and one independent validation, not a retry loop."""
    from cortex_runtime import graph_ledger as ledger
    history = [item for item in chains(connection, assignment["graph_id"])
               if item["source_node"] == source["key"]]
    used = {item["strategy_key"] for item in history}
    offered = [item for item in source["remediation"]["strategies"] if item["key"] not in used
               and (selected is None or item["key"] == selected)]
    if (chain["strategy"] >= source["remediation"]["strategy_budget"] or not offered
            or connection.execute("SELECT COUNT(*) FROM execution_nodes WHERE graph_id=?",
                                  (assignment["graph_id"],)).fetchone()[0] >= MAX_NODES):
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "strategy_or_node_budget")
        return
    phase = "validation" if selected is not None else "diagnostic"
    suffix = ledger._digest([chain["repair"], phase])[:16]
    key = "strategy-" + suffix
    if any(item["node"] == key for item in strategy_reviews(connection, assignment["graph_id"])):
        return
    checks = [{"key": "strategy-selection", "required": True,
               "description": "Classify whether one offered strategy is materially different, causally supported and wholly authorized; otherwise unavailable.",
               "strategy_options": [item["key"] for item in offered]}]
    for strategy in offered:
        checks.extend({**check, "key": "cause-" + ledger._digest([strategy["key"], check["key"]])[:16],
                       "description": strategy["key"] + ": " + check["description"]}
                      for check in strategy["diagnostic_checks"])
    node = {"key": key, "kind": "discovery", "responsibility": "evidence", "execution_mode": "read_only",
        "owner": "Independent strategy validation" if selected else "Bounded strategy diagnosis",
        "contributions": [], "verifies": [{"kind": "contribution", "name": name} for name in source["contributions"]] + source["verifies"],
        "mutation_domains": [], "requires": [], "provides": [], "dependencies": [],
        "work": ["Inspect the failed regression and original contract. Execute the causal checks for the offered preauthorized approaches. Select only a materially different causally justified approach; do not edit files or invent authority.",
                 *(["Independently verify the diagnostic conclusion; disagreement or insufficient evidence cannot authorize another repair."] if selected else [])],
        "acceptance": source["acceptance"], "checks": checks, "activation": "always",
        "strategy_options": offered}
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
                       (assignment["graph_id"], key, ledger._json(node)))
    ledger._event(connection, assignment["graph_id"], "strategy_review_created", {
        "node": key, "phase": phase, "source_node": source["key"], "source_assignment": assignment["assignment_id"],
        "source_report": report_id, "chain": chain, "selected": selected, "diagnostic_report": diagnostic_report})


def _expand_strategy_result(connection, *, assignment, report_id, review, source):
    from cortex_runtime import graph_ledger as ledger
    row = connection.execute("SELECT state,facts_json,artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key=?",
                             (assignment["graph_id"], review["node"])).fetchone()
    choices = {fact["strategy_assessment"] for fact in json.loads(row["facts_json"])
               if "strategy_assessment" in fact}
    if row["state"] != "complete" or len(choices) != 1 or "unavailable" in choices:
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "strategy_unconfirmed")
        return
    selected = choices.pop()
    chain = review["chain"]
    if review["phase"] == "diagnostic":
        _strategy_review(connection, assignment=assignment, source=source, report_id=review["source_report"],
                         chain=chain, selected=selected, diagnostic_report=report_id)
        return
    if selected != review["selected"]:
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "strategy_disagreement")
        return
    current = connection.execute("SELECT fingerprint FROM artifact_generations WHERE generation_key=?",
                                 (row["artifact_generation"],)).fetchone()
    # Validation is evidence about a real preceding mutation, not permission
    # to count a renamed strategy or prose-only publication as progress.
    if not current or current[0] == chain["source_fingerprint"]:
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "artifact_non_progress")
        return
    regression = connection.execute("SELECT facts_json FROM execution_nodes WHERE graph_id=? AND node_key=?",
                                    (assignment["graph_id"], chain["regression"])).fetchone()
    failed = [fact for fact in json.loads(regression[0]) if fact.get("classification")]
    if {fact["classification"] for fact in failed} != {"defect_within_contract"}:
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "finding_not_authorized")
        return
    progress = ledger._digest([assignment["revision"], assignment["graph_id"], row["artifact_generation"],
                              sorted(_finding_keys(failed)), selected, review["diagnostic_report"], report_id])
    _append_chain(connection, assignment=assignment, report_id=review["source_report"], chain=chain,
        source=source, failed=failed, fingerprint=progress, current_fingerprint=current[0],
        strategy=selected, strategy_reports=[review["diagnostic_report"], report_id])


def _finding_keys(facts):
    return {(fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"])
        for fact in facts if fact.get("classification")}


def _repeat_regression(connection, *, assignment, report_id, chain, source):
    """Append a progressive same-strategy repair; never reopen failed nodes."""
    from cortex_runtime import graph_ledger as ledger
    row = connection.execute("SELECT state,facts_json,artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key=?",
        (assignment["graph_id"], chain["regression"])).fetchone()
    if row["state"] not in {"partial", "blocked", "failed"}:
        return
    facts = json.loads(row["facts_json"])
    failed = [fact for fact in facts if fact.get("classification")]
    resolved_keys = {(fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"])
                     for fact in facts if fact["state"] == "executed"}
    current = connection.execute("SELECT fingerprint FROM artifact_generations WHERE generation_key=?", (row["artifact_generation"],)).fetchone()
    policy = source["remediation"]
    fingerprint = ledger._digest([assignment["revision"], assignment["graph_id"], row["artifact_generation"],
        sorted(_finding_keys(failed)), [(fact["check_key"], fact["state"], fact.get("classification")) for fact in failed], chain["strategy"]])
    reason = None
    if not failed or {fact["classification"] for fact in failed} != {"defect_within_contract"}:
        return
    if chain["generation"] >= policy["generation_budget"]:
        reason = "generation_budget"
    elif not current or current[0] == chain["source_fingerprint"] or not resolved_keys.intersection(_finding_keys(chain["findings"])):
        reason = "non_progress"
    elif any(previous.get("progress") == fingerprint for previous in chains(connection, assignment["graph_id"])):
        reason = "non_progress"
    elif connection.execute("SELECT COUNT(*) FROM execution_nodes WHERE graph_id=?", (assignment["graph_id"],)).fetchone()[0] + 2 > MAX_NODES:
        reason = "node_bound"
    if reason:
        ledger._event(connection, assignment["graph_id"], "remediation_retry_stopped", {
            "source_node": source["key"], "report": report_id, "reason": reason,
            "strategy": chain["strategy"], "generation": chain["generation"],
            "exhausted": chain["strategy"] >= policy["strategy_budget"]})
        if reason != "node_bound" and chain["strategy"] < policy["strategy_budget"]:
            _strategy_review(connection, assignment=assignment, source=source,
                             report_id=report_id, chain=chain)
        return
    _append_chain(connection, assignment=assignment, report_id=report_id, chain=chain,
        source=source, failed=failed, fingerprint=fingerprint, current_fingerprint=current[0])


def _append_chain(connection, *, assignment, report_id, chain, source, failed, fingerprint,
                  current_fingerprint, strategy=None, strategy_reports=None):
    from cortex_runtime import graph_ledger as ledger
    if connection.execute("SELECT COUNT(*) FROM execution_nodes WHERE graph_id=?",
                          (assignment["graph_id"],)).fetchone()[0] + 2 > MAX_NODES:
        _strategy_exhausted(connection, assignment["graph_id"], source, report_id, "node_bound")
        return
    suffix = ledger._digest([assignment["graph_id"], source["key"], assignment["assignment_id"], fingerprint])[:16]
    repair_key, regression_key = "repair-" + suffix, "regression-" + suffix
    nodes = {item["node_key"]: json.loads(item["content_json"]) for item in connection.execute(
        "SELECT node_key,content_json FROM execution_nodes WHERE graph_id=? AND node_key IN (?,?)",
        (assignment["graph_id"], chain["repair"], chain["regression"]))}
    repair, regression = nodes[chain["repair"]], nodes[chain["regression"]]
    repair.update(key=repair_key, provides=[repair_key], work=[
        "Use the immediately preceding failed regression to repair the remaining authorized defects with a materially different evidence-backed approach. Preserve findings already resolved and all original constraints."])
    selected = strategy or chain["strategy_key"]
    repair["work"].extend(next(item["work"] for item in source["remediation"]["strategies"] if item["key"] == selected))
    regression.update(key=regression_key, requires=[], dependencies=[], attempt_predecessor=repair_key)
    for node in (repair, regression):
        connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
            (assignment["graph_id"], node["key"], ledger._json(node)))
    ledger._event(connection, assignment["graph_id"], "remediation_created", {
        **chain, "source_assignment": connection.execute(
            "SELECT assignment_id FROM execution_nodes WHERE graph_id=? AND node_key=?",
            (assignment["graph_id"], chain["regression"])).fetchone()[0], "source_report": report_id,
        "original_report": chain.get("original_report", chain["source_report"]),
        "predecessor_regression": chain["regression"], "repair": repair_key, "regression": regression_key,
        "generation": 1 if strategy else chain["generation"] + 1, "findings": failed,
        "strategy": chain["strategy"] + int(strategy is not None), "strategy_key": selected,
        "strategy_reports": strategy_reports or chain.get("strategy_reports", []),
        "source_fingerprint": current_fingerprint, "progress": fingerprint})


def _classification_review(connection, *, assignment, source, failures, report_id,
                           predecessor_report: str | None = None, progress: str | None = None) -> None:
    from cortex_runtime import graph_ledger as ledger
    previous = [item for item in reviews(connection, assignment["graph_id"])
        if item["source_assignment"] == assignment["assignment_id"] and item["source_node"] == source["key"]]
    if previous and predecessor_report is None:
        return
    budget = ledger._graph(connection, assignment["graph_id"])[1].data()["budgets"]["additional_evidence"]
    seen = previous[-1]["seen_progress"] if previous else []
    reason = "evidence_budget" if len(previous) >= budget else "non_progress" if progress is not None and progress in seen else None
    if reason is not None:
        ledger._event(connection, assignment["graph_id"], "classification_exhausted", {"source_node": source["key"], "reason": reason, "report": predecessor_report})
        return
    if connection.execute("SELECT COUNT(*) FROM execution_nodes WHERE graph_id=?", (assignment["graph_id"],)).fetchone()[0] >= MAX_NODES:
        ledger._event(connection, assignment["graph_id"], "classification_exhausted", {"source_node": source["key"], "reason": "node_bound"})
        return
    key = "classify-" + ledger._digest([assignment["assignment_id"], source["key"], len(previous) + 1])[:16]
    checks = [{**check, "classification_review": True,
        "classification_subjects": [fact["subject"] for fact in failures if fact["check_key"] == check["key"]],
        "description": "Independently classify the source finding for this check against the unchanged contract."}
        for check in source["checks"] if any(fact["check_key"] == check["key"] for fact in failures)]
    node = {"key": key, "kind": "discovery", "responsibility": "evidence", "execution_mode": "read_only",
        "owner": "Independent finding classification", "contributions": [],
        "verifies": [{"kind": "contribution", "name": name} for name in source["contributions"]] + source["verifies"],
        "mutation_domains": [], "requires": [], "provides": [], "dependencies": [],
        "work": ["Independently classify every source finding. Distinguish authorized repair from product change, missing authority, risk change, or inconclusive evidence. Do not change files."],
        "acceptance": source["acceptance"], "checks": checks, "activation": "always"}
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')", (assignment["graph_id"], key, ledger._json(node)))
    ledger._event(connection, assignment["graph_id"], "classification_review_created", {
        "node": key, "source_node": source["key"], "source_assignment": assignment["assignment_id"],
        "source_report": report_id, "findings": failures, "generation": len(previous) + 1,
        "predecessor_report": predecessor_report, "seen_progress": [*seen, *([progress] if progress else [])]})


def expand(connection, *, assignment_id: str, report_id: str, _classification_evidence: str | None = None) -> None:
    from cortex_runtime import graph_ledger as ledger
    ledger._transaction(connection)
    assignment = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
    record, graph = ledger._graph(connection, assignment["graph_id"])
    if record["graph_kind"] not in {"candidate", "generated"} or record["activation"] != "active":
        return
    definitions = {node["key"]: node for node in graph.data()["nodes"]}
    previous = chains(connection, assignment["graph_id"])
    for review in strategy_reviews(connection, assignment["graph_id"]):
        if review["node"] in json.loads(assignment["nodes_json"]):
            _expand_strategy_result(connection, assignment=assignment, report_id=report_id,
                                    review=review, source=definitions[review["source_node"]])
    for chain in previous:
        if chain["regression"] in json.loads(assignment["nodes_json"]):
            _repeat_regression(connection, assignment=assignment, report_id=report_id,
                chain=chain, source=definitions[chain["source_node"]])
    for review in reviews(connection, assignment["graph_id"]):
        if review["node"] not in json.loads(assignment["nodes_json"]):
            continue
        row = connection.execute("SELECT state,facts_json FROM execution_nodes WHERE graph_id=? AND node_key=?", (assignment["graph_id"], review["node"])).fetchone()
        actual = {(fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"]): fact["classification_assessment"]
            for fact in json.loads(row["facts_json"]) if "classification_assessment" in fact}
        expected = {(fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"]): fact["classification"] for fact in review["findings"]}
        if row["state"] == "complete" and actual == expected and set(actual.values()) == {"defect_within_contract"}:
            expand(connection, assignment_id=review["source_assignment"], report_id=review["source_report"], _classification_evidence=report_id)
        else:
            ledger._event(connection, assignment["graph_id"], "classification_unresolved", {"source_node": review["source_node"], "report": report_id, "agreed": actual == expected})
            original = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (review["source_assignment"],)).fetchone()
            # Fingerprints ignore summary wording, profile, model, and effort.
            # Repeating the same structured observations cannot buy a retry.
            progress = ledger._digest(sorted((fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"],
                fact["state"], fact.get("classification_assessment"), fact.get("classification"))
                for fact in json.loads(row["facts_json"])))
            _classification_review(connection, assignment=original, source=definitions[review["source_node"]],
                failures=review["findings"], report_id=review["source_report"], predecessor_report=report_id, progress=progress)
    for key in json.loads(assignment["nodes_json"]):
        source = definitions.get(key)
        if source is None or "remediation" not in source:
            continue
        row = connection.execute("SELECT state,facts_json FROM execution_nodes WHERE graph_id=? AND node_key=?", (assignment["graph_id"], key)).fetchone()
        if row["state"] not in {"partial", "blocked", "failed"}:
            continue
        facts = json.loads(row["facts_json"])
        failures = [fact for fact in facts if fact.get("classification")]
        if not failures:
            continue
        policy = source["remediation"]
        if policy["classification_verification"] and _classification_evidence is None:
            _classification_review(connection, assignment=assignment, source=source, failures=failures, report_id=report_id)
            continue
        if {fact["classification"] for fact in failures} != {"defect_within_contract"}:
            continue
        if any(chain["source_assignment"] == assignment_id and chain["source_node"] == key for chain in previous):
            continue
        # A source node is never reopened; a later regression finding has its
        # own evidence-linked generation and progress gate.
        if any(chain["source_node"] == key for chain in previous):
            continue
        if connection.execute("SELECT COUNT(*) FROM execution_nodes WHERE graph_id=?", (assignment["graph_id"],)).fetchone()[0] + 2 > MAX_NODES:
            ledger._event(connection, assignment["graph_id"], "remediation_expansion_exhausted", {"source_node": key, "reason": "node_bound"})
            continue
        suffix = ledger._digest([assignment["graph_id"], key, assignment_id])[:16]
        repair_key, regression_key = "repair-" + suffix, "regression-" + suffix
        subjects = [{"kind": "contribution", "name": contribution} for contribution in source["contributions"]] + source["verifies"]
        repair = {
            "key": repair_key, "kind": "remediation", "responsibility": "delivery", "execution_mode": "mutating",
            "owner": "Repair the confirmed in-contract finding", "contributions": [], "verifies": subjects,
            "mutation_domains": policy["mutation_domains"], "requires": [], "provides": ["repair-" + suffix],
            "dependencies": [], "work": ["Repair only the confirmed in-contract defects in the source finding. Preserve all original acceptance and constraints.",
                                           *policy["strategies"][0]["work"]],
            "acceptance": source["acceptance"], "checks": source["checks"], "activation": "remediation",
        }
        regression_checks = {check["key"]: dict(check) for check in [*source["checks"], *policy["regression_checks"]]}
        for check in source["checks"]:
            regression_checks[check["key"]]["required"] |= check["required"]
        regression = {
            "key": regression_key, "kind": "verification", "responsibility": "evidence", "execution_mode": "read_only",
            "owner": "Independent regression verification", "contributions": [], "verifies": subjects,
            "mutation_domains": [], "requires": [], "provides": policy["restores"],
            "dependencies": [], "attempt_predecessor": repair_key,
            "work": ["Independently repeat every source and regression check against the sealed terminal repair attempt, including an incomplete attempt. Do not assume the repair succeeded. Account for every source finding; only complete observed checks can restore the source capabilities."],
            "acceptance": source["acceptance"],
            "checks": list(regression_checks.values()),
            "activation": "remediation",
        }
        for node in (repair, regression):
            if connection.execute("SELECT 1 FROM execution_nodes WHERE graph_id=? AND node_key=?", (assignment["graph_id"], node["key"])).fetchone():
                raise GraphError("generated_node_collision")
            connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')", (assignment["graph_id"], node["key"], ledger._json(node)))
        target = connection.execute("SELECT g.fingerprint FROM execution_publications p JOIN artifact_generations g ON g.generation_key=p.artifact_generation WHERE p.assignment_id=?", (assignment_id,)).fetchone()
        ledger._event(connection, assignment["graph_id"], "remediation_created", {
            "source_node": key, "source_assignment": assignment_id, "source_report": report_id,
            "classification_report": _classification_evidence,
            "repair": repair_key, "regression": regression_key, "generation": 1, "strategy": 1,
            "strategy_key": policy["strategies"][0]["key"],
            "findings": [{"subject": fact["subject"], "check_key": fact["check_key"], "state": fact["state"], "classification": fact["classification"]} for fact in failures],
            "source_fingerprint": target[0] if target else None,
        })


def projections(connection, graph_id: str, *, current_generation: str | None,
                admitted: bool) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return derived resolutions separately from immutable original facts."""
    overrides, generated = {}, []
    for review in [*reviews(connection, graph_id), *strategy_reviews(connection, graph_id)]:
        row = connection.execute("SELECT * FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, review["node"])).fetchone()
        state = row["state"]
        reasons = []
        if state == "waiting":
            if not admitted or current_generation is None:
                reasons.append({"kind": "graph_or_project_not_admitted"})
            else:
                state = "ready"
        node = json.loads(row["content_json"])
        generated.append({"node": review["node"], "state": state, "reasons": reasons,
            "contributions": [], "verifies": node["verifies"], "artifact_generation": current_generation})
    for chain in chains(connection, graph_id):
        rows = {row["node_key"]: dict(row) for row in connection.execute(
            "SELECT * FROM execution_nodes WHERE graph_id=? AND node_key IN (?,?)", (graph_id, chain["repair"], chain["regression"]))}
        repair, regression = rows[chain["repair"]], rows[chain["regression"]]
        for row in (repair, regression):
            node = json.loads(row["content_json"])
            state, reasons = row["state"], []
            if state in {"waiting", "complete"}:
                if not admitted:
                    reasons.append({"kind": "graph_or_project_not_admitted"})
                if current_generation is None:
                    reasons.append({"kind": "artifact_generation_missing"})
                if row is regression and (repair["state"] not in {"complete", "partial", "failed", "blocked"}
                                          or repair["artifact_generation"] is None):
                    reasons.append({"kind": "terminal_attempt_missing", "node": chain["repair"]})
                if row is regression and state == "complete" and row["artifact_generation"] != current_generation:
                    reasons.append({"kind": "artifact_generation_stale"})
                state = "waiting" if reasons else ("complete" if state == "complete" else "ready")
            generated.append({"node": node["key"], "state": state, "reasons": reasons,
                "contributions": [], "verifies": node["verifies"], "artifact_generation": current_generation})
        if admitted and generated[-1]["state"] == "complete":
            overrides[chain["source_node"]] = {"state": "resolved", "artifact_generation": current_generation,
                "has_failed": False, "has_not_run": False}
    for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='classification_exhausted'", (graph_id,)):
        source = json.loads(row[0])["source_node"]
        if source not in overrides:
            overrides[source] = {"state": "exhausted", "has_failed": True, "has_not_run": False}
    for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='strategy_exhausted'", (graph_id,)):
        source = json.loads(row[0])["source_node"]
        if source not in overrides:
            overrides[source] = {"state": "exhausted", "has_failed": True, "has_not_run": False}
    for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='remediation_retry_stopped'", (graph_id,)):
        event = json.loads(row[0])
        if event["exhausted"] and event["source_node"] not in overrides:
            overrides[event["source_node"]] = {"state": "exhausted", "has_failed": True, "has_not_run": False}
    # A later complete independent regression resolves the earlier chain's
    # blocking evidence without rewriting its immutable publication rows.
    history = chains(connection, graph_id)
    latest = {chain["source_node"]: chain for chain in history}
    by_key = {item["node"]: item for item in generated}
    for source, last in latest.items():
        if by_key[last["regression"]]["state"] != "complete":
            if overrides.get(source, {}).get("state") == "resolved":
                overrides.pop(source)
            continue
        if by_key[last["repair"]]["state"] in {"partial", "failed", "blocked"}:
            by_key[last["repair"]].update(state="resolved", reasons=[])
        for earlier in history:
            if earlier["source_node"] == source and earlier["regression"] != last["regression"]:
                for key in (earlier["repair"], earlier["regression"]):
                    if by_key[key]["state"] not in {"active", "stale"}:
                        by_key[key].update(state="resolved", reasons=[])
    return overrides, generated
