#!/usr/bin/env python3
"""Create a clean, non-overwriting Phase 3 protocol trial directory."""

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def reset(destination: Path) -> dict:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    for name in ("manifest.json", "workloads-v1.json", "ledger_schema.json", "README.md"):
        shutil.copy2(ROOT / name, destination / name)
    (destination / "ledger.json").write_text(json.dumps({"suite_version": "phase3-consultation-v1", "records": []}, indent=2) + "\n", encoding="utf-8")
    return {"suite_version": "phase3-consultation-v1", "destination": str(destination), "outcome": "reset", "exit_code": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(reset(args.destination), sort_keys=True))
    except FileExistsError as exc:
        print(json.dumps({"outcome": "error", "exit_code": 1, "reason": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
