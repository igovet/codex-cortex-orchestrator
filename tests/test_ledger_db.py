"""Migration and storage invariants for the SQLite Cortex ledger."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex
from cortex_runtime import ledger_db


class LedgerDatabaseTests(unittest.TestCase):
    @staticmethod
    def create_task(root: Path, task_id: str = "artifact-task") -> None:
        definition = {"schema": cortex.SCHEMA, "task_id": task_id, "created_at": "2026-01-01T00:00:00+00:00"}
        state = {
            "schema": cortex.SCHEMA, "task_id": task_id, "task_number": 1,
            "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00",
        }
        ledger_db.create_task(root, definition, state, f"tasks/0001-{task_id}")

    def test_fresh_ledger_records_each_numbered_migration_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            # This is the first normal server path after a Marketplace update:
            # no separate administrator command may be required to migrate a
            # developer's project ledger before the first MCP call.
            self.assertEqual(cortex.ledger_root({"project_root": str(root.parents[1])}), root)
            first = ledger_db.migration_history(root)
            self.assertEqual(cortex.ledger_root({"project_root": str(root.parents[1])}), root)
            second = ledger_db.migration_history(root)

            self.assertEqual(first, second)
            self.assertEqual(
                [item["version"] for item in first],
                list(range(1, ledger_db.DATABASE_SCHEMA_VERSION + 1)),
            )
            self.assertTrue((root / "cortex.db").is_file())
            self.assertFalse((root / "task-index.json").exists())

    def test_pre_database_files_are_never_imported_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            task_dir = root / "tasks" / "0001-prior-state"
            task_dir.mkdir(parents=True)
            index = root / "task-index.json"
            index.write_text(json.dumps({"prior-task": {"number": 1, "directory": task_dir.name}}), encoding="utf-8")
            index.chmod(0o600)
            task_file = task_dir / "task.json"
            task_file.write_text(json.dumps({"task_id": "prior-task"}), encoding="utf-8")
            task_file.chmod(0o600)

            ledger_db.ensure_database(root)

            self.assertIsNone(ledger_db.load_task(root, "prior-task"))
            self.assertTrue(index.is_file())
            self.assertTrue(task_file.is_file())
            self.assertEqual(
                [item["name"] for item in ledger_db.migration_history(root)],
                ["sqlite-ledger-base", "immutable-artifact-catalog-and-chunks"],
            )

    def test_artifact_catalog_chunks_large_text_without_field_limit_and_signs_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            content = ("# Harvest section\n\n" + ("verified behavior line\n" * 4000)).strip() + "\n"
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="report_markdown", title="reports/markdown/report-0001.md",
                mime_type="text/markdown", content=content, export_path="reports/markdown/report-0001.md",
            )
            self.assertGreater(artifact["byte_size"], 64 * 1024)
            self.assertGreater(artifact["chunk_count"], 2)
            first = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"], max_bytes=8192,
            )
            self.assertFalse(first["complete"])
            self.assertLessEqual(first["returned_bytes"], 8192)
            self.assertTrue(first["content_part"].startswith("# Harvest section"))
            cursor = ledger_db.encode_artifact_cursor(root, {
                "type": "artifact_read", "task_id": "artifact-task", "artifact_ref": artifact["artifact_ref"],
                "digest_sha256": artifact["digest_sha256"], "byte_offset": first["next_byte_offset"], "audience": "coordinator",
            })
            decoded = ledger_db.decode_artifact_cursor(root, cursor)
            self.assertEqual(decoded["byte_offset"], first["next_byte_offset"])
            with self.assertRaisesRegex(ValueError, "signature"):
                ledger_db.decode_artifact_cursor(root, cursor[:-1] + ("A" if cursor[-1] != "A" else "B"))
            listed, next_offset = ledger_db.list_artifacts(root, "artifact-task", kind="report_markdown", page_size=1)
            self.assertEqual(next_offset, None)
            self.assertEqual(listed, [artifact])
            with sqlite3.connect(root / "cortex.db") as connection:
                names = {row[1] for row in connection.execute("PRAGMA index_list('artifacts')")}
            self.assertIn("artifacts_task_kind_created_idx", names)
            self.assertIn("artifacts_task_created_idx", names)

    def test_artifact_transport_makes_progress_for_a_multibyte_character(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="report_markdown", title="reports/markdown/unicode.md",
                mime_type="text/markdown", content="😀 verified", export_path="reports/markdown/unicode.md",
            )

            first = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"], max_bytes=1,
            )

            self.assertEqual(first["content_part"], "😀")
            self.assertEqual(first["returned_bytes"], len("😀".encode("utf-8")))
            self.assertEqual(first["next_byte_offset"], len("😀".encode("utf-8")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
