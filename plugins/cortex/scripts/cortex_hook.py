#!/usr/bin/env python3
"""Privacy-preserving lifecycle telemetry for an active orchestration task."""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Hooks may be loaded through ``importlib`` by host integration checks rather
# than executed from this directory.  Make the bundled runtime resolvable in
# both modes, just like the MCP entrypoint, while keeping hook failures
# fail-open below when the runtime itself is unavailable.
_SCRIPTS_ROOT = str(Path(__file__).resolve().parent)
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

try:
    from cortex import (
        bind_host_session_from_hook,
        bind_host_worker_from_hook,
        ledger_root_path,
        finalize_host_worker_stop_from_hook,
    )
except (ImportError, RuntimeError):  # pragma: no cover - hook remains fail-open.
    bind_host_session_from_hook = None
    bind_host_worker_from_hook = None
    ledger_root_path = None
    finalize_host_worker_stop_from_hook = None

try:
    # Hooks run in their own short-lived process.  Read the same SQLite
    # ledger as the MCP server instead of keeping a second, JSON-file based
    # view of task state.  The imports intentionally remain independent of
    # the stdio server so an installed hook can still make the conservative
    # decision to emit no context if the runtime is unavailable.
    from cortex_runtime.ledger_db import (
        hook_snapshot as db_hook_snapshot,
        hook_snapshot_global as db_hook_snapshot_global,
        hook_snapshot_load_task as db_hook_snapshot_load_task,
        hook_snapshot_task_index as db_hook_snapshot_task_index,
        hook_snapshot_tool_context_epoch as db_hook_snapshot_tool_context_epoch,
        hook_snapshot_find_successful_tool_observation as db_hook_snapshot_find_successful_tool_observation,
        hook_tool_context_epoch as db_hook_tool_context_epoch,
        hook_find_successful_tool_observation as db_hook_find_successful_tool_observation,
        hook_mark_tool_observation_duplicate as db_hook_mark_tool_observation_duplicate,
        hook_record_tool_observation as db_hook_record_tool_observation,
    )
