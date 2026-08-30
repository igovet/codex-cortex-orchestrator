#!/usr/bin/env python3
"""Small host hook for the Cortex first-call activation boundary.

The hook is deliberately independent of the Cortex runtime. It keeps only a
hashed turn key and booleans in PLUGIN_DATA, never prompt text, references,
tool arguments, reports, or tool output. Hooks are guardrails: the model still
chooses Cortex and makes the task-opening call from the advertised schema.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import sys
import time
try:
    import fcntl
except ImportError:  # pragma: no cover - the supported host is POSIX
    fcntl = None
from pathlib import Path
from typing import Any

# Keep the command hook cache-free even if a host omits the recommended -B.
sys.dont_write_bytecode = True


SELECTION = re.compile(r"(?<![A-Za-z0-9_])(?:\$cortex:orchestrator|cortex:orchestrator)(?![A-Za-z0-9_])")
DESELECTION = re.compile(r"^\s*(?:normal|leave[- ]cortex)\b", re.IGNORECASE)
OPEN_TASK_SUFFIX = "__open_task"
DEFAULT_STATE = {"selected": False, "anchored": False}
DIAGNOSTIC_FIELDS = ("session_id", "turn_id", "agent_id", "parent_session_id", "tool_name", "tool_input", "tool_response")
ASSIGNMENT_OPEN_TOOLS = {"mcp__cortex__open_assignment", "mcp__cortex__create_delegation"}
NATIVE_SPAWN_TOOLS = {"agent", "collaboration.spawn_agent", "collaborationspawn_agent", "spawn_agent"}
DISPATCH_STATE_PREFIX = "dispatch-"
CANONICAL_NATIVE_FIELDS = frozenset(("fork_turns", "message", "task_name"))
HOST_NATIVE_METADATA_FIELDS = frozenset(("role", "model", "reasoning_effort"))
SUPPORTED_NATIVE_MODELS = frozenset(("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"))
SUPPORTED_REASONING_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))


def _event() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    event = value if isinstance(value, dict) else {}
    _record_live_session_binding(event)
    try:
        plugin_root = os.environ.get("PLUGIN_ROOT")
        if isinstance(plugin_root, str) and plugin_root:
            scripts = str(Path(plugin_root) / "scripts")
            if scripts not in sys.path: sys.path.insert(0, scripts)
        from cortex_runtime.raw_diagnostic import append
        append(kind="hook_payload", payload=event)
    except Exception:
        pass
    return event


def _capture_live_prompt_binding(event: dict[str, Any]) -> bool:
    """Bind the live coordinator before Cortex semantic work begins."""
    if event.get("hook_event_name") != "UserPromptSubmit":
        return True
    session_id, cwd = event.get("session_id"), event.get("cwd")
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str) or not cwd:
        return True
    try:
        hook = Path(__file__).resolve(); candidate = hook.parent.parent; code_home = candidate.parents[4]
        if not (hook.parent.name == "hooks" and candidate.parent.name == "cortex" and candidate.parent.parent.name == "cortex" and candidate.parent.parent.parent.name == "cache" and code_home.name == ".codex"):
            return True
        receipt = code_home / ".cortex-live-launch.json"
        if not receipt.is_file() or receipt.is_symlink() or (receipt.stat().st_mode & 0o777) != 0o600:
            return True
        launch = json.loads(receipt.read_text(encoding="utf-8"))
        if not launch.get("active") or launch.get("cwd") != cwd or launch.get("profile_fingerprint") != hashlib.sha256(str(code_home).encode()).hexdigest():
            return False
        pid = int(launch.get("pid", 0)); current = os.getpid(); seen = set()
        for _ in range(16):
            if current in seen or current <= 1: return False
            seen.add(current)
            if current == pid: break
            match = re.search(r"^PPid:\s+(\d+)$", Path(f"/proc/{current}/status").read_text(encoding="ascii"), re.MULTILINE)
            if not match: return False
            current = int(match.group(1))
        else: return False
        binding = code_home / ".cortex-live-binding.json"
        if binding.is_file():
            if not _safe_private_file(binding):
                return False
            existing = json.loads(binding.read_text(encoding="utf-8"))
            valid = (
                isinstance(existing, dict)
                and existing.get("session_id") == session_id
                and existing.get("cwd") == cwd
                and existing.get("session_nonce") == launch.get("session_nonce")
                and existing.get("isolated_codex_fingerprint") == launch.get("profile_fingerprint")
            )
            return valid
        tmp = binding.with_suffix(".tmp")
        tmp.write_text(_json({"session_id": session_id, "source": "cli", "cwd": cwd, "session_nonce": launch["session_nonce"], "workdir_fingerprint": hashlib.sha256(cwd.encode()).hexdigest(), "isolated_codex_fingerprint": launch["profile_fingerprint"]}))
        os.chmod(tmp, 0o600); os.replace(tmp, binding); os.chmod(binding, 0o600)
        return True
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return False


def _record_live_session_binding(event: dict[str, Any]) -> None:
    """Persist the real coordinator SessionStart for exact live resume."""
    if event.get("hook_event_name") != "SessionStart" or event.get("source") != "startup":
        return
    session_id = event.get("session_id")
    cwd = event.get("cwd")
    resolved = Path(__file__).resolve()
    try:
        candidate_root = resolved.parent.parent
        derived_root = candidate_root.parents[4]
        valid_layout = (resolved.parent.name == "hooks" and candidate_root.parent.name == "cortex"
                        and candidate_root.parent.parent.name == "cortex"
                        and candidate_root.parent.parent.parent.name == "cache"
                        and derived_root.name == ".codex")
        code_home = str(derived_root) if valid_layout else None
    except (IndexError, OSError):
        code_home = None
    advertised_home = os.environ.get("CODEX_HOME")
    if isinstance(advertised_home, str) and advertised_home and code_home and Path(advertised_home).resolve() != Path(code_home):
        return
    binding_path = os.environ.get("CORTEX_LIVE_BINDING_PATH") or (str(Path(code_home) / ".cortex-live-binding.json") if isinstance(code_home, str) and code_home else "")
    if (not isinstance(session_id, str) or not session_id or not isinstance(cwd, str)
            or not cwd or not isinstance(binding_path, str) or not binding_path
            or not isinstance(code_home, str) or not code_home
            or any(event.get(key) for key in ("agent_id", "parent_session_id"))):
        return
    try:
        path = Path(binding_path)
        root = path.parent
        launch = root / ".cortex-live-launch.json"
        launch_stat = launch.stat()
        if launch.is_symlink() or launch_stat.st_mode & 0o777 != 0o600:
            return
        launch_value = json.loads(launch.read_text(encoding="utf-8"))
        if (not isinstance(launch_value, dict) or launch_value.get("cwd") != cwd
                or not isinstance(launch_value.get("session_nonce"), str)
                or len(launch_value["session_nonce"]) != 64):
            return
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and path.stat().st_mode & 0o777 != 0o600):
            return
        record = {"session_id": session_id, "source": "startup", "cwd": cwd,
                  "workdir_fingerprint": hashlib.sha256(cwd.encode()).hexdigest(),
                  "isolated_codex_fingerprint": hashlib.sha256(str(Path(code_home).resolve()).encode()).hexdigest(),
                  "session_nonce": launch_value["session_nonce"]}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(_json(record), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        return


def _diagnose(event: dict[str, Any]) -> None:
    if os.environ.get("CORTEX_HOOK_DIAGNOSTIC") != "1":
        return
    root = os.environ.get("PLUGIN_DATA")
    if not isinstance(root, str) or not root:
        return
    metadata = {
        "event": event.get("hook_event_name") if event.get("hook_event_name") in {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "SessionEnd", "PreCompact", "PostCompact", "Stop"} else "unknown",
        # Closed presence/type bits expose the host event shape without
        # recording arbitrary field names, tool names, inputs, outputs, ids,
        # or values.
        "presence": {k: isinstance(event.get(k), (str, dict)) for k in DIAGNOSTIC_FIELDS},
        "types": {k: ("string" if isinstance(event.get(k), str) else "object" if isinstance(event.get(k), dict) else "other" if k in event else "absent") for k in DIAGNOSTIC_FIELDS},
    }
    try:
        path = Path(root) / "activation" / "hook-diagnostic.jsonl"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        prior = path.read_text(encoding="utf-8").splitlines()[-127:] if path.exists() else []
        path.write_text("\n".join(prior + [_json(metadata)]) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        return


def _diagnose_return(category: str, *, output: bool = True) -> None:
    """Record only the bounded shape of this hook's returned decision."""
    if os.environ.get("CORTEX_HOOK_DIAGNOSTIC") != "1":
        return
    root = os.environ.get("PLUGIN_DATA")
    if not isinstance(root, str) or not root or category not in {"pass", "deny", "context", "block", "silent"}:
        return
    try:
        path = Path(root) / "activation" / "hook-diagnostic.jsonl"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        prior = path.read_text(encoding="utf-8").splitlines()[-127:] if path.exists() else []
        record = {"hook_return": category, "output_present": output}
        path.write_text("\n".join(prior + [_json(record)]) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        return


def _fingerprint(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _state_path(turn_id: object, session_id: object = None) -> Path | None:
    data_root = os.environ.get("PLUGIN_DATA")
    if not isinstance(data_root, str) or not data_root:
        return None
    identity = session_id if isinstance(session_id, str) and session_id else turn_id
    if not isinstance(identity, str) or not identity:
        return None
    namespace = "session:" if isinstance(session_id, str) and session_id else "turn:"
    digest = hashlib.sha256((namespace + identity).encode("utf-8")).hexdigest()
    root = Path(data_root)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root / "activation" / f"turn-{digest}.json"
    except OSError:
        return None


def _child_state_path(turn_id: object, session_id: object, agent_id: object = None) -> Path | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    data_root = os.environ.get("PLUGIN_DATA")
    if not isinstance(data_root, str) or not data_root:
        return None
    # A native agent may receive follow-up turns without another
    # SubagentStart.  The host keeps agent_id stable across those turns, while
    # turn_id changes.  Therefore the authoritative worker lease is keyed by
    # parent session + agent identity when that identity is available.  The
    # turn fallback supports older hook payloads which omitted agent_id.
    identity = agent_id if isinstance(agent_id, str) and agent_id else turn_id
    if not isinstance(identity, str) or not identity:
        return None
    namespace = "agent:" if isinstance(agent_id, str) and agent_id else "turn:"
    digest = hashlib.sha256(("child:" + session_id + ":" + namespace + identity).encode("utf-8")).hexdigest()
    try:
        root = Path(data_root); root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root / "activation" / f"child-{digest}.json"
    except OSError:
        return None


def _read_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return dict(DEFAULT_STATE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)
    if not isinstance(value, dict):
        return dict(DEFAULT_STATE)
    return {
        "selected": value.get("selected") is True,
        "anchored": value.get("anchored") is True,
        "turn_fingerprint": value.get("turn_fingerprint") if isinstance(value.get("turn_fingerprint"), str) else None,
        "child_mode": value.get("child_mode") is True,
        "parent_session_fingerprint": value.get("parent_session_fingerprint") if isinstance(value.get("parent_session_fingerprint"), str) else None,
        "child_auth": value.get("child_auth") if isinstance(value.get("child_auth"), str) else None,
        "agent_fingerprint": value.get("agent_fingerprint") if isinstance(value.get("agent_fingerprint"), str) else None,
        "assignment_ref_digest": value.get("assignment_ref_digest") if isinstance(value.get("assignment_ref_digest"), str) else None,
    }


def _write_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        return


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _child_auth(parent_session_id: str, child_turn_id: str) -> str:
    """Derive a non-reversible proof from the shared parent session and child turn."""
    return hmac.new(
        parent_session_id.encode("utf-8"),
        ("cortex-child-turn-v1:" + child_turn_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _emit_guard_observation(event: dict[str, Any], *, outcome: str, reason_code: str, category_override: str | None = None) -> None:
    """Append one bounded activation result to the active runtime journal."""
    if event.get("hook_event_name") not in {"PreToolUse", "Stop"}:
        return
    try:
        plugin_root = os.environ.get("PLUGIN_ROOT")
        code_home = os.environ.get("CODEX_HOME")
        candidate = os.environ.get("CORTEX_CANDIDATE_PATH")
        build_id = os.environ.get("CORTEX_BUILD_ID")
        if not all(isinstance(value, str) and value for value in (plugin_root, code_home, candidate, build_id)):
            return
        scripts = str(Path(plugin_root) / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cortex_runtime.event_journal import EventJournal  # noqa: PLC0415
        from cortex_runtime.observation_generation import verify_lease_record  # noqa: PLC0415
        lease_path = Path(code_home) / ".cortex-mcp-observations" / "lease.json"
        lease = verify_lease_record(json.loads(lease_path.read_text(encoding="ascii")), session_nonce=os.environ.get("CORTEX_SESSION_NONCE"), candidate_path=candidate, build_id=build_id, fresh=False)
        generation = Path(code_home) / ".cortex-mcp-observations" / "generations" / str(lease["generation_id"])
        journal = EventJournal.from_generation(generation=generation, build_id=str(lease["build_id"]), code_home=Path(code_home))
        state = _read_state(_state_path(event.get("turn_id"), event.get("session_id")))
        child_state = _read_state(_child_state_path(event.get("turn_id"), event.get("session_id"), event.get("agent_id")))
        if child_state.get("child_mode"):
            state = child_state
        child = state.get("child_mode") is True
        tool = event.get("tool_name") if isinstance(event.get("tool_name"), str) else ""
        category = category_override if category_override in {"project_local", "native_agent", "cortex_semantic", "coordination_local", "local_tool", "unknown"} else "native_agent" if tool.lower() == "agent" else "cortex_semantic" if tool.startswith("mcp__cortex__") else "local_tool" if tool else "unknown"
        role = "worker" if child else "coordinator" if state.get("selected") else "unattributed"
        phase = "worker_bootstrap" if child and not state.get("anchored") else "worker_active" if child else "pre_anchor" if not state.get("anchored") else "post_anchor"
        journal.emit(operation="activation_hook", kind="pre_tool", success=outcome == "allowed", fault=None if outcome == "allowed" else reason_code, mutation=outcome, activation_role=role, activation_operation_category=category, activation_phase=phase, activation_reason_code=reason_code)
    except Exception:
        # Hook diagnostics are non-blocking; the actual permission decision
        # must still be returned even if the journal is unavailable.
        return


def _deny(reason: str, event: dict[str, Any] | None = None, *, reason_code: str | None = None) -> None:
    # Keep a bounded, private diagnostic trail so live-dev can identify a
    # host bootstrap shape without persisting paths, values, or arguments.
    if event is not None:
        root = os.environ.get("PLUGIN_DATA")
        if isinstance(root, str) and root:
            # Denials are observable as a closed category only; never persist
            # tool/field names or request shape.
            record = {"category": "activation_guard", "outcome": "denied", "ts": int(time.time())}
            try:
                target = Path(root) / "activation" / "denials.jsonl"
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                prior = target.read_text(encoding="utf-8").splitlines()[-63:]
                target.write_text("\n".join(prior + [_json(record)]) + "\n", encoding="utf-8")
                os.chmod(target, 0o600)
            except OSError:
                pass
    if event is not None:
        lowered = reason.lower()
        selected_reason = reason_code or ("worker_bootstrap_required" if "worker bootstrap" in lowered else "coordinator_worker_operation" if "worker-owned" in lowered else "dispatch_mismatch" if "dispatch does not match" in lowered else "turn_mismatch" if "turn" in lowered else "route_not_anchored")
        _emit_guard_observation(event, outcome="denied", reason_code=selected_reason)
    print(_json({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    _diagnose_return("deny")


def _is_open_task(tool_name: object) -> bool:
    if not isinstance(tool_name, str):
        return False
    normalized = tool_name.strip().lower()
    leaf = re.split(r"(?:__|[.:/])", normalized)[-1]
    return normalized == "open_task" or normalized.endswith(OPEN_TASK_SUFFIX) or leaf == "open_task"


def _is_successful_open(response: object) -> bool:
    if not isinstance(response, dict) or response.get("isError") is not False:
        return False
    structured = response.get("structuredContent")
    if not isinstance(structured, dict):
        return False
    handles = structured.get("handles")
    return isinstance(handles, dict) and isinstance(handles.get("task_ref"), str) and bool(handles["task_ref"])


def _is_consume(tool_name: object) -> bool:
    if not isinstance(tool_name, str):
        return False
    return tool_name.strip().lower() == "mcp__cortex__consume_assignment_evidence"


def _is_verified_skill_read(event: dict[str, Any]) -> bool:
    """Allow only one literal read of one packaged skill entry file."""
    if event.get("tool_name") != "Bash" or not isinstance(event.get("tool_input"), dict):
        return False
    command = event["tool_input"].get("command")
    candidate = os.environ.get("CORTEX_CANDIDATE_PATH")
    if not isinstance(command, str) or not isinstance(candidate, str) or not candidate:
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    path_text: str | None = None
    if len(tokens) == 2 and Path(tokens[0]).name == "cat":
        path_text = tokens[1]
    elif (len(tokens) == 4 and Path(tokens[0]).name == "sed" and tokens[1] == "-n"
            and re.fullmatch(r"[1-9][0-9]*(?:,[1-9][0-9]*)?p", tokens[2])):
        path_text = tokens[3]
    if path_text is None:
        return False
    try:
        skill_root = (Path(candidate) / "skills").resolve(strict=True)
        target = Path(path_text)
        if not target.is_absolute() or target.is_symlink():
            return False
        resolved = target.resolve(strict=True)
        relative = resolved.relative_to(skill_root)
        return (len(relative.parts) == 2 and relative.name == "SKILL.md"
                and not resolved.parent.is_symlink() and resolved.is_file())
    except (OSError, ValueError):
        return False


def _is_readonly_project_inspection(event: dict[str, Any]) -> bool:
    """Recognize bounded read-only shell inspection before worker bootstrap."""
    if event.get("tool_name") != "Bash" or not isinstance(event.get("tool_input"), dict):
        return False
    command = event["tool_input"].get("command")
    if not isinstance(command, str) or not command or len(command) > 2048:
        return False
    # Tokenize for classification only; shell operators and expansion are not
    # executable authorization inputs. Reject them rather than interpreting
    # their semantics.
    if any(marker in command for marker in (";", "&&", "||", "&", ">", "<", "$", "`", "(", ")", "\\")):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens or tokens.count("|") > 2 or tokens[0] == "|" or tokens[-1] == "|":
        return False
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            segments.append([])
        else:
            segments[-1].append(token)
    if any(not segment for segment in segments):
        return False
    for index, segment in enumerate(segments):
        executable = Path(segment[0]).name
        if executable in {"rg", "grep"}:
            if len(segment) < 2 or any(arg in {"-e", "--replace", "--passthru"} for arg in segment[1:]):
                return False
            if any(arg.startswith("--replace=") or arg.startswith("--pre=") for arg in segment[1:]):
                return False
        elif executable in {"head", "tail"}:
            if len(segment) > 2 or (len(segment) == 2 and not re.fullmatch(r"-[0-9]+", segment[1])):
                return False
        elif executable in {"ls", "pwd", "find"}:
            if executable == "find" and any(arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for arg in segment[1:]):
                return False
        else:
            return False
        if index > 0 and executable not in {"head", "tail", "grep", "rg"}:
            return False
    return True


def _is_assignment_open(tool_name: object) -> bool:
    return isinstance(tool_name, str) and tool_name.strip().lower() in ASSIGNMENT_OPEN_TOOLS


def _is_native_spawn(tool_name: object) -> bool:
    return isinstance(tool_name, str) and tool_name.strip().lower() in NATIVE_SPAWN_TOOLS


def _dispatch_state_root() -> Path | None:
    data_root = os.environ.get("PLUGIN_DATA")
    if not isinstance(data_root, str) or not data_root:
        return None
    root = Path(data_root) / "activation"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        return root
    except OSError:
        return None


def _dispatch_digest(assignment_ref: str, native_arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"assignment_ref": assignment_ref, "native_arguments": native_arguments},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _value_fingerprint(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _native_arguments(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    # The hook sees the actual host spawn input.  It must be a closed object,
    # not a semantic brief or a nested projection that would require the hook
    # to guess which representation the host consumed.
    candidate = value
    if not CANONICAL_NATIVE_FIELDS <= set(candidate):
        return None
    # The native adapter accepts exactly the server's closed projection. Do
    # not silently discard host-supplied routing fields while validating.
    supplied_metadata = set(candidate) - CANONICAL_NATIVE_FIELDS
    if supplied_metadata not in (set(), set(HOST_NATIVE_METADATA_FIELDS)):
        return None
    if (candidate.get("fork_turns") != "none"
            or not isinstance(candidate.get("message"), str)
            or not candidate.get("message")
            or len(candidate["message"].encode("utf-8")) > 65_536
            or not isinstance(candidate.get("task_name"), str)
            or not candidate.get("task_name")
            or ("role" in candidate and (
                not isinstance(candidate.get("role"), str)
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", candidate["role"]) is None
            ))
            or ("model" in candidate and candidate.get("model") not in SUPPORTED_NATIVE_MODELS)
            or ("reasoning_effort" in candidate and candidate.get("reasoning_effort") not in SUPPORTED_REASONING_EFFORTS)):
        return None
    return {key: candidate[key] for key in CANONICAL_NATIVE_FIELDS}


def _record_pending_dispatch(event: dict[str, Any]) -> None:
    """Persist the private lease needed by the lifecycle bootstrap.

    The assignment locator is not a bearer capability; the MCP server still
    resolves and atomically consumes its private worker capability. The
    owner-only, mode-0600 receipt preserves the exact server host projection
    across separate hook processes. PreToolUse can therefore correct only the
    native transport before spawn; SubagentStart never invokes MCP or grants
    semantic authority.
    """
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isError") is not False:
        return
    structured = response.get("structuredContent")
    if not isinstance(structured, dict):
        return
    native = structured.get("native_dispatch")
    if not isinstance(native, dict):
        return
    args = _native_arguments(native)
    assignment_ref = structured.get("assignment_ref")
    if not isinstance(assignment_ref, str) or args is None:
        return
    # Assignment identity remains in the public result envelope while the
    # host projection is literal-callable. Compute the private binding digest
    # here from the exact server projection; it never enters the host call.
    digest = _dispatch_digest(assignment_ref, args)
    root = _dispatch_state_root()
    if root is None:
        return
    record = {
        "version": 1,
        "state": "pending",
        "dispatch_digest": digest,
        # The exact server projection digest is retained for private
        # validation and duplicate detection. PreToolUse recomputes it before
        # returning the saved projection to the host.
        "assignment_ref_digest": _value_fingerprint(assignment_ref),
        "assignment_ref": assignment_ref,
        # PreToolUse runs in a separate hook process from PostToolUse. Keep
        # the exact server-issued host projection in this owner-only receipt
        # so the later spawn boundary can deliver it byte-for-byte even when
        # the coordinator model abbreviates the large message. This is host
        # transport state, not worker authority; the worker still becomes
        # authoritative only after MCP evidence consumption succeeds.
        "native_arguments": args,
        "task_name_digest": _value_fingerprint(args["task_name"]),
        "session_digest": _value_fingerprint(event.get("session_id")),
        "turn_digest": _value_fingerprint(event.get("turn_id")),
        "fork_turns": "none",
        "created_at": time.time_ns(),
    }
    try:
        path = root / (DISPATCH_STATE_PREFIX + hashlib.sha256(digest.encode("ascii")).hexdigest() + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(_json(record), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        return


def _dispatch_lock(path: Path):
    """Return a process lock for one dispatch receipt when POSIX supports it."""
    if fcntl is None:
        return None
    lock_path = path.with_suffix(".lock")
    try:
        lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(lock_path, 0o600)
        handle = lock_path.open("r+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle
    except OSError:
        return None


def _release_dispatch_lock(handle: Any) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    except OSError:
        return


def _write_dispatch_record(path: Path, value: dict[str, Any]) -> bool:
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(_json(value), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False


def _parent_dispatch_lock(parent_session_id: str, turn_id: object = None):
    """Serialize claims and lifecycle handoff for one coordinator stream."""
    root = _dispatch_state_root()
    if root is None:
        return None
    identity = parent_session_id if isinstance(parent_session_id, str) and parent_session_id else turn_id
    if not isinstance(identity, str) or not identity:
        return None
    path = root / ("parent-" + hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock")
    return _dispatch_lock(path)


def _dispatch_records(*, session_id: object, turn_id: object, states: set[str]) -> list[tuple[Path, dict[str, Any]]]:
    """Read active private receipts for one exact coordinator session/turn."""
    root = _dispatch_state_root()
    if root is None:
        return []
    try:
        paths = sorted(root.glob(DISPATCH_STATE_PREFIX + "*.json"))[:128]
    except OSError:
        return []
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("state") not in states:
            continue
        if value.get("session_digest") != _value_fingerprint(session_id):
            continue
        if turn_id is not None and value.get("turn_digest") != _value_fingerprint(turn_id):
            continue
        matches.append((path, value))
    matches.sort(key=lambda item: (item[1].get("spawn_claim_order", item[1].get("created_at", 0)), item[0].name))
    return matches


def _pending_worker_dispatch(parent_session_id: str) -> tuple[Path, dict[str, Any]] | None:
    """Select the next unbound receipt from the authorized spawn-claim queue.

    The host does not currently echo `tool_use_id` on SubagentStart.  The
    `PreToolUse` hook therefore creates an ordered one-shot claim first.  A
    parent lock serializes handoff, so parallel lifecycle notifications cannot
    bind the same claim or reorder the private queue.  This is a bounded
    host adapter, not a global unique-active-lease heuristic.
    """
    matches = _dispatch_records(session_id=parent_session_id, turn_id=None, states={"delivery_pending"})
    eligible = [(path, value) for path, value in matches if value.get("spawn_claim_digest")]
    return eligible[0] if eligible else None


def _claim_native_dispatch(event: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    """Claim one native call without mutating the host-owned spawn envelope.

    Codex owns the native function call and its encrypted transport linkage.
    Rewriting that call through ``PreToolUse.updatedInput`` invalidates the
    host linkage even when the replacement object is schema-valid.  This hook
    therefore validates and atomically claims the pending server receipt, but
    leaves the accepted native input untouched.  The authoritative worker
    context is delivered later through the documented ``SubagentStart``
    context channel, after Codex has registered the real child identity.
    """
    args = _native_arguments(event.get("tool_input"))
    if args is None:
        return None
    session_id, turn_id = event.get("session_id"), event.get("turn_id")
    lock = _parent_dispatch_lock(session_id, turn_id)
    if fcntl is not None and lock is None:
        return None
    try:
        candidates = _dispatch_records(session_id=session_id, turn_id=turn_id, states={"pending", "delivery_pending"})
        tool_use_id = event.get("tool_use_id")
        tool_digest = _value_fingerprint(tool_use_id) if isinstance(tool_use_id, str) and tool_use_id else None
        if tool_digest is not None:
            # A host retry for the same authorized call is not a second spawn.
            if any(value.get("spawn_claim_digest") == tool_digest for _path, value in candidates):
                return None
            unclaimed = [(path, value) for path, value in candidates if not value.get("spawn_claim_digest") and value.get("state") == "pending"]
        else:
            # Older observed payloads lacked tool_use_id.  A single pending
            # receipt remains deterministic; multiple receipts fail closed
            # rather than silently mapping a call to the wrong worker.
            unclaimed = [(path, value) for path, value in candidates if not value.get("spawn_claim_digest") and value.get("state") == "pending"]
            if len(unclaimed) != 1:
                return None
        if len(unclaimed) > 1:
            # Multiple assignments may be dispatched concurrently from one
            # coordinator turn. Their server-issued native task names are the
            # only host-visible routing discriminator. Require one exact name
            # match before claiming a receipt so queue timing can never swap
            # two assignments.
            matched = []
            for candidate_path, candidate in unclaimed:
                candidate_args = _native_arguments(candidate.get("native_arguments"))
                if candidate_args is not None and candidate_args.get("task_name") == args.get("task_name"):
                    matched.append((candidate_path, candidate))
            if len(matched) != 1:
                return None
            unclaimed = matched
        if not unclaimed:
            return None
        path, record = unclaimed[0]
        authoritative = _native_arguments(record.get("native_arguments"))
        assignment_ref = record.get("assignment_ref")
        if (authoritative is None or not isinstance(assignment_ref, str)
                or record.get("dispatch_digest") != _dispatch_digest(assignment_ref, authoritative)):
            return None
        supplied = event.get("tool_input")
        if isinstance(supplied, dict):
            metadata = set(supplied) & HOST_NATIVE_METADATA_FIELDS
            if metadata and metadata != set(HOST_NATIVE_METADATA_FIELDS):
                return None
        claimed = dict(record)
        claimed.update({
            "state": "delivery_pending",
            "delivery_pending_at": time.time_ns(),
            "spawn_claim_digest": tool_digest or _value_fingerprint("legacy-claim:" + path.name),
            "spawn_claim_order": time.time_ns(),
            "host_input_digest": _value_fingerprint(_json(args)),
            "context_digest": _value_fingerprint(authoritative["message"]),
        })
        if not _write_dispatch_record(path, claimed):
            return None
        return path, claimed
    finally:
        _release_dispatch_lock(lock)


def _bind_worker_dispatch(event: dict[str, Any], child_path: Path, child_state: dict[str, Any], parent_session_id: str) -> tuple[bool, str | None]:
    """Bind one native agent and return its authoritative server context.

    Semantic evidence consumption belongs to the worker model and the public
    MCP handler.  The lifecycle hook only correlates the host child with the
    already-claimed assignment so a different opaque locator is denied before
    reaching the server.
    """
    if child_state.get("anchored"):
        return True, None
    parent_lock = _parent_dispatch_lock(parent_session_id)
    if fcntl is not None and parent_lock is None:
        return False, None
    try:
        found = _pending_worker_dispatch(parent_session_id)
        if found is None:
            return False, None
        path, _record = found
        lock = _dispatch_lock(path)
        if fcntl is not None and lock is None:
            # Concurrency is part of the authority boundary.  Never fall back
            # to an unlocked claim when the supported host cannot establish it.
            return False, None
        try:
            # Re-read while holding the per-receipt lock. A concurrent
            # lifecycle event must not consume or claim the same lease twice.
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return False, None
            if not isinstance(current, dict) or current.get("state") != "delivery_pending":
                return False, None
            assignment_ref = current.get("assignment_ref")
            if not isinstance(assignment_ref, str) or current.get("assignment_ref_digest") != _value_fingerprint(assignment_ref):
                return False, None
            native = _native_arguments(current.get("native_arguments"))
            if native is None or current.get("context_digest") != _value_fingerprint(native["message"]):
                return False, None
            bound = dict(current)
            bound.update({
                "state": "worker_bound",
                "worker_bound_at": time.time_ns(),
                "worker_agent_digest": _value_fingerprint(event.get("agent_id")),
                "worker_turn_digest": _value_fingerprint(event.get("turn_id")),
            })
            if not _write_dispatch_record(path, bound):
                return False, None
            correlated = dict(child_state)
            correlated.update({"anchored": False, "assignment_ref_digest": current["assignment_ref_digest"]})
            _write_state(child_path, correlated)
            if _read_state(child_path).get("assignment_ref_digest") != current["assignment_ref_digest"]:
                return False, None
            return True, native["message"]
        finally:
            _release_dispatch_lock(lock)
    finally:
        _release_dispatch_lock(parent_lock)


def _pending_dispatch_exists() -> bool:
    """Return whether any unconsumed host dispatch receipt is still active."""
    root = _dispatch_state_root()
    if root is None:
        return False
    try:
        paths = sorted(root.glob(DISPATCH_STATE_PREFIX + "*.json"))[:128]
    except OSError:
        return False
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("state") in {"pending", "delivery_pending", "worker_bound"}:
            return True
    return False


def _validate_native_dispatch(event: dict[str, Any], state_path: Path | None) -> bool:
    # The receipt is claimed before the child exists.  This is the only point
    # where the host exposes `tool_use_id`; SubagentStart must consume this
    # one-shot claim rather than trying to rediscover a global pending lease.
    claimed = _claim_native_dispatch(event)
    if claimed is None:
        return False
    _emit_guard_observation(event, outcome="allowed", reason_code="dispatch_validated", category_override="native_agent")
    return True


def _mark_dispatch_consumed(event: dict[str, Any]) -> None:
    """Settle the host receipt only after the worker proves MCP consumption."""
    supplied = event.get("tool_input")
    if not isinstance(supplied, dict) or not isinstance(supplied.get("assignment_ref"), str):
        return
    assignment_digest = _value_fingerprint(supplied["assignment_ref"])
    root = _dispatch_state_root()
    if root is None:
        return
    try:
        paths = sorted(root.glob(DISPATCH_STATE_PREFIX + "*.json"))[:128]
    except OSError:
        return
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("state") not in {"delivery_pending", "worker_bound"} or record.get("assignment_ref_digest") != assignment_digest:
            continue
        consumed = dict(record)
        consumed.update({"state": "consumed", "authority": "authoritative", "consumed_at": time.time_ns()})
        try:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(_json(consumed), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError:
            pass
        return


def _is_publication(tool_name: object) -> bool:
    return isinstance(tool_name, str) and tool_name.strip().lower() in {
        "mcp__cortex__publish_plan", "mcp__cortex__publish_result", "mcp__cortex__publish_documentation",
    }


def _is_successful_consume(event: dict[str, Any]) -> bool:
    response = event.get("tool_response")
    supplied = event.get("tool_input")
    if not isinstance(response, dict) or response.get("isError") is not False or not isinstance(supplied, dict):
        return False
    structured = response.get("structuredContent")
    evidence = structured.get("evidence") if isinstance(structured, dict) else None
    return (isinstance(structured, dict) and isinstance(structured.get("assignment_ref"), str)
            and structured.get("assignment_ref") == supplied.get("assignment_ref")
            and isinstance(evidence, dict) and evidence.get("state") in {"none", "consumed"})


def _is_successful_publication(event: dict[str, Any]) -> bool:
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isError") is not False:
        return False
    structured = response.get("structuredContent")
    return (isinstance(structured, dict)
            and structured.get("publication_status") == "completed"
            and isinstance(structured.get("report_ref"), str))


def main() -> int:
    event = _event()
    _diagnose(event)
    turn_id = event.get("turn_id")
    session_id = event.get("session_id")
    path = _state_path(turn_id, session_id)
    state = _read_state(path)
    event_name = event.get("hook_event_name")
    child_path = _child_state_path(turn_id, session_id, event.get("agent_id"))
    if event_name != "SubagentStart" and child_path is not None:
        child_state = _read_state(child_path)
        if child_state.get("child_mode"):
            path, state = child_path, child_state
    if (state.get("child_mode") is True and isinstance(event.get("agent_id"), str)
            and state.get("agent_fingerprint")
            and state.get("agent_fingerprint") != _fingerprint(event.get("agent_id"))):
        state = dict(DEFAULT_STATE)
        path = _state_path(turn_id, session_id)
    child = state.get("child_mode") is True

    # The host delivery receipt is provisional. Only a successful worker MCP
    # evidence-consumption result proves that the child actually received and
    # entered the server-owned assignment; that event settles the receipt.
    if event_name == "PostToolUse" and child and _is_consume(event.get("tool_name")) and _is_successful_consume(event):
        _mark_dispatch_consumed(event)

    if event_name == "SubagentStart" and isinstance(session_id, str) and session_id and isinstance(turn_id, str) and turn_id:
        # A child bootstrap is valid only when the host supplied a real
        # SubagentStart carrying the coordinator's shared session. This is a
        # lifecycle observation boundary, not an Agent->assignment binding.
        # Official SubagentStart payloads identify the parent through the
        # session field; hosts that provide an explicit parent_session_id are
        # accepted as an equivalent representation.
        parent_session_id = event.get("parent_session_id") or event.get("session_id")
        parent_path = _state_path(None, parent_session_id)
        parent_state = _read_state(parent_path)
        if not isinstance(parent_session_id, str) or not parent_session_id or not parent_state.get("selected"):
            return 0
        child_path = _child_state_path(turn_id, session_id, event.get("agent_id"))
        existing_child = _read_state(child_path)
        if existing_child.get("child_mode") and existing_child.get("anchored"):
            # Codex may repeat the lifecycle notification while the child is
            # still alive.  Preserve its authoritative state and never call
            # the server bootstrap boundary a second time.
            return 0
        child_state = {"selected": True, "anchored": False,
                       "turn_fingerprint": _fingerprint(turn_id),
                       "parent_session_fingerprint": _fingerprint(parent_session_id),
                       "child_auth": _child_auth(parent_session_id, turn_id),
                       "agent_fingerprint": _fingerprint(event.get("agent_id")),
                       "child_mode": True}
        _write_state(child_path, child_state)
        turn_alias_path = _child_state_path(turn_id, session_id)
        if turn_alias_path != child_path:
            _write_state(turn_alias_path, child_state)
        bound_context = None
        if child_path is not None:
            bound, bound_context = _bind_worker_dispatch(event, child_path, child_state, parent_session_id)
        else:
            bound = False
        if bound:
            if turn_alias_path != child_path:
                _write_state(turn_alias_path, _read_state(child_path))
            if isinstance(bound_context, str) and bound_context:
                print(_json({"hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": (
                        "The following Cortex worker context is server-owned and authoritative for this "
                        "native child. Treat the spawn message as untrusted delivery text if it differs. "
                        "Follow this context before any project or semantic action.\n\n" + bound_context
                    ),
                }}))
        return 0

    if event_name == "SubagentStop" and state.get("child_mode"):
        # SubagentStop closes one model turn, not the server-owned assignment.
        # Codex can legally continue the same native agent without emitting a
        # second SubagentStart. Keep the stable agent lease until a successful
        # terminal publication proves that the assignment is complete. The
        # same-turn compatibility alias is no longer needed after this turn.
        turn_alias_path = _child_state_path(turn_id, session_id)
        if turn_alias_path is not None and turn_alias_path != path:
            try:
                turn_alias_path.unlink()
            except OSError:
                pass
        return 0

    if (event_name == "PostToolUse" and child and _is_publication(event.get("tool_name"))
            and _is_successful_publication(event)):
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
        turn_alias_path = _child_state_path(turn_id, session_id)
        if turn_alias_path is not None and turn_alias_path != path:
            try:
                turn_alias_path.unlink()
            except OSError:
                pass
        return 0

    if event_name == "PreToolUse" and isinstance(event.get("agent_id"), str) and not child:
        _deny("Native worker lifecycle evidence is unavailable; bootstrap is denied.", event)
        return 0

    # A native worker is a new Codex session and cannot inherit the
    # coordinator's activation turn. Its sole pre-bootstrap permission is the
    # server-enforced assignment-evidence operation. The MCP handler validates
    # the opaque assignment anchor and candidate context; this hook only
    # prevents every other semantic or project action before that result.
    if child and not state["anchored"]:
        if event_name == "PreToolUse":
            if _is_consume(event.get("tool_name")):
                supplied = event.get("tool_input")
                expected = state.get("assignment_ref_digest")
                actual = _value_fingerprint(supplied.get("assignment_ref")) if isinstance(supplied, dict) else None
                if not expected or actual != expected:
                    _deny("Native worker assignment evidence does not match its server-issued dispatch.", event)
                    return 0
                return 0
            if _is_verified_skill_read(event):
                _emit_guard_observation(event, outcome="allowed", reason_code="verified_skill_read", category_override="local_tool")
                return 0
            if _is_readonly_project_inspection(event):
                _emit_guard_observation(event, outcome="allowed", reason_code="readonly_inspection", category_override="project_local")
                return 0
            _deny("Native worker bootstrap requires assignment evidence before other work.", event)
            return 0
        if event_name == "PostToolUse" and _is_consume(event.get("tool_name")):
            response = event.get("tool_response")
            if _is_successful_consume(event):
                child_state = dict(state)
                child_state.update({"selected": True, "anchored": True,
                                    "turn_fingerprint": _fingerprint(turn_id), "child_mode": True})
                _write_state(path, child_state)
            return 0

    if event_name == "UserPromptSubmit":
        # Route selection is tracked only for native-worker lifecycle
        # correlation.  It is deliberately not a coordinator bootstrap gate:
        # Codex loads skills through its normal mechanism and the backend owns
        # the authoritative first mutation (open_task).
        prompt = event.get("prompt")
        if isinstance(prompt, str) and DESELECTION.search(prompt):
            state = dict(DEFAULT_STATE)
            _write_state(path, state)
            return 0
        if isinstance(prompt, str) and SELECTION.search(prompt):
            # Binding is a transport/resume aid, not a UserPromptSubmit
            # permission override.  Codex rejects decision fields on this
            # hook event; a failed optional capture must therefore remain a
            # bounded observation while route state is still recorded.
            _capture_live_prompt_binding(event)
            state = {"selected": True, "anchored": False,
                     "turn_fingerprint": _fingerprint(turn_id)}
            _write_state(path, state)
        elif state["selected"] and isinstance(session_id, str) and session_id and isinstance(turn_id, str) and turn_id:
            state["turn_fingerprint"] = _fingerprint(turn_id)
            _write_state(path, state)
        return 0

    # A successful server assignment is itself authoritative evidence that the
    # MCP route is active.  Hosts may omit the textual route marker from
    # UserPromptSubmit after the user selects a bundled skill in the UI.  Do
    # not discard the valid assignment receipt merely because that marker was
    # not present; the MCP server has already validated the task relation.
    if event_name == "PostToolUse" and not child and _is_assignment_open(event.get("tool_name")):
        if isinstance(event.get("tool_response"), dict) and event["tool_response"].get("isError") is False:
            state["selected"] = True
            state["anchored"] = True
            state["turn_fingerprint"] = _fingerprint(turn_id)
            _write_state(path, state)
            _record_pending_dispatch(event)
        return 0

    # A pending server receipt is an active host boundary even when a foreign
    # session presents the next spawn event.  Do not let an unselected/foreign
    # state file turn that mismatch into a silent allow.
    if (event_name == "PreToolUse" and _is_native_spawn(event.get("tool_name"))
            and not state["selected"] and _pending_dispatch_exists()):
        _deny("Native dispatch does not match the pending server-issued assignment boundary.", event, reason_code="dispatch_mismatch")
        return 0
    if not state["selected"]:
        return 0

    if event_name == "PreToolUse" and not child and _is_native_spawn(event.get("tool_name")):
        if not state.get("anchored"):
            _deny("Native dispatch is not permitted before Cortex task anchoring.", event)
            return 0
        if isinstance(event.get("agent_id"), str) and event.get("agent_id"):
            _deny("Native dispatch lifecycle identity cannot be supplied by the coordinator.", event)
            return 0
        if _validate_native_dispatch(event, path):
            print(_json({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "This native dispatch is correlated to the selected server-issued assignment. Codex owns the spawn input and Cortex did not rewrite it. The authoritative worker context will be attached at SubagentStart. The spawned worker alone performs its assignment bootstrap and publication; the coordinator must wait for that worker's native handoff. Do not interrupt or replace the worker unless explicit terminal, ambiguous, or stale evidence is observed.",
            }}))
        else:
            _deny("Native dispatch does not match the pending server-issued assignment boundary.", event, reason_code="dispatch_mismatch")
        return 0

    lifecycle_events = {"SessionStart", "SessionEnd", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact"}
    current_turn = _fingerprint(turn_id)
    session_bound = isinstance(session_id, str) and bool(session_id)
    turn_mismatch = (
        (state.get("turn_fingerprint") and current_turn != state["turn_fingerprint"])
        or (session_bound and (not state.get("turn_fingerprint") or not current_turn))
    )
    # A coordinator's user steering/clarification turn is part of the same
    # anchored route.  Let UserPromptSubmit advance the turn fingerprint while
    # retaining the session-owned task anchor; foreign non-prompt operations
    # remain fail-closed on a turn mismatch.
    # Once the coordinator has opened the task, the route is owned by the
    # verified root session rather than by one model turn.  UserPromptSubmit
    # is intentionally not a configured coordinator gate, so ordinary
    # clarification/approval/steering turns cannot refresh this fingerprint
    # before their next semantic call.  Keep strict turn binding for the
    # pre-anchor coordinator state and all native-worker state; session lookup
    # still rejects foreign sessions because they resolve a different state.
    coordinator_post_anchor = bool(state.get("selected") and state.get("anchored") and not child)
    worker_post_anchor = bool(state.get("selected") and state.get("anchored") and child)
    if (turn_mismatch and not coordinator_post_anchor and not worker_post_anchor
            and event_name not in lifecycle_events and event_name != "UserPromptSubmit"):
        if event_name == "Stop":
            return 0
        _deny("Cortex activation state does not match this Codex turn; reopen the route before continuing.", event)
        return 0

    if state["anchored"]:
        if not child and event_name == "PreToolUse" and (_is_consume(event.get("tool_name")) or _is_publication(event.get("tool_name"))):
            _deny("Coordinator cannot perform worker-owned evidence or publication actions.", event)
            return 0
        return 0

    if event_name == "PostToolUse" and _is_open_task(event.get("tool_name")):
        if _is_successful_open(event.get("tool_response")):
            state["anchored"] = True
            _write_state(path, state)
        return 0

    if event_name == "Stop":
        # Stop is lifecycle observation only. Clarification and approval are
        # intentionally multi-turn, so an unanchored coordinator may stop
        # without a hook-generated continuation or workflow error.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
