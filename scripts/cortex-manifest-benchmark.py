#!/usr/bin/env python3
"""Generate a large synthetic repository and benchmark bounded manifest capture."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

import cortex


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=50000)
    parser.add_argument("--max-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.files < 1 or args.files > 100000:
        raise SystemExit("--files must be between 1 and 100000")
    with tempfile.TemporaryDirectory(prefix="cortex-manifest-benchmark-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        generation_started = time.monotonic()
        for index in range(args.files):
            bucket = project / f"bucket-{index // 1000:03d}"
            bucket.mkdir(exist_ok=True)
            (bucket / f"file-{index:06d}.txt").write_text(f"fixture-{index}\n", encoding="utf-8")
        generation_seconds = time.monotonic() - generation_started
        policy = dict(cortex.TRACKER_POLICY)
        policy["manifest_limits"] = {
            "max_entries": args.files,
            "max_hashed_bytes": max(args.files * 64, 1024),
            "max_seconds": args.max_seconds,
        }
        first_started = time.monotonic()
        first = cortex.capture_project_manifest(project, policy=policy)
        first_seconds = time.monotonic() - first_started
        second_started = time.monotonic()
        second = cortex.capture_project_manifest(project, policy=policy)
        second_seconds = time.monotonic() - second_started
    result = {
        "schema": "cortex/manifest-benchmark/v1",
        "files": args.files,
        "generation_seconds": round(generation_seconds, 3),
        "first_capture_seconds": round(first_seconds, 3),
        "cached_capture_seconds": round(second_seconds, 3),
        "first_partial": bool(first["partial_manifest"]["partial"]),
        "cached_partial": bool(second["partial_manifest"]["partial"]),
        "cached_digest_hits": second["capture_metrics"]["digest_cache_hits"],
        "digests_equal": first["digest"] == second["digest"],
        "target_met": (
            not first["partial_manifest"]["partial"]
            and not second["partial_manifest"]["partial"]
            and first["digest"] == second["digest"]
            and second["capture_metrics"]["digest_cache_hits"] == args.files
        ),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
