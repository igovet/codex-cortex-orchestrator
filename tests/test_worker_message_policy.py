"""Focused checks for the policy actually delivered to fresh workers."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/cortex/scripts"))
from cortex_runtime import worker_message


def _task() -> dict[str, str]:
    return {"task_id": "task-" + "a" * 64 + "-" + "b" * 32}


def _delegation() -> dict[str, str]:
    return {
        "delegation_id": "delegation-" + "c" * 64 + "-" + "d" * 32,
        "profile_name": "explorer",
        "native_task_name": "memory-first-worker",
    }


def test_fresh_worker_receives_compact_bootstrap_then_full_assignment_policy():
    rendered = worker_message.render_worker_message(task=_task(), delegation=_delegation(), decisions=[])
    message = rendered["message"]
    policy = worker_message.assignment_worker_policy("explorer")
    assert policy is not None
    normalized = " ".join(policy["common_policy"].split())

    assert "Before any other action or tool call" in message
    assert "assignment read is the sole authority" in message
    assert "Codebase Memory as the mandatory first evidence route" not in message
    assert len(message.encode("utf-8")) < 1_024
    assert "Codebase Memory as the mandatory first evidence route" in normalized
    assert "canonical `project_root` returned in the server-owned assignment context" in normalized
    assert "bounded repository-native enumeration or text-search fallback" in normalized
    assert "environment blocker" in normalized
    assert "actual graph call" in normalized
    assert "Never silently skip the graph" in normalized
    assert "A plan publication always declares one explicit review disposition" in policy["common_policy"]
    assert "A planning worker completes all bounded discovery before publishing one terminal plan" in normalized
    assert "never publishes a supplementary result or documentation outcome" in normalized
    assert "separate evidence assignment followed by a fresh planning revision" in normalized
    assert policy["profile_name"] == "explorer"
    assert policy["profile_instructions"]
    assert rendered["renderer"]["common_policy_digest"] == (
        "sha256:" + hashlib.sha256(
            worker_message._MANDATORY_PROJECT_POLICY.encode("utf-8")
        ).hexdigest()
    )


def test_common_policy_is_exposed_by_the_assignment_policy_boundary():
    policy = worker_message.assignment_worker_policy("explorer")
    assert policy is not None
    assert policy["common_policy"] == worker_message._MANDATORY_PROJECT_POLICY.strip()


def test_fresh_planner_bootstraps_with_assignment_read_and_has_no_governance_authority():
    delegation = {**_delegation(), "profile_name": "planner"}
    message = worker_message.render_worker_message(
        task=_task(), delegation=delegation, decisions=[]
    )["message"]

    # A native planner is still a worker: assignment evidence must be its
    # first Cortex action, before project work or any coordinator lifecycle
    # operation.  Keep the assertion semantic rather than prescribing a
    # public MCP argument shape.
    first_action = message.index("Before any other action or tool call")
    assert message.index("server-owned Cortex", first_action) > first_action
    assert "You are a worker, not a coordinator" in message
