#!/usr/bin/env python3
"""Probe signed V17/V18 durable-question migration into V19.

This is a direct production-module probe, not a unit test. It creates exact
supported signed histories in isolated temporary ledgers, opens them through
the real V19 bootstrap, and fails closed on any semantic or integrity drift.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

import cortex  # noqa: E402,F401
from cortex_runtime import ledger_db  # noqa: E402
from cortex_runtime.attempt_facade import (  # noqa: E402
    _latest_answered_question_for_attempt,
    _open_question_for_attempt,
)


NOW = "2026-08-26T12:00:00+00:00"
TASK_ID = "task-migration"
PENDING_TEXT = "Какой режим нужен? 🚀\nСохранить перенос строки."
ANSWERED_TEXT = "Подтвердите требование 日本語\nВторая строка."
ANSWERED_VALUE = "Да — сохранить точно. ✅\nБез нормализации."
NEW_ANSWER = "Новый свободный ответ 🌍\nСтрока 2."


def _state() -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "task_number": 1,
        "status": "needs_input",
        "revision": 1,
        "task_revision": 1,
        "principal": "local",
        "updated_at": NOW,
        "attempts": [
            {
                "attempt_id": "attempt-pending",
                "status": "waiting_question",
                "attempt_generation": 1,
                "dispatch_ref": "dispatch-pending",
                "profile": "general",
            },
            {
                "attempt_id": "attempt-answered",
                "status": "waiting_question",
                "attempt_generation": 1,
                "dispatch_ref": "dispatch-answered",
                "profile": "general",
            },
        ],
    }


def _question(
    question_ref: str,
    attempt_id: str,
    dispatch_ref: str,
    question_text: str,
    status: str,
    published_sequence: int,
    answer: str | None = None,
) -> dict[str, Any]:
    return {
        "question_ref": question_ref,
        "task_id": TASK_ID,
        "attempt_id": attempt_id,
        "dispatch_ref": dispatch_ref,
        "profile": "general",
        "task_revision": 1,
        "attempt_generation": 1,
        "submission_id": "submission-" + question_ref,
        "question_category": "requirement",
        "question_text": question_text,
        "status": status,
        "content_digest": ledger_db.durable_question_content_digest(
            "requirement", question_text,
        ),
        "published_sequence": published_sequence,
        "answer": answer,
        "answer_submission_id": "answer-" + question_ref if answer is not None else None,
        "answer_digest": hashlib.sha256(answer.encode("utf-8")).hexdigest()
        if answer is not None else None,
        "answered_sequence": published_sequence + 10 if answer is not None else None,
        "created_at": NOW,
        "answered_at": NOW if answer is not None else None,
        "superseded_at": None,
    }


def _fresh_fixture(root: Path) -> dict[str, Any]:
    ledger_db.ensure_database(root)
    state = _state()
    ledger_db.create_task(
        root,
        {
            "task_id": TASK_ID,
            "user_request": "migration probe",
            "user_language": "en",
            "created_at": NOW,
        },
        state,
        "tasks/task-migration",
    )
    ledger_db.put_durable_question(root, _question(
        "question-pending", "attempt-pending", "dispatch-pending",
        PENDING_TEXT, "open", 1,
    ))
    ledger_db.put_durable_question(root, _question(
        "question-answered", "attempt-answered", "dispatch-answered",
        ANSWERED_TEXT, "answered", 2, ANSWERED_VALUE,
    ))
    return state


def _downgrade_to_signed_predecessor(root: Path, version: int) -> list[dict[str, Any]]:
    if version not in {17, 18}:
        raise ValueError("probe predecessor must be V17 or V18")
    database = ledger_db.database_path(root)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        before = [dict(row) for row in connection.execute(
            "SELECT * FROM durable_questions ORDER BY published_sequence"
        )]
        for row in before:
            connection.execute(
                "UPDATE durable_questions SET content_digest=?,answer_digest=? WHERE question_ref=?",
                (
                    hashlib.sha256(row["question_text"].encode("utf-8")).hexdigest(),
                    "sha256:" + hashlib.sha256(row["answer"].encode("utf-8")).hexdigest()
                    if row["answer"] is not None else None,
                    row["question_ref"],
                ),
            )
        if version == 17:
            connection.execute("DELETE FROM durable_questions")
            connection.executescript("""
                CREATE TABLE question_batches(
                    batch_id TEXT PRIMARY KEY, task_id TEXT, attempt_id TEXT,
                    status TEXT, created_at TEXT, answered_at TEXT
                );
                CREATE TABLE question_items(
                    batch_id TEXT, question_key TEXT,
                    canonical_question TEXT, ordinal INTEGER
                );
                CREATE TABLE question_answers(
                    batch_id TEXT, question_key TEXT, answer_original TEXT
                );
            """)
            for index, row in enumerate(before, 1):
                batch_id = f"batch-{index}"
                question_key = f"key-{index}"
                connection.execute(
                    "INSERT INTO question_batches VALUES(?,?,?,?,?,?)",
                    (
                        batch_id, row["task_id"], row["attempt_id"], row["status"],
                        row["created_at"], row["answered_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO question_items VALUES(?,?,?,?)",
                    (batch_id, question_key, row["question_text"], index),
                )
                if row["answer"] is not None:
                    connection.execute(
                        "INSERT INTO question_answers VALUES(?,?,?)",
                        (batch_id, question_key, row["answer"]),
                    )
        connection.execute("ALTER TABLE durable_questions DROP COLUMN question_category")
        connection.execute("DELETE FROM schema_migrations")
        history = (
            ledger_db._SUPPORTED_PREVIOUS_V17_HISTORIES[0]
            if version == 17 else ledger_db._SUPPORTED_PREVIOUS_V18_HISTORIES[0]
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version,name,applied_at,checksum) VALUES(?,?,?,?)",
            [(item_version, name, NOW, digest) for item_version, name, digest in history],
        )
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
    ledger_db._forget_database_readiness(root)
    return before


def _assert_exact_migration(
    version: int,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> None:
    if len(before) != 2 or len(after) != len(before):
        raise AssertionError("durable-question cardinality changed during migration")
    preserved = (
        "task_id", "attempt_id", "question_text", "status", "published_sequence",
        "answer", "created_at", "answered_at", "superseded_at",
    )
    for prior, migrated in zip(before, after):
        version_preserved = preserved + (
            (
                "question_ref", "dispatch_ref", "profile", "task_revision",
                "attempt_generation", "submission_id", "answer_submission_id",
                "answered_sequence",
            ) if version == 18 else ()
        )
        for field in version_preserved:
            if prior[field] != migrated[field]:
                raise AssertionError(f"V{version} question field changed: {field}")
        if version == 17:
            index = int(migrated["published_sequence"])
            suffix = hashlib.sha256(
                f"batch-{index}\x00key-{index}".encode("utf-8")
            ).hexdigest()[:24]
            if migrated["question_ref"] != "question-v17-" + suffix:
                raise AssertionError("V17 question reference is not deterministic")
        if migrated["question_category"] != "requirement":
            raise AssertionError("pre-V19 question did not receive conservative category")
        if migrated["content_digest"] != ledger_db.durable_question_content_digest(
            "requirement", migrated["question_text"],
        ):
            raise AssertionError("migrated question digest is not category-bound")
        expected_answer_digest = (
            hashlib.sha256(migrated["answer"].encode("utf-8")).hexdigest()
            if migrated["answer"] is not None else None
        )
        if migrated["answer_digest"] != expected_answer_digest:
            raise AssertionError("migrated answer idempotency digest is not current")


def _inject_null_question(root: Path, template_ref: str) -> None:
    with sqlite3.connect(ledger_db.database_path(root)) as connection:
        connection.row_factory = sqlite3.Row
        template = dict(connection.execute(
            "SELECT * FROM durable_questions WHERE question_ref=?", (template_ref,)
        ).fetchone())
        template.update({
            "question_ref": "question-injected-null",
            "submission_id": "submission-injected-null",
            "question_category": None,
            "status": "open",
            "published_sequence": 99,
            "answer": None,
            "answer_submission_id": None,
            "answer_digest": None,
            "answered_sequence": None,
            "answered_at": None,
        })
        columns = tuple(template)
        connection.execute(
            "INSERT INTO durable_questions(" + ",".join(columns) + ") VALUES("
            + ",".join("?" for _ in columns) + ")",
            tuple(template[column] for column in columns),
        )
        connection.commit()
    ledger_db._forget_database_readiness(root)


def _probe_version(version: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"cortex-v{version}-question-") as directory:
        root = Path(directory)
        state = _fresh_fixture(root)
        before = _downgrade_to_signed_predecessor(root, version)
        ledger_db.ensure_database(root)
        after, has_more = ledger_db.page_durable_questions(root, TASK_ID, limit=16)
        if has_more:
            raise AssertionError("bounded migration probe unexpectedly paginated")
        _assert_exact_migration(version, before, after)

        pending = _open_question_for_attempt(root, state, "attempt-pending")
        if pending is None or pending["question_text"] != PENDING_TEXT:
            raise AssertionError("migrated pending question is not visible")
        answered = _latest_answered_question_for_attempt(root, state, "attempt-answered")
        if answered is None or answered["answer"] != ANSWERED_VALUE:
            raise AssertionError("migrated answered question is not resumable")

        answered_pending = dict(pending)
        answered_pending.update({
            "status": "answered",
            "answer": NEW_ANSWER,
            "answer_submission_id": "answer-new",
            "answer_digest": hashlib.sha256(NEW_ANSWER.encode("utf-8")).hexdigest(),
            "answered_sequence": 21,
            "answered_at": NOW,
        })
        ledger_db.put_durable_question(root, answered_pending)
        if ledger_db.get_durable_question(
            root, TASK_ID, str(pending["question_ref"]),
        )["answer"] != NEW_ANSWER:
            raise AssertionError("migrated pending question is not answerable")

        history = ledger_db.migration_history(root)
        rows = ledger_db.page_durable_questions(root, TASK_ID, limit=16)[0]
        ledger_db._forget_database_readiness(root)
        ledger_db.ensure_database(root)
        if ledger_db.migration_history(root) != history:
            raise AssertionError("second V19 open changed migration history")
        if ledger_db.page_durable_questions(root, TASK_ID, limit=16)[0] != rows:
            raise AssertionError("second V19 open changed migrated questions")
        expected_history_rows = (
            len(ledger_db._SUPPORTED_PREVIOUS_V17_HISTORIES[0]) + 2
            if version == 17
            else len(ledger_db._SUPPORTED_PREVIOUS_V18_HISTORIES[0]) + 1
        )
        if len(history) != expected_history_rows:
            raise AssertionError("append-only migration history cardinality changed")

        _inject_null_question(root, str(pending["question_ref"]))
        if _open_question_for_attempt(root, state, "attempt-pending") is not None:
            raise AssertionError("post-V19 NULL question authorized a user stop")

        with sqlite3.connect(ledger_db.database_path(root)) as connection:
            connection.execute(
                "UPDATE schema_migrations SET checksum='tampered' WHERE version=19"
            )
            connection.commit()
        ledger_db._forget_database_readiness(root)
        try:
            ledger_db.ensure_database(root)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered V19 migration history was accepted")

        return {
            "version": version,
            "rows": len(after),
            "history_rows": len(history),
            "pending_visible": True,
            "answered_resumable": True,
            "answer_exact": True,
            "second_open_idempotent": True,
            "injected_null_ignored": True,
            "tamper_failed_closed": True,
        }


def _probe_atomic_rollback() -> bool:
    with tempfile.TemporaryDirectory(prefix="cortex-v18-question-rollback-") as directory:
        root = Path(directory)
        _fresh_fixture(root)
        _downgrade_to_signed_predecessor(root, 18)
        database = ledger_db.database_path(root)
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE repair_escrow")
            connection.execute("CREATE TABLE repair_escrow(invalid TEXT)")
            connection.commit()
        try:
            ledger_db.ensure_database(root)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed predecessor unexpectedly migrated")
        with sqlite3.connect(database) as connection:
            columns = {
                str(row[1]) for row in connection.execute(
                    "PRAGMA table_info(durable_questions)"
                )
            }
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            history = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        if "question_category" in columns or version != 18 or history != [(18,)]:
            raise AssertionError("failed V19 migration did not roll back atomically")
        return True


def main() -> int:
    result = {
        "v17": _probe_version(17),
        "v18": _probe_version(18),
        "rollback_atomic": _probe_atomic_rollback(),
    }
    print("question migration probe passed: " + json.dumps(
        result, ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
