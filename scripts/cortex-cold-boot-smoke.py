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
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
from jsonrpc_harness import JsonRpcHarness  # noqa: E402
import cortex  # noqa: E402

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
        {"workers": [{"phase": "research"}]},
        {"workers": [{"phase": "architecture"}, {"phase": "database_architecture"}]},
        {"workers": [{"phase": "planning"}]},
        {"workers": [{"phase": "implementation"}]},
        {"workers": [{"phase": "testing"}]},
        {"workers": [{"phase": "code_review"}]},
    ]


def approve_plan_elicitation(request: dict[str, object]) -> dict[str, object]:
    """Answer only the native two-button plan-approval interaction."""
    params = request.get("params")
    if not isinstance(params, dict) or params.get("_meta", {}).get("cortex", {}).get("schema") != "cortex/plan-approval/v1":
        raise AssertionError(f"unexpected native elicitation in cold-boot smoke: {request}")
    schema = params.get("requestedSchema")
    if not isinstance(schema, dict):
        raise AssertionError(f"plan approval elicitation omitted its schema: {request}")
    decision = schema.get("properties", {}).get("decision", {})
    choices = decision.get("oneOf", []) if isinstance(decision, dict) else []
    if not any(isinstance(choice, dict) and choice.get("const") == "approve" for choice in choices):
        raise AssertionError(f"plan approval elicitation omitted its Approve action: {request}")
    return {"action": "accept", "content": {"decision": "approve"}}


def workspace_summary(project: Path) -> dict[str, object]:
    """Return a small, truthful closure-workspace summary.

    The fixture itself intentionally changes project files.  The closure
    contract records that state; it does not equate an uncommitted fixture
    change with an unresolved review finding.
    """
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return {"modified": [], "untracked": [], "staged": [], "committed": "not_required"}
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
            continue
        if len(line) < 4:
            continue
        index, worktree, path = line[0], line[1], line[3:]
        if index != " ":
            staged.append(path)
        if worktree != " ":
            modified.append(path)
    has_changes = bool(modified or untracked or staged)
    return {
        "modified": sorted(modified),
        "untracked": sorted(untracked),
        "staged": sorted(staged),
        "committed": False if has_changes else "not_required",
    }


def passing_closure(project: Path, gate: str) -> dict[str, object]:
    """Build the separate review/close closure required by record_report."""
    return {
        "decision": "pass",
        # The fixture found no actionable debt, so no open blocker can enter
        # the canonical findings table and divert the close gate to rework.
        "findings": [],
        "verification": {
            "executed": [f"Cold-boot fixture verification completed for the {gate} gate."],
            "not_executed": [],
            "required_missing": [],
            "limitations": [],
        },
        "workspace": workspace_summary(project),
    }


