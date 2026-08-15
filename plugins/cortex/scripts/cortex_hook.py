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
    from cortex import bind_host_session_from_hook
except (ImportError, RuntimeError):  # pragma: no cover - hook remains fail-open.
    bind_host_session_from_hook = None

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SCHEMA = "cortex/v7"
PROFILE_CONTRACT = Path(__file__).resolve().parents[1] / "profiles.json"


def profile_names() -> set[str]:
    try:
        payload = json.loads(PROFILE_CONTRACT.read_text(encoding="utf-8"))
        return {str(item["name"]) for item in payload.get("profiles", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return set()


PROFILES = profile_names()
HOOK_NAMES = {"SessionStart", "SubagentStart", "SubagentStop", "PostToolUse"}
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
    "data, or secret canaries. Material user decisions use the exact public worker_question identity from the dispatch, "
    "then return only QUESTION_RECORDED plus its ref through the native parent channel and remain available for the answer. "
    "Never call Cortex lifecycle, pipeline, gate, delegation, or management operations. For a Cortex-managed "
    "dispatch, follow the exact worker identity supplied in that dispatch and call only public worker_question when needed "
    "and public record_report once after all blocking questions are answered. After it succeeds, return only REPORT_RECORDED report_ref=<value> plus at most a two-sentence "
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
    """Resolve the documented Codex session identity with legacy fallbacks."""
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
    index_path = ledger / "active-tasks.json"
    if session_id and index_path.exists():
        try:
            found = valid_task_id(json.loads(index_path.read_text(encoding="utf-8")).get(session_id))
            if found:
                return found
        except (OSError, json.JSONDecodeError):
            pass
    return None


def task_ref(ledger: Path, task_id: str) -> str | None:
    """Resolve the public opaque ref without guessing from a task id."""
    path = reject_symlink_ancestry(ledger / "v3-operations.json", "v3 operation registry")
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
            if value.get("schema") == "cortex/orchestration/v3" and value.get("ok") is True:
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


def task_directory(ledger: Path, task_id: str) -> Path:
    """Resolve a v7 numbered task directory without trusting index paths."""
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


def canonical_agent_name(event: dict, state: dict | None = None) -> str | None:
    """Resolve a canonical profile from a host agent or native task key."""
    candidate = str(event.get("agent_type", "")).strip()
    if candidate in PROFILES:
        return candidate
    if not isinstance(state, dict) or not candidate:
        return None
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
        if candidate not in aliases:
            continue
        profile = str(attempt.get("profile") or attempt.get("agent") or "").strip()
        return profile if profile in PROFILES else None
    return None


def hook_context(event_name: str, context: str) -> dict:
    """Return the documented model-visible hook output envelope."""
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


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
        # unambiguous.  Authorization still uses the task's unique v3
        # principal, so multiple tasks in one host session cannot cross-read
        # each other's activation state.
        active = activation(ledger, str(state.get("principal") or ""))
        if not active or active.get("task_id") != task_id:
            print("{}")
            return
        agent_name = canonical_agent_name(event, state)
        safe = {
            "at": datetime.now(timezone.utc).isoformat(),
            "hook": str(event.get("hook_event_name")) if str(event.get("hook_event_name")) in HOOK_NAMES else "unknown",
            "agent_type": agent_name,
            "display_name": agent_name,
            "thread_id_digest": hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest() if session_id else None,
            "tool_name": str(event.get("tool_name")) if TOOL_NAME_RE.fullmatch(str(event.get("tool_name", ""))) else None,
        }
        append_lifecycle_event(task_dir, safe)
        publication_context = report_publication_context(event)
        if publication_context:
            print(json.dumps(hook_context("PostToolUse", publication_context), ensure_ascii=False))
            return
        if safe["hook"] in {"SessionStart", "SubagentStart"} and agent_name:
            print(json.dumps(hook_context(safe["hook"], f"Canonical agent name: {agent_name}. Use exactly this value as the subagent display name and thread label. {WORKER_CONTEXT}"), ensure_ascii=False))
            return
        if safe["hook"] == "SessionStart":
            current_gates = state.get("current_gates") or [state.get("current_gate", "unknown")]
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