except (ImportError, RuntimeError):  # pragma: no cover - hook remains fail-open.
    db_hook_snapshot = None
    db_hook_snapshot_global = None
    db_hook_snapshot_load_task = None
    db_hook_snapshot_task_index = None
    db_hook_snapshot_tool_context_epoch = None
    db_hook_snapshot_find_successful_tool_observation = None
    db_hook_tool_context_epoch = None
    db_hook_find_successful_tool_observation = None
    db_hook_mark_tool_observation_duplicate = None
    db_hook_record_tool_observation = None

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SCHEMA = "cortex/v8"
HOST_SESSION_SCHEMA = "cortex/host-sessions/v1"
PROFILE_CONTRACT = Path(__file__).resolve().parents[1] / "profiles.json"
# Bundled skills are outside a dispatched project root, but they are still
# immutable plugin-owned files that can safely participate in read dedupe.
CORTEX_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def profile_names() -> set[str]:
    try:
        payload = json.loads(PROFILE_CONTRACT.read_text(encoding="utf-8"))
        return {str(item["name"]) for item in payload.get("profiles", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return set()


PROFILES = profile_names()
HOOK_NAMES = {"SessionStart", "SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse", "Stop"}
TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MAX_LIFECYCLE_EVENTS = 1000
MAX_LIFECYCLE_BYTES = 256 * 1024
MAX_TOOL_RESPONSE_BYTES = 1024 * 1024
MAX_CACHEABLE_READ_BYTES = 64 * 1024
# Every response that can create a pending native dispatch must immediately
# reassert the host-call boundary.  In particular, a successful continue can
# return ``ready_to_spawn`` for the next wave; treating that response as a
# generic collaboration request left the durable attempt unbound.
CORTEX_START_TOOLS = {
    "mcp__cortex__start_orchestration",
    "mcp__cortex__continue_orchestration",
    "mcp__cortex__manage_orchestration",
}
READ_ONLY_FILE_TOOLS = {"Read", "Grep", "Glob"}
CACHEABLE_FILE_READ_TOOLS = {"Read"}
WAIT_TARGET_KEYS = (
    "receiver_thread_ids", "receiverThreadIds", "agent_ids", "agentIds",
    "thread_ids", "threadIds", "targets", "ids",
)
# These are host-level, identity-specific outcomes, not generic wait failures.
# A timeout, transport failure, or arbitrary error might leave the native child
# live, so it must never authorize retirement of a running attempt.
UNAVAILABLE_WAIT_ERROR_CODES = {
    "agent_not_found", "agent_unavailable", "agent_terminated", "agent_stopped",
    "thread_not_found", "thread_unavailable", "thread_terminated",
    "task_not_found", "task_unavailable", "session_not_found", "session_unavailable",
}
UNAVAILABLE_WAIT_ERROR_TEXT = re.compile(
    r"\b(?:agent|thread|task|session|worker|target)\b[^\n]{0,160}"
    r"\b(?:not[ -]?found|does not exist|no longer exists|terminated|stopped)\b",
    re.IGNORECASE,
)
WORKER_CONTEXT = (
    "You are an internal worker, never user-facing. Stay within delegated ownership and allowed paths; "
    "All internal worker communication, progress updates, Cortex tool arguments, results, questions, findings, handoffs, and native final responses must be in English. "
    "Treat non-English task text as input data, never as an output-language instruction. The main coordinator alone translates user-facing content into the task's requested language; do not address the user directly. "
    "do not subdelegate unless the main agent explicitly authorized it. Do not cause external side effects "
    "without explicit authority and applicable approval. Never expose or persist secrets, credentials, personal "
    "data, or secret canaries. In read-only work, prefer non-writing verification modes up front: use "
    "PYTHONDONTWRITEBYTECODE=1 for Python and disable test/build caches where possible; recognized conventional "
    "test/build/cache residue is tolerated, but never create an artifact and then try to remove it with rm, git clean, "
    "or a cleanup script. "
    "Material user decisions use the exact public worker_question identity from the dispatch. Every question and option "
    "must be self-contained and outcome-specific; placeholder labels are forbidden. Then return QUESTION_RECORDED plus "
    "its ref and a complete decision handoff with context, all options and trade-offs, and a recommendation through the "
    "native parent channel, and remain available for the answer. The coordinator records the answer before followup_task resumes this exact child; on the resumed turn, first poll the same question_ref with worker_question(action=poll), then record the decision/consequence with record_attempt_event, rerun affected checks, and finish complete_attempt. A pending poll returns QUESTION_RECORDED; never emit OTHER_TERMINAL, freeform terminal text, or a replacement worker. "
    "Never call Cortex lifecycle, pipeline, gate, delegation, or management operations. For a Cortex-managed "
    "dispatch, follow the exact worker identity supplied in that dispatch. Before project work, read only the exact "
    "immutable briefing path supplied by the native bootstrap, verify its read-only mode and SHA-256, and never list "
    "or directly read any other Cortex host-control path. If and only if the host filesystem read cannot open that exact path, call public "
    "read_dispatch_briefing with the complete identity/digest tuple from the bootstrap; if its bounded response is incomplete, continue only with its exact next_cursor. You may call public read_worker_result "
    "only for predecessor refs explicitly listed in the dispatch, public worker_question when needed, public record_attempt_event for bounded semantic checkpoints, and public "
    "complete_attempt for the final semantic result. Successful briefing and predecessor reads create server-owned receipts; do not author digest, predecessor, changed-file, timestamp, identity, or evidence markers. "
    "For every allowed worker tool, a caller/input/schema validation error or retryable=true result must be corrected "
    "from its diagnostic and retried on the same attempt. Such rejected calls consume no worker attempt and must "
    "never end the worker. Retry every correctable failure on the same attempt; a terminal failed result is durable "
    "evidence for server-owned corrective recovery, not a Cortex stop. During work, checkpoint semantic facts with "
    "record_attempt_event and finish with complete_attempt using only "
    "status, summary, findings, decisions_needed, unresolved, and any advertised typed gate payload. Invalid input is corrected "
    "and retried on the same attempt; finalization/projection failures never authorize a replacement worker. After it succeeds, return only ATTEMPT_COMPLETED attempt_result_ref=<generated id> plus at most a two-sentence "
    "summary; never paste a generated result view into the parent channel. If exact attempt identity is absent or a tool "
    "returns an explicit identity error, preserve the exact task, wave, attempt, project, and tool identifiers and "
    "route the condition through server-owned recovery or one concrete user question; never stop Cortex. Use only tools actually available in this worker context and record unavailable capabilities as "
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
            if ledger_root_path is None:
                break
            try:
                ledger = ledger_root_path({"project_root": str(parent)})
                database = ledger / "cortex.db"
                info = database.lstat()
            except (OSError, ValueError):
                continue
            if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                return parent
    raise ValueError("Cortex project root is unavailable")


def root(event: dict) -> Path:
    if ledger_root_path is None:
        raise ValueError("Cortex host ledger resolver is unavailable")
    project = project_directory(event)
    ledger = ledger_root_path({"project_root": str(project)})
    return reject_symlink_ancestry(ledger, "Cortex host ledger")


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
    if not session_id or db_hook_snapshot is None or db_hook_snapshot_global is None or db_hook_snapshot_load_task is None:
        return None
    try:
        with db_hook_snapshot(ledger) as snapshot:
            if snapshot is None:
                return None
            return _active_task_from_snapshot(snapshot, session_id)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def _active_task_from_snapshot(snapshot: sqlite3.Connection, session_id: str | None) -> str | None:
    """Resolve an active task without opening a second SQLite snapshot."""
    if not session_id or db_hook_snapshot_global is None or db_hook_snapshot_load_task is None:
        return None
    payload = db_hook_snapshot_global(snapshot, "host_sessions", {})
    if payload.get("schema") != HOST_SESSION_SCHEMA or not isinstance(payload.get("tasks"), dict):
        return None
    active: list[str] = []
    for raw_task_id, bound_session in payload["tasks"].items():
        task_id = valid_task_id(raw_task_id)
        if not task_id or bound_session != session_id:
            continue
        try:
            loaded = db_hook_snapshot_load_task(snapshot, task_id)
            state = loaded[1] if loaded is not None else {}
        except (OSError, ValueError, TypeError, sqlite3.Error):
            continue
        if state.get("schema") == SCHEMA and state.get("task_id") == task_id and state.get("status") in {"active", "blocked"}:
            active.append(task_id)
    return active[0] if len(active) == 1 else None


def _active_task_context(ledger: Path, session_id: str | None, snapshot: sqlite3.Connection | None = None) -> dict | None:
    """Load task, state, artifact directory, and activation from one snapshot."""
    if (
        not session_id
        or db_hook_snapshot_global is None
        or db_hook_snapshot_load_task is None
        or (snapshot is None and db_hook_snapshot is None)
    ):
        return None


    def read(connection: sqlite3.Connection) -> dict | None:
        task_id = _active_task_from_snapshot(connection, session_id)
        if not task_id:
            return None
        loaded = db_hook_snapshot_load_task(connection, task_id)
        if loaded is None:
            return None
        definition, state, plan, artifact_dir = loaded
        if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            return None
        activations = db_hook_snapshot_global(connection, "activations", {})
        active = activations.get(str(state.get("principal") or "")) if isinstance(activations, dict) else None
        if not isinstance(active, dict) and isinstance(activations, dict):
            # Older active tasks may have been initialized with a thread
            # principal while activation records were keyed by that thread.
            # Recover only an exact task-bound record; never pick an arbitrary
            # active coordinator from the shared registry.
            active = next(
                (
                    item for item in activations.values()
                    if isinstance(item, dict) and item.get("task_id") == task_id
                ),
                None,
            )
        if not isinstance(active, dict) or active.get("schema") != SCHEMA or active.get("task_id") != task_id:
            return None
        return {"task_id": task_id, "loaded": loaded, "active": active}
    try:
        if snapshot is not None:
            return read(snapshot)
        with db_hook_snapshot(ledger) as connection:
            return read(connection) if connection is not None else None
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def _task_context_from_snapshot(snapshot: sqlite3.Connection, task_id: str) -> dict | None:
    """Load an already authenticated task without rereading after a bind write."""
    if db_hook_snapshot_load_task is None or db_hook_snapshot_global is None:
        return None
    loaded = db_hook_snapshot_load_task(snapshot, task_id)
    if loaded is None:
        return None
    _definition, state, _plan, _artifact_dir = loaded
    if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
        return None
    activations = db_hook_snapshot_global(snapshot, "activations", {})
    active = activations.get(str(state.get("principal") or "")) if isinstance(activations, dict) else None
    if not isinstance(active, dict) and isinstance(activations, dict):
        active = next((item for item in activations.values() if isinstance(item, dict) and item.get("task_id") == task_id), None)
    if not isinstance(active, dict) or active.get("schema") != SCHEMA or active.get("task_id") != task_id:
        return None
    return {"task_id": task_id, "loaded": loaded, "active": active}


def pending_task_from_subagent_start(ledger: Path, event: dict, snapshot: sqlite3.Connection | None = None) -> str | None:
    """Recover one exact pending dispatch when the start-tool hook was skipped.

    Codex treats changed Pre/PostToolUse hooks as untrusted until their content
    hashes are approved.  SubagentStart may still be trusted and delivered. In
    that case the native start event is stronger evidence than coordinator
    prose: bind only when exactly one active task has an awaiting dispatch that
    matches the native task key, or (for hosts designating ``default``) the
    observed model. Ambiguity fails closed.
    """
    if str(event.get("hook_event_name")) != "SubagentStart":
        return None
    agent_type = str(event.get("agent_type") or "").strip()
    model = str(event.get("model") or "").strip()
    if not agent_type:
        return None
    # Some native hosts prefix the issued task key with ``/root/`` in
    # SubagentStart while the durable spawn request stores the unprefixed
    # key.  Preserve exact identity matching, but normalize this transport
    # wrapper rather than treating an otherwise exact child as unspawned.
    agent_type_candidates = {agent_type}
    if agent_type.startswith("/root/") and agent_type.count("/") == 2:
        agent_type_candidates.add(agent_type.removeprefix("/root/"))
    if (
        db_hook_snapshot_task_index is None
        or db_hook_snapshot_load_task is None
        or (snapshot is None and db_hook_snapshot is None)
    ):
        return None
    def read(connection: sqlite3.Connection) -> str | None:
        index = db_hook_snapshot_task_index(connection)
        matches: list[str] = []
        for raw_task_id in index if isinstance(index, dict) else {}:
            task_id = valid_task_id(raw_task_id)
            if not task_id:
                continue
            try:
                loaded = db_hook_snapshot_load_task(connection, task_id)
                state = loaded[1] if loaded is not None else {}
            except (OSError, ValueError, TypeError, sqlite3.Error):
                continue
            if state.get("schema") != SCHEMA or state.get("status") not in {"active", "blocked"}:
                continue
            pending = [
                attempt for attempt in state.get("attempts", [])
                if isinstance(attempt, dict)
                and not attempt.get("invalidated")
                and attempt.get("status") == "awaiting_host_spawn"
            ]
            if agent_type == "default":
                candidates = [
                    attempt for attempt in pending
                    if model
                    and str(attempt.get("expected_model") or attempt.get("selected_model") or "") == model
                ]
            else:
                candidates = [
                    attempt for attempt in pending
                    if str((attempt.get("spawn_request") or {}).get("task_name") or "") in agent_type_candidates
                ]
            if len(candidates) == 1:
                matches.append(task_id)
        return matches[0] if len(matches) == 1 else None
    try:
        if snapshot is not None:
            return read(snapshot)
        with db_hook_snapshot(ledger) as connection:
            return read(connection) if connection is not None else None
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def task_ref(ledger: Path, task_id: str, snapshot: sqlite3.Connection | None = None) -> str | None:
    """Resolve the public opaque ref without guessing from a task id."""
    if db_hook_snapshot_global is None or (snapshot is None and db_hook_snapshot is None):
        return None
    def read(connection: sqlite3.Connection) -> str | None:
        payload = db_hook_snapshot_global(connection, "operation_registry", {})
        record = payload.get("tasks", {}).get(task_id) if isinstance(payload, dict) else None
        candidate = record.get("start", {}).get("task_ref") if isinstance(record, dict) else None
        candidate = str(candidate or "")
        return candidate if SAFE_ID_RE.fullmatch(candidate) else None
    try:
        if snapshot is not None:
            return read(snapshot)
        with db_hook_snapshot(ledger) as connection:
            return read(connection) if connection is not None else None
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def is_context_recovery(event: dict) -> bool:
    """Recognize host resume/clear/compact starts without trusting arbitrary text."""
    for key in ("source", "reason", "startup_reason", "thread_start_reason", "trigger"):
        value = str(event.get(key, "")).strip().lower()
        if value in {"resume", "resumed", "clear", "cleared", "compact", "compaction"}:
            return True
    return False


def _bounded_digest(value: object) -> str | None:
    """Hash a serializable value without retaining its potentially private text."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_TOOL_RESPONSE_BYTES:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _event_tool_input(event: dict) -> dict:
    value = event.get("tool_input")
    return value if isinstance(value, dict) else {}


def _normalised_file_path(project: Path, value: object) -> tuple[Path, str] | None:
    """Return an in-workspace regular path and its privacy-preserving identity."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or "\x00" in raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(project.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        info = resolved.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return resolved, relative.as_posix()


def _owned_cortex_skill_path(value: object) -> tuple[Path, str] | None:
    """Return a regular bundled ``SKILL.md`` without accepting arbitrary paths."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or "\x00" in raw:
        return None
    try:
        root = CORTEX_SKILLS_ROOT.resolve(strict=True)
        resolved = Path(raw).resolve(strict=True)
        relative = resolved.relative_to(root)
        info = resolved.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if relative.name != "SKILL.md" or not stat.S_ISREG(info.st_mode):
        return None
    return resolved, f"cortex-skill/{relative.as_posix()}"


def _safe_range_descriptor(tool_input: dict) -> tuple[dict, bool]:
    """Keep numeric ranges useful while hashing opaque cursors and values."""
    values: dict[str, object] = {}
    has_range = False
    for key in ("offset", "limit", "start", "end", "line_start", "line_end", "range", "cursor"):
        if key not in tool_input:
            continue
        has_range = True
        value = tool_input.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            values[key] = value
        else:
            digest = _bounded_digest(value)
            if digest:
                values[f"{key}_digest"] = digest
    return values, has_range


def tool_observation(event: dict, project: Path, task_id: str, attempt_id: str, context_epoch: int) -> dict | None:
    """Create bounded, non-secret metadata for one safe file tool invocation.

    The raw path and search text influence the fingerprint but never leave this
    process.  The durable observation stores only hashes plus numeric ranges.
    """
    tool_name = str(event.get("tool_name") or "")
    if tool_name not in READ_ONLY_FILE_TOOLS:
        return None
    tool_input = _event_tool_input(event)
    raw_path = next((tool_input.get(key) for key in ("file_path", "path", "filename") if tool_input.get(key)), None)
    path_info = _normalised_file_path(project, raw_path)
    resource_kind = "project_file"
    if path_info is None:
        path_info = _owned_cortex_skill_path(raw_path)
        resource_kind = "cortex_skill" if path_info is not None else "external_or_unknown"
    normalised_path = path_info[1] if path_info else ""
    ranges, has_range = _safe_range_descriptor(tool_input)
    raw_query = next((tool_input.get(key) for key in ("query", "pattern", "search_query") if tool_input.get(key) is not None), None)
    query_digest = _bounded_digest(raw_query) if raw_query is not None else None
    path_digest = hashlib.sha256(normalised_path.encode("utf-8")).hexdigest()
    file_digest = None
    workspace_generation = "unavailable"
    cacheable = False
    if tool_name in CACHEABLE_FILE_READ_TOOLS and path_info and not has_range:
        try:
            file_path = path_info[0]
            size = file_path.stat().st_size
            if 0 <= size <= MAX_CACHEABLE_READ_BYTES:
                file_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                supplied_generation = event.get("workspace_generation", event.get("workspaceGeneration"))
                generation_digest = _bounded_digest(supplied_generation) if supplied_generation is not None else None
                workspace_generation = f"host:{generation_digest}" if generation_digest else f"file:{file_digest}"
                cacheable = True
        except OSError:
            pass
    if not cacheable:
        supplied_generation = event.get("workspace_generation", event.get("workspaceGeneration"))
        generation_digest = _bounded_digest(supplied_generation) if supplied_generation is not None else None
        workspace_generation = f"host:{generation_digest}" if generation_digest else "unavailable"
    normalized_arguments = {
        "path_digest": path_digest,
        "query_digest": query_digest,
        "range": ranges,
    }
    fingerprint_input = {
        "tool_name": tool_name,
        "path": normalised_path,
        "query": str(raw_query or ""),
        "range": ranges,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "context_epoch": context_epoch,
        "workspace_generation": workspace_generation,
        "file_digest": file_digest,
    }
    fingerprint = _bounded_digest(fingerprint_input)
    if not fingerprint:
        return None
    return {
        "fingerprint": fingerprint,
        "tool_name": tool_name,
        "normalized_arguments": json.dumps(normalized_arguments, sort_keys=True, separators=(",", ":")),
        "workspace_generation": workspace_generation,
        "coverage": "full" if cacheable else "noncacheable",
        "cacheable": cacheable,
        "resource_kind": resource_kind,
    }


def tool_call_failed(event: dict) -> bool:
    """Treat missing or explicitly error-shaped post-tool results as failures."""
    response = event.get("tool_response")
    if response is None:
        return True
    if isinstance(response, dict):
        return bool(response.get("is_error") or response.get("isError") or response.get("error"))
    return False


def _wait_target_ids(event: dict) -> list[str]:
    """Return bounded, explicit native wait targets without inferring aliases."""
    tool_input = _event_tool_input(event)
    targets: list[str] = []
    for key in WAIT_TARGET_KEYS:
        value = tool_input.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            candidate = str(item or "").strip()
            if candidate and len(candidate) <= 256 and candidate not in targets:
                targets.append(candidate)
    return targets


def _unavailable_wait_error(event: dict) -> tuple[bool, str]:
    """Recognize only a bounded host proof that a wait target is gone.

    Hook payloads are host-controlled and may contain private diagnostics.  The
    predicate retains no response text: it accepts an enumerated error code or
    a narrowly scoped identity-unavailable phrase, and callers emit only the
    stable Cortex recovery reason.
    """
    if not tool_call_failed(event):
        return False, ""
    response = event.get("tool_response")
    try:
        rendered = json.dumps(response, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return False, ""
    if len(rendered.encode("utf-8")) > MAX_TOOL_RESPONSE_BYTES:
        return False, ""
    codes: set[str] = set()
    queue: list[object] = [response]
    visited = 0
    while queue and visited < 64:
        value = queue.pop(0)
        visited += 1
        if isinstance(value, dict):
            for key in ("code", "error_code", "errorCode"):
                candidate = str(value.get(key) or "").strip().lower().replace("-", "_")
                if candidate:
                    codes.add(candidate)
            queue.extend(value.get(key) for key in ("error", "result", "structuredContent", "content") if key in value)
        elif isinstance(value, list):
            queue.extend(value[:8])
        elif isinstance(value, str) and len(value.encode("utf-8")) <= MAX_TOOL_RESPONSE_BYTES:
            try:
                queue.append(json.loads(value))
            except (json.JSONDecodeError, TypeError):
                pass
    if codes.intersection(UNAVAILABLE_WAIT_ERROR_CODES):
        return True, rendered.lower()
    return bool(UNAVAILABLE_WAIT_ERROR_TEXT.search(rendered)), rendered.lower()


def unavailable_wait_target_ids(event: dict, state: dict | None = None) -> list[str]:
    """Return running child ids that a failed host wait proved unavailable.

    A code that does not name a target may be used only for a single exact
    target.  In a multi-target wait, each retired child must be named by the
    host response; otherwise the event is deliberately non-terminal.
    """
    if (
        str(event.get("hook_event_name")) != "PostToolUse"
        or str(event.get("tool_name")) not in {"Agent", "wait"}
    ):
        return []
    targets = _wait_target_ids(event)
    unavailable, response_text = _unavailable_wait_error(event)
    if not unavailable or not targets:
        return []
    if len(targets) == 1:
        candidates = targets
    else:
        candidates = [target for target in targets if target.lower() in response_text]
    running_ids = {
        str((attempt.get("host_spawn") or {}).get("agent_id") or "").strip()
        for attempt in (state or {}).get("attempts", [])
        if isinstance(attempt, dict)
        and not attempt.get("invalidated")
        and attempt.get("status") == "running"
    }
    return [target for target in candidates if target in running_ids]


def attempt_for_tool_observation(event: dict, state: dict) -> str:
    """Bind observations to an exact worker attempt, or the coordinator lane."""
    candidates = {
        str(event.get("agent_id") or "").strip(),
        str(event.get("agent_type") or "").strip(),
    }
    candidates.discard("")
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        spawn_request = attempt.get("spawn_request") or {}
        host_spawn = attempt.get("host_spawn") or {}
        aliases = {
            str(attempt.get("attempt_id") or "").strip(),
            str(spawn_request.get("task_name") or "").strip(),
            str(host_spawn.get("agent_id") or "").strip(),
            str(host_spawn.get("task_name") or "").strip(),
        }
        if aliases.intersection(candidates):
            attempt_id = str(attempt.get("attempt_id") or "").strip()
            if attempt_id:
                return attempt_id
    return "coordinator"


def context_epoch_for_tool_observation(ledger: Path, task_id: str, event: dict, snapshot: sqlite3.Connection | None = None) -> int:
    """Use a durable epoch, rolling it on an explicitly recorded compaction."""
    if db_hook_snapshot is None or db_hook_snapshot_tool_context_epoch is None:
        return 0
    try:
        supplied = event.get("context_epoch", event.get("contextEpoch"))
        if isinstance(supplied, int) and not isinstance(supplied, bool) and supplied >= 0:
            return supplied
        if is_context_recovery(event) and db_hook_tool_context_epoch is not None:
            return int(db_hook_tool_context_epoch(ledger, task_id, bump=True))
        if snapshot is not None:
            return int(db_hook_snapshot_tool_context_epoch(snapshot, task_id))
        with db_hook_snapshot(ledger) as connection:
            return int(db_hook_snapshot_tool_context_epoch(connection, task_id)) if connection is not None else 0
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return 0


def bump_context_epoch_for_recovery(ledger: Path, task_id: str, event: dict) -> int | None:
    """Roll and record the durable epoch for an authenticated recovery start.

    Host-provided epochs are advisory only: a missing, default, or stale value
    must not prevent a new durable boundary.  Returning ``None`` on bounded
    ledger failures keeps the lifecycle hook fail-open.
    """
    if db_hook_tool_context_epoch is None:
        return None
    try:
        durable_epoch = int(db_hook_tool_context_epoch(ledger, task_id, bump=True))
        event["context_epoch"] = durable_epoch
        return durable_epoch
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def apply_tool_deduplication(
    event: dict, project: Path, ledger: Path, task_id: str, state: dict,
    snapshot: sqlite3.Connection | None = None,
) -> dict | None:
    """Persist observations and return an advisory for duplicate full reads.

    A duplicate is useful telemetry and a cache hint, but it is not an
    authorization failure: the host may still need to perform the read when a
    prior result was lost or the caller intentionally retries it.
    """
    if db_hook_record_tool_observation is None:
        return None
    attempt_id = attempt_for_tool_observation(event, state)
    epoch = context_epoch_for_tool_observation(ledger, task_id, event, snapshot)
    observation = tool_observation(event, project, task_id, attempt_id, epoch)
    if observation is None:
        return None
    hook_name = str(event.get("hook_event_name") or "")
    if hook_name == "PreToolUse" and observation["cacheable"]:
        try:
            if snapshot is not None and db_hook_snapshot_find_successful_tool_observation is not None:
                already_read = db_hook_snapshot_find_successful_tool_observation(
                    snapshot, task_id, attempt_id, epoch, observation["fingerprint"], observation["workspace_generation"],
                )
            else:
                already_read = db_hook_find_successful_tool_observation(
                    ledger, task_id, attempt_id, epoch, observation["fingerprint"], observation["workspace_generation"],
                )
            if already_read:
                db_hook_mark_tool_observation_duplicate(ledger, task_id, attempt_id, epoch, observation["fingerprint"])
                return {
                    "duplicate": True,
                    "tool_name": observation["tool_name"],
                    "resource_kind": observation.get("resource_kind"),
                }
        except (OSError, ValueError, TypeError):
            return None
    elif hook_name == "PostToolUse":
        result_digest = _bounded_digest(event.get("tool_response"))
        try:
            db_hook_record_tool_observation(
                ledger,
                task_id=task_id,
                attempt_id=attempt_id,
                context_epoch=epoch,
                fingerprint=observation["fingerprint"],
                tool_name=observation["tool_name"],
                normalized_arguments=observation["normalized_arguments"],
                workspace_generation=observation["workspace_generation"],
                result_digest=result_digest,
                coverage=observation["coverage"],
                status="failed" if tool_call_failed(event) else "success",
            )
        except (OSError, ValueError, TypeError):
            return None
    return None


def duplicate_read_advisory(resource_kind: object = None) -> dict[str, object]:
    """Return a non-blocking PreToolUse hint for an already observed Read."""
    message = (
        "CORTEX SKILL READ ADVISORY: this exact bundled skill already loaded successfully "
        "in this context epoch. Reuse the loaded skill; do not reload it unless the first read was truncated, "
        "the skill changed, or a distinct unread range is required. This read remains allowed when the prior "
        "result is unavailable or an intentional retry is necessary."
        if resource_kind == "cortex_skill" else
        "CORTEX TOOL DEDUPLICATION ADVISORY: an unchanged small full-file read "
        "already succeeded in this context epoch. Reusing the prior result may "
        "be faster, but this read remains allowed if the result is unavailable "
        "or the caller intentionally retries it."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


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
            if value.get("schema") == "cortex/orchestration/v5" and value.get("ok") is True:
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
        "child id. Do not use a generic collaboration spawn, self-authored task name, or replacement child as a "
        "substitute: it cannot bind to or advance this Cortex attempt. A planned dispatch or empty wait is not a "
        "spawned worker." + suffix
    )


def empty_agent_wait_reason(event: dict, state: dict | None = None) -> str | None:
    """Return a worker-only denial before waiting without a spawned child.

    The coordinator receives an advisory instead of a permission denial so a
    missing target cannot deadlock the control plane. Worker identity and
    target authorization remain fail-closed below.
    """
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
    present_targets = [tool_input.get(key) for key in WAIT_TARGET_KEYS if key in tool_input]
    # The dedicated ``wait`` host tool is inherently wait-shaped even when
    # the host sends an empty input object.  Without this branch a failed
    # spawn could fall through as an ordinary tool call and allow an
    # unspawned wait to block before Cortex can emit its actionable dispatch
    # diagnostic.
    wait_shaped = (
        str(event.get("tool_name")) == "wait"
        or action in {"wait", "wait_agent"}
        or bool(present_targets)
    )
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
        "CORTEX DISPATCH ADVISORY: this wait has no child target, so no worker was spawned. "
        "Invoke the exact pending server-owned spawn dispatch and retry the lifecycle step; "
        "do not claim dispatch success or invent a replacement worker. If the host cannot spawn, "
        "keep the task resumable and surface one concrete user question."
    )


def coordinator_read_advisory(event: dict, state: dict | None = None) -> str | None:
    """Advise a coordinator on safe inspection without authorizing project work."""
    if str(event.get("hook_event_name")) != "PreToolUse":
        return None
    if str(event.get("tool_name") or "") not in READ_ONLY_FILE_TOOLS:
        return None
    agent_type = str(event.get("agent_type") or "").strip()
    if agent_type and agent_type in PROFILES:
        return None
    return (
        "CORTEX COORDINATOR READ ADVISORY: read-only project inspection is non-blocking but outside coordinator "
        "ownership. Remain on the control plane, use the exact Cortex lifecycle and worker-result tools, and do not "
        "infer a task, read task content, credentials, or delegated files from this hook."
    )


def task_directory(ledger: Path, task_id: str) -> Path:
    """Resolve the registered artifact directory from SQLite, never by scan."""
    if db_hook_snapshot is None or db_hook_snapshot_load_task is None:
        return ledger / "tasks" / f"missing-{task_id}"
    try:
        with db_hook_snapshot(ledger) as snapshot:
            if snapshot is None:
                return ledger / "tasks" / f"missing-{task_id}"
            loaded = db_hook_snapshot_load_task(snapshot, task_id)
            if loaded is None:
                return ledger / "tasks" / f"missing-{task_id}"
            relative = Path(str(loaded[3]))
            if relative.is_absolute() or ".." in relative.parts:
                return ledger / "tasks" / f"missing-{task_id}"
            return reject_symlink_ancestry(ledger / relative, "task directory")
    except (OSError, ValueError, sqlite3.Error):
        pass
    return ledger / "tasks" / f"missing-{task_id}"


def activation(ledger: Path, session_id: str | None) -> dict | None:
    if not session_id or db_hook_snapshot is None or db_hook_snapshot_global is None:
        return None
    try:
        with db_hook_snapshot(ledger) as snapshot:
            if snapshot is None:
                return None
            record = db_hook_snapshot_global(snapshot, "activations", {}).get(session_id)
            if isinstance(record, dict) and record.get("schema") == SCHEMA and record.get("coordinator") == "main" and record.get("mode") == "main-orchestrator":
                return record
            return None
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def snapshot_task(ledger: Path, task_id: str) -> tuple[dict, dict, dict | None, str] | None:
    """Load one task from the same bounded read-only hook snapshot."""
    if db_hook_snapshot is None or db_hook_snapshot_load_task is None:
        return None
    try:
        with db_hook_snapshot(ledger) as snapshot:
            if snapshot is None:
                return None
            return db_hook_snapshot_load_task(snapshot, task_id)
    except (OSError, ValueError, TypeError, sqlite3.Error):
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


def worker_context_recovery(
    event: dict,
    state: dict,
    definition: dict,
    task_dir: Path,
) -> str:
    """Rehydrate one worker's immutable bootstrap after host compaction.

    The hook emits only artifacts already bound to the exact native attempt;
    it never scans the ledger or reconstructs prompts from mutable state.
    Workers still verify every supplied digest before reading.
    """
    profile, _display = worker_identity(event, state)
    if not profile or not is_context_recovery(event):
        return ""
    candidates = {str(event.get("agent_type") or "").strip(), str(event.get("agent_id") or "").strip()}
    matches = []
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict) or str(attempt.get("profile") or attempt.get("agent") or "") != profile:
            continue
        aliases = {
            str((attempt.get("spawn_request") or {}).get("task_name") or "").strip(),
            str((attempt.get("host_spawn") or {}).get("agent_id") or "").strip(),
            str((attempt.get("host_spawn") or {}).get("task_name") or "").strip(),
        }
        if aliases.intersection(candidates):
            matches.append(attempt)
    if len(matches) != 1:
        return " CORTEX WORKER RECOVERY ROUTE: exact worker attempt identity is unavailable; do not infer task context. Let the server derive the next recovery action."
    attempt = matches[0]
    fields = []
    for label, relative, digest in (
        ("assignment/briefing", attempt.get("briefing_file"), attempt.get("briefing_digest")),
        ("compiled plan unit", attempt.get("plan_unit_file"), attempt.get("plan_unit_digest")),
        ("immutable user intent", definition.get("user_intent_artifact_path"), definition.get("user_request_digest")),
    ):
        raw = str(relative or "").strip()
        sha = str(digest or "").strip().lower()
        if not raw or not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        fields.append(f"{label}={task_dir / candidate} (sha256={sha})")
    if not fields:
        return " CORTEX WORKER RECOVERY ROUTE: immutable worker artifacts are unavailable; do not reconstruct context from transcript. Let the server derive the next recovery action."
    return (
        " CONTEXT RECOVERY: host resumed this exact worker after compaction. "
        f"Attempt {str(attempt.get('attempt_id') or '')!r} remains authoritative. "
        "Re-read only these exact immutable artifacts, verify mode and SHA-256, then continue the same attempt: "
        + "; ".join(fields) + ". Preserve the AttemptResult contract and do not spawn a replacement worker."
    )


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
    """Reassert the exact recovery boundary after a stopped native worker.

    A canonical ``WORK_COMPLETED``/``FINALIZING`` attempt is special: its
    worker work already succeeded and only a server-side projection retry is
    pending. It must never inherit the ordinary resultless-stop instruction
    that creates a failed continuation/replacement path.
    """
    if (
        str(event.get("hook_event_name")) != "PostToolUse"
        or str(event.get("tool_name")) not in {"Agent", "wait"}
    ):
        return None
    attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict) and not item.get("invalidated")
    ]
    current_gates = {
        str(item) for item in (state.get("current_gates") or [])
        if str(item).strip()
    }
    finalization_pending = [
        item for item in attempts
        if item.get("host_stop_outcome") == "work_completed_finalization_pending"
        and (not current_gates or str(item.get("gate") or "") in current_gates)
    ]
    if finalization_pending:
        pending = finalization_pending[-1]
        attempt_id = str(pending.get("attempt_id") or "unknown")
        result_ref = str(pending.get("attempt_result_ref") or "").strip()
        result_note = f" (attempt_result_ref={result_ref!r})" if result_ref else ""
        return (
            f"Internal lifecycle receipt: attempt {attempt_id!r} already recorded a successful canonical "
            f"AttemptResult{result_note}, but its generated projection is pending. Do not submit status='failed', "
            "wait on or follow up the stopped native worker, or spawn a replacement. Retry complete_attempt only "
            "for this same persisted attempt; Cortex will reconcile the projection automatically."
        )
    stopped_attempts = [
        item for item in attempts
        if item.get("host_stop_outcome") == "native_worker_stopped_without_result"
        and (not current_gates or str(item.get("gate") or "") in current_gates)
    ]
    if not stopped_attempts:
        return None
    stopped = stopped_attempts[-1]
    attempt_id = str(stopped.get("attempt_id") or "unknown")
    dispatch_ref = str(stopped.get("dispatch_ref") or "").strip()
    host_spawn = stopped.get("host_spawn") or {}
    host_agent_id = str(host_spawn.get("agent_id") or "").strip()
    host_task_name = str(host_spawn.get("task_name") or (stopped.get("spawn_request") or {}).get("task_name") or "").strip()
    if not dispatch_ref or not host_agent_id or not host_task_name:
        return None
    return (
        f"Internal lifecycle receipt: attempt {attempt_id!r} stopped without an AttemptResult and is terminal failed "
        f"(dispatch_ref={dispatch_ref!r}, reason='native_worker_stopped_without_result'). Do not wait on, respawn, "
        f"or follow up the stopped native worker (agent_id={host_agent_id!r}, task_name={host_task_name!r}). "
        + (f"task_ref={public_task_ref!r}. " if public_task_ref else "")
        + "Submit exactly one result with status='failed', this dispatch_ref, and this reason; Cortex will apply its "
        + "server-owned corrective route automatically."
    )


def active_worker_stop_block(event: dict, state: dict) -> str | None:
    """Compatibility shim: Stop never blocks a coordinator turn.

    Native child identity, AttemptResult finalization, and recovery remain
    server-owned lifecycle facts.  The command hook is deliberately
    telemetry-only: it must not inject ``CORTEX ACTIVE WORKER`` text, emit a
    ``decision=block`` response, or make a stale binding look live.
    """
    return None


def append_lifecycle_event(task_dir: Path, event: dict) -> None:
    """Append bounded telemetry, materializing only the task-local export.

    Task initialization intentionally leaves its artifact directory absent.
    Telemetry is an optional projection, but an active lifecycle hook may
    create the task directory and its three private files at the moment it
    records an event.  All existing ancestry is checked without following
    symlinks before creation; no broad layout is recreated as a side effect.
    """
    task_dir = reject_symlink_ancestry(task_dir, "task directory")
    parent = reject_symlink_ancestry(task_dir.parent, "task directory parent")
    try:
        parent_info = parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("task directory parent is unavailable") from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise ValueError("task directory parent must be a real directory")
    try:
        task_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    task_info = task_dir.lstat()
    if stat.S_ISLNK(task_info.st_mode) or not stat.S_ISDIR(task_info.st_mode):
        raise ValueError("task directory must be a real directory")
    task_dir.chmod(0o700, follow_symlinks=False)
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


def _run(event: dict, snapshot: sqlite3.Connection | None = None) -> None:
    try:
        project = project_directory(event)
        ledger = root(event)
        session_id = session_identity(event)
        bind_post_tool_session(event, project, session_id)
        task_context = _active_task_context(ledger, session_id, snapshot)
        task_id = task_context.get("task_id") if task_context else None
        if not task_id and session_id and bind_host_session_from_hook is not None:
            recovered_task_id = pending_task_from_subagent_start(ledger, event, snapshot)
            recovered_ref = task_ref(ledger, recovered_task_id, snapshot) if recovered_task_id else None
            if recovered_task_id:
                if recovered_ref:
                    bind_host_session_from_hook(str(project), recovered_ref, session_id)
                # The read snapshot is intentionally not refreshed after this
                # bounded host-session bind.  Authorization continues from
                # the already authenticated pre-write snapshot.
                task_context = _task_context_from_snapshot(snapshot, recovered_task_id) if snapshot is not None else None
                task_id = task_context.get("task_id") if task_context else None
    except Exception as exc:
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
        print("{}")
        return
    if not task_id:
        print("{}")
        return
    try:
        if not task_context or task_context.get("task_id") != task_id:
            task_context = _active_task_context(ledger, session_id, snapshot)
        if not task_context:
            print("{}")
            return
        loaded = task_context.get("loaded")
        if not isinstance(loaded, tuple) or len(loaded) != 4:
            raise ValueError("active task snapshot is unavailable")
        _definition, state, _plan, artifact_dir = loaded
        relative_task_dir = Path(str(artifact_dir))
        if relative_task_dir.is_absolute() or ".." in relative_task_dir.parts:
            raise ValueError("task artifact directory is outside the ledger root")
        task_dir = ledger / relative_task_dir
        task_dir = reject_symlink_ancestry(task_dir, "task directory")
        if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            raise ValueError("unsupported or mismatched task state")
        # The host-session index selects a task only when the binding is
        # unambiguous. Authorization still uses the task's unique
        # principal, so multiple tasks in one host session cannot cross-read
        # each other's activation state.
        # active_task already authenticated the host session against the
        # canonical task binding. Activation records remain keyed by the
        # task's durable coordinator principal, not by the transient native
        # host session alias used for SubagentStart recovery.
        active = task_context.get("active")
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
                    # The worker binding is durably written by the host
                    # adapter, but the hook deliberately keeps its original
                    # read snapshot open.  Rehydrate identity from the exact
                    # attempt returned by that write instead of opening a
                    # second connection or relying on the generic
                    # ``agent_type=default`` alias to match a named spawn.
                    bound_attempt_id = str(binding.get("attempt_id") or "").strip()
                    bound_attempt = next(
                        (
                            item for item in state.get("attempts", [])
                            if isinstance(item, dict)
                            and str(item.get("attempt_id") or "").strip() == bound_attempt_id
                        ),
                        None,
                    )
                    if isinstance(bound_attempt, dict):
                        profile = str(bound_attempt.get("profile") or bound_attempt.get("agent") or "").strip()
                        if profile in PROFILES:
                            display_name = str(bound_attempt.get("display_name") or "").strip() or profile
                            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ]{0,95}", display_name):
                                display_name = profile
                            agent_name = profile
                        else:
                            agent_name, display_name = worker_identity(event, state)
                    else:
                        agent_name, display_name = worker_identity(event, state)
                else:
                    reason = str(binding.get("reason") or "host_binding_failed")
                    # Binding failures are recorded by the host adapter and
                    # recovered on the control plane. Never inject a blocker
                    # or stop instruction into the worker transcript.
                    host_binding_blocker = ""
            except Exception:
                host_binding_blocker = ""
        if (
            str(event.get("hook_event_name")) == "SubagentStop"
            and finalize_host_worker_stop_from_hook is not None
            and str(event.get("agent_id") or "").strip()
        ):
            try:
                finalized = finalize_host_worker_stop_from_hook(
                    str(project),
                    task_id,
                    session_id,
                    event.get("agent_id"),
                )
                # The snapshot was authenticated before the native child
                # stopped. Reflect the exact server-owned question pause in
                # this in-memory copy so the same hook invocation cannot
                # re-block the parent turn as if the child were still live.
                if finalized.get("outcome") == "awaiting_user":
                    question_refs = list(finalized.get("question_refs") or [])
                    for attempt in state.get("attempts", []):
                        if not isinstance(attempt, dict):
                            continue
                        if str((attempt.get("host_spawn") or {}).get("agent_id") or "") != str(event.get("agent_id") or ""):
                            continue
                        attempt["lifecycle_status"] = "paused_awaiting_user"
                        attempt["host_stop_outcome"] = "awaiting_user"
                        attempt["host_resumable"] = True
                        attempt["host_question_refs"] = question_refs
                        break
                agent_name, display_name = worker_identity(event, state)
            except Exception:
                # Lifecycle persistence is fail-open for the host. The
                # warning remains private and the next inspect call will
                # expose any still-running inconsistency.
                print("orchestration_hook warning: SubagentStop persistence failed", file=sys.stderr)
        # A native child can become unreachable after compaction without a
        # corresponding SubagentStop event.  When the host itself returns an
        # exact, identity-specific unavailable result for a persisted wait
        # target, use the same terminal resultless-stop transition. Generic
        # wait errors remain observational: they do not prove that a child is
        # gone and must not authorize a duplicate replacement dispatch.
        if (
            str(event.get("hook_event_name")) == "PostToolUse"
            and finalize_host_worker_stop_from_hook is not None
        ):
            for unavailable_worker_id in unavailable_wait_target_ids(event, state):
                try:
                    finalized = finalize_host_worker_stop_from_hook(
                        str(project), task_id, session_id, unavailable_worker_id,
                    )
                except Exception:
                    # The hook is telemetry/recovery assistance only.  Do not
                    # surface host response text or turn a persistence failure
                    # into an inferred child lifecycle transition.
                    print("orchestration_hook warning: unavailable wait persistence failed", file=sys.stderr)
                    continue
                if finalized.get("outcome") != "native_worker_stopped_without_result":
                    continue
                # ``state`` is the authenticated pre-write snapshot. Update
                # only the exact entry needed for the static post-wait
                # recovery instruction below; durable state remains the
                # authoritative saved transition from the finalizer.
                for attempt in state.get("attempts", []):
                    if not isinstance(attempt, dict):
                        continue
                    if str((attempt.get("host_spawn") or {}).get("agent_id") or "") != unavailable_worker_id:
                        continue
                    attempt["status"] = "failed"
                    attempt["lifecycle_status"] = "needs_recovery"
                    attempt["host_stop_outcome"] = "native_worker_stopped_without_result"
                    attempt["host_resumable"] = False
                    break
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
        # A context reset is an authenticated lifecycle boundary, not a tool
        # observation.  Roll the durable epoch here before dedupe so a
        # SessionStart that carries no numeric epoch still invalidates stale
        # worker read receipts.  ``apply_tool_deduplication`` reuses this
        # value and therefore does not bump the epoch a second time.
        if safe["hook"] == "SessionStart" and is_context_recovery(event):
            bump_context_epoch_for_recovery(ledger, task_id, event)
        append_lifecycle_event(task_dir, safe)
        dedupe = apply_tool_deduplication(event, project, ledger, task_id, state, snapshot)
        if dedupe and dedupe.get("duplicate") and safe["hook"] == "PreToolUse":
            print(json.dumps(duplicate_read_advisory(dedupe.get("resource_kind")), ensure_ascii=False))
            return
        stop_context = stopped_worker_after_wait_context(event, state, task_ref(ledger, task_id, snapshot))
        if stop_context:
            print(json.dumps(hook_context("PostToolUse", stop_context), ensure_ascii=False))
            return
        dispatch_context = dispatch_required_context(event)
        if dispatch_context:
            print(json.dumps(hook_context("PostToolUse", dispatch_context), ensure_ascii=False))
            return
        dispatch_failure_reason = empty_agent_wait_reason(event, state)
        if dispatch_failure_reason and safe["hook"] == "PreToolUse":
            if not agent_name:
                print(json.dumps(hook_context("PreToolUse", (
                    "CORTEX COORDINATOR WAIT ADVISORY: no worker target was supplied. Remain idle on the "
                    "control plane, preserve the exact task_ref, and use the pending Cortex dispatch or inspect "
                    "route; do not infer a worker, task, or project content from this event."
                )), ensure_ascii=False))
                return
            print(json.dumps(hook_context("PreToolUse", dispatch_failure_reason), ensure_ascii=False))
            return
        coordinator_advisory = coordinator_read_advisory(event, state)
        if coordinator_advisory:
            print(json.dumps(hook_context("PreToolUse", coordinator_advisory), ensure_ascii=False))
            return
        if dispatch_failure_reason:
            print(json.dumps(hook_context("PostToolUse", dispatch_failure_reason), ensure_ascii=False))
            return
        if safe["hook"] in {"SessionStart", "SubagentStart"} and agent_name:
            worker_recovery = worker_context_recovery(event, state, _definition, task_dir)
            print(json.dumps(hook_context(
                safe["hook"],
                f"Canonical profile: {agent_name}. Worker display name: {display_name}. Preserve both exact values; do not relabel this worker. {WORKER_CONTEXT}{worker_recovery}{host_binding_blocker}",
            ), ensure_ascii=False))
            return
        if safe["hook"] == "SessionStart":
            current_gates = state.get("current_gates") or ["unknown"]
            context = f"Active orchestration task: {task_id}; status: {state.get('status', 'unknown')}; current executable gates: {', '.join(str(item) for item in current_gates)}. Use cortex before dispatching or closing a gate."
            context += (
                " COORDINATOR ROUTE: the main/root agent must not inspect, search, read, edit, patch, build, test, or run the target project. "
                "Use only Cortex lifecycle calls, exact returned worker dispatches, waiting, result evaluation, user communication, and safe recovery. "
                "Remain idle while workers run; worker delay or failure is never permission for direct coordinator work."
            )
            if is_context_recovery(event):
                public_ref = task_ref(ledger, task_id, snapshot)
                if public_ref:
                    context += (
                        f" CONTEXT RECOVERY: the host resumed this task after a context reset or compaction; preserve opaque task_ref={public_ref!r}. "
                        f"Call manage_orchestration(intent='inspect', task_ref={public_ref!r}) exactly once before any other lifecycle, dispatch, or result-read call. "
                        "Treat the returned context_handoff, current pipeline, AttemptResult refs, and relative step as authoritative. "
                        "Do not call start_orchestration again, replay completed dispatches, or reconstruct state from the transcript. "
                        "Read a completed predecessor only through read_worker_result and retain its machine read receipt."
                    )
                else:
                    context += (
                        " CONTEXT RECOVERY: the host resumed this task after a context reset or compaction. Preserve the opaque task_ref from the durable task context "
                        "and call manage_orchestration(intent='inspect') exactly once before any other lifecycle, dispatch, or result-read call. "
                        "Treat context_handoff and the current ledger as authoritative; do not restart, replay completed dispatches, or use the raw transcript."
                    )
            print(json.dumps(hook_context(safe["hook"], context), ensure_ascii=False))
            return
    except Exception as exc:
        # Hooks are telemetry-only; never inject untrusted exception text into model context.
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
    print("{}")


def main() -> None:
    try:
        _event = json.load(sys.stdin)
    except Exception:
        print("{}")
    else:
        try:
            _project = project_directory(_event)
            _ledger = root(_event)
            if db_hook_snapshot is None:
                _run(_event, None)
            else:
                with db_hook_snapshot(_ledger) as _snapshot:
                    _run(_event, _snapshot)
        except Exception as _exc:
            print(f"orchestration_hook warning: {type(_exc).__name__}", file=sys.stderr)
            print("{}")


if __name__ == "__main__":
    main()
