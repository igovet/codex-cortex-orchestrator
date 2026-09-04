from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins/cortex/hooks/cortex_activation.py"


def test_retired_delegation_operation_is_not_a_dispatch_boundary():
    spec = importlib.util.spec_from_file_location("cortex_activation_contract", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._is_assignment_open("mcp__cortex__open_assignment")
    assert not module._is_assignment_open("mcp__cortex__create_delegation")
    assert not hasattr(module, "_capture_live_prompt_binding")


def invoke(tmp_path: Path, event: dict) -> tuple[int, dict | None]:
    if event.get("hook_event_name") == "SubagentStart" and "transcript_path" not in event:
        seed = hashlib.sha256(str(event.get("agent_id", "worker")).encode()).hexdigest()
        thread_id = f"{seed[:8]}-{seed[8:12]}-4{seed[13:16]}-8{seed[17:20]}-{seed[20:32]}"
        event = dict(event, transcript_path=f"/tmp/rollout-test-{thread_id}.jsonl")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(tmp_path / "codex-home")
    environment["PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    environment["PLUGIN_ROOT"] = str(ROOT / "plugins/cortex")
    completed = subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(event), text=True, capture_output=True, env=environment, check=False)
    output = completed.stdout.strip()
    return completed.returncode, json.loads(output) if output else None


def state_file(tmp_path: Path, session: str) -> Path:
    digest = hashlib.sha256(("session:" + session).encode()).hexdigest()
    return tmp_path / "plugin-data" / "activation" / f"turn-{digest}.json"


def worker_message(worker_ref: str) -> str:
    return f'worker contract\n```json\n{{"assignment context":{{"task_ref":"{worker_ref}"}}}}\n```'


def native_dispatch(worker_ref: str, task_name: str) -> dict[str, str]:
    return {
        "fork_turns": "none",
        "message": worker_message(worker_ref),
        "task_name": task_name,
        "reasoning_effort": "low",
    }


def test_open_task_anchors_from_direct_task_ref_receipt(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_task", "tool_response": {"isError": False, "structuredContent": {"task_ref": "t_0123456789ab", "replayed": False}}})
    assert json.loads(state_file(tmp_path, session).read_text())["anchored"] is True


def test_selected_coordinator_denies_every_pre_anchor_cortex_call_except_open_task(
    tmp_path: Path,
) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator run a long task",
    })
    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__assess_governance",
        "tool_input": {"task_ref": "invalid", "mode": "full"},
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "open_task" in denied["hookSpecificOutput"]["permissionDecisionReason"]

    code, allowed_open = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_task",
        "tool_input": {
            "project_root": "/project", "request_original": "task",
            "user_language": "en", "outcomes": [], "constraints": [],
        },
    })
    assert code == 0 and allowed_open is None
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_task",
        "tool_response": {
            "isError": False,
            "structuredContent": {"task_ref": "t_0123456789ab", "replayed": False},
        },
    })
    code, allowed_assessment = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__assess_governance",
        "tool_input": {"task_ref": "t_0123456789ab", "mode": "full"},
    })
    assert code == 0 and allowed_assessment is None


def test_selected_coordinator_denies_nested_cortex_calls_but_allows_direct_calls(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "functions.exec",
        "tool_input": {
            "code": "const result = await tools.mcp__cortex__open_task({}); text(result);",
        },
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "separate direct tool calls" in denied["hookSpecificOutput"]["permissionDecisionReason"]

    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_task",
        "tool_input": {
            "outcomes": [], "project_root": "/project", "request_original": "x",
            "user_language": "en", "constraints": [],
        },
    })
    assert code == 0 and allowed is None


def test_programmatic_guard_does_not_block_ordinary_exec(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "functions.exec",
        "tool_input": {
            "code": "const result = await tools.exec_command({cmd: 'pwd'}); text(result.output);",
        },
    })
    assert code == 0 and allowed is None


