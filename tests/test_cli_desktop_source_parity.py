"""Real hook plus persistent-stdio parity for CLI and Desktop lifecycle order."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_first_turn_requires_the_complete_direct_only_cortex_catalogue() -> None:
    """Keep Cortex out of every indirect host tool-routing surface."""
    companion = json.loads(
        (ROOT / "plugins/cortex/.mcp.json").read_text(encoding="utf-8")
    )
    server = companion["mcpServers"]["cortex"]
    assert server["required"] is True
    assert server["omit_tools_from"] == ["code_mode", "deferred"]
    assert set(server["omit_tools_from"]) == {"code_mode", "deferred"}


def test_desktop_packaged_first_turn_blocks_placeholder_governance_before_stdio(
    tmp_path: Path,
) -> None:
    """Desktop host guard keeps a model's wrong first choice out of MCP."""
    home = tmp_path / "profile"
    codex_home = home / ".codex"
    version = json.loads(
        (ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    package_root = codex_home / "plugins/cache/cortex/cortex" / version
    package_root.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "plugins/cortex", package_root)
    home.chmod(0o700)
    codex_home.chmod(0o700)
    plugin_data = codex_home / "plugins/data/cortex-cortex"
    project = tmp_path / "project"
    project.mkdir()
    session, turn = "desktop-root-session", "desktop-root-turn"

    def hook(event: dict) -> dict | None:
        return _installed_hook(package_root, plugin_data, home, event)

    hook({
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn,
        "prompt": "$cortex:orchestrator execute a long constrained product task",
    })
    denied = hook({
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_use_id": "desktop-placeholder-governance",
        "tool_name": "mcp__cortex__assess_governance",
        "tool_input": {"task_ref": "invalid", "mode": "full"},
    })
    assert denied is not None
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "open_task" in denied["hookSpecificOutput"]["permissionDecisionReason"]

    with _desktop_packaged_stdio_session(package_root, home) as coordinator:
        assert hook({
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": turn, "tool_use_id": "desktop-open-task",
            "tool_name": "mcp__cortex__open_task",
            "tool_input": {"project_root": str(project)},
        }) is None
        opened = coordinator("open_task", {
            "project_root": str(project),
            "request_original": "Exercise Desktop first-operation ordering.",
            "user_language": "en",
            "outcomes": [{
                "outcome": "Open the Desktop task before governance.",
                "acceptance": ["The host blocks placeholder governance."],
                "constraints": ["Do not mutate the fixture project."],
                "verification": ["Governance succeeds only after task opening."],
            }],
            "constraints": ["Keep the regression bounded."],
        })
        assert not opened["result"].get("isError"), opened
        task_ref = opened["result"]["structuredContent"]["task_ref"]
        hook({
            "hook_event_name": "PostToolUse", "session_id": session,
            "turn_id": turn, "tool_use_id": "desktop-open-task",
            "tool_name": "mcp__cortex__open_task",
            "tool_input": {"project_root": str(project)},
            "tool_response": opened["result"],
        })
        assert hook({
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": turn, "tool_use_id": "desktop-valid-governance",
            "tool_name": "mcp__cortex__assess_governance",
            "tool_input": {"task_ref": task_ref, "mode": "full"},
        }) is None
        assessed = coordinator("assess_governance", {
            "task_ref": task_ref, "mode": "full",
        })
        assert not assessed["result"].get("isError"), assessed


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


@contextmanager
def _desktop_packaged_stdio_session(package_root: Path, home: Path):
    """Run an installed-topology MCP exactly without Desktop-missing env vars."""
    environment = dict(os.environ)
    environment.update({
        "HOME": str(home),
        "CORTEX_SOURCE_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for name in (
        "CODEX_HOME", "PLUGIN_DATA", "CORTEX_SESSION_NONCE", "PYTHONPATH",
        "CODEX_THREAD_ID", "CODEX_SESSION_ID",
    ):
        environment.pop(name, None)
    process = subprocess.Popen(
        [sys.executable, "-B", str(package_root / "scripts/cortex.py")],
        cwd=package_root, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    request_id = 0
    notifications: list[dict] = []

    def rpc(method: str, params: dict) -> dict:
        nonlocal request_id
        request_id += 1
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params,
        }) + "\n")
        process.stdin.flush()
        while True:
            line = process.stdout.readline()
            if not line.strip():
                diagnostic = process.stderr.read(512) if process.stderr is not None else ""
                raise AssertionError(
                    "packaged Desktop MCP closed before replying"
                    + (f": {diagnostic}" if diagnostic else "")
                )
            payload = json.loads(line)
            if "id" not in payload and payload.get("method") == "notifications/tools/list_changed":
                notifications.append(payload)
                continue
            return payload

    initialized = rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "desktop-no-codex-home", "version": "1"},
    })
    assert process.stdin is not None
    process.stdin.write(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    }) + "\n")
    process.stdin.flush()

    def call(tool_name: str, arguments: dict) -> dict:
        return rpc("tools/call", {"name": tool_name, "arguments": arguments})

    call.rpc = rpc  # type: ignore[attr-defined]
    call.notifications = notifications  # type: ignore[attr-defined]
    call.initialize_result = initialized  # type: ignore[attr-defined]
    try:
        yield call
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=10)
        stderr_text = process.stderr.read(512) if process.stderr is not None else ""
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        assert process.returncode == 0, stderr_text


