"""Trusted-adapter fixtures are not native execution qualification."""
import json
import sqlite3

import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import record_steering
from cortex_runtime.verification_journal import VerificationJournal, VerificationJournalError
from cortex_runtime.v12_contract import task_ref as public_task_ref
from test_node_assignment_receipts import node_case
from test_domain_public_api_contract import PROVENANCE
from test_typed_public_api import dispatch_and_consume

KEY = b"v" * 32


@pytest.fixture
def journal_case(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    _, worker = dispatch_and_consume(task, nodes=["baseline"])
    assignment = worker["assignment_id"]
    node = store._read(lambda c: graph_ledger.assignment_scope(c, assignment)["nodes"][0])
    def call(operation, *positional, **keywords):
        return store._write(lambda c: getattr(VerificationJournal(c, assignment_id=assignment, key=KEY), operation)(*positional, **keywords))
    return store, task, assignment, node, call


def receipt(call, event="fixture-event", **override):
    return call("ingest_host_receipt", event, **dict(
        {"state": "exited", "exit_code": 0, "command_digest": "a" * 64, "output_digest": "b" * 64}, **override))


def test_incremental_fact_survives_failed_report_and_is_not_completion(journal_case):
    store, _, _, node, call = journal_case
    ref = receipt(call)
    assert receipt(call) == ref
    args = (node["key"], node["checks"][0]["key"], ref, "Observed declared check")
    fact = call("record_check", *args)
    assert call("record_check", *args) == fact
    before = call("inspect")
    with pytest.raises(RuntimeError, match="report formatting failed"):
        store._write(lambda c: (_ for _ in ()).throw(RuntimeError("report formatting failed")))
    assert call("inspect") == before
    assert before[0]["execution"]["exit_code"] == 0
    assert before[0]["requirement_completion"] == "unverified"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]) == 0


def test_receipt_cannot_be_rewritten_or_reassigned(journal_case):
    store, _, assignment, node, call = journal_case
    ref = receipt(call)
    with pytest.raises(VerificationJournalError, match="host_receipt_conflict"):
        receipt(call, output_digest="c" * 64)
    call("record_check", node["key"], node["checks"][0]["key"], ref, "First observation")
    with pytest.raises(VerificationJournalError, match="verification_note_conflict"):
        call("record_check", node["key"], node["checks"][0]["key"], ref, "Changed explanation")
    with pytest.raises(VerificationJournalError, match="consumed_assignment_required"):
        store._write(lambda c: VerificationJournal(c, assignment_id="foreign", key=KEY).inspect())
    with pytest.raises(VerificationJournalError, match="host_receipt_unavailable"):
        store._write(lambda c: VerificationJournal(c, assignment_id=assignment, key=b"x" * 32).inspect())
    def reject_deletion(c):
        for table in ("verification_receipts", "verification_notes"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(f"DELETE FROM {table}")
    store._write(reject_deletion)


@pytest.mark.parametrize("state,exit_code", [("observed", 0), ("interrupted", 0), ("exited", None), ("exited", True)])
def test_missing_or_invalid_exit_evidence_is_not_success(journal_case, state, exit_code):
    _, _, _, _, call = journal_case
    with pytest.raises(VerificationJournalError, match="host_exit_observation_invalid"):
        receipt(call, state=state, exit_code=exit_code)


def test_stdout_observation_and_failed_exit_stay_distinct(journal_case):
    _, _, _, node, call = journal_case
    observed = receipt(call, "stdout-only", state="observed", exit_code=None)
    failed = receipt(call, "failed-command", exit_code=1)
    for ref in (observed, failed):
        call("record_check", node["key"], node["checks"][0]["key"], ref, "Observed response")
    result = call("inspect")
    assert [item["execution"]["exit_code"] for item in result] == [None, 1]
    assert all(item["requirement_completion"] == "unverified" for item in result)


def test_unknown_check_and_missing_receipt_are_rejected(journal_case):
    _, _, _, node, call = journal_case
    ref = receipt(call)
    with pytest.raises(VerificationJournalError, match="declared_check_required"):
        call("record_check", node["key"], "invented", ref, "Invented proof")
    with pytest.raises(VerificationJournalError, match="host_receipt_unavailable"):
        call("record_check", node["key"], node["checks"][0]["key"], "missing", "No receipt")
    assert call("inspect") == []


def test_steering_keeps_old_facts_but_revokes_new_claims(journal_case):
    _, task, _, node, call = journal_case
    ref = receipt(call)
    call("record_check", node["key"], node["checks"][0]["key"], ref, "Old check")
    before = call("inspect")
    record_steering(task_ref=task, response_original="Also inspect the license", user_language="en",
        add=[{"outcome": "License", "acceptance": [], "constraints": [], "verification": []}], retire=[])
    assert call("inspect") == before
    late = receipt(call, "late-observation", state="interrupted", exit_code=None)
    with pytest.raises(VerificationJournalError, match="assignment_evidence_stale"):
        call("record_check", node["key"], node["checks"][0]["key"], late, "Late current proof")
    assert call("inspect") == before


def test_journal_stores_digests_not_commands_or_stdout(journal_case):
    store, _, _, _, call = journal_case
    receipt(call)
    content = store._read(lambda c: c.execute("SELECT content_json FROM verification_receipts").fetchone()[0])
    assert set(json.loads(content)) == {"revision", "generation", "state", "exit_code", "command_digest", "output_digest"}
