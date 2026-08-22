from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class ManifestBudgetTests(unittest.TestCase):
    def test_manifest_ignores_advisory_entry_limit_and_keeps_all_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            for index in range(5):
                (root / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
            policy = dict(cortex.TRACKER_POLICY)
            policy["manifest_limits"] = {"max_entries": 2, "max_hashed_bytes": 1024, "max_seconds": 30}
            manifest = cortex.capture_project_manifest(root, policy=policy)
        self.assertFalse(manifest["partial_manifest"]["partial"])
        self.assertIsNone(manifest["partial_manifest"]["reason"])
        self.assertEqual(manifest["entry_count"], 5)
        self.assertEqual(set(manifest["entries"]), {f"file-{index}.txt" for index in range(5)})

    def test_full_capture_sees_changed_path_despite_advisory_cutoff_values(self) -> None:
        """Capture policy values never hide a canonical file from comparison."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "a.txt").write_text("stable\n", encoding="utf-8")
            (root / "b.txt").write_text("baseline\n", encoding="utf-8")
            policy = dict(cortex.TRACKER_POLICY)
            policy["manifest_limits"] = {"max_entries": 1, "max_hashed_bytes": 1024, "max_seconds": 30}
            baseline = cortex.capture_project_manifest(root, policy=policy)
            (root / "z-unscanned.txt").write_text("changed after cutoff\n", encoding="utf-8")
            current = cortex.capture_project_manifest(root, policy=policy)

        comparison = cortex.compare_manifests(baseline, current)
        self.assertFalse(baseline["partial_manifest"]["partial"])
        self.assertFalse(current["partial_manifest"]["partial"])
        self.assertTrue(comparison["complete"])
        self.assertIn("z-unscanned.txt", comparison["changed_paths"])

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
