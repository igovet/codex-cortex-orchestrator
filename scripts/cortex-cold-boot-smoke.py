#!/usr/bin/env python3
"""Exercise the public Cortex one-call-per-wave orchestration over JSON-RPC."""
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


def report(
    worker: int,
    step: int,
    predecessor_reports: list[str],
    gate: str,
    acceptance: list[str],
    verification: list[str],
    project: Path,
    task_acceptance: list[str],
    task_verification: list[str],
    changed_files: list[str],
) -> dict[str, object]:
    evidence = [f"step {step} worker {worker} observed the cold-boot fixture state"]
    if predecessor_reports:
        evidence.append("Predecessor review: " + ", ".join(predecessor_reports))
    for index, _criterion in enumerate(acceptance, 1):
        evidence.append(f"Gate acceptance {index}: PASS - Repository path inspection and fixture state comparison produced conclusive evidence.")
    for index, _criterion in enumerate(verification, 1):
        evidence.append(f"Gate verification {index}: PASS - Cold-boot verification command completed successfully with concrete observed output.")
    if gate == "close":
        for index, _criterion in enumerate(task_acceptance, 1):
            evidence.append(f"Task acceptance {index}: PASS - Completed workflow provides concrete end-to-end fixture evidence.")
        for index, _criterion in enumerate(task_verification, 1):
            evidence.append(f"Task verification {index}: PASS - Final cold-boot check completed successfully with observed output.")
    checks: list[object]
    if gate in {"implementation", "qa", "security", "performance", "accessibility", "ux", "review", "documentation", "close"}:
        checks = [{
            "command": "git status --short",
            "cwd": str(project),
            "exit_code": 0,
            "evidence": "Command completed and the fixture repository state was observed successfully.",
        }]
    else:
        checks = ["cold-boot public-orchestration simulation"]
    return {
        "summary": f"relative step {step} worker {worker} completed",
        "findings": [], "questions": [], "changed_files": changed_files,
        "tests": checks,
        "evidence": evidence,
        "uncertainty": [], "next_action": "advance",
    }


def planning(worker: int, step: int) -> dict[str, object]:
    return {
        "overview": f"Cold-boot planner breakdown for relative step {step}.",
        "work_packages": [{
            "id": "smoke_core",
            "title": "Cold-boot core",
            "objective": "Exercise the durable Planner work-breakdown protocol.",
            "allowed_paths": ["."],
            "microtasks": [{
                "id": "smoke_validate",
                "title": "Validate the smoke path",
                "objective": f"Record the bounded work item owned by simulated worker {worker}.",
                "profile": "backend_dev",
                "allowed_paths": ["."],
                "acceptance_criteria": ["The work breakdown is persisted."],
                "verification": ["Run the cold-boot smoke workflow."],
            }],
        }],
    }