def test_native_worker_also_denies_nested_cortex_calls(tmp_path: Path) -> None:
    session = "root"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": "root-turn", "prompt": "$cortex:orchestrator",
    })
    invoke(tmp_path, {
        "hook_event_name": "SubagentStart", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
    })
    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "exec",
        "tool_input": {
            "source": "const report = await tools['mcp__cortex__publish_result']({}); text(report);",
        },
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_compact_session_start_reloads_exact_skills_repeatedly_without_shell_or_approval(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    event = {
        "hook_event_name": "SessionStart", "source": "compact",
        "session_id": session, "turn_id": turn,
    }
    first_code, first = invoke(tmp_path, event)
    second_code, second = invoke(tmp_path, event)
    assert first_code == second_code == 0
    assert first == second
    context = first["hookSpecificOutput"]["additionalContext"]
    assert "Exact packaged Cortex skill reload: orchestrator/SKILL.md" in context
    assert "Exact packaged Cortex skill reload: cortex-control/SKILL.md" in context
    assert "## Routing state machine" in context
    assert "## Worker routing state machine" in context
    assert "approval" in context.lower()
    assert "cat " not in context.lower()


def test_compacted_coordinator_must_refresh_state_before_mutation(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    task_ref = "t_0123456789ab"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_task",
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": task_ref, "replayed": False,
        }},
    })
    invoke(tmp_path, {
        "hook_event_name": "SessionStart", "source": "compact",
        # Real Desktop compaction recovery does not necessarily include a
        # turn_id on SessionStart; the session-scoped guard must still arm.
        "session_id": session,
    })

    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "turn-after-compact",
        "tool_name": "mcp__cortex__record_plan_review",
        "tool_input": {"task_ref": task_ref},
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    state_input = {"task_ref": task_ref}
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "turn-after-compact", "tool_name": "mcp__cortex__read_state",
        "tool_input": state_input,
    })
    assert code == 0 and allowed is None
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "turn-after-compact", "tool_name": "mcp__cortex__read_state",
        "tool_input": state_input,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": task_ref, "data": {"unfinished_assignment_count": 0},
        }},
    })
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "turn-after-compact",
        "tool_name": "mcp__cortex__record_plan_review",
        "tool_input": {"task_ref": task_ref},
    })
    assert code == 0 and allowed is None


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_recovery_requires_all_continuation_pages_for_the_same_task(tmp_path: Path, source: str) -> None:
    task_ref = "t_0123456789ab"
    common = {"session_id": "recovery", "turn_id": "turn"}
    invoke(tmp_path, {**common, "hook_event_name": "UserPromptSubmit", "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__open_task",
        "tool_response": {"isError": False, "structuredContent": {"task_ref": task_ref, "replayed": False}}})
    invoke(tmp_path, {**common, "hook_event_name": "SessionStart", "source": source})
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__read_state",
        "tool_input": {"task_ref": task_ref}, "tool_response": {"isError": False, "structuredContent": {
            "task_ref": task_ref, "data": {"unfinished_assignment_count": 2},
        }}})
    for operation in ("read_scope", "read_timeline", "record_steering", "open_assignment", "close_task"):
        _, denied = invoke(tmp_path, {**common, "hook_event_name": "PreToolUse",
            "tool_name": f"mcp__cortex__{operation}", "tool_input": {"task_ref": task_ref}})
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    for response_ref, has_more in ((task_ref, True), ("t_abcdef012345", False)):
        invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__read_continuations",
            "tool_response": {"isError": False, "structuredContent": {
                "task_ref": response_ref, "has_more": has_more, "data": {"continuations": []},
            }}})
        assert json.loads(state_file(tmp_path, "recovery").read_text())["continuations_read_required"] is True
    invoke(tmp_path, {**common, "hook_event_name": "PostToolUse", "tool_name": "mcp__cortex__read_continuations",
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": task_ref, "has_more": False, "data": {"continuations": []},
        }}})
    _, allowed = invoke(tmp_path, {**common, "hook_event_name": "PreToolUse", "tool_name": "mcp__cortex__read_scope",
        "tool_input": {"task_ref": task_ref, "responsibility": "delivery"}})
    assert allowed is None


