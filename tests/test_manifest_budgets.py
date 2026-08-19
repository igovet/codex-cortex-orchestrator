from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class ManifestBudgetTests(unittest.TestCase):
    def test_manifest_returns_explicit_partial_receipt_at_entry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            for index in range(5):
                (root / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
            policy = dict(cortex.TRACKER_POLICY)
            policy["manifest_limits"] = {"max_entries": 2, "max_hashed_bytes": 1024, "max_seconds": 30}
            manifest = cortex.capture_project_manifest(root, policy=policy)
        self.assertTrue(manifest["partial_manifest"]["partial"])
        self.assertEqual(manifest["partial_manifest"]["reason"], "entry_limit")
        self.assertEqual(manifest["entry_count"], 2)

    def test_unchanged_file_digest_is_reused_from_bounded_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "stable.txt").write_text("stable", encoding="utf-8")
            first = cortex.capture_project_manifest(root)
            second = cortex.capture_project_manifest(root)
        self.assertEqual(first["digest"], second["digest"])
        self.assertGreaterEqual(second["capture_metrics"]["digest_cache_hits"], 1)

    def test_cached_recapture_reuses_immutable_snapshot_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "stable.txt").write_text("stable", encoding="utf-8")
            ledger = root / ".codex" / "cortex"
            cortex.ensure_ledger_database(ledger)
            task_dir = ledger / "tasks" / "task-manifest-cache"
            task_dir.mkdir(parents=True)
            first = cortex.capture_project_manifest(root)
            first_ref = cortex.store_manifest_snapshot(task_dir, first)
            second = cortex.capture_project_manifest(root)
            second_ref = cortex.store_manifest_snapshot(task_dir, second)
        self.assertEqual(first_ref, second_ref)
        self.assertGreaterEqual(second["capture_metrics"]["digest_cache_hits"], 1)


if __name__ == "__main__":
    unittest.main()
