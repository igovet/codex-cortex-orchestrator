"""Storage guards for immutable source and append-only obligation history.

These guards are not semantic completeness or user-authentication evidence.
Those require independent source coverage and host-bound user events. They
prevent ordinary plan/report/recovery code from rewriting historical facts.
"""
from __future__ import annotations

import sqlite3


_GUARDS = {
    "task_source_no_update": """BEFORE UPDATE OF user_request_original ON tasks
        WHEN NEW.user_request_original IS NOT OLD.user_request_original
        BEGIN SELECT RAISE(ABORT,'original request is immutable'); END""",
    "task_source_no_delete": """BEFORE DELETE ON tasks
        BEGIN SELECT RAISE(ABORT,'original request is immutable'); END""",
    "task_initial_contract_no_update": """BEFORE UPDATE OF project_hash,project_root,
        objective,user_language,task_contract_version,requirements_json,
        constraints_json,acceptance_criteria_json,verification_plan_json,context_json
        ON tasks BEGIN SELECT RAISE(ABORT,'initial task contract is immutable'); END""",
    "obligation_details_no_update": """BEFORE UPDATE ON effective_contract_item_details
        BEGIN SELECT RAISE(ABORT,'obligation criteria are immutable'); END""",
    "obligation_details_no_delete": """BEFORE DELETE ON effective_contract_item_details
        BEGIN SELECT RAISE(ABORT,'obligation criteria are immutable'); END""",
    "obligation_history_no_delete": """BEFORE DELETE ON effective_contract_items
        BEGIN SELECT RAISE(ABORT,'obligation history is immutable'); END""",
    "obligation_identity_no_update": """BEFORE UPDATE OF item_id,project_hash,
        task_id,category,ordinal,text,created_revision ON effective_contract_items
        BEGIN SELECT RAISE(ABORT,'obligation identity is immutable'); END""",
    "obligation_retirement_no_rewrite": """BEFORE UPDATE OF retired_revision
        ON effective_contract_items WHEN OLD.retired_revision IS NOT NULL
        OR NEW.retired_revision IS NULL OR NEW.retired_revision <= OLD.created_revision
        BEGIN SELECT RAISE(ABORT,'obligation retirement is irreversible'); END""",
    "obligation_revision_no_update": """BEFORE UPDATE ON effective_contract_revisions
        BEGIN SELECT RAISE(ABORT,'obligation revisions are immutable'); END""",
    "obligation_revision_no_delete": """BEFORE DELETE ON effective_contract_revisions
        BEGIN SELECT RAISE(ABORT,'obligation revisions are immutable'); END""",
    "obligation_revision_contiguous": """BEFORE INSERT ON effective_contract_revisions
        WHEN NEW.revision != (SELECT COALESCE(MAX(revision),0)+1
        FROM effective_contract_revisions WHERE task_id=NEW.task_id)
        BEGIN SELECT RAISE(ABORT,'obligation revision must be contiguous'); END""",
    "obligation_decision_single_revision": """BEFORE INSERT ON effective_contract_revisions
        WHEN NEW.decision_id IS NOT NULL AND EXISTS(
        SELECT 1 FROM effective_contract_revisions WHERE decision_id=NEW.decision_id)
        BEGIN SELECT RAISE(ABORT,'user decision already changed obligations'); END""",
    "obligation_revision_requires_decision": """BEFORE INSERT ON effective_contract_revisions
        WHEN (NEW.revision=1 AND NEW.decision_id IS NOT NULL) OR
        (NEW.revision>1 AND NOT EXISTS(SELECT 1 FROM user_decisions d
        WHERE d.decision_id=NEW.decision_id AND d.task_id=NEW.task_id
        AND d.attribution='user_via_coordinator'))
        BEGIN SELECT RAISE(ABORT,'obligation revision requires a task-bound decision'); END""",
    "obligation_creation_requires_revision": """BEFORE INSERT ON effective_contract_items
        WHEN NOT EXISTS(SELECT 1 FROM effective_contract_revisions r
        WHERE r.task_id=NEW.task_id AND r.revision=NEW.created_revision)
        BEGIN SELECT RAISE(ABORT,'obligation creation requires a recorded revision'); END""",
    "obligation_retirement_requires_revision": """BEFORE UPDATE OF retired_revision
        ON effective_contract_items WHEN NOT EXISTS(
        SELECT 1 FROM effective_contract_revisions r WHERE r.task_id=NEW.task_id
        AND r.revision=NEW.retired_revision AND r.decision_id IS NOT NULL)
        BEGIN SELECT RAISE(ABORT,'obligation retirement requires a recorded decision'); END""",
}
REQUIRED_TRIGGERS = frozenset(_GUARDS)


def install_guards(connection: sqlite3.Connection) -> None:
    """Create current-schema guards; never migrate or backfill an old ledger."""
    for name, body in _GUARDS.items():
        connection.execute(f"CREATE TRIGGER {name} {body}")
