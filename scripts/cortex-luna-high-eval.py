#!/usr/bin/env python3
"""Fixture and optional live evaluation for a Luna-high Cortex parent."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "plugins/cortex/scripts/cortex.py"
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
import cortex  # noqa: E402


def workspace_summary(project: Path) -> dict[str, object]:
    """Describe the actual fixture workspace without treating it as a blocker."""
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
    """Build a valid no-blocker closure for deterministic fixture reports."""
    return {
        "decision": "pass",
        "findings": [],
        "verification": {
            "executed": [f"Deterministic Luna-high fixture verification completed for the {gate} gate."],
            "not_executed": [],
            "required_missing": [],
            "limitations": [],
        },
        "workspace": workspace_summary(project),
    }


def canonical_artifacts(
    ledger: Path, task_id: str, *, kind: str | None = None
) -> list[dict[str, object]]:
    """List canonical SQLite artifacts without relying on local projections."""
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


def report(label: str, project: Path, changed_files: list[str] | None = None) -> dict[str, object]:
    return {
        "summary": label, "findings": [], "questions": [], "changed_files": changed_files or [],
        "tests": [{
            "command": "python3 -c 'print(\"luna-high fixture\")'",
            "cwd": str(project),
            "exit_code": 0,
            "evidence": "The deterministic fixture command printed luna-high fixture and exited zero.",
        }],
        "evidence": [label], "uncertainty": [],
        "next_action": "advance",
    }


def task(user_request: str, complexity: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "user_request": user_request,
        "acceptance_criteria": ["The requested fixture lifecycle completes with a verified handoff."],
        "verification": ["Run and record the deterministic fixture check through the close gate."],
    }
    if complexity is not None:
        value["complexity"] = complexity
    return value


def planning(label: str) -> dict[str, object]:
    return {
        "overview": f"Deterministic work breakdown for {label}.",
        "work_packages": [{
            "id": "fixture_core",
            "title": "Complete fixture lifecycle",
            "objective": "Exercise the current durable report and close contract.",
            "allowed_paths": ["."],
            "microtasks": [{
                "id": "fixture_verify",
                "title": "Verify fixture lifecycle",
                "objective": "Record a complete, reproducible fixture result.",
                "profile": "backend_dev",
                "allowed_paths": ["."],
                "acceptance_criteria": ["The fixture result is recorded."],
                "verification": ["Run the deterministic fixture command."],
            }],
        }],
    }


def finish(project: Path, current: dict[str, object]) -> dict[str, object]:
    if not current.get("ok"):
        raise AssertionError(current)
    while current.get("outcome") != "completed":
        if current.get("outcome") == "awaiting_plan_approval":
            current = cortex.manage_orchestration({
                "project_root": str(project),
                "task_ref": current["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "approve"},
            })
            if not current.get("ok"):
                raise AssertionError(current)
            continue
        dispatches = current.get("dispatches") or []
        parallel = len(dispatches) > 1
        ledger = cortex.ledger_root({"project_root": str(project)})
        registry = cortex._operation_registry(ledger)
        task_id = next(
            candidate for candidate, record in registry["tasks"].items()
            if record.get("start", {}).get("task_ref") == current["task_ref"]
        )
        task_dir, state, _ = cortex._v3_task_state(ledger, task_id)
        task_definition = cortex.load_task_definition(task_dir, state)
        active_attempts = [
            item for item in state["attempts"]
            if item.get("status") not in cortex.TERMINAL_ATTEMPT_STATUSES
            and item.get("gate") in cortex.active_gates(state)
        ][-len(dispatches):]
        results = []
        for worker, (dispatch, attempt) in enumerate(zip(dispatches, active_attempts), 1):
            label = f"step {current['step']} worker {worker}"
            changed_files: list[str] = []
            if attempt.get("gate") == "implementation":
                (project / "result.md").write_text("Verified Luna-high fixture result.\n", encoding="utf-8")
                changed_files = ["result.md"]
            worker_report = report(label, project, changed_files)
            evidence = worker_report["evidence"]
            predecessor_reports = list(attempt.get("context_report_ids") or [])
            if predecessor_reports:
                evidence.append("Predecessor review: " + ", ".join(predecessor_reports))
            for index, _criterion in enumerate(attempt.get("acceptance_criteria") or [], 1):
                evidence.append(f"Gate acceptance {index}: PASS - Deterministic fixture lifecycle produced the required recorded result.")
            for index, _criterion in enumerate(attempt.get("verification") or [], 1):
                evidence.append(f"Gate verification {index}: PASS - Exact deterministic fixture command completed with exit code zero.")
            if attempt.get("gate") == "close":
                for index, _criterion in enumerate(task_definition.get("acceptance_criteria") or [], 1):
                    evidence.append(f"Task acceptance {index}: PASS - Completed fixture lifecycle produced a durable verified handoff.")
                for index, _criterion in enumerate(task_definition.get("verification") or [], 1):
                    evidence.append(f"Task verification {index}: PASS - Final deterministic fixture check completed with exit code zero.")
            evidence.append("Dispatch briefing reviewed: " + str(attempt["briefing_digest"]))
            publication: dict[str, object] = {
                "project_root": str(project),
                "task_id": state["task_id"],
                "attempt_id": attempt["attempt_id"],
                "profile": dispatch["profile"],
                "report": worker_report,
            }
            if attempt.get("gate") in {"review", "close"}:
                publication["closure"] = passing_closure(project, str(attempt["gate"]))
            if attempt.get("gate") == "plan":
                publication["planning"] = planning(label)
            published = cortex.publish_worker_report(publication)
            if not published.get("ok"):
                raise AssertionError(published)
            value: dict[str, object] = {"report_ref": published["report_ref"]}
            if parallel:
                value["worker"] = worker
            results.append(value)
        current = cortex.continue_orchestration({
            "project_root": str(project), "step": current["step"], "results": results,
        })
        if not current.get("ok"):
            raise AssertionError(current)
    return current


def fixture_eval(base: Path) -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []

    sequential = base / "sequential"
    sequential.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(sequential), "task": task("sequential Luna fixture"),
    })
    completed = finish(sequential, current)
    scenarios.append({"name": "automatic_sequential", "outcome": completed["outcome"]})

    parallel = base / "parallel"
    parallel.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(parallel), "task": task("parallel Luna fixture", "standard"),
        "waves": [{"workers": [{"phase": "research"}, {"phase": "architecture"}]}],
    })
    if len(current.get("dispatches") or []) != 2:
        raise AssertionError("parallel fixture did not return two relative worker slots")
    completed = finish(parallel, current)
    scenarios.append({"name": "compact_parallel", "outcome": completed["outcome"]})

    blocked = base / "blocked"
    blocked.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(blocked), "task": task("blocked resume Luna fixture", "C2"),
        "waves": [{"workers": [{"phase": "discover"}]}],
    })
    blocked_result = cortex.continue_orchestration({
        "project_root": str(blocked), "step": current["step"],
        "results": [{
            "status": "blocked",
            "reason": "fixture dependency unavailable",
            "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
        }],
    })
    if blocked_result.get("outcome") != "blocked":
        raise AssertionError(blocked_result)
    resumed = cortex.manage_orchestration({
        "project_root": str(blocked), "intent": "resume", "reason": "fixture dependency restored",
    })
    completed = finish(blocked, resumed)
    scenarios.append({"name": "blocked_resume", "outcome": completed["outcome"]})

    for project in (sequential, parallel, blocked):
        task_dir = next((project / ".codex/cortex/tasks").iterdir())
        state = cortex.load_task_state_for_artifact(task_dir)
        ledger = cortex.ledger_root({"project_root": str(project)})
        report_artifacts = canonical_artifacts(ledger, state["task_id"], kind="worker_report")
        report_records = [
            json.loads(cortex.db_read_artifact_content(ledger, state["task_id"], str(item["artifact_ref"])))
            for item in report_artifacts
        ]
        closure_records = [
            item for item in report_records if item.get("gate") in {"review", "close"}
        ]
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        snapshot_cleanup = state.get("manifest_snapshot_cleanup") or {}
        if (
            state.get("status") != "completed"
            or not close_evidence
            or not state.get("handoff_created")
            or snapshot_cleanup.get("status") != "completed"
            or not report_records
            or any(set(item.get("report", {})) != {
                "summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty", "next_action",
            } for item in report_records)
            or any(
                not isinstance(item.get("closure"), dict)
                or item["closure"].get("decision") != "pass"
                or item["closure"].get("findings") != []
                or item["closure"].get("verification", {}).get("required_missing") != []
                for item in closure_records
            )
            or any(
                cortex.db_get_manifest_snapshot(
                    cortex.ledger_root({"project_root": str(project)}), str(reference)
                ) is not None
                for reference in [
                    state.get("initial_manifest_ref"),
                    *[
                        item.get("result_baseline_ref")
                        for item in state.get("attempts", [])
                        if isinstance(item, dict)
                    ],
                ]
                if reference
            )
        ):
            raise AssertionError(f"{project.name} lacks close evidence or handoff")
    return scenarios


def live_prompt(scenario: str, project: Path, source_task_ref: str | None = None) -> str:
    if scenario == "follow_up_partial":
        return (
            "Use only the public Cortex tools for this isolated partial smoke test. "
            f"The exact project_root is {project}. The completed source task_ref is {source_task_ref!r}. "
            "Call manage_orchestration exactly once with intent=follow_up, that task_ref, and payload.user_request exactly "
            "'Correct the fixture result because the completed task produced the wrong behavior.' with complexity C1. "
            "Do not call start_orchestration, continue_orchestration, or any private Cortex tool. Do not execute the returned "
            "worker dispatch: this test must stop after Cortex has created the linked corrective task. You may inspect that new task "
            "once with manage_orchestration to confirm it is awaiting its first worker."
        )
    common = (
        "Use the Cortex MCP public tools to complete this isolated task. "
        "You are the parent orchestrator. The exact task contract is the content inside <cortex_task_contract>; "
        "do not copy any surrounding host metadata into the task. Call start_orchestration exactly once with that contract, "
        "and use one continue_orchestration per wave; "
        "never call orchestrate or any private Cortex tool. Execute every native dispatch; workers must persist all eight report sections with record_report and return only report_ref plus a short summary. "
        "For every review or close dispatch, record_report must also include a separate top-level closure sibling: decision=pass only when there are no open blockers, findings=[], verification with executed/not_executed/required_missing/limitations arrays (required_missing=[] only after required checks ran), and workspace with modified/untracked/staged arrays plus committed true, false, or not_required. Never place closure inside the strict eight-key report. "
        "Read every ref with read_worker_report and advance with report_ref. "
        "and finish only after close evidence and handoff. Do not ask for manual argument corrections. "
        f"The exact project_root is {project}. "
    )
    if scenario == "automatic_sequential":
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"inspect README.md and append a concise verified note to result.md.\","
            "\"acceptance_criteria\":[\"README.md is inspected and the note is grounded in its verified content.\","
            "\"result.md has a concise note appended, preserving existing content.\","
            "\"The final handoff identifies the changed file and includes evidence that the append was verified.\"],"
            "\"verification\":[\"Read README.md and confirm the appended note in result.md.\","
            "\"Inspect the resulting diff or equivalent file evidence to verify only the intended concise append was made.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract>"
        )
    if scenario == "compact_parallel":
        return common + "Use an explicit compact first wave with parallel discovery and architecture workers, then complete objective: create result.md summarizing the fixture."
    if scenario == "planner_work_breakdown":
        return common + (
            "Exercise the Planner work-breakdown contract end to end. Start a C2 task with plan_approval required and "
            "a plan wave followed by the necessary implementation, documentation, review, and close waves. The Planner "
            "must call record_report with the normal strict report plus a separate planning object containing an overview "
            "and at least two dependency-linked work_packages, each with non-empty microtasks, ownership profiles, paths, "
            "acceptance criteria, and verification. Read the Planner report and inspect the plan_review returned by Cortex. "
            "For this automated acceptance exercise, the user has explicitly pre-authorized approving that reviewed plan: "
            "call manage_orchestration intent=plan_approval with decision=approve, then complete the task by creating "
            "result.md. Do not bypass the approval gate or edit .codex/cortex directly."
        )
    return common + "Exercise a blocked/resume or future-wave reassessment path safely, then create result.md and complete the task."


def live_eval(base: Path, scenarios: tuple[str, ...] | None = None) -> list[dict[str, object]]:
    codex = shutil.which("codex")
    if not codex:
        return [{"status": "SKIP", "reason": "codex runtime unavailable; no live evidence"}]
    results: list[dict[str, object]] = []
    for scenario in scenarios or ("automatic_sequential", "compact_parallel", "blocked_resume", "planner_work_breakdown"):
        project = base / f"live-{scenario}"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / "README.md").write_text("# Luna high Cortex fixture\n", encoding="utf-8")
        source_task_ref = None
        source_snapshot: tuple[dict[str, object], dict[str, object]] | None = None
        if scenario == "follow_up_partial":
            source = cortex.start_orchestration({
                "project_root": str(project),
                "task": task("Complete a deterministic source fixture before follow-up testing.", "C1"),
            })
            completed_source = finish(project, source)
            if completed_source.get("outcome") != "completed":
                raise AssertionError(completed_source)
            source_task_ref = str(source["task_ref"])
            source_dir = next((project / ".codex/cortex/tasks").iterdir())
            source_snapshot = (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            )
        command = [
            codex, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox", "-C", str(project),
            "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="high"',
            "-c", f'mcp_servers.cortex.command="{sys.executable}"',
            "-c", f'mcp_servers.cortex.args=["{SERVER}"]',
            live_prompt(scenario, project, source_task_ref),
        ]
        completed = subprocess.run(command, cwd=project, text=True, capture_output=True, timeout=1800, check=False)
        events = []
        for line in completed.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        tool_names: list[str] = []
        completed_tool_names: list[str] = []
        failed_public_calls: list[str] = []
        tool_events: list[dict[str, object]] = []
        for event in events:
            item = event.get("item") if isinstance(event, dict) else None
            if not isinstance(item, dict) or item.get("type") not in {"mcp_tool_call", "tool_call"}:
                continue
            name = item.get("tool") or item.get("name")
            if isinstance(name, str):
                short_name = name.rsplit("__", 1)[-1]
                tool_names.append(short_name)
                if item.get("status") == "completed":
                    completed_tool_names.append(short_name)
                    raw_result = item.get("result")
                    structured = raw_result.get("structured_content") if isinstance(raw_result, dict) else None
                    if isinstance(structured, dict) and structured.get("ok") is False:
                        failed_public_calls.append(short_name)
                result_text = json.dumps(item.get("result"), ensure_ascii=False, sort_keys=True, default=str)
                tool_events.append({
                    "tool": short_name,
                    "status": item.get("status"),
                    "result_tail": result_text[-1200:],
                })
        task_dirs = list((project / ".codex/cortex/tasks").glob("*"))
        task_dir = task_dirs[0] if len(task_dirs) == 1 else None
        if scenario == "follow_up_partial":
            task_dir = next(
                (path for path in task_dirs if isinstance(cortex.load_task_definition(path).get("follow_up"), dict)),
                None,
            )
        state = cortex.load_task_state_for_artifact(task_dir) if task_dir else {}
        ledger = cortex.ledger_root({"project_root": str(project)})
        report_artifacts = canonical_artifacts(ledger, str(state.get("task_id") or ""), kind="worker_report") if task_dir else []
        report_records = [
            json.loads(cortex.db_read_artifact_content(ledger, str(state["task_id"]), str(item["artifact_ref"])))
            for item in report_artifacts
        ]
        report_keys = {"summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty", "next_action"}
        strict_reports = bool(report_records) and all(
            set(record.get("report", {})) == report_keys
            for record in report_records
        )
        closures_valid = all(
            isinstance(record.get("closure"), dict)
            and record["closure"].get("decision") == "pass"
            and record["closure"].get("findings") == []
            and record["closure"].get("verification", {}).get("required_missing") == []
            and set(record["closure"].get("workspace", {})) == {"modified", "untracked", "staged", "committed"}
            for record in report_records if record.get("gate") in {"review", "close"}
        )
        attempts_by_wave: dict[str, set[str]] = {}
        for attempt in state.get("attempts", []):
            if attempt.get("invalidated"):
                continue
            attempts_by_wave.setdefault(str(attempt.get("orchestration_wave_id") or ""), set()).add(str(attempt.get("gate") or ""))
        parallel_exercised = any(len(gates) > 1 for gates in attempts_by_wave.values())
        adaptive_exercised = bool(state.get("resume_events")) or len(state.get("reassessment_receipts", [])) > 1 or bool(state.get("pipeline_changes"))
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        planning_manifest = cortex.current_planning_manifest(task_dir) if task_dir else {}
        checks = {
            "process_ok": completed.returncode == 0,
            "used_start": "start_orchestration" in tool_names,
            "used_continue": "continue_orchestration" in tool_names,
            "avoided_private_tools": "orchestrate" not in tool_names,
            "single_task": len(task_dirs) == 1,
            "completed": state.get("status") == "completed",
            "close_evidence": close_evidence,
            "handoff": bool(state.get("handoff_created")),
            "manifest_snapshots_cleaned": (
                (state.get("manifest_snapshot_cleanup") or {}).get("status") == "completed"
                and bool(task_dir)
                and not any(
                    cortex.db_get_manifest_snapshot(
                        cortex.ledger_root({"project_root": str(project)}), str(reference)
                    ) is not None
                    for reference in [
                        state.get("initial_manifest_ref"),
                        *[
                            item.get("result_baseline_ref")
                            for item in state.get("attempts", [])
                            if isinstance(item, dict)
                        ],
                    ]
                    if reference
                )
            ),
            "strict_worker_reports": strict_reports,
            "review_close_closures": closures_valid,
            "no_failed_public_calls": not failed_public_calls,
            "one_start": completed_tool_names.count("start_orchestration") == 1,
        }
        if scenario == "compact_parallel":
            checks["parallel_wave_exercised"] = parallel_exercised
        if scenario == "blocked_resume":
            checks["resume_or_reassessment_exercised"] = adaptive_exercised
        if scenario == "planner_work_breakdown":
            package_artifacts = planning_manifest.get("work_packages") if isinstance(planning_manifest, dict) else []
            checks["plan_approval_exercised"] = state.get("plan_approval", {}).get("status") == "approved"
            checks["planning_manifest"] = (
                planning_manifest.get("schema") == "cortex/planning/v1"
                and len(package_artifacts) >= 2
                and all(
                    isinstance(package, dict)
                    and cortex.db_get_artifact_for_export_path(
                        ledger, str(state["task_id"]), str(package.get("artifact_path") or ""),
                    ) is not None
                    for package in package_artifacts
                )
            )
        if scenario == "follow_up_partial":
            source_dir = next((path for path in task_dirs if path != task_dir), None)
            corrective_task = cortex.load_task_definition(task_dir) if task_dir else {}
            source_unchanged = bool(source_dir and source_snapshot and (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            ) == source_snapshot)
            checks = {
                "process_ok": completed.returncode == 0,
                "used_follow_up": "manage_orchestration" in tool_names,
                "avoided_start_and_continue": "start_orchestration" not in tool_names and "continue_orchestration" not in tool_names,
                "avoided_private_tools": "orchestrate" not in tool_names,
                "created_one_linked_corrective_task": len(task_dirs) == 2 and task_dir is not None,
                "source_unchanged": source_unchanged,
                "corrective_task_active": state.get("status") == "active",
                "follow_up_link": corrective_task.get("follow_up", {}).get("source_task_ref") == source_task_ref,
                "first_corrective_dispatch_prepared": bool(state.get("attempts")) and state["attempts"][0].get("gate") in {"plan", "discover"},
                "no_failed_public_calls": not failed_public_calls,
            }
        passed = all(checks.values())
        results.append({
            "scenario": scenario, "status": "PASS" if passed else "FAIL",
            "launch_model": "gpt-5.6-luna", "launch_reasoning_effort": "high",
            "exit_code": completed.returncode, "tool_names": tool_names,
            "checks": checks, "failed_public_calls": failed_public_calls,
        })
        if not passed:
            failure_dir = Path(tempfile.mkdtemp(prefix="cortex-luna-high-failure-"))
            shutil.copytree(project, failure_dir / "project")
            (failure_dir / "events.json").write_text(
                json.dumps(events, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
            )
            results[-1]["failure_artifacts"] = str(failure_dir)
            results[-1]["last_tool_events"] = tool_events[-12:]
            raise AssertionError(f"live Luna-high scenario failed: {results[-1]}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="run the three real gpt-5.6-luna high parent scenarios")
    parser.add_argument(
        "--scenario", choices=("automatic_sequential", "compact_parallel", "blocked_resume", "planner_work_breakdown", "follow_up_partial"),
        help="run one live scenario for diagnosis; the default release run still requires all three",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cortex-luna-high-") as directory:
        base = Path(directory)
        fixtures = fixture_eval(base)
        if args.live or os.environ.get("CORTEX_RUN_LIVE_LUNA") == "1":
            live = live_eval(base, (args.scenario,) if args.scenario else None)
        else:
            live = [{"status": "SKIP", "reason": "live flag not supplied; no live release evidence"}]
    print(json.dumps({"status": "PASS", "fixtures": fixtures, "live": live}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
