"""Real hook plus persistent-stdio parity for CLI and Desktop lifecycle order."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_test_support(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMANDS = _load_test_support(
    "cortex_parity_command_receipts", "test_command_receipts.py",
)
HOOKS = _load_test_support(
    "cortex_parity_activation_hook", "test_activation_hook.py",
)


def _hook(tmp_path: Path, event: dict) -> dict | None:
    returncode, result = HOOKS.invoke(tmp_path, event)
    assert returncode == 0, result
    return result


@pytest.mark.parametrize("surface", ["cli", "desktop"])
def test_real_hook_and_persistent_stdio_worker_lifecycle_are_equivalent(
    tmp_path: Path, surface: str,
) -> None:
    home = str(tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    plugin_data = str(tmp_path / "plugin-data")
    root_session = "parity-root-session"
    root_turn = "parity-root-turn"
    worker_turn = f"{surface}-worker-turn"
    worker_agent = f"{surface}-worker-agent"
    outcome = {
        "outcome": f"Complete the {surface} parity fixture.",
        "acceptance": ["One exact worker publication is durable."],
        "constraints": ["Use the real hook and one persistent MCP connection."],
        "verification": ["Coordinator evidence contains one completed result."],
    }

    with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}):
        with COMMANDS._source_stdio_session(home) as coordinator:
            opened = coordinator("open_task", {
                "project_root": str(project),
                "request_original": f"Exercise ordinary {surface} worker startup.",
                "user_language": "en",
                "outcomes": [outcome],
                "constraints": ["Keep CLI and Desktop results equivalent."],
            })
            assert not opened["result"].get("isError"), opened
            task_ref = opened["result"]["structuredContent"]["task_ref"]
            assessed = coordinator("assess_governance", {
                "task_ref": task_ref,
                "mode": "minimal",
                "rationale": "Single source-level delivery fixture.",
                "risk_factors": [],
            })
            assert not assessed["result"].get("isError"), assessed
            assigned = coordinator("open_assignment", {
                "task_ref": task_ref,
                "role": f"{surface} parity worker",
                "profile_name": "backend_dev",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
                "responsibility": "delivery",
                "goal": "Publish one exact result through the assigned connection.",
                "scope": outcome["outcome"],
                "instructions": "Consume the assignment first, then publish once.",
                "report_policy": "none",
            })
            assert not assigned["result"].get("isError"), assigned
            assignment = assigned["result"]["structuredContent"]
            native_dispatch = assignment["native_dispatch"]
            worker_ref = re.search(
                r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                native_dispatch["message"],
            ).group(1)

            _hook(tmp_path, {
                "hook_event_name": "UserPromptSubmit",
                "session_id": root_session,
                "turn_id": root_turn,
                "prompt": "$cortex:orchestrator run the parity fixture",
            })
            _hook(tmp_path, {
                "hook_event_name": "PostToolUse",
                "session_id": root_session,
                "turn_id": root_turn,
                "tool_name": "mcp__cortex__open_task",
                "tool_input": {"project_root": str(project)},
                "tool_response": opened["result"],
            })
            _hook(tmp_path, {
                "hook_event_name": "PostToolUse",
                "session_id": root_session,
                "turn_id": root_turn,
                "tool_name": "mcp__cortex__open_assignment",
                "tool_input": {"task_ref": task_ref},
                "tool_response": assigned["result"],
            })
            spawn_result = _hook(tmp_path, {
                "hook_event_name": "PreToolUse",
                "session_id": root_session,
                "turn_id": root_turn,
                "tool_use_id": f"{surface}-spawn",
                "tool_name": "collaboration.spawn_agent",
                "tool_input": native_dispatch,
            })
            assert spawn_result is not None
            spawn_output = spawn_result["hookSpecificOutput"]
            assert spawn_output.get("permissionDecision") != "deny"
            assert spawn_output["additionalContext"]

            start_event = {
                "hook_event_name": "SubagentStart",
                "session_id": root_session,
                "turn_id": worker_turn,
                "agent_id": worker_agent,
            }
            if surface == "cli":
                _hook(tmp_path, start_event)

            with COMMANDS._source_stdio_session(home) as worker:
                assert worker.initialize_result["result"]["capabilities"] == {
                    "tools": {"listChanged": True},
                }
                listed = worker.rpc("tools/list", {})
                initial_tools = {item["name"] for item in listed["result"]["tools"]}
                assert "open_task" in initial_tools
                assert "publish_result" in initial_tools
                if surface == "desktop":
                    # Desktop initializes before SubagentStart. With no
                    # trustworthy initialize identity, the connection remains
                    # a neutral superset until exact bootstrap consumption.
                    _hook(tmp_path, start_event)

                read_input = {"task_ref": worker_ref}
                assert _hook(tmp_path, {
                    "hook_event_name": "PreToolUse",
                    "session_id": root_session,
                    "turn_id": worker_turn,
                    "agent_id": worker_agent,
                    "tool_use_id": f"{surface}-worker-read",
                    "tool_name": "mcp__cortex__read_task",
                    "tool_input": read_input,
                }) is None
                consumed = worker("read_task", read_input)
                assert not consumed["result"].get("isError"), consumed
                assert consumed["result"]["structuredContent"]["view"] == "assignment"
                assert _hook(tmp_path, {
                    "hook_event_name": "PostToolUse",
                    "session_id": root_session,
                    "turn_id": worker_turn,
                    "agent_id": worker_agent,
                    "tool_use_id": f"{surface}-worker-read",
                    "tool_name": "mcp__cortex__read_task",
                    "tool_input": read_input,
                    "tool_response": consumed["result"],
                }) is None

                narrowed = worker.rpc("tools/list", {})
                worker_tools = {item["name"] for item in narrowed["result"]["tools"]}
                assert worker_tools == {
                    "read_task", "publish_plan", "publish_result",
                    "publish_documentation",
                }
                assert worker.notifications == [{
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                    "params": {},
                }]

                publication_input = {
                    "task_ref": worker_ref,
                    "summary": f"{surface.capitalize()} parity fixture completed.",
                    "outcome": "The assigned source lifecycle completed once.",
                    "changes": [],
                    "verification_facts": [{
                        "state": "executed",
                        "summary": "The real hook and persistent stdio connection succeeded.",
                    }],
                    "outcome_coverage": [{
                        "outcome": outcome["outcome"],
                        "status": "complete",
                        "verification": ["One non-replayed publication was returned."],
                    }],
                    "documentation_impact": "No product documentation change in the fixture.",
                    "risks": [],
                    "unresolved": [],
                    "status": "completed",
                }
                assert _hook(tmp_path, {
                    "hook_event_name": "PreToolUse",
                    "session_id": root_session,
                    "turn_id": worker_turn,
                    "agent_id": worker_agent,
                    "tool_use_id": f"{surface}-worker-publish",
                    "tool_name": "mcp__cortex__publish_result",
                    "tool_input": publication_input,
                }) is None
                published = worker("publish_result", publication_input)
                assert not published["result"].get("isError"), published
                result = published["result"]["structuredContent"]
                assert result["state"] == "published"
                assert result["replayed"] is False

            evidence = coordinator("read_task", {
                "task_ref": task_ref,
                "view": "evidence",
            })
            assert not evidence["result"].get("isError"), evidence
            reports = evidence["result"]["structuredContent"]["data"]["reports"]
            assert len(reports) == 1
            assert f"{surface.capitalize()} parity fixture completed." in json.dumps(reports)