def canonical_artifacts(
    ledger: Path, task_id: str, *, kind: str | None = None
) -> list[dict[str, object]]:
    """Read artifact metadata from SQLite without assuming an eager export."""
    artifacts: list[dict[str, object]] = []
    offset = 0
    while True:
        page, next_offset = cortex.db_list_artifacts(
            ledger, task_id, kind=kind, offset=offset, page_size=100,
        )
        artifacts.extend(page)
        if next_offset is None:
            return artifacts
        offset = next_offset


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
    checks = [{
        "command": "git status --short",
        "cwd": str(project),
        "exit_code": 0,
        "evidence": "Command completed and the fixture repository state was observed successfully.",
    }]
    return {
        "summary": f"relative step {step} worker {worker} completed",
        "findings": [], "questions": [], "changed_files": changed_files,
        "tests": checks,
        "evidence": evidence,
        "uncertainty": [],
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
    with JsonRpcHarness(server, project, ledger, elicitation_responder=approve_plan_elicitation) as rpc:
        listed = rpc.request("tools/list", {})["tools"]
        names = [item["name"] for item in listed]
        if names != ["start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "get_report_template", "record_report", "read_dispatch_briefing", "read_worker_report"]:
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
    with JsonRpcHarness(server, project, ledger, elicitation_responder=approve_plan_elicitation) as rpc:
        task_definition = cortex.load_task_definition(ledger / "tasks" / task_directory)
        current = rpc.tool("manage_orchestration", {"intent": "inspect", "task_ref": task_ref})
        continue_calls = 0
        parallel_wave_seen = False
        plan_approval_seen = False
        implementation_applied = False
        dynamic_replan_applied = False
        pending_implementation_drop_rejected = False
        last_payload = None
        while current["outcome"] != "completed":
            if current.get("outcome") == "awaiting_plan_approval":
                if not current.get("plan_review", {}).get("report_ref"):
                    raise AssertionError(f"plan approval omitted its planner report reference: {current}")
                plan_approval_seen = True
                prompt = rpc.tool("manage_orchestration", {
                    "intent": "plan_approval",
                    "task_ref": task_ref,
                    "payload": {"decision": "prompt"},
                })
                if prompt.get("outcome") == "ready_to_spawn":
                    current = prompt
                    continue
                interaction = prompt.get("plan_approval_interaction") or {}
                approve = next((action for action in interaction.get("actions", []) if action.get("id") == "approve"), None)
                if not isinstance(approve, dict) or not isinstance(approve.get("arguments"), dict):
                    raise AssertionError(f"plan approval interaction omitted its Approve action: {prompt}")
                current = rpc.tool("manage_orchestration", approve["arguments"])
                if not current.get("ok"):
                    raise AssertionError(f"plan approval failed: {current}")
                continue
            dispatches = current.get("dispatches", [])
            if not dispatches:
                raise AssertionError(f"active relative step {current.get('step')} has no dispatches")
            parallel = len(dispatches) > 1
            parallel_wave_seen |= parallel
            continue_calls += 1
            state = cortex.load_task_state_for_artifact(ledger / "tasks" / task_directory)
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
                if attempt.get("gate") in {"review", "close"}:
                    publication["closure"] = passing_closure(project, str(attempt["gate"]))
                if dispatch.get("phase") == "plan":
                    publication["planning"] = planning(index, int(current["step"]))
                template = rpc.tool("get_report_template", {
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": dispatch["profile"],
                })
                if not template.get("ok") or template.get("persisted") is not False:
                    raise AssertionError(f"get_report_template failed: {template}")
                draft_path = Path(str(template["draft_path"]))
                if not draft_path.is_file():
                    raise AssertionError(f"get_report_template did not create its draft file: {template}")
                published = rpc.tool("record_report", {
                    **publication,
                    "draft_ref": template["draft_ref"],
                })
                if not published.get("ok"):
                    raise AssertionError(f"record_report failed: {published}")
                if draft_path.exists():
                    raise AssertionError("record_report did not delete the successfully persisted draft file")
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
            active_phases = {str(item.get("phase")) for item in dispatches}
            if active_phases == {"discover"} and not dynamic_replan_applied:
                rejected = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "documentation"}]},
                    ],
                    "reason": "exercise the pending implementation retention invariant",
                })
                if rejected.get("ok"):
                    raise AssertionError("dynamic pipeline accepted removal of pending implementation")
                diagnostics = rejected.get("diagnostics") or []
                if (
                    rejected.get("attempt_budget_consumed") is not False
                    or not diagnostics
                    or "pending implementation" not in str(diagnostics[0].get("message", ""))
                ):
                    raise AssertionError(f"pending implementation rejection lost its safe diagnostic: {rejected}")
                pending_implementation_drop_rejected = True
                current = rpc.tool("continue_orchestration", {
                    **last_payload,
                    "future_waves": [
                        {"workers": [{"phase": "architecture"}, {"phase": "database_architecture"}]},
                        {"workers": [{"phase": "plan"}]},
                        {"workers": [{"phase": "implementation"}]},
                        {"workers": [{"phase": "qa"}]},
                        {"workers": [{"phase": "security"}, {"phase": "performance"}]},
                        {"workers": [{"phase": "review"}]},
                    ],
                    "reason": "add required audit phases while retaining the pending delivery phase",
                })
                dynamic_replan_applied = True
            else:
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
    if not dynamic_replan_applied or not pending_implementation_drop_rejected:
        raise AssertionError("the smoke did not exercise dynamic replanning and implementation retention")
    if not implementation_applied:
        raise AssertionError("the dynamically replanned pipeline never executed implementation")

    task_path = ledger / "tasks" / task_directory
    state = cortex.load_task_state_for_artifact(task_path)
    task = cortex.load_task_definition(task_path, state)
    receipt_artifacts = canonical_artifacts(ledger, state["task_id"], kind="report_receipt")
    receipts = [
        json.loads(cortex.db_read_artifact_content(ledger, state["task_id"], str(item["artifact_ref"])))
        for item in receipt_artifacts
    ]
    receipt_states = [
        cortex.db_get_task_document(ledger, state["task_id"], f"receipt_state:{item['receipt_id']}")
        for item in receipts
    ]
    if task.get("schema") != "cortex/v8" or state.get("schema") != "cortex/v8" or state.get("status") != "completed":
        raise AssertionError("public orchestration did not preserve the cortex/v8 ledger or complete the task")
    if not receipts or any(not item or not item.get("consumed_at") for item in receipt_states):
        raise AssertionError("every passed worker report must have a consumed canonical receipt state")
    if not state.get("handoff_created"):
        raise AssertionError("handoff or durable transaction commit is missing")
    if cortex.current_planning_manifest(task_path) is None:
        raise AssertionError("Planner work-breakdown manifest is missing")
    planning_artifacts = canonical_artifacts(ledger, state["task_id"], kind="planning_revision")
    if not planning_artifacts:
        raise AssertionError("Planner work-breakdown artifacts are missing from the canonical SQLite catalog")
    passed_gates = {
        str(item.get("gate"))
        for item in state.get("attempts", [])
        if item.get("status") == "passed" and not item.get("invalidated")
    }
    expected_gates = {
        "discover", "architecture", "database_architecture", "plan",
        "implementation", "qa", "security", "performance", "review",
        "documentation", "close",
    }
    if not expected_gates.issubset(passed_gates):
        raise AssertionError(
            "dynamic pipeline skipped required gates: "
            + ", ".join(sorted(expected_gates - passed_gates))
        )
    return {
        "status": "PASS", "fixture": str(base), "task_directory": str(task_path),
        "continue_calls": continue_calls, "worker_attempts": len(state.get("attempts", [])),
        "report_count": len(receipts), "parallel_wave_seen": parallel_wave_seen,
        "plan_approval_seen": plan_approval_seen,
        "dynamic_replan_applied": dynamic_replan_applied,
        "pending_implementation_drop_rejected": pending_implementation_drop_rejected,
        "implementation_phase_seen": "implementation" in passed_gates,
        "passed_gates": sorted(passed_gates),
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
