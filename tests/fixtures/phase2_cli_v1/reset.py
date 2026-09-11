#!/usr/bin/env python3
"""Reset one Phase 2 fixture into a new disposable destination."""

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TASKS = {f"F-0{i}" for i in range(1, 7)}


def reset(task_id: str, destination: Path) -> dict:
    if task_id not in TASKS:
        raise ValueError(f"unknown task_id: {task_id}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing destination: {destination}")
    source = ROOT / "trees" / task_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return {"task_id": task_id, "source": source.relative_to(ROOT).as_posix(),
            "destination": str(destination), "outcome": "reset", "exit_code": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(reset(args.task, args.destination), sort_keys=True))
    except (FileExistsError, ValueError) as exc:
        print(json.dumps({"outcome": "error", "exit_code": 1, "reason": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
