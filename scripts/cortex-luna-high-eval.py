#!/usr/bin/env python3
"""Fixture and optional live evaluation for a Luna-high Cortex v3 parent."""
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


def report(label: str) -> dict[str, object]:
    return {
        "summary": label, "findings": [], "questions": [], "changed_files": [],
        "tests": ["luna-high fixture"], "evidence": [label], "uncertainty": [],
        "next_action": "advance",
    }


def finish(project: Path, current: dict[str, object]) -> dict[str, object]:
    while current.get("outcome") != "completed":
        if current.get("outcome") == "awaiting_plan_approval":
            current = cortex.manage_orchestration({
                "project_root": str(project),
                "intent": "plan_approval",
                "payload": {"decision": "approve"},
            })
            if not current.get("ok"):
                raise AssertionError(current)
            continue
        dispatches = current.get("dispatches") or []
        parallel = len(dispatches) > 1
        results = []
        for worker in range(1, len(dispatches) + 1):
            value: dict[str, object] = {"report": report(f"step {current['step']} worker {worker}")}
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
        "project_root": str(sequential), "task": {"user_request": "sequential Luna fixture"},
    })
    completed = finish(sequential, current)
    scenarios.append({"name": "automatic_sequential", "outcome": completed["outcome"]})

    parallel = base / "parallel"
    parallel.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(parallel), "task": {"user_request": "parallel Luna fixture", "complexity": "standard"},
        "waves": [{"workers": [{"phase": "research"}, {"phase": "architecture"}]}],
    })
    if len(current.get("dispatches") or []) != 2:
        raise AssertionError("parallel fixture did not return two relative worker slots")
    completed = finish(parallel, current)
    scenarios.append({"name": "compact_parallel", "outcome": completed["outcome"]})

    blocked = base / "blocked"
    blocked.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(blocked), "task": {"user_request": "blocked resume Luna fixture", "complexity": "C2"},
        "waves": [{"workers": [{"phase": "discover"}]}],
    })
    blocked_result = cortex.continue_orchestration({
        "project_root": str(blocked), "step": current["step"],
        "results": [{"status": "blocked", "reason": "fixture dependency unavailable"}],
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
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        if state.get("status") != "completed" or not close_evidence or not state.get("handoff_created"):
            raise AssertionError(f"{project.name} lacks close evidence or handoff")
    return scenarios


def live_prompt(scenario: str, project: Path, source_task_ref: str | None = None) -> str:
    if scenario == "follow_up_partial":
        return (
            "Use only Cortex v3 public tools for this isolated partial smoke test. "
            f"The exact project_root is {project}. The completed source task_ref is {source_task_ref!r}. "
            "Call manage_orchestration exactly once with intent=follow_up, that task_ref, and payload.user_request exactly "
            "'Correct the fixture result because the completed task produced the wrong behavior.' with complexity C1. "
            "Do not call start_orchestration, continue_orchestration, or any private Cortex tool. Do not execute the returned "
            "worker dispatch: this test must stop after Cortex has created the linked corrective task. You may inspect that new task "
            "once with manage_orchestration to confirm it is awaiting its first worker."
        )
    common = (
        "Use the Cortex v3 MCP public tools to complete this isolated task. "
        "You are the parent orchestrator. Preserve the task text exactly in start_orchestration task.user_request, "
        "and use one continue_orchestration per wave; "
        "never call orchestrate or any private Cortex tool. Execute every native dispatch; workers must persist all eight report sections with record_report and return only report_ref plus a short summary. "
        "Read every ref with read_worker_report and advance with report_ref. "
        "and finish only after close evidence and handoff. Do not ask for manual argument corrections. "
        f"The exact project_root is {project}. "
    )
    if scenario == "automatic_sequential":
        return common + "Use the automatic pipeline for objective: inspect README.md and append a concise verified note to result.md."
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
        source_snapshot: tuple[str, str] | None = None
        if scenario == "follow_up_partial":
            source = cortex.start_orchestration({
                "project_root": str(project),
                "task": {"user_request": "Complete a deterministic source fixture before follow-up testing.", "complexity": "C1"},
            })
            completed_source = finish(project, source)
            if completed_source.get("outcome") != "completed":
                raise AssertionError(completed_source)
            source_task_ref = str(source["task_ref"])
            source_dir = next((project / ".codex/cortex/tasks").iterdir())
            source_snapshot = (
                (source_dir / "task.json").read_text(encoding="utf-8"),
                (source_dir / "current.json").read_text(encoding="utf-8"),
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
                (path for path in task_dirs if isinstance(json.loads((path / "task.json").read_text(encoding="utf-8")).get("follow_up"), dict)),
                None,
            )
        state = json.loads((task_dir / "current.json").read_text(encoding="utf-8")) if task_dir else {}
        report_records = list((task_dir / "reports/records").glob("*.json")) if task_dir else []
        report_keys = {"summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty", "next_action"}
        strict_reports = bool(report_records) and all(
            set(json.loads(path.read_text(encoding="utf-8")).get("report", {})) == report_keys
            for path in report_records
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
        planning_root = task_dir / "planning" if task_dir else None
        planning_manifest = (
            json.loads((planning_root / "manifest.json").read_text(encoding="utf-8"))
            if planning_root and (planning_root / "manifest.json").is_file() else {}
        )
        checks = {
            "process_ok": completed.returncode == 0,
            "used_start": "start_orchestration" in tool_names,
            "used_continue": "continue_orchestration" in tool_names,
            "avoided_private_tools": "orchestrate" not in tool_names,
            "single_task": len(task_dirs) == 1,
            "completed": state.get("status") == "completed",
            "close_evidence": close_evidence,
            "handoff": bool(state.get("handoff_created")),
            "strict_worker_reports": strict_reports,
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
                    and (task_dir / str(package.get("artifact_path") or "")).is_file()
                    for package in package_artifacts
                )
            )
        if scenario == "follow_up_partial":
            source_dir = next((path for path in task_dirs if path != task_dir), None)
            corrective_task = json.loads((task_dir / "task.json").read_text(encoding="utf-8")) if task_dir else {}
            source_unchanged = bool(source_dir and source_snapshot and (
                (source_dir / "task.json").read_text(encoding="utf-8"),
                (source_dir / "current.json").read_text(encoding="utf-8"),
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
