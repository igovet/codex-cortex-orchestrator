"""Transactional, immutable host-input inbox primitives.

Only a trusted input adapter may call capture. These primitives do not identify
human authorship from text and are not public model-callable operations. The
adapter/connection binding must be qualified before this queue grants authority.
Each observed submission is retained, including identical repeated messages.
Consumption of one server-issued reference is independently idempotent.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import sqlite3
import uuid


class SubmissionError(ValueError):
    pass


DDL = (
    """CREATE TABLE source_submissions(
        arrival INTEGER PRIMARY KEY AUTOINCREMENT, source_ref TEXT NOT NULL UNIQUE,
        session_digest TEXT NOT NULL, turn_digest TEXT NOT NULL,
        body TEXT NOT NULL, signature TEXT NOT NULL)""",
    """CREATE TABLE source_consumptions(
        source_ref TEXT PRIMARY KEY REFERENCES source_submissions(source_ref),
        task_id TEXT NOT NULL REFERENCES tasks(task_id), purpose TEXT NOT NULL
        CHECK(purpose IN ('initial','change','decision','information')))""",
    "CREATE INDEX source_submission_session_order ON source_submissions(session_digest,arrival)",
    "CREATE UNIQUE INDEX source_initial_task ON source_consumptions(task_id) WHERE purpose='initial'",
    """CREATE TRIGGER source_submissions_no_update BEFORE UPDATE ON source_submissions
        BEGIN SELECT RAISE(ABORT,'source submissions are immutable'); END""",
    """CREATE TRIGGER source_submissions_no_delete BEFORE DELETE ON source_submissions
        BEGIN SELECT RAISE(ABORT,'source submissions are immutable'); END""",
    """CREATE TRIGGER source_consumptions_no_update BEFORE UPDATE ON source_consumptions
        BEGIN SELECT RAISE(ABORT,'source consumptions are immutable'); END""",
    """CREATE TRIGGER source_consumptions_no_delete BEFORE DELETE ON source_consumptions
        BEGIN SELECT RAISE(ABORT,'source consumptions are immutable'); END""",
)


def _transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise SubmissionError("transaction_required")


def create_tables(connection: sqlite3.Connection) -> None:
    _transaction(connection)
    for statement in DDL:
        connection.execute(statement)


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise SubmissionError("host_identity_invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _namespace(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT value FROM v12_metadata WHERE key='project_root_digest'").fetchone()
    if row is None:
        raise SubmissionError("source_namespace_missing")
    return row[0]


def _signature(key: bytes, values: tuple[object, ...]) -> str:
    if not isinstance(key, bytes) or len(key) != 32:
        raise SubmissionError("host_key_invalid")
    return hmac.new(key, json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode(),
                    hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Submission:
    reference: str
    arrival: int
    text: str


def capture(connection: sqlite3.Connection, *, session: str, turn: str,
            text: str, key: bytes) -> Submission:
    _transaction(connection)
    session_digest, turn_digest = _identity(session), _identity(turn)
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 2 * 1024 * 1024:
        raise SubmissionError("source_text_invalid")
    reference = "src_" + uuid.uuid4().hex
    # Arrival is allocated under the caller's serialized write transaction and
    # included in the signature. It cannot be reordered by an unsigned edit.
    arrival = connection.execute("SELECT COALESCE(MAX(arrival),0)+1 FROM source_submissions").fetchone()[0]
    signed = (_namespace(connection), reference, arrival, session_digest, turn_digest, text)
    connection.execute("INSERT INTO source_submissions VALUES (?,?,?,?,?,?)",
                       (arrival, reference, session_digest, turn_digest, text, _signature(key, signed)))
    return Submission(reference, arrival, text)


def read(connection: sqlite3.Connection, *, reference: str, session: str,
         key: bytes) -> Submission:
    _transaction(connection)
    row = connection.execute("""SELECT source_ref,arrival,session_digest,turn_digest,body,signature
        FROM source_submissions WHERE source_ref=?""", (reference,)).fetchone()
    if row is None or row[2] != _identity(session):
        raise SubmissionError("source_not_available")
    if not hmac.compare_digest(row[5], _signature(key, (_namespace(connection), *row[:5]))):
        raise SubmissionError("source_signature_invalid")
    return Submission(row[0], row[1], row[4])


def consume(connection: sqlite3.Connection, *, reference: str, session: str,
            task_id: str, purpose: str, key: bytes) -> bool:
    """Bind the next submission once; return whether this is reconciliation."""
    source = read(connection, reference=reference, session=session, key=key)
    if purpose not in {"initial", "change", "decision", "information"}:
        raise SubmissionError("source_purpose_invalid")
    existing = connection.execute("SELECT task_id,purpose FROM source_consumptions WHERE source_ref=?",
                                  (reference,)).fetchone()
    if existing is not None:
        if tuple(existing) != (task_id, purpose):
            raise SubmissionError("source_already_consumed")
        return True
    task = connection.execute("SELECT user_request_original FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if task is None:
        raise SubmissionError("source_task_unavailable")
    initial = connection.execute("""SELECT s.session_digest FROM source_consumptions c
        JOIN source_submissions s ON s.source_ref=c.source_ref
        WHERE c.task_id=? AND c.purpose='initial'""", (task_id,)).fetchone()
    if purpose == "initial":
        if source.text != task[0]:
            raise SubmissionError("original_source_mismatch")
        if initial is not None:
            raise SubmissionError("initial_source_already_bound")
    else:
        if initial is None:
            raise SubmissionError("initial_source_required")
        if initial[0] != _identity(session):
            raise SubmissionError("source_task_session_mismatch")
    earlier = connection.execute("""SELECT 1 FROM source_submissions s
        LEFT JOIN source_consumptions c ON c.source_ref=s.source_ref
        WHERE s.session_digest=? AND s.arrival<? AND c.source_ref IS NULL LIMIT 1""",
        (_identity(session), source.arrival)).fetchone()
    if earlier is not None:
        raise SubmissionError("earlier_source_pending")
    connection.execute("INSERT INTO source_consumptions VALUES (?,?,?)", (reference, task_id, purpose))
    return False


def pending(connection: sqlite3.Connection, *, session: str) -> tuple[str, ...]:
    """Return references only; raw messages never leak through progress output."""
    _transaction(connection)
    rows = connection.execute("""SELECT s.source_ref FROM source_submissions s
        LEFT JOIN source_consumptions c ON c.source_ref=s.source_ref
        WHERE s.session_digest=? AND c.source_ref IS NULL ORDER BY s.arrival LIMIT 32""",
        (_identity(session),))
    return tuple(row[0] for row in rows)
