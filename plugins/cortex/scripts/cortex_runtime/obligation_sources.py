"""Derive source-review subjects from canonical obligations, never from a plan.

This projection does not establish host authorship or semantic coverage. It
supplies the immutable content binding for the independent extraction review.
No public caller recopy of known criterion text participates in the digest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3


class ObligationSourceError(ValueError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class RegistrySubject:
    reference: str
    requirement: str
    role: str
    text: str


@dataclass(frozen=True)
class RegistrySource:
    reference: str
    text: str
    revision: int
    provenance: str


@dataclass(frozen=True)
class RegistrySnapshot:
    revision: int
    content_digest: str
    subjects: tuple[RegistrySubject, ...]
    sources: tuple[RegistrySource, ...]


def snapshot(connection: sqlite3.Connection, task_id: str) -> RegistrySnapshot:
    """Read a consistent source/registry snapshot inside the caller transaction.

    References distinguish equal criterion text in different semantic roles.
    The content digest changes with any obligation, criterion, source or revision
    change, but not with graph edits, reports, retries or remediation attempts.
    """
    if not connection.in_transaction:
        raise ObligationSourceError("transaction_required")
    task = connection.execute("SELECT user_request_original,constraints_json FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
    if task is None:
        raise ObligationSourceError("task_not_found")
    revision = connection.execute("SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?",
                                  (task_id,)).fetchone()[0]
    if type(revision) is not int or revision < 1:
        raise ObligationSourceError("registry_revision_missing")
    sources = [RegistrySource("src_" + _digest([task_id, "original"])[:24], task[0], 1,
                              "recorded_original_not_host_attested")]
    for row in connection.execute("""SELECT r.revision,d.decision_id,d.response_original
        FROM effective_contract_revisions r LEFT JOIN user_decisions d ON d.decision_id=r.decision_id
        AND d.task_id=r.task_id WHERE r.task_id=? AND r.revision>1 ORDER BY r.revision""", (task_id,)):
        if row[1] is None:
            raise ObligationSourceError("registry_decision_missing")
        sources.append(RegistrySource("src_" + _digest([task_id, row[1]])[:24], row[2], row[0],
                                      "recorded_decision_not_host_attested"))
    subjects = []
    rows = connection.execute("""SELECT i.item_id,i.text,d.details_json FROM effective_contract_items i
        LEFT JOIN effective_contract_item_details d ON d.item_id=i.item_id
        WHERE i.task_id=? AND i.retired_revision IS NULL ORDER BY i.ordinal,i.item_id""", (task_id,))
    for item_id, name, details_json in rows:
        if details_json is None:
            raise ObligationSourceError("registry_criteria_missing")
        details = json.loads(details_json)
        requirement = "req_" + _digest([task_id, item_id])[:24]
        subjects.append(RegistrySubject(requirement, requirement, "requirement", name))
        for role, key in (("acceptance", "acceptance_criteria"),
                          ("constraint", "constraints"), ("verification", "verification_criteria")):
            values = details.get(key)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ObligationSourceError("registry_criteria_invalid")
            for index, text in enumerate(values):
                reference = "crit_" + _digest([task_id, item_id, role, index])[:24]
                subjects.append(RegistrySubject(reference, requirement, role, text))
    constraints = json.loads(task[1])
    if not isinstance(constraints, list) or any(not isinstance(value, str) or not value.strip() for value in constraints):
        raise ObligationSourceError("registry_constraints_invalid")
    for index, text in enumerate(constraints):
        reference = "rule_" + _digest([task_id, "task_constraint", index])[:24]
        subjects.append(RegistrySubject(reference, reference, "task_constraint", text))
    if len({s.reference for s in subjects}) != len(subjects):
        raise ObligationSourceError("registry_reference_ambiguous")
    content = {"revision": revision,
        "subjects": [(s.reference, s.requirement, s.role, s.text) for s in subjects],
        "sources": [(s.reference, s.text, s.revision, s.provenance) for s in sources]}
    return RegistrySnapshot(revision, _digest(content), tuple(subjects), tuple(sources))
