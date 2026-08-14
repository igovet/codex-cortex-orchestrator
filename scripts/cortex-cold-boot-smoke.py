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


def report(worker: int, step: int, predecessor_reports: list[str]) -> dict[str, object]:
    evidence = [f"step {step} worker {worker} evidence"]
    if predecessor_reports:
        evidence.append("Predecessor review: " + ", ".join(predecessor_reports))
    return {
        "summary": f"relative step {step} worker {worker} completed",
        "findings": [], "questions": [], "changed_files": [],
        "tests": ["cold-boot v3 simulation"],
        "evidence": evidence,
        "uncertainty": [], "next_action": "advance",
    }


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
        if names != ["start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "record_report", "read_worker_report"]:
            raise AssertionError(f"unexpected Cortex v3 public tools: {names}")
        current = rpc.tool("start_orchestration", start_request)
        replay = rpc.tool("start_orchestration", start_request)
        if (
            not current.get("ok")
            or current.get("replayed") is not False
            or replay.get("replayed") is not True
            or replay.get("task_ref") != current.get("task_ref")
            or not current.get("dispatches")
            or replay.get("dispatches") != []
        ):
            raise AssertionError("identical start did not replay its committed response")
        task_ref = str(current["task_ref"])
        task_directory = next((ledger / "tasks").iterdir()).name

    (project / "tracked.txt").write_text("after\n", encoding="utf-8")
    (project / "delete.txt").unlink()
    (project / "old.txt").rename(project / "new.txt")
    (project / "added.txt").write_text("untracked\n", encoding="utf-8")

    # A fresh process must reconstruct the active relative step read-only.
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        current = rpc.tool("manage_orchestration", {"intent": "inspect", "task_ref": task_ref})
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
            state = json.loads((ledger / "tasks" / task_directory / "current.json").read_text(encoding="utf-8"))
            active_attempts = [
                item for item in state["attempts"]
                if item.get("status") == "awaiting_host_spawn" and not item.get("invalidated")
            ][-len(dispatches):]
            results = []
            for index, (dispatch, attempt) in enumerate(zip(dispatches, active_attempts), 1):
                published = rpc.tool("record_report", {
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": dispatch["profile"],
                    "report": report(index, int(current["step"]), list(attempt.get("context_report_ids") or [])),
                })
                if not published.get("ok"):
                    raise AssertionError(f"record_report failed: {published}")
                read = rpc.tool("read_worker_report", {"task_ref": task_ref, "report_ref": published["report_ref"]})
                if not read.get("ok") or read.get("report", {}).get("summary") != published.get("summary"):
                    raise AssertionError(f"read_worker_report failed: {read}")
                result_value: dict[str, object] = {"report_ref": published["report_ref"]}
                if parallel:
                    result_value["worker"] = index
                results.append(result_value)
            last_payload = {
                "task_ref": task_ref,
                "step": current["step"],
                "results": results,
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