def _installed_hook(
    package_root: Path, plugin_data: Path, home: Path, event: dict,
) -> dict | None:
    environment = dict(os.environ)
    environment.update({
        "HOME": str(home), "PLUGIN_DATA": str(plugin_data),
        "PLUGIN_ROOT": str(package_root), "PYTHONDONTWRITEBYTECODE": "1",
    })
    environment.pop("CODEX_HOME", None)
    completed = subprocess.run(
        [sys.executable, "-B", str(package_root / "hooks/cortex_activation.py")],
        input=json.dumps(event), text=True, capture_output=True,
        env=environment, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout.strip()
    return json.loads(output) if output else None


@pytest.mark.parametrize(
    ("surface", "paginated"),
    [
        ("cli", False), ("desktop", False),
        ("cli", True), ("desktop", True),
    ],
)
def test_real_hook_and_persistent_stdio_worker_lifecycle_are_equivalent(
    tmp_path: Path, surface: str, paginated: bool,
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
        "outcome": f"Complete the {surface} {'paginated ' if paginated else ''}parity fixture.",
        "acceptance": ["One exact worker publication is durable."],
        "constraints": ["Use the real hook and one persistent MCP connection."],
        "verification": ["Coordinator evidence contains one completed result."],
    }
    evidence_outcomes = [
        {
            "outcome": f"Supply bounded predecessor evidence {index} for {surface}.",
            "acceptance": ["One finalized evidence report exists."],
            "constraints": ["Keep the public task-opening request bounded."],
            "verification": ["The report is consumed by the target assignment."],
        }
        for index in range(6)
    ] if paginated else []

    with patch.dict(os.environ, {"PLUGIN_DATA": plugin_data}):
        with COMMANDS._source_stdio_session(home) as coordinator:
            opened = coordinator("open_task", {
                "project_root": str(project),
                "request_original": f"Exercise ordinary {surface} worker startup.",
                "user_language": "en",
                "outcomes": [outcome, *evidence_outcomes],
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
            if paginated:
                # Build large predecessor evidence through ordinary public MCP
                # calls. Each call remains below the operation limit, while
                # the target assignment must be consumed across real pages.
                report_filler = "bounded-predecessor-report-" * 900
                for index, evidence_outcome in enumerate(evidence_outcomes):
                    evidence_assignment = coordinator("open_assignment", {
                        "task_ref": task_ref,
                        "role": f"predecessor evidence worker {index}",
                        "profile_name": "explorer",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "high",
                        "responsibility": "evidence",
                        "outcomes": [evidence_outcome["outcome"]],
                        "goal": "Publish one bounded predecessor report.",
                        "scope": evidence_outcome["outcome"],
                        "instructions": "Consume the assignment, then publish one report.",
                        "report_policy": "none",
                    })
                    assert not evidence_assignment["result"].get("isError"), evidence_assignment
                    evidence_ref = re.search(
                        r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
                        evidence_assignment["result"]["structuredContent"]
                        ["native_dispatch"]["message"],
                    ).group(1)
                    evidence_identity = (
                        f"{surface}-evidence-agent-{index}",
                        f"{surface}-evidence-turn-{index}",
                        f"{surface}-evidence-session-{index}",
                    )
                    COMMANDS._write_host_worker_receipt(
                        plugin_data, evidence_ref, authorize=True,
                        agent_id=evidence_identity[0],
                        turn_id=evidence_identity[1],
                        session_id=evidence_identity[2],
                    )
                    with COMMANDS._source_stdio_session(
                        home, host_identity=evidence_identity,
                    ) as evidence_worker:
                        evidence_read = evidence_worker("read_task", {
                            "task_ref": evidence_ref,
                        })
                        assert not evidence_read["result"].get("isError"), evidence_read
                        evidence_published = evidence_worker("publish_result", {
                            "task_ref": evidence_ref,
                            "summary": f"Predecessor {index}: {report_filler}",
                            "outcome": "The bounded predecessor evidence was captured.",
                            "changes": [],
                            "verification_facts": [{
                                "state": "executed",
                                "summary": "The source fixture produced this report.",
                            }],
                            "outcome_coverage": [{
                                "outcome": evidence_outcome["outcome"],
                                "status": "complete",
                                "verification": ["The report was finalized."],
                            }],
                            "documentation_impact": "No documentation change.",
                            "risks": [], "unresolved": [], "status": "completed",
                        })
                        assert not evidence_published["result"].get("isError"), evidence_published
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
                "outcomes": [outcome["outcome"]],
                "report_policy": "all_finalized" if paginated else "none",
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
                page_count = 0
                while True:
                    page_count += 1
                    tool_use_id = f"{surface}-worker-read-{page_count}"
                    assert _hook(tmp_path, {
                        "hook_event_name": "PreToolUse",
                        "session_id": root_session,
                        "turn_id": worker_turn,
                        "agent_id": worker_agent,
                        "tool_use_id": tool_use_id,
                        "tool_name": "mcp__cortex__read_task",
                        "tool_input": read_input,
                    }) is None
                    consumed = worker("read_task", read_input)
                    assert not consumed["result"].get("isError"), consumed
                    structured = consumed["result"]["structuredContent"]
                    post_read = _hook(tmp_path, {
                        "hook_event_name": "PostToolUse",
                        "session_id": root_session,
                        "turn_id": worker_turn,
                        "agent_id": worker_agent,
                        "tool_use_id": tool_use_id,
                        "tool_name": "mcp__cortex__read_task",
                        "tool_input": read_input,
                        "tool_response": consumed["result"],
                    })
                    if structured["has_more"]:
                        assert post_read is None
                    else:
                        assert post_read is not None
                        assert "Do not read the task again" in (
                            post_read["hookSpecificOutput"]["additionalContext"]
                        )
                    if not structured["has_more"]:
                        break
                    read_input = {
                        "task_ref": worker_ref,
                                                "continue": True,
                    }
                assert (page_count > 1) is paginated

                narrowed = worker.rpc("tools/list", {})
                worker_tools = {item["name"] for item in narrowed["result"]["tools"]}
                assert worker_tools == {
                    "read_task", "publish_plan", "publish_result",
                    "publish_documentation",
                }
                # Desktop adopts worker identity only after the terminal
                # assignment page. A mid-turn catalogue notification here can
                # make the host replay that already-successful bootstrap.
                assert worker.notifications == []

                _hook(tmp_path, {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": root_session,
                    "turn_id": worker_turn,
                    "agent_id": worker_agent,
                })
                recovery_input = {"task_ref": worker_ref}
                recovery_pages = 0
                while True:
                    recovery_pages += 1
                    recovery_tool_use_id = (
                        f"{surface}-worker-recovery-read-{recovery_pages}"
                    )
                    assert _hook(tmp_path, {
                        "hook_event_name": "PreToolUse",
                        "session_id": root_session,
                        "turn_id": worker_turn,
                        "agent_id": worker_agent,
                        "tool_use_id": recovery_tool_use_id,
                        "tool_name": "mcp__cortex__read_task",
                        "tool_input": recovery_input,
                    }) is None
                    recovered = worker("read_task", recovery_input)
                    assert not recovered["result"].get("isError"), recovered
                    recovered_page = recovered["result"]["structuredContent"]
                    recovery_context = _hook(tmp_path, {
                        "hook_event_name": "PostToolUse",
                        "session_id": root_session,
                        "turn_id": worker_turn,
                        "agent_id": worker_agent,
                        "tool_use_id": recovery_tool_use_id,
                        "tool_name": "mcp__cortex__read_task",
                        "tool_input": recovery_input,
                        "tool_response": recovered["result"],
                    })
                    if recovered_page["has_more"]:
                        assert recovery_context is None
                    else:
                        assert recovery_context is not None
                        assert "Do not read the task again" in (
                            recovery_context["hookSpecificOutput"]["additionalContext"]
                        )
                    if not recovered_page["has_more"]:
                        break
                    recovery_input = {
                        "task_ref": worker_ref,
                                                "continue": True,
                    }
                assert recovery_pages == page_count
                assert worker.notifications == []

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

            _hook(tmp_path, {
                "hook_event_name": "SessionStart",
                "source": "compact",
                "session_id": root_session,
                "turn_id": "coordinator-after-compact",
            })
            coordinator_state_input = {"task_ref": task_ref}
            assert _hook(tmp_path, {
                "hook_event_name": "PreToolUse",
                "session_id": root_session,
                "turn_id": "coordinator-after-compact",
                "tool_name": "mcp__cortex__read_state",
                "tool_input": coordinator_state_input,
            }) is None
            coordinator_state = coordinator("read_state", coordinator_state_input)
            assert not coordinator_state["result"].get("isError"), coordinator_state
            assert _hook(tmp_path, {
                "hook_event_name": "PostToolUse",
                "session_id": root_session,
                "turn_id": "coordinator-after-compact",
                "tool_name": "mcp__cortex__read_state",
                "tool_input": coordinator_state_input,
                "tool_response": coordinator_state["result"],
            }) is None

            reports = []
            evidence_input = {"task_ref": task_ref, "report_policy": "all_finalized"}
            while True:
                evidence = coordinator("read_evidence", evidence_input)
                assert not evidence["result"].get("isError"), evidence
                evidence_page = evidence["result"]["structuredContent"]
                reports.extend(evidence_page["data"]["reports"])
                if not evidence_page["has_more"]:
                    break
                evidence_input = {
                    "task_ref": task_ref,
                    "report_policy": "all_finalized",
                    "continue": True,
                }
            target_summary = f"{surface.capitalize()} parity fixture completed."
            assert json.dumps(reports).count(target_summary) == 1


def test_desktop_packaged_worker_claims_hook_authorization_without_codex_home_env(
    tmp_path: Path,
) -> None:
    """Ordinary Desktop supplies HOME but may omit CODEX_HOME and PLUGIN_DATA."""
    home = tmp_path / "profile"
    codex_home = home / ".codex"
    version = json.loads(
        (ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    package_root = codex_home / "plugins/cache/cortex/cortex" / version
    package_root.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "plugins/cortex", package_root)
    home.chmod(0o700)
    codex_home.chmod(0o700)
    plugin_data = codex_home / "plugins/data/cortex-cortex"
    project = tmp_path / "project"
    project.mkdir()
    root_session = "desktop-real-root-session"
    root_turn = "desktop-real-root-turn"
    worker_turn = "desktop-real-worker-turn"
    worker_agent = "desktop-real-worker-agent"
    outcome = {
        "outcome": "Complete the ordinary Desktop worker bootstrap fixture.",
        "acceptance": ["The first worker assignment read succeeds without CODEX_HOME."],
        "constraints": ["Use installed package topology and the real activation hook."],
        "verification": ["One worker result is published through the same connection."],
    }

    with _desktop_packaged_stdio_session(package_root, home) as coordinator:
        opened = coordinator("open_task", {
            "project_root": str(project),
            "request_original": "Exercise ordinary Desktop worker startup.",
            "user_language": "en", "outcomes": [outcome],
            "constraints": ["Do not inject CODEX_HOME into the MCP process."],
        })
        assert not opened["result"].get("isError"), opened
        task_ref = opened["result"]["structuredContent"]["task_ref"]
        assessed = coordinator("assess_governance", {
            "task_ref": task_ref, "mode": "minimal",
            "rationale": "Single Desktop environment regression.", "risk_factors": [],
        })
        assert not assessed["result"].get("isError"), assessed
        assigned = coordinator("open_assignment", {
            "task_ref": task_ref, "role": "Desktop environment parity worker",
            "profile_name": "backend_dev", "model": "gpt-5.6-luna",
            "reasoning_effort": "high", "responsibility": "delivery",
            "goal": "Consume and publish once without MCP environment injection.",
            "scope": outcome["outcome"],
            "instructions": "Consume the exact assignment, then publish one result.",
            "report_policy": "none",
        })
        assert not assigned["result"].get("isError"), assigned
        native_dispatch = assigned["result"]["structuredContent"]["native_dispatch"]
        worker_ref = re.search(
            r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"',
            native_dispatch["message"],
        ).group(1)

        def hook(event: dict) -> dict | None:
            return _installed_hook(package_root, plugin_data, home, event)

        hook({
            "hook_event_name": "UserPromptSubmit", "session_id": root_session,
            "turn_id": root_turn, "prompt": "$cortex:orchestrator run the fixture",
        })
        hook({
            "hook_event_name": "PostToolUse", "session_id": root_session,
            "turn_id": root_turn, "tool_name": "mcp__cortex__open_task",
            "tool_input": {"project_root": str(project)},
            "tool_response": opened["result"],
        })
        hook({
            "hook_event_name": "PostToolUse", "session_id": root_session,
            "turn_id": root_turn, "tool_name": "mcp__cortex__open_assignment",
            "tool_input": {"task_ref": task_ref},
            "tool_response": assigned["result"],
        })
        spawn = hook({
            "hook_event_name": "PreToolUse", "session_id": root_session,
            "turn_id": root_turn, "tool_use_id": "desktop-real-spawn",
            "tool_name": "collaboration.spawn_agent", "tool_input": native_dispatch,
        })
        assert spawn is not None
        assert spawn["hookSpecificOutput"].get("permissionDecision") != "deny"

        with _desktop_packaged_stdio_session(package_root, home) as worker:
            listed = worker.rpc("tools/list", {})
            assert {tool["name"] for tool in listed["result"]["tools"]} >= {
                "open_task", "read_task", "publish_result",
            }
            hook({
                "hook_event_name": "SubagentStart", "session_id": root_session,
                "turn_id": worker_turn, "agent_id": worker_agent,
                "transcript_path": (
                    "/tmp/rollout-01a0612d-0a96-7b01-823f-9128e1142472.jsonl"
                ),
            })
            # Mirror the real failed Desktop trace: an unrelated shell tool ran
            # before the first Cortex read. It must neither consume nor revoke
            # the worker lease.
            for event_name in ("PreToolUse", "PostToolUse"):
                hook({
                    "hook_event_name": event_name, "session_id": root_session,
                    "turn_id": worker_turn, "agent_id": worker_agent,
                    "tool_use_id": "desktop-real-intervening-shell",
                    "tool_name": "functions.exec_command",
                    "tool_input": {"cmd": "true"},
                    **({"tool_response": {"isError": False}} if event_name == "PostToolUse" else {}),
                })

            # The neutral catalogue advertises a dedicated worker assignment
            # read with no coordinator view or report-policy selector.
            read_input = {"task_ref": worker_ref}
            assert hook({
                "hook_event_name": "PreToolUse", "session_id": root_session,
                "turn_id": worker_turn, "agent_id": worker_agent,
                "tool_use_id": "desktop-real-worker-read",
                "tool_name": "mcp__cortex__read_task", "tool_input": read_input,
            }) is None
            consumed = worker("read_task", read_input)
            assert not consumed["result"].get("isError"), consumed
            terminal_context = hook({
                "hook_event_name": "PostToolUse", "session_id": root_session,
                "turn_id": worker_turn, "agent_id": worker_agent,
                "tool_use_id": "desktop-real-worker-read",
                "tool_name": "mcp__cortex__read_task", "tool_input": read_input,
                "tool_response": consumed["result"],
            })
            assert terminal_context is not None
            assert "Do not read the task again" in (
                terminal_context["hookSpecificOutput"]["additionalContext"]
            )

            publication = {
                "task_ref": worker_ref, "summary": "Desktop env fallback verified.",
                "outcome": "The host authorization was consumed without CODEX_HOME.",
                "changes": [],
                "verification_facts": [{
                    "state": "executed",
                    "summary": "Packaged MCP claimed the real hook authorization.",
                }],
                "outcome_coverage": [{
                    "outcome": outcome["outcome"], "status": "complete",
                    "verification": ["The exact first read and publication succeeded."],
                }],
                "documentation_impact": "Regression-only fixture.",
                "risks": [], "unresolved": [], "status": "completed",
            }
            published = worker("publish_result", publication)
            assert not published["result"].get("isError"), published
            assert published["result"]["structuredContent"]["replayed"] is False

        evidence = coordinator("read_evidence", {"task_ref": task_ref, "report_policy": "all_finalized"})
        assert not evidence["result"].get("isError"), evidence
        assert len(evidence["result"]["structuredContent"]["data"]["reports"]) == 1