def test_postcompact_is_observation_only_and_does_not_emit_unsupported_context(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    code, result = invoke(tmp_path, {
        "hook_event_name": "PostCompact", "session_id": session,
        "turn_id": turn,
    })
    assert code == 0
    assert result is None


def test_nonrecovery_session_start_does_not_repeat_skills(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    for source in ("startup", "clear"):
        code, result = invoke(tmp_path, {
            "hook_event_name": "SessionStart", "source": source,
            "session_id": session, "turn_id": turn,
        })
        assert code == 0
        assert result is None


def test_activation_session_start_does_not_overwrite_lifecycle_owned_live_binding(tmp_path: Path) -> None:
    """The lifecycle observer is the sole writer of the exact resume identity."""
    codex_home = tmp_path / "home/.codex"
    hook = codex_home / "plugins/cache/cortex/cortex/1.15.0/hooks/cortex_activation.py"
    hook.parent.mkdir(parents=True)
    hook.write_bytes(HOOK.read_bytes())
    launch = codex_home / ".cortex-live-launch.json"
    launch.write_text(json.dumps({"cwd": "/project", "session_nonce": "n" * 64}) + "\n")
    launch.chmod(0o600)
    binding = codex_home / ".cortex-live-binding.json"
    original = {
        "session_id": "root-session",
        "source": "cli",
        "cwd": "/project",
        "session_nonce": "n" * 64,
        "workdir_fingerprint": "workdir",
        "isolated_codex_fingerprint": "profile",
    }
    binding.write_text(json.dumps(original, sort_keys=True) + "\n")
    binding.chmod(0o600)
    environment = os.environ.copy()
    environment.update({
        "CODEX_HOME": str(codex_home),
        "CORTEX_LIVE_BINDING_PATH": str(binding),
        "PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "PLUGIN_ROOT": str(ROOT / "plugins/cortex"),
    })
    completed = subprocess.run(
        [sys.executable, "-B", str(hook)],
        input=json.dumps({
            "hook_event_name": "SessionStart", "source": "startup",
            "session_id": "root-session", "cwd": "/project",
        }),
        text=True, capture_output=True, env=environment, check=False,
    )
    assert completed.returncode == 0
    assert json.loads(binding.read_text()) == original


def test_open_assignment_receipt_is_correlated_without_public_assignment_identity(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native_dispatch(worker_ref, "qa_worker"), "replayed": False}}})
    records = list((tmp_path / "plugin-data/activation/sessions").glob("*/dispatch/dispatch-*.json"))
    assert len(records) == 1
    stored = json.loads(records[0].read_text())
    assert stored["worker_task_ref_digest"] == hashlib.sha256(worker_ref.encode()).hexdigest()
    assert stored["assignment_ref_digest"] == hashlib.sha256(("d_" + worker_ref[-12:]).encode()).hexdigest()
    assert stored["message_digest"] == hashlib.sha256(worker_message(worker_ref).encode()).hexdigest()
    serialized = records[0].read_text()
    assert worker_ref not in serialized
    assert "t_0123456789ab" not in serialized


def test_parallel_dispatches_claim_by_exact_native_task_name(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    for index in range(2):
        ref = "t_0123456789ab_" + f"{index + 1:032x}"
        native = native_dispatch(ref, f"worker_{index}")
        invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    for index in (1, 0):
        native = native_dispatch("t_0123456789ab_" + f"{index + 1:032x}", f"worker_{index}")
        code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": f"spawn-{index}", "tool_name": "collaboration.spawn_agent", "tool_input": native})
        assert code == 0 and result["hookSpecificOutput"]["additionalContext"]


def test_parallel_workers_without_agent_id_keep_thread_scoped_publication_leases(tmp_path: Path) -> None:
    """Desktop child tool hooks may omit agent_id without aliasing siblings."""
    session, root_turn = "root", "turn"
    worker_turn = "shared-worker-turn"
    refs = ["t_0123456789ab_" + f"{index + 1:032x}" for index in range(2)]
    threads = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": root_turn, "prompt": "$cortex:orchestrator",
    })
    natives = []
    for index, ref in enumerate(refs):
        native = native_dispatch(ref, f"worker_{index}")
        natives.append(native)
        invoke(tmp_path, {
            "hook_event_name": "PostToolUse", "session_id": session,
            "turn_id": root_turn, "tool_name": "mcp__cortex__open_assignment",
            "tool_input": {"task_ref": "t_0123456789ab"},
            "tool_response": {"isError": False, "structuredContent": {
                "native_dispatch": native, "replayed": False,
            }},
        })
        code, spawned = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": root_turn, "tool_use_id": f"spawn-{index}",
            "tool_name": "collaboration.spawn_agent", "tool_input": native,
        })
        assert code == 0 and spawned["hookSpecificOutput"]["additionalContext"]

    for index, ref in enumerate(refs):
        transcript = f"/tmp/rollout-{threads[index]}.jsonl"
        invoke(tmp_path, {
            "hook_event_name": "SubagentStart", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "transcript_path": transcript,
        })
        read_input = {"task_ref": ref}
        code, allowed = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "transcript_path": transcript,
            "tool_use_id": f"read-{index}", "tool_name": "mcp__cortex__read_task",
            "tool_input": read_input,
        })
        assert code == 0 and allowed is None
        invoke(tmp_path, {
            "hook_event_name": "PostToolUse", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "transcript_path": transcript,
            "tool_name": "mcp__cortex__read_task", "tool_input": read_input,
            "tool_response": {"isError": False, "structuredContent": {
                "task_ref": ref, "data": {}, "has_more": False,
            }},
        })

    for index, ref in enumerate(refs):
        code, allowed = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": worker_turn,
            "transcript_path": f"/tmp/rollout-{threads[index]}.jsonl",
            "tool_name": "mcp__cortex__publish_result",
            "tool_input": {"task_ref": ref},
        })
        assert code == 0 and allowed is None


