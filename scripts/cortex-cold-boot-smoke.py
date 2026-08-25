#!/usr/bin/env python3
"""Exercise one fresh V11 Cortex JSON-RPC lifecycle without legacy recovery."""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
from jsonrpc_harness import JsonRpcHarness  # noqa: E402
import cortex  # noqa: E402


SERVER = ROOT / "plugins/cortex/scripts/cortex.py"
_BOOTSTRAP_IDENTITY = re.compile(
    r'read_dispatch_briefing\(\{"task_ref":"([^"]+)","assignment_ref":"([^"]+)"\}\)'
)


def waves() -> list[dict[str, object]]:
    """Return the one bounded canonical wave exercised by the smoke."""
    return [{"phase": "discover", "workers": [{}]}]


@contextlib.contextmanager
def host_private_control_store(host_state_dir: Path):
    previous = os.environ.get("CORTEX_HOST_STATE_DIR")
    os.environ["CORTEX_HOST_STATE_DIR"] = str(host_state_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CORTEX_HOST_STATE_DIR", None)
        else:
            os.environ["CORTEX_HOST_STATE_DIR"] = previous


def _assignment_authority(dispatch: dict[str, Any], task_ref: str) -> dict[str, str]:
    arguments = dispatch.get("arguments") if isinstance(dispatch.get("arguments"), dict) else {}
    message = str(arguments.get("message") or "")
    match = _BOOTSTRAP_IDENTITY.search(message)
    if match is None or match.group(1) != task_ref:
        raise AssertionError("native dispatch did not carry one exact V11 worker capability")
    return {"task_ref": match.group(1), "assignment_ref": match.group(2)}


def run(base: Path, server: Path = SERVER) -> dict[str, object]:
    project = base / "project"
    host_state_dir = base / "host-private-store"
    project.mkdir()
    host_state_dir.mkdir(mode=0o700)
    host_state_dir.chmod(0o700)
    with host_private_control_store(host_state_dir):
        with JsonRpcHarness(server, project, host_state_dir, audience="coordinator") as coordinator:
            started = coordinator.tool("start_orchestration", {
                "task": {
                    "user_request": "prove one V11 native worker lifecycle over fresh JSON-RPC",
                    "complexity": "C1",
                    "acceptance_criteria": ["A canonical V11 worker result is read and continued exactly once."],
                    "verification": ["Run this bounded cold-boot smoke."],
                },
                "waves": waves(),
            }, request_id="cold-boot-start")
            if not started.get("ok") or started.get("outcome") != "ready_to_spawn":
                raise AssertionError("V11 start did not return an executable dispatch")
            task_ref = str(started.get("task_ref") or "")
            coordinator_ref = str(started.get("coordinator_ref") or "")
            dispatches = started.get("dispatches")
            if not task_ref or not coordinator_ref or not isinstance(dispatches, list) or len(dispatches) != 1:
                raise AssertionError("V11 start omitted an exact coordinator capability or dispatch")
            assignment_authority = _assignment_authority(dict(dispatches[0]), task_ref)

        with JsonRpcHarness(server, project, host_state_dir, audience="worker") as worker:
            briefing = worker.tool("read_dispatch_briefing", assignment_authority)
            if not briefing.get("ok"):
                raise AssertionError("worker could not read its exact scoped briefing")
            checkpoint = worker.tool("record_attempt_event", {
                **assignment_authority,
                "event_type": "progress",
                "payload": {"summary": "V11 cold-boot lifecycle checkpoint."},
            })
            if not checkpoint.get("ok"):
                raise AssertionError("worker checkpoint failed")
            completed = worker.tool("complete_attempt", {
                **assignment_authority,
                "outcome": {
                    "status": "completed",
                    "summary": "V11 cold-boot worker completed its bounded discovery assignment.",
                    "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
                },
            })
            if completed != {
                "schema": "cortex/worker-completion/v11",
                "ok": True,
                "terminal": True,
            }:
                raise AssertionError("worker did not finalize a canonical V11 AttemptResult")

        with JsonRpcHarness(server, project, host_state_dir, audience="coordinator") as coordinator:
            read = coordinator.tool("read_worker_result", {
                "task_ref": task_ref,
                "coordinator_ref": coordinator_ref,
                "step": started["step"],
            })
            continuation = read.get("continuation")
            if (
                not read.get("ok")
                or not isinstance(read.get("results"), list)
                or len(read["results"]) != 1
                or not isinstance(continuation, dict)
            ):
                raise AssertionError("coordinator could not derive the exact canonical worker result wave")
            terminal = coordinator.tool("continue_orchestration", {
                "task_ref": task_ref,
                "coordinator_ref": coordinator_ref,
                "step": continuation["step"],
                "results": continuation["results"],
            })
            if not terminal.get("ok"):
                raise AssertionError("V11 continuation did not accept the exact canonical result")

    # Resolve the postcondition through the same temporary host-private store
    # used by the two JSON-RPC audiences.  Do not consult the process-global
    # default host state after the context manager has restored the caller's
    # environment.
    with host_private_control_store(host_state_dir):
        ledger = cortex.ledger_root({"project_root": str(project)})
        task_dir = next((ledger / "tasks").iterdir())
        state = cortex.load_task_state_for_artifact(task_dir)
        task = cortex.load_task_definition(task_dir, state)
    if task.get("schema") != "cortex/v11" or state.get("schema") != "cortex/v11":
        raise AssertionError("cold-boot did not preserve the V11 ledger schema")
    if cortex.DATABASE_SCHEMA_VERSION != 17:
        raise AssertionError("cold-boot did not use the canonical SQLite schema v17")
    return {
        "status": "PASS",
        "task_schema": task["schema"],
        "state_schema": state["schema"],
        "ledger_schema": "v17",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=Path, default=SERVER)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cortex-cold-boot-") as directory:
        result = run(Path(directory), arguments.server)
    print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
