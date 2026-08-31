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


def test_fresh_worker_receives_common_memory_and_review_policy():
    rendered = worker_message.render_worker_message(task=_task(), delegation=_delegation(), decisions=[])
    message = rendered["message"]
    normalized = " ".join(message.split())

    assert "first Cortex action is the server-owned assignment read" in message
    assert "Codebase Memory as the mandatory first evidence route" in normalized
    assert "canonical `project_root` returned in the server-owned assignment context" in normalized
    assert "bounded repository-native enumeration or text-search fallback" in normalized
    assert "environment blocker" in normalized
    assert "actual graph call" in normalized
    assert "Never silently skip the graph" in normalized
    assert "A plan publication always declares one explicit review disposition" in message
    assert "A planning worker completes all bounded discovery before publishing one terminal plan" in normalized
    assert "never publishes a supplementary result or documentation outcome" in normalized
    assert "separate evidence assignment followed by a fresh planning revision" in normalized
    assert rendered["renderer"]["common_policy_digest"] == (
        "sha256:" + hashlib.sha256(
            worker_message._MANDATORY_PROJECT_POLICY.encode("utf-8")
        ).hexdigest()
    )


def test_common_policy_is_not_only_a_dead_renderer_constant():
    rendered = worker_message.render_worker_message(task=_task(), delegation=_delegation(), decisions=[])
    assert rendered["message"].index("# Mandatory project-work invariants") < rendered["message"].index("# Cortex worker bootstrap")