def test_parallel_workers_bind_by_first_assignment_read_when_lifecycle_order_differs(tmp_path: Path) -> None:
    """Concurrent SubagentStart order must not swap child assignment leases."""
    session, root_turn = "root", "turn"
    worker_turn = "shared-worker-turn"
    refs = ["t_0123456789ab_" + f"{index + 1:032x}" for index in range(2)]
    threads = [
        "33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
    ]
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": root_turn, "prompt": "$cortex:orchestrator"})
    for index, ref in enumerate(refs):
        native = native_dispatch(ref, f"worker_{index}")
        invoke(tmp_path, {
            "hook_event_name": "PostToolUse", "session_id": session,
            "turn_id": root_turn, "tool_name": "mcp__cortex__open_assignment",
            "tool_input": {"task_ref": "t_0123456789ab"},
            "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}},
        })
        code, spawned = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": root_turn, "tool_use_id": f"spawn-{index}",
            "tool_name": "collaboration.spawn_agent", "tool_input": native,
        })
        assert code == 0 and spawned["hookSpecificOutput"]["additionalContext"]

    # Lifecycle notifications intentionally arrive in reverse order.  Each
    # child then presents its own task_ref; lazy binding must select that
    # exact receipt rather than the first pending queue entry.
    for index in (1, 0):
        transcript = f"/tmp/rollout-{threads[index]}.jsonl"
        invoke(tmp_path, {
            "hook_event_name": "SubagentStart", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "transcript_path": transcript,
        })
        read_input = {"task_ref": refs[index]}
        code, allowed = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            # Real follow-up tool hooks do not reliably repeat the transcript
            # path exposed by SubagentStart.
            "tool_use_id": f"read-{index}",
            "tool_name": "mcp__cortex__read_task", "tool_input": read_input,
        })
        assert code == 0 and allowed is None
        invoke(tmp_path, {
            "hook_event_name": "PostToolUse", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "tool_name": "mcp__cortex__read_task",
            "tool_input": read_input,
            "tool_response": {"isError": False, "structuredContent": {"task_ref": refs[index], "data": {}, "has_more": False}},
        })

    # Each child retains its exact lease for publication even though the
    # follow-up hook again omits transcript_path and siblings share a turn.
    for index, ref in enumerate(refs):
        code, allowed = invoke(tmp_path, {
            "hook_event_name": "PreToolUse", "session_id": session,
            "turn_id": worker_turn, "agent_id": f"agent-{index}",
            "tool_name": "mcp__cortex__publish_result",
            "tool_input": {"task_ref": ref},
        })
        assert code == 0 and allowed is None


def test_assignment_receipt_and_spawn_may_use_different_turns(tmp_path: Path) -> None:
    """A dispatch is session-scoped, not incorrectly coupled to one turn."""
    session = "root"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": "turn-open", "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "turn-open", "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "turn-spawn", "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    assert code == 0 and result["hookSpecificOutput"]["additionalContext"]


def test_native_spawn_issues_signed_catalogue_hint_without_call_authority(tmp_path: Path) -> None:
    from cortex_runtime.audience_attestation import (
        claim_worker_candidate,
        verify_worker_catalogue_pending,
    )

    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_assignment",
        "tool_input": {"task_ref": "t_0123456789ab"},
        "tool_response": {"isError": False, "structuredContent": {
            "native_dispatch": native, "replayed": False,
        }},
    })
    code, result = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_use_id": "spawn",
        "tool_name": "collaboration.spawn_agent", "tool_input": native,
    })
    assert code == 0 and result["hookSpecificOutput"]["additionalContext"]
    receipt = next((tmp_path / "plugin-data/activation/sessions").glob(
        "*/dispatch/dispatch-*.json"
    ))
    record = json.loads(receipt.read_text())
    assert record["state"] == "worker_catalogue_pending"
    assert verify_worker_catalogue_pending(tmp_path / "plugin-data", record)
    assert claim_worker_candidate(
        tmp_path / "plugin-data", task_ref=worker_ref,
        connection_nonce="catalogue-hint-has-no-call-authority",
    ) is None


