"""Assignment-bound incremental evidence, separate from report formatting.

Host receipt ingestion is a private adapter boundary, not a model-callable API.
An observed tool response is not a successful process exit. Neither a receipt
nor a model explanation grants requirement completion or closure authority.
Native receipt ingestion and public small-operation routing still need wiring.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid

from cortex_runtime import canonical_json


class VerificationJournalError(ValueError):
    pass


TABLES = {
    "verification_receipts": {"receipt_ref", "assignment_id", "event_digest", "content_json", "signature"},
    "verification_notes": {"note_ref", "assignment_id", "node_key", "check_key", "receipt_ref", "summary"},
}
GUARDS = frozenset(f"{table}_no_{op}" for table in TABLES for op in ("update", "delete")) | {
    "verification_note_receipt_bound",
}


def create_tables(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise VerificationJournalError("transaction_required")
    connection.execute("""CREATE TABLE verification_receipts(
        receipt_ref TEXT PRIMARY KEY,
        assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id),
        event_digest TEXT NOT NULL UNIQUE, content_json TEXT NOT NULL, signature TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE verification_notes(
        note_ref TEXT PRIMARY KEY,
        assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id),
        node_key TEXT NOT NULL, check_key TEXT NOT NULL,
        receipt_ref TEXT NOT NULL REFERENCES verification_receipts(receipt_ref), summary TEXT NOT NULL,
        UNIQUE(assignment_id,node_key,check_key,receipt_ref))""")
    connection.execute("""CREATE TRIGGER verification_note_receipt_bound BEFORE INSERT ON verification_notes
        WHEN NOT EXISTS(SELECT 1 FROM verification_receipts WHERE receipt_ref=NEW.receipt_ref
        AND assignment_id=NEW.assignment_id)
        BEGIN SELECT RAISE(ABORT,'verification receipt is not assignment-bound'); END""")
    for table in TABLES:
        for op in ("update", "delete"):
            connection.execute(f"""CREATE TRIGGER {table}_no_{op} BEFORE {op.upper()} ON {table}
                BEGIN SELECT RAISE(ABORT,'verification history is immutable'); END""")


class VerificationJournal:
    """Internal connection-bound operations; identity is supplied by the server."""

    def __init__(self, connection: sqlite3.Connection, *, assignment_id: str, key: bytes):
        if not connection.in_transaction:
            raise VerificationJournalError("transaction_required")
        if not isinstance(key, bytes) or len(key) != 32:
            raise VerificationJournalError("host_key_invalid")
        self.connection, self.assignment_id, self.key = connection, assignment_id, key

    def _assignment(self, *, current: bool):
        row = self.connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?",
                                      (self.assignment_id,)).fetchone()
        consumed = self.connection.execute("SELECT consumed_sequence FROM worker_capabilities WHERE assignment_id=?",
                                           (self.assignment_id,)).fetchone()
        if row is None or consumed is None or consumed[0] is None:
            raise VerificationJournalError("consumed_assignment_required")
        if current:
            revision = self.connection.execute("SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?",
                                               (row["task_id"],)).fetchone()[0]
            generation = self.connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0]
            if (row["state"] != "active" or row["revision"] != revision
                    or (row["mode"] != "artifact_independent" and row["target_generation"] != generation)):
                raise VerificationJournalError("assignment_evidence_stale")
        return row

    def _sign(self, reference, assignment, event, content):
        namespace = self.connection.execute("SELECT value FROM v12_metadata WHERE key='project_root_digest'").fetchone()[0]
        message = canonical_json.dumps([namespace, reference, assignment, event, content]).encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def ingest_host_receipt(self, event: str, *, state: str, exit_code: int | None,
                            command_digest: str, output_digest: str) -> str:
        """Private trusted-adapter ingestion, including late non-current facts.

        A host lacking an exit observation must use observed with no exit code;
        it cannot infer a successful exit by parsing program-authored stdout.
        """
        owner = self._assignment(current=False)
        if not isinstance(event, str) or not event or len(event) > 512:
            raise VerificationJournalError("host_event_invalid")
        if state not in {"observed", "exited", "interrupted"}:
            raise VerificationJournalError("host_execution_state_invalid")
        if (state == "exited" and (type(exit_code) is not int or not -65536 <= exit_code <= 65536)
                or state != "exited" and exit_code is not None):
            raise VerificationJournalError("host_exit_observation_invalid")
        if any(not isinstance(d, str) or len(d) != 64 or any(c not in "0123456789abcdef" for c in d)
               for d in (command_digest, output_digest)):
            raise VerificationJournalError("host_digest_invalid")
        event_digest = hashlib.sha256(event.encode()).hexdigest()
        content = canonical_json.dumps({"revision": owner["revision"], "generation": owner["target_generation"],
            "state": state, "exit_code": exit_code, "command_digest": command_digest, "output_digest": output_digest})
        prior = self.connection.execute("SELECT receipt_ref,assignment_id,content_json,signature FROM verification_receipts WHERE event_digest=?",
                                        (event_digest,)).fetchone()
        if prior:
            if (prior[1] != self.assignment_id or prior[2] != content
                    or not hmac.compare_digest(prior[3], self._sign(prior[0], prior[1], event_digest, prior[2]))):
                raise VerificationJournalError("host_receipt_conflict")
            return prior[0]
        count = self.connection.execute("SELECT COUNT(*) FROM verification_receipts WHERE assignment_id=?",
                                        (self.assignment_id,)).fetchone()[0]
        if count >= 4096:
            raise VerificationJournalError("host_receipt_capacity_reached")
        reference = "execution_" + uuid.uuid4().hex
        self.connection.execute("INSERT INTO verification_receipts VALUES (?,?,?,?,?)",
            (reference, self.assignment_id, event_digest, content,
             self._sign(reference, self.assignment_id, event_digest, content)))
        return reference

    def _receipt(self, reference):
        row = self.connection.execute("SELECT event_digest,content_json,signature FROM verification_receipts WHERE receipt_ref=? AND assignment_id=?",
                                      (reference, self.assignment_id)).fetchone()
        if row is None or not hmac.compare_digest(row[2], self._sign(reference, self.assignment_id, row[0], row[1])):
            raise VerificationJournalError("host_receipt_unavailable")
        return json.loads(row[1])

    def record_check(self, node: str, check: str, receipt: str, summary: str) -> str:
        owner = self._assignment(current=True)
        if not isinstance(summary, str) or not summary.strip() or len(summary.encode()) > 4096:
            raise VerificationJournalError("verification_summary_invalid")
        row = self.connection.execute("SELECT content_json FROM execution_nodes WHERE graph_id=? AND node_key=? AND assignment_id=?",
                                      (owner["graph_id"], node, self.assignment_id)).fetchone()
        if row is None or check not in {c["key"] for c in json.loads(row[0])["checks"]}:
            raise VerificationJournalError("declared_check_required")
        observation = self._receipt(receipt)
        if observation["revision"] != owner["revision"] or observation["generation"] != owner["target_generation"]:
            raise VerificationJournalError("receipt_generation_stale")
        prior = self.connection.execute("SELECT note_ref,summary FROM verification_notes WHERE assignment_id=? AND node_key=? AND check_key=? AND receipt_ref=?",
                                        (self.assignment_id, node, check, receipt)).fetchone()
        if prior:
            if prior[1] != summary:
                raise VerificationJournalError("verification_note_conflict")
            return prior[0]
        count = self.connection.execute("SELECT COUNT(*) FROM verification_notes WHERE assignment_id=?",
                                        (self.assignment_id,)).fetchone()[0]
        if count >= 4096:
            raise VerificationJournalError("verification_note_capacity_reached")
        reference = "fact_" + uuid.uuid4().hex
        self.connection.execute("INSERT INTO verification_notes VALUES (?,?,?,?,?,?)",
                                (reference, self.assignment_id, node, check, receipt, summary))
        return reference

    def inspect(self) -> list[dict]:
        self._assignment(current=False)
        result = []
        for row in self.connection.execute("SELECT node_key,check_key,receipt_ref,summary FROM verification_notes WHERE assignment_id=? ORDER BY rowid",
                                           (self.assignment_id,)):
            observed = self._receipt(row[2])
            result.append({"node": row[0], "check": row[1], "summary": row[3],
                           "execution": observed, "requirement_completion": "unverified"})
        return result
