"""Focused checks for the policy actually delivered to fresh workers."""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

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
    normalized_message = " ".join(message.split())
    policy = worker_message.assignment_worker_policy("explorer")
    assert policy is not None
    normalized = " ".join(policy["common_policy"].split())

    assert "First consume the server-owned assignment" in message
    assert "live advertised `read_task`" in message
    assert "select assignment evidence" in normalized_message.lower()
    assert "`open_assignment` creates assignments for a" in normalized_message
    assert "never reads or consumes a worker assignment" in normalized_message
    assert "assignment read is the sole authority" in message
    assert "Do not load coordinator/orchestrator skills first" in message
    assert "exact worker policy and packaged profile" in normalized_message
    assert "Codebase Memory as the mandatory first evidence route" not in message
    assert len(message.encode("utf-8")) < 1_024
    assert "finite first read from the live contract" in message
    assert "correct it once only when diagnostics are unambiguous" in message
    assert "never repeat malformed input or guess authority" in normalized_message.lower()
    assert "Coordinator-only operations" in message
    assert "Do no project work before successful consumption" in normalized_message
    assert "Codebase Memory as the preferred first evidence route when it is available" in normalized
    assert "canonical `project_root` returned in the server-owned assignment context" in normalized
    assert "exactly one safe assignment-scoped repository-native enumeration or text-search fallback" in normalized
    assert "unavailable, denied, times out, errors" in normalized
    assert "absence alone is not a blocked publication cause" in normalized
    assert "Do not silently skip an available usable graph" in normalized
    assert "A plan publication does not choose or declare its review disposition" in policy["common_policy"]
    assert "A planning worker completes all bounded discovery before publishing one terminal plan" in normalized
    assert "never publishes a supplementary result or documentation outcome" in normalized
    assert "separate evidence assignment followed by a fresh planning revision" in normalized
    assert "Never repeat a terminal assignment read during normal execution" in normalized
    assert "After host context compaction or reset" in normalized
    assert "restart the same assignment from the beginning on this authenticated connection" in normalized
    assert "fresh server-owned reconciliation projection" in normalized
    assert "sole terminal-read exception" in normalized
    assert "grants no new authority" in normalized
    assert "publish exactly one matching terminal outcome" in normalized
    assert "confirmed successful terminal-publication response ends all worker tool activity" in normalized
    assert "Never call any tool or repeat/reconcile that mutation after success" in normalized
    assert "actually ambiguous transport result" in normalized
    assert "Every native worker and packaged profile is worker-only" in normalized
    assert "including governance assessment" in normalized
    assert "Never ask the user directly" in policy["common_policy"]
    assert "available safe choices" in normalized
    assert "material consequence or stopping condition of each choice" in normalized
    assert "context-free question or approval request" in normalized
    assert policy["profile_name"] == "explorer"
    assert policy["profile_instructions"]
    assert rendered["renderer"]["common_policy_digest"] == (
        "sha256:" + hashlib.sha256(
            worker_message._MANDATORY_PROJECT_POLICY.encode("utf-8")
        ).hexdigest()
    )

    bootstrap_match = re.search(
        r"## Server-bound worker context\n\n```json\n(\{.*\})\n```",
        message,
    )
    assert bootstrap_match is not None
    bootstrap = json.loads(bootstrap_match.group(1))
    assert bootstrap == {
        "assignment context": {
            "task_ref": worker_message._worker_task_ref(
                _task()["task_id"], _delegation()["delegation_id"],
            ),
        },
    }
    assert "worker label" not in bootstrap_match.group(1)
    assert len(re.findall(r"t_[0-9a-f]{12}_[0-9a-f]{32}", message)) == 1


def test_worker_catalogue_without_codebase_memory_still_authorizes_one_bounded_fallback():
    tool_catalogue = [
        {"name": "mcp__cortex__read_task"},
        {"name": "mcp__cortex__publish_result"},
    ]
    assert not any("codebase_memory" in item["name"] for item in tool_catalogue)

    policy = worker_message.assignment_worker_policy("explorer")
    assert policy is not None
    normalized = " ".join(policy["common_policy"].split())
    assert "exactly one safe assignment-scoped repository-native enumeration or text-search fallback" in normalized
    assert "Its absence alone is not a blocked publication cause" in normalized
    assert "stop and report an environment blocker" not in normalized


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
    first_action = message.index("First consume the server-owned assignment")
    assert message.index("live advertised `read_task`", first_action) > first_action
    assert "select assignment evidence" in " ".join(message.split()).lower()
    assert "`open_assignment` creates assignments" in " ".join(message.split())
    assert "You are a worker, not a coordinator" in message


@pytest.mark.parametrize("profile_name", worker_message.packaged_profile_names())
def test_every_packaged_profile_inherits_worker_only_governance_and_terminal_read_policy(profile_name):
    policy = worker_message.assignment_worker_policy(profile_name)
    assert policy is not None
    normalized = " ".join(policy["common_policy"].split())

    assert "Every native worker and packaged profile is worker-only" in normalized
    assert "Coordinator-only operations, including governance assessment" in normalized
    assert "Never repeat a terminal assignment read during normal execution" in normalized
    assert "After host context compaction or reset" in normalized
    assert "fresh server-owned reconciliation projection" in normalized
    assert "publish exactly one matching terminal outcome" in normalized
    assert "confirmed successful terminal-publication response ends all worker tool activity" in normalized
    assert "Never ask the user directly" in policy["common_policy"]
    assert "available safe choices" in normalized
    assert "material consequence or stopping condition of each choice" in normalized