def test_host_protected_message_preserves_server_dispatch_correlation(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    changed = dict(native)
    changed["message"] = "gAAAA-host-protected-native-message"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert result["hookSpecificOutput"]["additionalContext"]


def test_host_explicit_luna_matches_server_omitted_default_model(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    materialized = dict(native)
    materialized["model"] = "gpt-5.6-luna"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": materialized})
    assert code == 0
    assert result["hookSpecificOutput"]["additionalContext"]


def test_host_may_omit_optional_non_luna_routing_from_protected_spawn_view(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    native.update({"model": "gpt-5.6-terra", "reasoning_effort": "medium"})
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    protected = {key: native[key] for key in ("fork_turns", "message", "task_name")}
    protected["message"] = "gAAAA-host-protected-native-message"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": protected})
    assert code == 0
    assert result["hookSpecificOutput"]["additionalContext"]


def test_spawn_routing_must_equal_the_server_projection(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    worker_ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(worker_ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    changed = dict(native)
    changed["reasoning_effort"] = "high"
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": changed})
    assert code == 0
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_requires_terminal_worker_bootstrap_before_publication(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent"})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__publish_result", "tool_input": {"task_ref": ref}})
    assert code == 0
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_use_id": "worker-read", "tool_name": "mcp__cortex__read_task", "tool_input": {"task_ref": ref}})
    assert code == 0 and allowed is None
    receipt = next((tmp_path / "plugin-data/activation/sessions").glob("*/dispatch/dispatch-*.json"))
    authorized = json.loads(receipt.read_text())
    assert authorized["state"] == "worker_call_authorized"
    assert authorized["authorized_tool_use_digest"] == hashlib.sha256(b"worker-read").hexdigest()

    code, terminal = invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__read_task", "tool_input": {"task_ref": ref}, "tool_response": {"isError": False, "structuredContent": {"task_ref": ref, "data": {}, "has_more": False}}})
    assert code == 0
    context = terminal["hookSpecificOutput"]["additionalContext"]
    assert "assignment consumption is complete" in context
    assert "Do not read the task again" in context
    code, allowed = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_name": "mcp__cortex__publish_result", "tool_input": {"task_ref": ref}})
    assert code == 0 and allowed is None


def test_paginated_worker_bootstrap_keeps_one_lifecycle_authorization(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_assignment",
        "tool_input": {"task_ref": "t_0123456789ab"},
        "tool_response": {"isError": False, "structuredContent": {
            "native_dispatch": native, "replayed": False,
        }},
    })
    invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_use_id": "spawn",
        "tool_name": "collaboration.spawn_agent", "tool_input": native,
    })
    invoke(tmp_path, {
        "hook_event_name": "SubagentStart", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
    })

    first_input = {"task_ref": ref}
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "worker-read-1", "tool_name": "mcp__cortex__read_task",
        "tool_input": first_input,
    })
    assert code == 0 and allowed is None
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "worker-read-1", "tool_name": "mcp__cortex__read_task",
        "tool_input": first_input,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": ref, "data": {},
            "has_more": True,
        }},
    })

    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"task_ref": ref},
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    continuation_input = {
        "task_ref": ref, "continue": True,
    }
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "worker-read-2", "tool_name": "mcp__cortex__read_task",
        "tool_input": continuation_input,
    })
    assert code == 0 and allowed is None
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "worker-read-2", "tool_name": "mcp__cortex__read_task",
        "tool_input": continuation_input,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": ref, "data": {},
            "has_more": False,
        }},
    })
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"task_ref": ref},
    })
    assert code == 0 and allowed is None


