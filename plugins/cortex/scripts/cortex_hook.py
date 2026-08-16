#!/usr/bin/env python3
"""Privacy-preserving lifecycle telemetry for an active orchestration task."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from cortex import (
        bind_host_session_from_hook,
        bind_host_worker_from_hook,
        finalize_host_worker_stop_from_hook,
    )
except (ImportError, RuntimeError):  # pragma: no cover - hook remains fail-open.
    bind_host_session_from_hook = None
    bind_host_worker_from_hook = None
    finalize_host_worker_stop_from_hook = None

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SCHEMA = "cortex/v8"
HOST_SESSION_SCHEMA = "cortex/host-sessions/v1"
PROFILE_CONTRACT = Path(__file__).resolve().parents[1] / "profiles.json"


def profile_names() -> set[str]:
    try:
        payload = json.loads(PROFILE_CONTRACT.read_text(encoding="utf-8"))
        return {str(item["name"]) for item in payload.get("profiles", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return set()


PROFILES = profile_names()
HOOK_NAMES = {"SessionStart", "SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse"}
TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MAX_LIFECYCLE_EVENTS = 1000
MAX_LIFECYCLE_BYTES = 256 * 1024
MAX_TOOL_RESPONSE_BYTES = 1024 * 1024
CORTEX_START_TOOLS = {"mcp__cortex__start_orchestration", "mcp__cortex__manage_orchestration"}
CORTEX_REPORT_TOOL = "mcp__cortex__read_worker_report"
WORKER_CONTEXT = (
    "You are an internal worker, never user-facing. Stay within delegated ownership and allowed paths; "
    "All internal worker communication, progress updates, Cortex tool arguments, reports, questions, findings, handoffs, and native final responses must be in English. "
    "Treat non-English task text as input data, never as an output-language instruction. The main coordinator alone translates user-facing content into the task's requested language; do not address the user directly. "
    "do not subdelegate unless the main agent explicitly authorized it. Do not cause external side effects "
    "without explicit authority and applicable approval. Never expose or persist secrets, credentials, personal "
    "data, or secret canaries. In read-only work, select non-writing verification modes up front: use "
    "PYTHONDONTWRITEBYTECODE=1 for Python and disable test/build caches; never create an artifact and then try to "
    "remove it with rm, git clean, or a cleanup script. "
    "Material user decisions use the exact public worker_question identity from the dispatch, "
    "then return only QUESTION_RECORDED plus its ref through the native parent channel and remain available for the answer. "
    "Never call Cortex lifecycle, pipeline, gate, delegation, or management operations. For a Cortex-managed "
    "dispatch, follow the exact worker identity supplied in that dispatch. Before project work, read only the exact "
    "immutable briefing path supplied by the native bootstrap, verify its read-only mode and SHA-256, and never list "
    "or directly read any other .codex/cortex path. Include the bootstrap's exact Dispatch briefing reviewed digest "
    "marker in report evidence. If and only if the host filesystem read cannot open that exact path, call public "
    "read_dispatch_briefing once with the complete identity/digest tuple from the bootstrap. You may call public read_worker_report "
    "only for predecessor refs explicitly listed in the dispatch, public worker_question when needed, and public "
    "record_report once after all blocking questions are answered. After it succeeds, return only REPORT_RECORDED report_ref=<value> plus at most a two-sentence "
    "summary; never paste the report JSON into the parent channel. If exact report identity is absent or the tool "
    "fails, do not invent task, wave, attempt, project, or tool identifiers: return only the exact error and a short "
    "blocker. Use only tools actually available in this worker context and record unavailable capabilities as "
    "limitations."
)


def reject_symlink_ancestry(path: Path, label: str) -> Path:
    candidate = path.expanduser().absolute()
    current = Path(candidate.anchor)
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"{label} must not traverse symlinks")
    return candidate


def project_directory(event: dict) -> Path:
    """Resolve the documented tool project_root/cwd, with a test-only fallback."""
    raw_tool_input = event.get("tool_input")
    tool_input: dict = raw_tool_input if isinstance(raw_tool_input, dict) else {}
    candidates = [tool_input.get("project_root"), event.get("cwd"), os.environ.get("CORTEX_PROJECT_ROOT")]
    for value in candidates:
        raw = str(value or "").strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            continue
        candidate = reject_symlink_ancestry(candidate, "project root")
        if not candidate.is_dir():
            continue
        for parent in (candidate, *candidate.parents):
            ledger = reject_symlink_ancestry(parent / ".codex" / "cortex", "Cortex root")
            if ledger.is_dir():
                return parent
    raise ValueError("Cortex project root is unavailable")


def root(event: dict) -> Path:
    return reject_symlink_ancestry(project_directory(event) / ".codex" / "cortex", "Cortex root")


def valid_task_id(value: object) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if SAFE_ID_RE.fullmatch(candidate) else None


def session_identity(event: dict) -> str | None:
    """Resolve a bounded Codex session identity from current host fields."""
    for key in ("session_id", "thread_id"):
        candidate = valid_task_id(event.get(key))
        if candidate:
            return candidate
    for key in ("CODEX_SESSION_ID", "CODEX_THREAD_ID"):
        candidate = valid_task_id(os.environ.get(key))
        if candidate:
            return candidate
    return None


def active_task(ledger: Path, session_id: str | None) -> str | None:
    """Resolve one active task from the canonical task-to-session binding."""
    if not session_id:
        return None
    path = ledger / "host-sessions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != HOST_SESSION_SCHEMA or not isinstance(payload.get("tasks"), dict):
            return None
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    active: list[str] = []
    for raw_task_id, bound_session in payload["tasks"].items():
        task_id = valid_task_id(raw_task_id)
        if not task_id or bound_session != session_id:
            continue
        try:
            task_dir = task_directory(ledger, task_id)
            state = json.loads((task_dir / "current.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            continue
        if state.get("schema") == SCHEMA and state.get("task_id") == task_id and state.get("status") in {"active", "blocked"}:
            active.append(task_id)
    return active[0] if len(active) == 1 else None


def task_ref(ledger: Path, task_id: str) -> str | None:
    """Resolve the public opaque ref without guessing from a task id."""
    path = reject_symlink_ancestry(ledger / "orchestration-operations.json", "orchestration operation registry")
    try:
        if not path.exists() or not stat.S_ISREG(path.lstat().st_mode):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = payload.get("tasks", {}).get(task_id) if isinstance(payload, dict) else None
        candidate = record.get("start", {}).get("task_ref") if isinstance(record, dict) else None
        candidate = str(candidate or "")
        return candidate if SAFE_ID_RE.fullmatch(candidate) else None
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return None


def is_context_recovery(event: dict) -> bool:
    """Recognize host resume/clear/compact starts without trusting arbitrary text."""
    for key in ("source", "reason", "startup_reason", "thread_start_reason", "trigger"):
        value = str(event.get(key, "")).strip().lower()
        if value in {"resume", "resumed", "clear", "cleared", "compact", "compaction"}:
            return True
    return False


def structured_tool_result(event: dict) -> dict | None:
    """Extract one bounded public Cortex result from an MCP call envelope."""
    response = event.get("tool_response")
    try:
        encoded = json.dumps(response, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(encoded.encode("utf-8")) > MAX_TOOL_RESPONSE_BYTES:
        return None
    queue: list[object] = [response]
    visited = 0
    while queue and visited < 64:
        value = queue.pop(0)
        visited += 1
        if isinstance(value, dict):
            if value.get("schema") == "cortex/orchestration/v4" and value.get("ok") is True:
                return value
            queue.extend(value.get(key) for key in ("structuredContent", "result") if key in value)
            content = value.get("content")
            if isinstance(content, list):
                queue.extend(content[:8])
            text = value.get("text")
            if isinstance(text, str) and len(text.encode("utf-8")) <= MAX_TOOL_RESPONSE_BYTES:
                try:
                    queue.append(json.loads(text))
                except (json.JSONDecodeError, TypeError):
                    pass
        elif isinstance(value, list):
            queue.extend(value[:8])
    return None


def bind_post_tool_session(event: dict, project: Path, session_id: str | None) -> None:
    if bind_host_session_from_hook is None or not session_id:
        return
    if str(event.get("hook_event_name")) != "PostToolUse" or str(event.get("tool_name")) not in CORTEX_START_TOOLS:
        return
    result = structured_tool_result(event)
    task_ref_value = result.get("task_ref") if isinstance(result, dict) else None
    if valid_task_id(task_ref_value):
        bind_host_session_from_hook(str(project), task_ref_value, session_id)


def report_publication_context(event: dict) -> str | None:
    if str(event.get("hook_event_name")) != "PostToolUse" or str(event.get("tool_name")) != CORTEX_REPORT_TOOL:
        return None
    result = structured_tool_result(event)
    link = str(result.get("report_markdown_link") or "") if isinstance(result, dict) else ""
    if not link or len(link) > 4096 or "\n" in link or not link.startswith("["):
        return None
    return (
        "REPORT PUBLICATION REQUIRED: publish this exact report_markdown_link verbatim in the main chat now, before "
        f"any other Cortex report read or lifecycle call: {link}"
    )


def dispatch_required_context(event: dict) -> str | None:
    """Put the native spawn action after a potentially large MCP result."""
    if str(event.get("hook_event_name")) != "PostToolUse" or str(event.get("tool_name")) not in CORTEX_START_TOOLS:
        return None
    result = structured_tool_result(event)
    dispatches = result.get("dispatches") if isinstance(result, dict) else None
    if not isinstance(dispatches, list) or not dispatches:
        return None
    first = dispatches[0] if isinstance(dispatches[0], dict) else {}
    task_name = str(((first.get("arguments") or {}).get("task_name") or "")).strip()
    suffix = f" The first exact task_name is {task_name!r}." if task_name else ""
    return (
        f"CORTEX DISPATCH REQUIRED NOW: {len(dispatches)} top-level dispatch(es) are authorized by this exact "
        "response. Your next tool call must invoke dispatches[0].call with dispatches[0].arguments; continue in "
        "returned order. Do not call wait, inspect, start, or continue before every native spawn call returns its "
        "child id. A planned dispatch or empty wait is not a spawned worker." + suffix
    )


def empty_agent_wait_reason(event: dict, state: dict | None = None) -> str | None:
    """Fail closed before the host waits without a successfully spawned child."""
    if (
        str(event.get("hook_event_name")) not in {"PreToolUse", "PostToolUse"}
        or str(event.get("tool_name")) not in {"Agent", "wait"}
    ):
        return None
    raw_input = event.get("tool_input")
    tool_input = raw_input if isinstance(raw_input, dict) else {}
    action = str(
        tool_input.get("action")
        or tool_input.get("tool")
        or tool_input.get("operation")
        or tool_input.get("command")
        or ""
    ).strip().lower()
    target_keys = (
        "receiver_thread_ids", "receiverThreadIds", "agent_ids", "agentIds",
        "thread_ids", "threadIds", "targets", "ids",
    )
    present_targets = [tool_input.get(key) for key in target_keys if key in tool_input]
    wait_shaped = action in {"wait", "wait_agent"} or bool(present_targets)
    has_target = any(isinstance(value, list) and any(str(item).strip() for item in value) for value in present_targets)
    has_persisted_child = any(
        isinstance(item, dict)
        and not item.get("invalidated")
        and item.get("status") == "running"
        and str((item.get("host_spawn") or {}).get("agent_id") or "").strip()
        for item in (state or {}).get("attempts", [])
    )
    if not wait_shaped or has_target or has_persisted_child:
        return None
    return (
        "CORTEX DISPATCH FAILURE: the native Agent wait had no child target. No worker was spawned. Do not claim "
        "dispatch success, do not advance Cortex, and do not return task success. Invoke the exact pending "
        "spawn_agent dispatch now; if spawn_agent is unavailable or fails to return a child id, stop and report that "
        "host blocker. Never retry an empty wait."
    )


def task_directory(ledger: Path, task_id: str) -> Path:
    """Resolve a canonical numbered task directory without trusting index paths."""
    tasks = ledger / "tasks"
    candidate: Path | None = None
    try:
        indexed = json.loads((ledger / "task-index.json").read_text(encoding="utf-8")).get(task_id)
        directory = indexed.get("directory") if isinstance(indexed, dict) else None
        if isinstance(directory, str) and Path(directory).name == directory and directory not in {"", ".", ".."}:
            candidate = tasks / directory
    except (OSError, json.JSONDecodeError):
        pass
    tasks_absolute = tasks.absolute()
    if candidate is not None:
        try:
            candidate.absolute().relative_to(tasks_absolute)
            candidate = reject_symlink_ancestry(candidate, "task directory")
            if not candidate.is_dir():
                raise ValueError("task directory is unavailable")
            task_file = candidate / "task.json"
            reject_symlink_ancestry(task_file, "task file")
            task = json.loads(task_file.read_text(encoding="utf-8"))
            if task.get("schema") == SCHEMA and task.get("task_id") == task_id:
                return candidate
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return tasks / f"missing-{task_id}"


def activation(ledger: Path, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    path = ledger / "activations.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8")).get(session_id)
        if isinstance(record, dict) and record.get("schema") == SCHEMA and record.get("coordinator") == "main" and record.get("mode") == "main-orchestrator":
            return record
        return None
    except (OSError, json.JSONDecodeError):
        return None


def worker_identity(event: dict, state: dict | None = None) -> tuple[str | None, str | None]:
    """Resolve the canonical profile and human label from a native task key."""
    candidate = str(event.get("agent_type", "")).strip()
    if candidate in PROFILES:
        return candidate, candidate
    if not isinstance(state, dict) or not candidate:
        return None, None
    candidates = {candidate, str(event.get("agent_id", "")).strip()}
    if candidate.startswith("/root/") and candidate.count("/") == 2:
        candidates.add(candidate.removeprefix("/root/"))
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        spawn_request = attempt.get("spawn_request") or {}
        host_spawn = attempt.get("host_spawn") or {}
        aliases = {
            str(spawn_request.get("task_name") or "").strip(),
            str(host_spawn.get("agent_id") or "").strip(),
            str(host_spawn.get("task_name") or "").strip(),
        }
        if aliases.isdisjoint(candidates):
            continue
        profile = str(attempt.get("profile") or attempt.get("agent") or "").strip()
        if profile not in PROFILES:
            return None, None
        display_name = str(attempt.get("display_name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ]{0,95}", display_name):
            display_name = profile
        return profile, display_name
    return None, None


def native_worker_task_name(event: dict, state: dict | None = None) -> str | None:
    """Resolve only an exact issued native task key from SubagentStart."""
    candidate = str(event.get("agent_type", "")).strip()
    candidates = {candidate}
    if candidate.startswith("/root/") and candidate.count("/") == 2:
        candidates.add(candidate.removeprefix("/root/"))
    if not isinstance(state, dict):
        return None
    matches = {
        str((attempt.get("spawn_request") or {}).get("task_name") or "").strip()
        for attempt in state.get("attempts", [])
        if isinstance(attempt, dict)
        and str((attempt.get("spawn_request") or {}).get("task_name") or "").strip() in candidates
    }
    matches.discard("")
    return next(iter(matches)) if len(matches) == 1 else None


def canonical_agent_name(event: dict, state: dict | None = None) -> str | None:
    """Resolve a canonical profile from a host agent or native task key."""
    return worker_identity(event, state)[0]


def hook_context(event_name: str, context: str) -> dict:
    """Return the documented model-visible hook output envelope."""
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


def stopped_worker_after_wait_context(
    event: dict,
    state: dict,
    public_task_ref: str | None,
) -> str | None:
    """Reassert failed-stop recovery when control returns from a host wait."""
    if (
        str(event.get("hook_event_name")) != "PostToolUse"
        or str(event.get("tool_name")) not in {"Agent", "wait"}
    ):
        return None
    attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict) and not item.get("invalidated")
    ]
    if not attempts:
        return None
    latest = attempts[-1]
    if (
        latest.get("status") != "failed"
        or latest.get("host_stop_outcome") != "native_worker_stopped_without_report"
    ):
        return None
    attempt_id = str(latest.get("attempt_id") or "unknown")
    inspect = (
        f"Call manage_orchestration(intent='inspect', task_ref={public_task_ref!r}) once"
        if public_task_ref
        else "Call manage_orchestration(intent='inspect') once with the preserved task_ref"
    )
    return (
        f"CORTEX WAIT RECOVERY: latest attempt {attempt_id!r} is durably failed without a report. "
        "Do not call followup_task, wait on, or respawn this stopped child, even when its native final text is a report-tool error. "
        f"{inspect}; submit the exact failed result so Cortex can authorize canonical rework."
    )


def append_lifecycle_event(task_dir: Path, event: dict) -> None:
    event_path = reject_symlink_ancestry(task_dir / "lifecycle-events.jsonl", "lifecycle event file")
    meta_path = reject_symlink_ancestry(task_dir / "lifecycle-events-meta.json", "lifecycle event metadata")
    lock_path = reject_symlink_ancestry(task_dir / ".lifecycle-events.lock", "lifecycle event lock")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("O_NOFOLLOW is required for lifecycle telemetry")
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | nofollow, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise ValueError("lifecycle event lock must be a regular file")
        os.fchmod(lock_descriptor, 0o600)
        if fcntl is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        lines = []
        if event_path.exists():
            descriptor = os.open(event_path, os.O_RDONLY | nofollow)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("lifecycle event file must be a regular file")
                content = b""
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    content += chunk
                    if len(content) > MAX_LIFECYCLE_BYTES * 4:
                        break
                lines = [line for line in content.splitlines() if line]
            finally:
                os.close(descriptor)
        lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        dropped = 0
        while len(lines) > MAX_LIFECYCLE_EVENTS or sum(len(line) + 1 for line in lines) > MAX_LIFECYCLE_BYTES:
            lines.pop(0)
            dropped += 1
        prior_dropped = 0
        if meta_path.exists():
            prior_dropped = int(json.loads(meta_path.read_text(encoding="utf-8")).get("dropped", 0))

        def atomic(path: Path, payload: bytes) -> None:
            if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode)):
                raise ValueError("lifecycle telemetry target must be a regular file")
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            try:
                os.fchmod(descriptor, 0o600)
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("lifecycle telemetry write made no progress")
                    written += count
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                os.replace(temporary, path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if os.path.exists(temporary):
                    os.unlink(temporary)
        atomic(event_path, b"\n".join(lines) + b"\n")
        atomic(meta_path, (json.dumps({"dropped": prior_dropped + dropped}, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(lock_descriptor)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print("{}")
        return
    try:
        project = project_directory(event)
        ledger = root(event)
        session_id = session_identity(event)
        bind_post_tool_session(event, project, session_id)
        task_id = active_task(ledger, session_id)
    except Exception as exc:
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
        print("{}")
        return
    if not task_id:
        print("{}")
        return
    task_dir = task_directory(ledger, task_id)
    state_file = task_dir / "current.json"
    try:
        task_dir = reject_symlink_ancestry(task_dir, "task directory")
        state_file = reject_symlink_ancestry(state_file, "task state file")
        if not state_file.exists():
            raise FileNotFoundError(str(state_file))
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            raise ValueError("unsupported or mismatched task state")
        # The host-session index selects a task only when the binding is
        # unambiguous. Authorization still uses the task's unique
        # principal, so multiple tasks in one host session cannot cross-read
        # each other's activation state.
        active = activation(ledger, str(state.get("principal") or ""))
        if not active or active.get("task_id") != task_id:
            print("{}")
            return
        agent_name, display_name = worker_identity(event, state)
        host_task_name = native_worker_task_name(event, state)
        host_binding_blocker = ""
        if (
            str(event.get("hook_event_name")) == "SubagentStart"
            and bind_host_worker_from_hook is not None
            and str(event.get("agent_id") or "").strip()
            and str(event.get("model") or "").strip()
        ):
            try:
                binding = bind_host_worker_from_hook(
                    str(project),
                    task_id,
                    session_id,
                    host_task_name or event.get("agent_type"),
                    event.get("agent_id"),
                    event.get("model"),
                )
                if binding.get("bound"):
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    agent_name, display_name = worker_identity(event, state)
                else:
                    reason = str(binding.get("reason") or "host_binding_failed")
                    host_binding_blocker = (
                        " CORTEX HOST BINDING BLOCKER: the native worker could not be bound to this exact attempt "
                        f"({reason}). Stop before project work and return this blocker; do not guess another identity."
                    )
            except Exception:
                host_binding_blocker = (
                    " CORTEX HOST BINDING BLOCKER: the native SubagentStart identity could not be persisted. "
                    "Stop before project work and return this blocker; do not guess another identity."
                )
        if (
            str(event.get("hook_event_name")) == "SubagentStop"
            and finalize_host_worker_stop_from_hook is not None
            and str(event.get("agent_id") or "").strip()
        ):
            try:
                finalize_host_worker_stop_from_hook(
                    str(project),
                    task_id,
                    session_id,
                    event.get("agent_id"),
                )
                state = json.loads(state_file.read_text(encoding="utf-8"))
                agent_name, display_name = worker_identity(event, state)
            except Exception:
                # Lifecycle persistence is fail-open for the host. The
                # warning remains private and the next inspect call will
                # expose any still-running inconsistency.
                print("orchestration_hook warning: SubagentStop persistence failed", file=sys.stderr)
        safe = {
            "at": datetime.now(timezone.utc).isoformat(),
            "hook": str(event.get("hook_event_name")) if str(event.get("hook_event_name")) in HOOK_NAMES else "unknown",
            "agent_type": agent_name,
            "display_name": display_name,
            "thread_id_digest": hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest() if session_id else None,
            "host_agent_id_digest": (
                hashlib.sha256(str(event.get("agent_id") or "").encode("utf-8")).hexdigest()
                if str(event.get("agent_id") or "").strip() else None
            ),
            "tool_name": str(event.get("tool_name")) if TOOL_NAME_RE.fullmatch(str(event.get("tool_name", ""))) else None,
        }
        append_lifecycle_event(task_dir, safe)
        stop_context = stopped_worker_after_wait_context(event, state, task_ref(ledger, task_id))
        if stop_context:
            print(json.dumps(hook_context("PostToolUse", stop_context), ensure_ascii=False))
            return
        dispatch_context = dispatch_required_context(event)
        if dispatch_context:
            print(json.dumps(hook_context("PostToolUse", dispatch_context), ensure_ascii=False))
            return
        publication_context = report_publication_context(event)
        if publication_context:
            print(json.dumps(hook_context("PostToolUse", publication_context), ensure_ascii=False))
            return
        dispatch_failure_reason = empty_agent_wait_reason(event, state)
        if dispatch_failure_reason and safe["hook"] == "PreToolUse":
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": dispatch_failure_reason,
                }
            }, ensure_ascii=False))
            return
        if dispatch_failure_reason:
            print(json.dumps(hook_context("PostToolUse", dispatch_failure_reason), ensure_ascii=False))
            return
        if safe["hook"] in {"SessionStart", "SubagentStart"} and agent_name:
            print(json.dumps(hook_context(
                safe["hook"],
                f"Canonical profile: {agent_name}. Worker display name: {display_name}. Preserve both exact values; do not relabel this worker. {WORKER_CONTEXT}{host_binding_blocker}",
            ), ensure_ascii=False))
            return
        if safe["hook"] == "SessionStart":
            current_gates = state.get("current_gates") or ["unknown"]
            context = f"Active orchestration task: {task_id}; status: {state.get('status', 'unknown')}; current executable gates: {', '.join(str(item) for item in current_gates)}. Use cortex before dispatching or closing a gate."
            context += (
                " COORDINATOR LOCK: the main/root agent must not inspect, search, read, edit, patch, build, test, or run the target project. "
                "Use only Cortex lifecycle calls, exact returned worker dispatches, waiting, report evaluation, user communication, and safe recovery. "
                "Remain idle while workers run; worker delay or failure is never permission for direct coordinator work."
            )
            if is_context_recovery(event):
                public_ref = task_ref(ledger, task_id)
                if public_ref:
                    context += (
                        f" CONTEXT RECOVERY: the host resumed this task after a context reset or compaction; preserve opaque task_ref={public_ref!r}. "
                        f"Call manage_orchestration(intent='inspect', task_ref={public_ref!r}) exactly once before any other lifecycle, dispatch, or report-read call. "
                        "Treat the returned context_handoff, current pipeline, report refs, and relative step as authoritative. "
                        "Do not call start_orchestration again, replay completed dispatches, or reconstruct state from the transcript. "
                        "Publish every returned report_markdown_link verbatim in the main chat before the next lifecycle call."
                    )
                else:
                    context += (
                        " CONTEXT RECOVERY: the host resumed this task after a context reset or compaction. Preserve the opaque task_ref from the durable task context "
                        "and call manage_orchestration(intent='inspect') exactly once before any other lifecycle, dispatch, or report-read call. "
                        "Treat context_handoff and the current ledger as authoritative; do not restart, replay completed dispatches, or use the raw transcript."
                    )
            print(json.dumps(hook_context(safe["hook"], context), ensure_ascii=False))
            return
    except Exception as exc:
        # Hooks are telemetry-only; never inject untrusted exception text into model context.
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
    print("{}")


if __name__ == "__main__":
    main()
