"""Small, durable extraction operations; drafts grant no execution authority.

This is a new storage boundary, not an adapter for monolithic outcome inputs.
The trusted caller supplies task/session identity and a host key. Public host
bootstrap and independent semantic review are separate, still required gates.
No operation in this module seals a registry or changes effective obligations.
"""
from __future__ import annotations

import sqlite3
import uuid

from cortex_runtime import canonical_json, obligation_sources, source_coverage, submission_queue


class RegistryDraftError(ValueError):
    pass


TABLES = {
    "registry_drafts": {"draft_ref", "task_id", "source_ref", "base_digest"},
    "registry_draft_subjects": {"subject_ref", "draft_ref", "parent_ref", "kind", "text"},
    "registry_draft_links": {"draft_ref", "subject_ref", "start", "end"},
    "registry_draft_amendments": {"sequence", "draft_ref", "subject_ref", "text", "base_digest"},
}
GUARDS = frozenset(f"{table}_no_{operation}" for table in TABLES for operation in ("update", "delete")) | {
    "registry_draft_source_bound", "registry_draft_parent_bound", "registry_draft_link_bound",
    "registry_draft_amendment_bound",
}


def create_tables(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RegistryDraftError("transaction_required")
    connection.execute("""CREATE TABLE registry_drafts(
        draft_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        source_ref TEXT NOT NULL REFERENCES source_submissions(source_ref), base_digest TEXT NOT NULL,
        UNIQUE(task_id,source_ref,base_digest))""")
    connection.execute("""CREATE TABLE registry_draft_subjects(
        subject_ref TEXT PRIMARY KEY, draft_ref TEXT NOT NULL REFERENCES registry_drafts(draft_ref),
        parent_ref TEXT REFERENCES registry_draft_subjects(subject_ref),
        kind TEXT NOT NULL CHECK(kind IN ('requirement','acceptance','constraint','verification')),
        text TEXT NOT NULL, CHECK((kind='requirement') = (parent_ref IS NULL)))""")
    connection.execute("""CREATE UNIQUE INDEX registry_draft_subject_unique ON registry_draft_subjects
        (draft_ref,COALESCE(parent_ref,''),kind,text)""")
    connection.execute("""CREATE TABLE registry_draft_links(
        draft_ref TEXT NOT NULL REFERENCES registry_drafts(draft_ref),
        subject_ref TEXT NOT NULL REFERENCES registry_draft_subjects(subject_ref),
        start INTEGER NOT NULL, end INTEGER NOT NULL CHECK(end>start AND start>=0),
        PRIMARY KEY(draft_ref,subject_ref,start,end))""")
    connection.execute("""CREATE TABLE registry_draft_amendments(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_ref TEXT NOT NULL REFERENCES registry_drafts(draft_ref),
        subject_ref TEXT NOT NULL REFERENCES registry_draft_subjects(subject_ref), text TEXT NOT NULL,
        base_digest TEXT NOT NULL, UNIQUE(draft_ref,base_digest))""")
    connection.execute("""CREATE INDEX registry_draft_amendment_order
        ON registry_draft_amendments(draft_ref,subject_ref,sequence)""")
    connection.execute("""CREATE TRIGGER registry_draft_source_bound BEFORE INSERT ON registry_drafts
        WHEN NOT EXISTS(SELECT 1 FROM source_consumptions WHERE source_ref=NEW.source_ref
        AND task_id=NEW.task_id AND purpose IN ('initial','change'))
        BEGIN SELECT RAISE(ABORT,'registry draft source is not task-bound'); END""")
    connection.execute("""CREATE TRIGGER registry_draft_parent_bound BEFORE INSERT ON registry_draft_subjects
        WHEN NEW.parent_ref IS NOT NULL AND NOT EXISTS(SELECT 1 FROM registry_draft_subjects
        WHERE subject_ref=NEW.parent_ref AND draft_ref=NEW.draft_ref AND kind='requirement')
        BEGIN SELECT RAISE(ABORT,'registry draft parent is not a scoped requirement'); END""")
    connection.execute("""CREATE TRIGGER registry_draft_link_bound BEFORE INSERT ON registry_draft_links
        WHEN NOT EXISTS(SELECT 1 FROM registry_draft_subjects
        WHERE subject_ref=NEW.subject_ref AND draft_ref=NEW.draft_ref)
        BEGIN SELECT RAISE(ABORT,'registry draft link is not scoped'); END""")
    connection.execute("""CREATE TRIGGER registry_draft_amendment_bound BEFORE INSERT ON registry_draft_amendments
        WHEN NOT EXISTS(SELECT 1 FROM registry_draft_subjects
        WHERE subject_ref=NEW.subject_ref AND draft_ref=NEW.draft_ref)
        BEGIN SELECT RAISE(ABORT,'registry draft amendment is not scoped'); END""")
    for table in TABLES:
        for operation in ("update", "delete"):
            connection.execute(f"""CREATE TRIGGER {table}_no_{operation} BEFORE {operation.upper()} ON {table}
                BEGIN SELECT RAISE(ABORT,'registry draft history is immutable'); END""")


class RegistryDraft:
    """Connection-bound internal operations, each inside one store transaction."""

    def __init__(self, connection: sqlite3.Connection, *, task_id: str, session: str, key: bytes):
        if not connection.in_transaction:
            raise RegistryDraftError("transaction_required")
        self.connection, self.task_id, self.session, self.key = connection, task_id, session, key

    def _source(self, reference: str) -> submission_queue.Submission:
        source = submission_queue.read(self.connection, reference=reference, session=self.session, key=self.key)
        consumed = self.connection.execute("SELECT task_id,purpose FROM source_consumptions WHERE source_ref=?",
                                           (reference,)).fetchone()
        if consumed is None or consumed[0] != self.task_id or consumed[1] not in {"initial", "change"}:
            raise RegistryDraftError("requirement_source_unavailable")
        return source

    def _draft(self, reference: str):
        row = self.connection.execute("SELECT source_ref,base_digest FROM registry_drafts WHERE draft_ref=? AND task_id=?",
                                      (reference, self.task_id)).fetchone()
        if row is None:
            raise RegistryDraftError("draft_unavailable")
        source = self._source(row[0])
        if obligation_sources.snapshot(self.connection, self.task_id).content_digest != row[1]:
            raise RegistryDraftError("draft_registry_stale")
        return source, row[1]

    def begin(self, source_ref: str) -> str:
        self._source(source_ref)
        base = obligation_sources.snapshot(self.connection, self.task_id).content_digest
        existing = self.connection.execute("SELECT draft_ref FROM registry_drafts WHERE task_id=? AND source_ref=? AND base_digest=?",
                                           (self.task_id, source_ref, base)).fetchone()
        if existing is not None:
            return existing[0]
        count = self.connection.execute("SELECT COUNT(*) FROM registry_drafts WHERE task_id=?", (self.task_id,)).fetchone()[0]
        if count >= 128:
            raise RegistryDraftError("draft_capacity_reached")
        reference = "draft_" + uuid.uuid4().hex
        self.connection.execute("INSERT INTO registry_drafts VALUES (?,?,?,?)", (reference, self.task_id, source_ref, base))
        return reference

    def _subject(self, draft: str, reference: str):
        row = self.connection.execute("SELECT parent_ref,kind,text FROM registry_draft_subjects WHERE draft_ref=? AND subject_ref=?",
                                      (draft, reference)).fetchone()
        if row is None:
            raise RegistryDraftError("draft_subject_unavailable")
        return row

    def _add(self, draft: str, parent: str | None, kind: str, text: str) -> str:
        self._draft(draft)
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
            raise RegistryDraftError("draft_text_invalid")
        existing = self.connection.execute("""SELECT subject_ref FROM registry_draft_subjects
            WHERE draft_ref=? AND parent_ref IS ? AND kind=? AND text=?""", (draft, parent, kind, text)).fetchone()
        if existing is not None:
            return existing[0]
        count = self.connection.execute("SELECT COUNT(*) FROM registry_draft_subjects WHERE draft_ref=?", (draft,)).fetchone()[0]
        if count >= 1024:
            raise RegistryDraftError("draft_subject_capacity_reached")
        reference = "subject_" + uuid.uuid4().hex
        self.connection.execute("INSERT INTO registry_draft_subjects VALUES (?,?,?,?,?)", (reference, draft, parent, kind, text))
        return reference

    def add_requirement(self, draft: str, text: str) -> str:
        return self._add(draft, None, "requirement", text)

    def add_criterion(self, draft: str, requirement: str, kind: str, text: str) -> str:
        self._draft(draft)
        if kind not in {"acceptance", "constraint", "verification"}:
            raise RegistryDraftError("draft_criterion_kind_invalid")
        if self._subject(draft, requirement)[1] != "requirement":
            raise RegistryDraftError("draft_parent_not_requirement")
        return self._add(draft, requirement, kind, text)

    def amend_text(self, draft: str, subject: str, text: str, expected_digest: str) -> None:
        """Repair extraction text, preserving original and every prior amendment.

        Only an unsealed draft changes. The content digest changes, so a future
        semantic review must bind the new content rather than an old mapping.
        """
        self._draft(draft)
        self._subject(draft, subject)
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
            raise RegistryDraftError("draft_text_invalid")
        receipt = self.connection.execute("""SELECT subject_ref,text FROM registry_draft_amendments
            WHERE draft_ref=? AND base_digest=?""", (draft, expected_digest)).fetchone()
        if receipt is not None:
            if tuple(receipt) == (subject, text):
                return
            raise RegistryDraftError("draft_amendment_conflict")
        if self.inspect(draft)["content_digest"] != expected_digest:
            raise RegistryDraftError("draft_content_stale")
        count = self.connection.execute("SELECT COUNT(*) FROM registry_draft_amendments WHERE draft_ref=?", (draft,)).fetchone()[0]
        if count >= 4096:
            raise RegistryDraftError("draft_amendment_capacity_reached")
        self.connection.execute("INSERT INTO registry_draft_amendments(draft_ref,subject_ref,text,base_digest) VALUES (?,?,?,?)",
                                (draft, subject, text, expected_digest))

    def link_source(self, draft: str, subject: str, start: int, end: int) -> None:
        source, _ = self._draft(draft)
        self._subject(draft, subject)
        source_coverage.anchor(source.text, start, end)
        existing = self.connection.execute("SELECT 1 FROM registry_draft_links WHERE draft_ref=? AND subject_ref=? AND start=? AND end=?",
                                           (draft, subject, start, end)).fetchone()
        if existing is not None:
            return
        count = self.connection.execute("SELECT COUNT(*) FROM registry_draft_links WHERE draft_ref=?", (draft,)).fetchone()[0]
        if count >= 4096:
            raise RegistryDraftError("draft_link_capacity_reached")
        self.connection.execute("INSERT INTO registry_draft_links VALUES (?,?,?,?)", (draft, subject, start, end))

    def inspect(self, draft: str) -> dict:
        source, base = self._draft(draft)
        rows = self.connection.execute("""SELECT s.subject_ref,s.parent_ref,s.kind,COALESCE(
            (SELECT a.text FROM registry_draft_amendments a WHERE a.draft_ref=s.draft_ref
             AND a.subject_ref=s.subject_ref ORDER BY a.sequence DESC LIMIT 1),s.text) AS text
            FROM registry_draft_subjects s WHERE draft_ref=? ORDER BY subject_ref""",
                                       (draft,)).fetchall()
        subjects = tuple((row[0], None) if row[2] == "requirement" else (row[1], row[0]) for row in rows)
        names = {row[0]: (row[0], None) if row[2] == "requirement" else (row[1], row[0]) for row in rows}
        links = []
        for ref, start, end in self.connection.execute("SELECT subject_ref,start,end FROM registry_draft_links WHERE draft_ref=?", (draft,)):
            if ref not in names:
                raise RegistryDraftError("draft_link_corrupt")
            links.append(source_coverage.ExtractionLink(*names[ref], source_coverage.anchor(source.text, start, end)))
        audit = source_coverage.audit(source.text, subjects, links) if subjects else None
        amendment_sequence = self.connection.execute("SELECT COALESCE(MAX(sequence),0) FROM registry_draft_amendments WHERE draft_ref=?", (draft,)).fetchone()[0]
        content_digest = canonical_json.digest({"base": base, "source": source_coverage.source_digest(source.text),
                                               "subjects": [tuple(row) for row in rows],
                                               "amendment_sequence": amendment_sequence})
        return {"draft": draft, "base_digest": base, "content_digest": content_digest,
                "subjects": [dict(row) for row in rows],
                "uncovered": audit.uncovered if audit else ((0, len(source.text)),),
                "unmapped": audit.unmapped if audit else (),
                "mapping_digest": audit.mapping_digest if audit else None,
                "structurally_complete": audit.structurally_complete if audit else False,
                "semantic_review": "unverified", "execution_authority": False}