def test_compacted_worker_must_refresh_terminal_assignment_before_publication(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {
        "hook_event_name": "UserPromptSubmit", "session_id": session,
        "turn_id": turn, "prompt": "$cortex:orchestrator",
    })
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": turn, "tool_name": "mcp__cortex__open_assignment",
        "tool_input": {"task_ref": "t_0123456789ab"},
        "tool_response": {"isError": False, "structuredContent": {
            "native_dispatch": native, "replayed": False,
        }},
    })
    invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": turn, "tool_use_id": "spawn",
        "tool_name": "collaboration.spawn_agent", "tool_input": native,
    })
    invoke(tmp_path, {
        "hook_event_name": "SubagentStart", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
    })
    initial_input = {"task_ref": ref}
    invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "initial-read", "tool_name": "mcp__cortex__read_task",
        "tool_input": initial_input,
    })
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "initial-read", "tool_name": "mcp__cortex__read_task",
        "tool_input": initial_input,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": ref, "data": {},
            "has_more": False,
        }},
    })

    compact_code, compact_result = invoke(tmp_path, {
        "hook_event_name": "SessionStart", "source": "compact",
        "session_id": session, "turn_id": "worker-turn", "agent_id": "agent",
    })
    assert compact_code == 0
    compact_context = compact_result["hookSpecificOutput"]["additionalContext"]
    assert "Exact packaged Cortex skill reload: cortex-control/SKILL.md" in compact_context
    assert "## Worker routing state machine" in compact_context
    assert "Exact packaged Cortex skill reload: orchestrator/SKILL.md" not in compact_context
    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"task_ref": ref},
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    first_input = {"task_ref": ref}
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__read_task", "tool_input": first_input,
    })
    assert code == 0 and allowed is None
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__read_task", "tool_input": first_input,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": ref, "data": {},
            "has_more": True,
        }},
    })
    code, denied = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"task_ref": ref},
    })
    assert code == 0
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    continuation = {"task_ref": ref, "continue": True}
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__read_task", "tool_input": continuation,
    })
    assert code == 0 and allowed is None
    code, recovered = invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__read_task", "tool_input": continuation,
        "tool_response": {"isError": False, "structuredContent": {
            "task_ref": ref, "data": {},
            "has_more": False,
        }},
    })
    assert code == 0
    assert "Do not read the task again" in recovered["hookSpecificOutput"]["additionalContext"]
    code, allowed = invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_name": "mcp__cortex__publish_result",
        "tool_input": {"task_ref": ref},
    })
    assert code == 0 and allowed is None


def test_hook_rejects_worker_operation_for_another_assignment(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent"})
    code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent", "tool_use_id": "wrong-worker-read", "tool_name": "mcp__cortex__read_task", "tool_input": {"task_ref": "t_0123456789ab_" + "b" * 32}})
    assert code == 0
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_failed_worker_bootstrap_revokes_exact_host_call_authorization(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    native = native_dispatch(ref, "worker")
    invoke(tmp_path, {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_name": "mcp__cortex__open_assignment", "tool_input": {"task_ref": "t_0123456789ab"}, "tool_response": {"isError": False, "structuredContent": {"native_dispatch": native, "replayed": False}}})
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_use_id": "spawn", "tool_name": "collaboration.spawn_agent", "tool_input": native})
    invoke(tmp_path, {"hook_event_name": "SubagentStart", "session_id": session, "turn_id": "worker-turn", "agent_id": "agent"})
    failed_input = {"task_ref": ref, "worker_label": "invented"}
    invoke(tmp_path, {
        "hook_event_name": "PreToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "failed-read", "tool_name": "mcp__cortex__read_task",
        "tool_input": failed_input,
    })
    receipt = next((tmp_path / "plugin-data/activation/sessions").glob("*/dispatch/dispatch-*.json"))
    assert json.loads(receipt.read_text())["state"] == "worker_call_authorized"
    invoke(tmp_path, {
        "hook_event_name": "PostToolUse", "session_id": session,
        "turn_id": "worker-turn", "agent_id": "agent",
        "tool_use_id": "failed-read", "tool_name": "mcp__cortex__read_task",
        "tool_input": failed_input,
        "tool_response": {"isError": True, "structuredContent": {
            "error": {"code": "validation_error"},
        }},
    })
    assert json.loads(receipt.read_text())["state"] == "worker_candidate"


def test_root_coordinator_cannot_consume_or_publish_worker_authority(tmp_path: Path) -> None:
    session, turn = "root", "turn"
    ref = "t_0123456789ab_" + "a" * 32
    invoke(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "prompt": "$cortex:orchestrator"})
    for tool_name, tool_input in (
        ("mcp__cortex__read_task", {"task_ref": ref}),
        ("mcp__cortex__publish_result", {"task_ref": ref}),
    ):
        code, result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "session_id": session, "turn_id": turn, "tool_name": tool_name, "tool_input": tool_input})
        assert code == 0
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