def run(base: Path, server: Path = SERVER) -> dict[str, object]:
    project, ledger = fixture(base)
    start_request = {
        "task": {
            "user_request": "prove a fresh JSON-RPC process can complete public Cortex orchestration by relative waves",
            "complexity": "standard",
            "requirements": ["implementation, verification, documentation, and close invariants"],
            "acceptance_criteria": ["complete every planned wave"],
            "allowed_paths": ["."],
            "verification": ["preserve report, evidence, handoff, and manifest receipts"],
        },
        "waves": waves(),
    }
    with JsonRpcHarness(server, project, ledger) as rpc:
        listed = rpc.request("tools/list", {})["tools"]
        names = [item["name"] for item in listed]
        if names != ["start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "record_report", "read_dispatch_briefing", "read_worker_report"]:
            raise AssertionError(f"unexpected Cortex public tools: {names}")
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

    # A fresh process must reconstruct the active relative step read-only.
    with JsonRpcHarness(server, project, ledger) as rpc:
        task_definition = json.loads((ledger / "tasks" / task_directory / "task.json").read_text(encoding="utf-8"))
        current = rpc.tool("manage_orchestration", {"intent": "inspect", "task_ref": task_ref})
        continue_calls = 0
        parallel_wave_seen = False
        plan_approval_seen = False
        implementation_applied = False
        last_payload = None
        while current["outcome"] != "completed":
            if current.get("outcome") == "awaiting_plan_approval":
                if not current.get("plan_review", {}).get("report_ref"):
                    raise AssertionError(f"plan approval omitted its planner report reference: {current}")
                plan_approval_seen = True
                current = rpc.tool("manage_orchestration", {
                    "intent": "plan_approval",
                    "task_ref": task_ref,
                    "payload": {"decision": "approve"},
                })
                if not current.get("ok"):
                    raise AssertionError(f"plan approval failed: {current}")
                continue
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
                changed_files: list[str] = []
                if dispatch.get("phase") == "implementation" and not implementation_applied:
                    (project / "tracked.txt").write_text("after\n", encoding="utf-8")
                    (project / "delete.txt").unlink()
                    (project / "old.txt").rename(project / "new.txt")
                    (project / "added.txt").write_text("untracked\n", encoding="utf-8")
                    changed_files = ["added.txt", "delete.txt", "new.txt", "old.txt", "tracked.txt"]
                    implementation_applied = True
                worker_report = report(
                    index,
                    int(current["step"]),
                    list(attempt.get("context_report_ids") or []),
                    str(attempt["gate"]),
                    list(attempt.get("acceptance_criteria") or []),
                    list(attempt.get("verification") or []),
                    project,
                    list(task_definition.get("acceptance_criteria") or []),
                    list(task_definition.get("verification") or []),
                    changed_files,
                )
                worker_report["evidence"].append(
                    "Dispatch briefing reviewed: " + str(attempt["briefing_digest"])
                )
                publication = {
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": dispatch["profile"],
                    "report": worker_report,
                }
                if dispatch.get("phase") == "plan":
                    publication["planning"] = planning(index, int(current["step"]))
                published = rpc.tool("record_report", publication)
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
        if not replay.get("ok") or not replay.get("replayed"):
            raise AssertionError("final continue retry did not return a replay receipt")
        if replay.get("dispatches"):
            raise AssertionError("final continue retry repeated native dispatches")
        if replay.get("task_ref") != current.get("task_ref") or replay.get("status") != current.get("status"):
            raise AssertionError("final continue retry lost the completed task identity")
    if not parallel_wave_seen:
        raise AssertionError("the smoke plan did not return a parallel dispatch wave")
    if not plan_approval_seen:
        raise AssertionError("the C2 smoke plan did not pause for post-plan approval")

    task_path = ledger / "tasks" / task_directory
    state = json.loads((task_path / "current.json").read_text(encoding="utf-8"))
    task = json.loads((task_path / "task.json").read_text(encoding="utf-8"))
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (task_path / "reports/receipts").glob("*.json")]
    operations = [json.loads(path.read_text(encoding="utf-8")) for path in (ledger / "operations").glob("*.json")]
    if task.get("schema") != "cortex/v8" or state.get("schema") != "cortex/v8" or state.get("status") != "completed":
        raise AssertionError("public orchestration did not preserve the cortex/v8 ledger or complete the task")
    if not receipts or any(not item.get("consumed_at") for item in receipts):
        raise AssertionError("every passed worker report must be consumed by evidence")
    if not state.get("handoff_created") or not all(item.get("status") == "committed" for item in operations):
        raise AssertionError("handoff or durable transaction commit is missing")
    if not (task_path / "planning/manifest.json").is_file():
        raise AssertionError("Planner work-breakdown manifest is missing")
    return {
        "status": "PASS", "fixture": str(base), "task_directory": str(task_path),
        "continue_calls": continue_calls, "worker_attempts": len(state.get("attempts", [])),
        "report_count": len(receipts), "parallel_wave_seen": parallel_wave_seen,
        "plan_approval_seen": plan_approval_seen,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--server", type=Path, default=SERVER, help="Cortex MCP server path to exercise")
    arguments = parser.parse_args()
    server = arguments.server.expanduser().resolve()
    if not server.is_file() or server.is_symlink():
        raise SystemExit(f"cold-boot smoke: invalid Cortex server path: {server}")
    if arguments.keep:
        result_value = run(Path(tempfile.mkdtemp(prefix="cortex-boot-")), server)
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-boot-") as directory:
            result_value = run(Path(directory), server)
    print("cold-boot smoke: " + json.dumps(result_value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
