#!/usr/bin/env python3
"""Exercise Cortex v3 relative one-call-per-wave orchestration over JSON-RPC."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from jsonrpc_harness import JsonRpcHarness  # noqa: E402

SERVER = ROOT / "plugins/cortex/scripts/cortex.py"


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, text=True, capture_output=True, check=True)


def fixture(base: Path) -> tuple[Path, Path]:
    project = base / "project"
    ledger = project / ".codex" / "cortex"
    project.mkdir()
    git(project, "init", "-q")
    git(project, "config", "user.email", "codex@example.invalid")
    git(project, "config", "user.name", "Codex Smoke")
    (project / "tracked.txt").write_text("before\n", encoding="utf-8")
    (project / "delete.txt").write_text("delete me\n", encoding="utf-8")
    (project / "old.txt").write_text("rename me\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "fixture baseline")
    return project, ledger


def waves() -> list[dict[str, object]]:
    return [
        {"workers": [{"phase": "planning"}]},
        {"workers": [{"phase": "research"}, {"phase": "architecture"}]},
        {"workers": [{"phase": "implementation"}]},
        {"workers": [{"phase": "testing"}]},
        {"workers": [{"phase": "code_review"}]},
    ]


def result(worker: int, step: int, parallel: bool) -> dict[str, object]:
    value: dict[str, object] = {
        "report": {
            "summary": f"relative step {step} worker {worker} completed",
            "findings": [], "questions": [], "changed_files": [],
            "tests": ["cold-boot v3 simulation"],
            "evidence": [f"step {step} worker {worker} evidence"],
            "uncertainty": [], "next_action": "advance",
        }
    }
    if parallel:
        value["worker"] = worker
    return value


def run(base: Path) -> dict[str, object]:
    project, ledger = fixture(base)
    start_request = {
        "task": {
            "objective": "prove a fresh JSON-RPC process can complete Cortex v3 by relative waves",
            "complexity": "standard",
            "requirements": ["implementation, verification, documentation, and close invariants"],
            "acceptance_criteria": ["complete every planned wave"],
            "allowed_paths": ["."],
            "verification": ["preserve report, evidence, handoff, and manifest receipts"],
        },
        "waves": waves(),
    }
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        listed = rpc.request("tools/list", {})["tools"]
        names = [item["name"] for item in listed]
        if names != ["start_orchestration", "continue_orchestration", "manage_orchestration"]:
            raise AssertionError(f"unexpected Cortex v3 public tools: {names}")
        current = rpc.tool("start_orchestration", start_request)
        replay = rpc.tool("start_orchestration", start_request)
        if not current.get("ok") or replay != current:
            raise AssertionError("identical start did not replay its committed response")
        task_directory = next((ledger / "tasks").iterdir()).name

    (project / "tracked.txt").write_text("after\n", encoding="utf-8")
    (project / "delete.txt").unlink()
    (project / "old.txt").rename(project / "new.txt")
    (project / "added.txt").write_text("untracked\n", encoding="utf-8")

    # A fresh process must reconstruct the active relative step read-only.
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        current = rpc.tool("manage_orchestration", {"intent": "inspect"})
        continue_calls = 0
        parallel_wave_seen = False
        last_payload = None
        while current["outcome"] != "completed":
            dispatches = current.get("dispatches", [])
            if not dispatches:
                raise AssertionError(f"active relative step {current.get('step')} has no dispatches")
            parallel = len(dispatches) > 1
            parallel_wave_seen |= parallel
            continue_calls += 1
            last_payload = {
                "step": current["step"],
                "results": [result(index, int(current["step"]), parallel) for index in range(1, len(dispatches) + 1)],
            }
            current = rpc.tool("continue_orchestration", last_payload)
            if not current.get("ok"):
                raise AssertionError(f"continue failed: {current}")
        replay = rpc.tool("continue_orchestration", last_payload)
        if replay != current:
            raise AssertionError("final continue retry did not replay after completion")
        if not parallel_wave_seen:
            raise AssertionError("the smoke plan did not return a parallel dispatch wave")

    task_path = ledger / "tasks" / task_directory
    state = json.loads((task_path / "current.json").read_text(encoding="utf-8"))
    task = json.loads((task_path / "task.json").read_text(encoding="utf-8"))
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (task_path / "reports/receipts").glob("*.json")]
    operations = [json.loads(path.read_text(encoding="utf-8")) for path in (ledger / "operations").glob("*.json")]
    if task.get("schema") != "cortex/v7" or state.get("status") != "completed":
        raise AssertionError("v3 did not preserve the cortex/v7 ledger or complete the task")
    if not receipts or any(not item.get("consumed_at") for item in receipts):
        raise AssertionError("every passed worker report must be consumed by evidence")
    if not state.get("handoff_created") or not all(item.get("status") == "committed" for item in operations):
        raise AssertionError("handoff or durable transaction commit is missing")
    return {
        "status": "PASS", "fixture": str(base), "task_directory": str(task_path),
        "continue_calls": continue_calls, "worker_attempts": len(state.get("attempts", [])),
        "report_count": len(receipts), "parallel_wave_seen": parallel_wave_seen,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    arguments = parser.parse_args()
    if arguments.keep:
        result_value = run(Path(tempfile.mkdtemp(prefix="cortex-boot-")))
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-boot-") as directory:
            result_value = run(Path(directory))
    print("cold-boot smoke: " + json.dumps(result_value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
