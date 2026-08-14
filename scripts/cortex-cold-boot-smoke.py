#!/usr/bin/env python3
"""Exercise a complete orchestration lifecycle through black-box MCP JSON-RPC."""
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


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, text=True, capture_output=True, check=True)


def fixture(base: Path) -> tuple[Path, Path]:
    project = base / "project"
    ledger = project / ".codex" / "cortex"
    project.mkdir()
    ledger.mkdir(parents=True)
    git(project, "init", "-q")
    git(project, "config", "user.email", "codex@example.invalid")
    git(project, "config", "user.name", "Codex Smoke")
    (project / "tracked.txt").write_text("before\n", encoding="utf-8")
    (project / "delete.txt").write_text("delete me\n", encoding="utf-8")
    (project / "old.txt").write_text("rename me\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "fixture baseline")
    return project, ledger


def run(base: Path) -> dict:
    project, ledger = fixture(base)
    report_count = 0

    def confirm_native_spawn(rpc: JsonRpcHarness, delegation: dict) -> dict:
        """Model the host acknowledgement that follows a successful native spawn_agent call."""
        return rpc.tool("confirm_host_spawn", {
            "task_id": "cold-boot", "principal": PRINCIPAL,
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"],
            "host_agent_id": f"smoke-host-{delegation['attempt_id']}",
            "host_task_name": delegation["spawn_request"]["task_name"],
            "host_model": delegation["spawn_request"]["model"],
            "host_reasoning_effort": delegation["spawn_request"]["reasoning_effort"],
        })
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        rpc.tool("activate_orchestration", {"user_command": "/cortex", "principal": PRINCIPAL, "thread_id": PRINCIPAL})
        classification = rpc.tool("classify_task", {"complexity": "C2", "requirements": ["implementation, verification, and documentation"], "principal": PRINCIPAL})
        created = rpc.tool("init_task", {
            "task_id": "cold-boot",
            "objective": "prove a fresh JSON-RPC process can complete a durable C2 lifecycle",
            "complexity": "C2",
            "classification_id": classification["classification_id"],
            "requirements": ["implementation, verification, and documentation"],
            "principal": PRINCIPAL,
            "thread_id": PRINCIPAL,
        })
        task_directory = created["task_directory"]

    # A new server process must recover the same numbered task and revision.
    with JsonRpcHarness(SERVER, project, ledger) as rpc:
        recovered = rpc.tool("get_task_status", {"task_id": "cold-boot", "principal": PRINCIPAL})
        if recovered["state"]["revision"] != created["state"]["revision"]:
            raise AssertionError("task revision did not survive the MCP process restart")
        state = recovered["state"]
        (project / "tracked.txt").write_text("after\n", encoding="utf-8")
        (project / "delete.txt").unlink()
        (project / "old.txt").rename(project / "new.txt")
        (project / "added.txt").write_text("untracked\n", encoding="utf-8")

        failed_command_rejected = False
        for gate in list(state["current_pipeline"]):
            observed = rpc.tool("get_task_status", {"task_id": "cold-boot", "principal": PRINCIPAL})
            state = observed["state"]
            agent = "technical_writer" if gate == "documentation" else ("build_verification" if gate in {"qa", "review", "close"} else "general")
            delegation = rpc.tool("record_delegation", {
                "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": state["revision"],
                "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent,
                "task_kind": gate, "risk": "moderate", "requested_model": "gpt-5.6-terra", "requested_reasoning_effort": "medium",
                "objective": f"verify {gate}", "ownership": f"Own the {gate} gate",
                "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"],
                "verification": ["Record server-observed evidence"],
            })
            confirmed = confirm_native_spawn(rpc, delegation)
            report = rpc.tool("record_report", {
                "task_id": "cold-boot", "principal": PRINCIPAL, "attempt_id": delegation["attempt_id"],
                "submission_id": "final", "report": {
                    "summary": f"{gate} worker report", "findings": [], "questions": [], "changed_files": [],
                    "tests": [], "evidence": [f"{gate} evidence is ready"], "uncertainty": [], "next_action": "record gate evidence",
                },
            })
            if gate == "documentation":
                evidence = rpc.tool("record_evidence", {
                    "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": confirmed["state"]["revision"],
                    "gate": gate, "attempt_id": delegation["attempt_id"], "kind": "documentation",
                    "report_receipt": report["receipt"]["receipt_id"],
                    "decision": "not_applicable", "justification": "fixture behavior is fully described by executable smoke coverage",
                    "summary": "technical_writer made an explicit documentation decision",
                })
            else:
                if gate == "qa":
                    failed = rpc.tool("execute_verification_command", {
                        "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": confirmed["state"]["revision"],
                        "gate": gate, "attempt_id": delegation["attempt_id"], "summary": "intentional negative control",
                        "report_receipt": report["receipt"]["receipt_id"],
                        "verification_id": "benign_failure",
                    })
                    if failed["execution"]["exit_code"] != 1:
                        raise AssertionError("intentional failure exit code was not captured")
                    try:
                        rpc.tool("record_gate_outcome", {"task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": failed["state"]["revision"], "gate": gate, "outcome": "passed"})
                    except RuntimeError as exc:
                        failed_command_rejected = "failed or self-attested" in str(exc)
                    if not failed_command_rejected:
                        raise AssertionError("intentional failed command was accepted")
                    failed_gate = rpc.tool("record_gate_outcome", {"task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": failed["state"]["revision"], "gate": gate, "outcome": "failed"})
                    observed = rpc.tool("get_task_status", {"task_id": "cold-boot", "principal": PRINCIPAL})
                    delegation = rpc.tool("record_delegation", {
                        "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": failed_gate["state"]["revision"],
                        "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent,
                        "task_kind": gate, "risk": "moderate", "requested_model": "gpt-5.6-terra", "requested_reasoning_effort": "medium", "retry": 1,
                        "objective": "retry with a passing command", "ownership": f"Retry the {gate} gate",
                        "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"],
                        "verification": ["Record a passing server-observed command"],
                    })
                    confirmed = confirm_native_spawn(rpc, delegation)
                    report = rpc.tool("record_report", {
                        "task_id": "cold-boot", "principal": PRINCIPAL, "attempt_id": delegation["attempt_id"],
                        "submission_id": "retry-final", "report": {
                            "summary": "qa retry worker report", "findings": [], "questions": [], "changed_files": [],
                            "tests": ["benign success"], "evidence": ["retry is ready"], "uncertainty": [], "next_action": "record passing evidence",
                        },
                    })
                evidence = rpc.tool("execute_verification_command", {
                    "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": confirmed["state"]["revision"],
                    "gate": gate, "attempt_id": delegation["attempt_id"], "summary": f"real {gate} command",
                    "verification_id": "benign_success", "report_receipt": report["receipt"]["receipt_id"],
                })
            state = evidence["state"]
            if gate == "plan":
                try:
                    rpc.tool("record_gate_outcome", {
                        "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": state["revision"],
                        "gate": gate, "outcome": "passed", "pipeline_operations": [
                            {"op": "remove", "gate": "documentation"}, {"op": "remove", "gate": "close"},
                        ],
                    })
                except RuntimeError as exc:
                    if "retain documentation" not in str(exc):
                        raise
                else:
                    raise AssertionError("record_gate_outcome removed mandatory C2 gates")
            if gate == "discover":
                reassessed = rpc.tool("reassess_pipeline", {
                    "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": state["revision"],
                    "signals": [], "decision": "unchanged", "reason": "discovery found no new specialist or risk gate",
                })
                state = reassessed["state"]
            if gate == "close":
                reports = rpc.tool("list_task_reports", {"task_id": "cold-boot", "principal": PRINCIPAL})["reports"]
                if len(reports) != len(state["attempts"]):
                    raise AssertionError("every simulated worker attempt must publish exactly one indexed report")
                reconciled = rpc.tool("reconcile_report_bus", {"task_id": "cold-boot", "principal": PRINCIPAL})
                if reconciled["report_count"] != len(reports):
                    raise AssertionError("report bus reconciliation changed the indexed report count")
                report_count = len(reports)
                handoff = rpc.tool("create_handoff", {
                    "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": state["revision"],
                    "completed": ["black-box lifecycle and manifest reconciliation"],
                    "files": ["tracked.txt", "delete.txt", "old.txt", "new.txt", "added.txt"], "next_action": "none",
                })
                if not handoff["file_manifest_receipt"]["complete"]:
                    raise AssertionError("final file manifest was incomplete")
                state = handoff["state"]
            state = rpc.tool("record_gate_outcome", {
                "task_id": "cold-boot", "principal": PRINCIPAL, "expected_revision": state["revision"],
                "gate": gate, "outcome": "passed",
            })["state"]
        if state["status"] != "completed":
            raise AssertionError("C2 lifecycle did not complete")
    task_path = ledger / "tasks" / task_directory
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (task_path / "reports/receipts").glob("*.json")]
    if any(not item.get("consumed_at") for item in receipts):
        raise AssertionError("every C2 report receipt must be consumed by evidence")
    return {
        "status": "PASS",
        "fixture": str(base),
        "task_directory": str(task_path),
        "current": str(task_path / "current.json"),
        "baseline_manifest": str(task_path / "baseline-manifest.json"),
        "failed_command_rejected": failed_command_rejected,
        "report_count": report_count,
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
