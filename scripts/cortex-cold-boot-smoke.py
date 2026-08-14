#!/usr/bin/env python3
"""Exercise the Cortex v2 one-call-per-wave facade through MCP JSON-RPC."""
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
PRINCIPAL = "cold-boot"
TASK_ID = "cold-boot"


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
        {"wave_id": "plan", "delegations": [{"gate": "plan", "agent": "planner"}]},
        {
            "wave_id": "discovery",
            "delegations": [
                {"gate": "discover", "agent": "explorer"},
                {"gate": "architecture", "agent": "architect"},
            ],
        },
        {"wave_id": "implementation", "delegations": [{"gate": "implementation", "agent": "general"}]},
        {"wave_id": "qa", "delegations": [{"gate": "qa", "agent": "qa_engineer"}]},
        {"wave_id": "review", "delegations": [{"gate": "review", "agent": "code_reviewer"}]},
    ]


def completion(request: dict[str, object], wave_id: str) -> dict[str, object]:
    return {
        "attempt_id": request["attempt_id"],
        "host_tool": request["host_tool"],
        "host_agent_id": f"smoke-host-{request['attempt_id']}",
        "host_task_name": request["task_name"],
        "host_model": request.get("model") or request["expected_model"],
        "host_reasoning_effort": request["reasoning_effort"],
        "status": "passed",
        "report": {
            "summary": f"{wave_id} worker completed",
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": ["cold-boot facade simulation"],
            "evidence": [f"{wave_id} evidence"],
            "uncertainty": [],
            "next_action": "advance the facade",
        },
    }


def run(base: Path) -> dict[str, object]:
    project, ledger = fixture(base)
    start_request = {
        "operation": "start",
        "submission_id": "cold-boot-start",
        "principal": PRINCIPAL,
        "thread_id": PRINCIPAL,
        "task": {
            "task_id": TASK_ID,
            "objective": "prove a fresh JSON-RPC process can complete Cortex v2 by waves",
            "complexity": "C2",
            "requirements": ["implementation, verification, documentation, and close invariants"],
            "acceptance_criteria": ["complete every planned wave"],
            "allowed_paths": ["."],
            "verification": ["preserve report, evidence, handoff, and manifest receipts"],
        },
        "waves": waves(),
        "host_capabilities": {
            "spawn_agent_models": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
            "create_thread_models": ["gpt-5.6-luna"],
        },
    }
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        listed = rpc.request("tools/list", {})["tools"]
        if [item["name"] for item in listed] != ["orchestrate"]:
            raise AssertionError("Cortex v2 must publish exactly one MCP tool")
        current = rpc.tool("orchestrate", start_request)
        replay = rpc.tool("orchestrate", start_request)
        if not current.get("ok") or replay != {**current, "idempotent": True}:
            raise AssertionError("identical start submission did not replay its committed response")
        task_directory = next((ledger / "tasks").iterdir()).name

    (project / "tracked.txt").write_text("after\n", encoding="utf-8")
    (project / "delete.txt").unlink()
    (project / "old.txt").rename(project / "new.txt")
    (project / "added.txt").write_text("untracked\n", encoding="utf-8")

    # A fresh MCP process must reconstruct the same active wave without a write.
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        current = rpc.tool("orchestrate", {
            "operation": "inspect", "task_id": TASK_ID, "principal": PRINCIPAL, "thread_id": PRINCIPAL,
        })
        advance_calls = 0
        parallel_wave_seen = False
        while current["state"] != "completed":
            requests = current.get("spawn_requests", [])
            if not requests:
                raise AssertionError(f"active wave {current.get('wave_id')} produced no spawn requests")
            parallel_wave_seen |= len(requests) > 1
            wave_id = str(current["wave_id"])
            advance_calls += 1
            current = rpc.tool("orchestrate", {
                "operation": "advance",
                "submission_id": f"cold-boot-advance-{advance_calls:02d}",
                "task_id": TASK_ID,
                "wave_id": wave_id,
                "principal": PRINCIPAL,
                "thread_id": PRINCIPAL,
                "completions": [completion(request, wave_id) for request in requests],
            })
            if not current.get("ok"):
                raise AssertionError(f"advance failed: {current}")
        if not parallel_wave_seen:
            raise AssertionError("the smoke plan did not return a parallel spawn_requests array")

    task_path = ledger / "tasks" / task_directory
    state = json.loads((task_path / "current.json").read_text(encoding="utf-8"))
    task = json.loads((task_path / "task.json").read_text(encoding="utf-8"))
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (task_path / "reports/receipts").glob("*.json")]
    operations = [json.loads(path.read_text(encoding="utf-8")) for path in (ledger / "operations").glob("*.json")]
    if task.get("schema") != "cortex/v7" or state.get("status") != "completed":
        raise AssertionError("facade did not preserve the cortex/v7 ledger or complete the task")
    if not receipts or any(not item.get("consumed_at") for item in receipts):
        raise AssertionError("every passed worker report must be consumed by evidence")
    if not state.get("handoff_created") or not all(item.get("status") == "committed" for item in operations):
        raise AssertionError("handoff or durable transaction commit is missing")
    return {
        "status": "PASS",
        "fixture": str(base),
        "task_directory": str(task_path),
        "advance_calls": advance_calls,
        "worker_attempts": len(state.get("attempts", [])),
        "report_count": len(receipts),
        "parallel_wave_seen": parallel_wave_seen,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="preserve and print the isolated fixture directory")
    arguments = parser.parse_args()
    if arguments.keep:
        base = Path(tempfile.mkdtemp(prefix="cortex-boot-"))
        result = run(base)
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-boot-") as directory:
            result = run(Path(directory))
    print("cold-boot smoke: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
