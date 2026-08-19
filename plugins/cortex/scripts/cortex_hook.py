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
    # Hooks run in their own short-lived process.  Read the same SQLite
    # ledger as the MCP server instead of keeping a second, JSON-file based
    # view of task state.  The imports intentionally remain independent of
    # the stdio server so an installed hook can still make the conservative
    # decision to emit no context if the runtime is unavailable.
    from cortex_runtime.ledger_db import (
        artifact_path as db_task_artifact_path,
        get_global as db_get_global,
        load_task as db_load_task,
        find_successful_tool_observation as db_find_successful_tool_observation,
        mark_tool_observation_duplicate as db_mark_tool_observation_duplicate,
        record_tool_observation as db_record_tool_observation,
        task_index as db_task_index,
        tool_context_epoch as db_tool_context_epoch,
    )
except (ImportError, RuntimeError):  # pragma: no cover - hook remains fail-open.
    db_task_artifact_path = None
    db_get_global = None
    db_load_task = None
    db_find_successful_tool_observation = None
    db_mark_tool_observation_duplicate = None
    db_record_tool_observation = None
    db_task_index = None
    db_tool_context_epoch = None

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
MAX_CACHEABLE_READ_BYTES = 64 * 1024
CORTEX_START_TOOLS = {"mcp__cortex__start_orchestration", "mcp__cortex__manage_orchestration"}
CORTEX_REPORT_TOOL = "mcp__cortex__read_worker_report"
READ_ONLY_FILE_TOOLS = {"Read", "Grep", "Glob"}
CACHEABLE_FILE_READ_TOOLS = {"Read"}
WORKER_CONTEXT = (
    "You are an internal worker, never user-facing. Stay within delegated ownership and allowed paths; "
    "All internal worker communication, progress updates, Cortex tool arguments, reports, questions, findings, handoffs, and native final responses must be in English. "
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
    "native parent channel, and remain available for the answer. "
    "Never call Cortex lifecycle, pipeline, gate, delegation, or management operations. For a Cortex-managed "
    "dispatch, follow the exact worker identity supplied in that dispatch. Before project work, read only the exact "
    "immutable briefing path supplied by the native bootstrap, verify its read-only mode and SHA-256, and never list "
    "or directly read any other .codex/cortex path except the exact report draft_path later returned by "
    "get_report_template. Include the bootstrap's exact Dispatch briefing reviewed digest "
    "marker in report evidence. If and only if the host filesystem read cannot open that exact path, call public "
    "read_dispatch_briefing with the complete identity/digest tuple from the bootstrap; if its bounded response is incomplete, continue only with its exact next_cursor. You may call public read_worker_report "
    "only for predecessor refs explicitly listed in the dispatch, public worker_question when needed, public get_report_template, and public "
    "record_report after all blocking questions are answered. "
    "For every allowed worker tool, a caller/input/schema validation error or retryable=true result must be corrected "
    "from its diagnostic and retried on the same attempt. Such rejected calls consume no worker attempt and must "
    "never end the worker. Stop only for explicit retryable=false/outcome=blocked or genuinely unavailable exact "
    "identity. get_report_template creates a private temporary JSON "
    "file containing the current skeleton and returns draft_path plus draft_ref. Replace its placeholders, then call "
    "record_report with that same ref. If the sandbox cannot edit it, send a small merge patch or complete replacement "
    "through record_report. An invalid record keeps the file and consumes no worker attempt, so correct the named "
    "diagnostics and retry record_report. The tool validates, persists, and deletes the same file only after successful "
    "commit. After it succeeds, return only REPORT_RECORDED report_ref=<value> plus at most a two-sentence "
    "summary; never paste the report JSON into the parent channel. If exact report identity is absent or a tool "
    "returns an explicit non-retryable blocker, do not invent task, wave, attempt, project, or tool identifiers: "
    "return only the exact error and a short blocker. Use only tools actually available in this worker context and record unavailable capabilities as "
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
    if not session_id or db_get_global is None or db_load_task is None:
        return None
    try:
        payload = db_get_global(ledger, "host_sessions", {})
        if payload.get("schema") != HOST_SESSION_SCHEMA or not isinstance(payload.get("tasks"), dict):
            return None
    except (OSError, ValueError, TypeError):
        return None
    active: list[str] = []
    for raw_task_id, bound_session in payload["tasks"].items():
        task_id = valid_task_id(raw_task_id)
        if not task_id or bound_session != session_id:
            continue
        try:
            loaded = db_load_task(ledger, task_id)
            state = loaded[1] if loaded is not None else {}
        except (OSError, ValueError, TypeError):
            continue
        if state.get("schema") == SCHEMA and state.get("task_id") == task_id and state.get("status") in {"active", "blocked"}:
            active.append(task_id)
    return active[0] if len(active) == 1 else None


def pending_task_from_subagent_start(ledger: Path, event: dict) -> str | None:
    """Recover one exact pending dispatch when the start-tool hook was skipped.

    Codex treats changed Pre/PostToolUse hooks as untrusted until their content
    hashes are approved.  SubagentStart may still be trusted and delivered. In
    that case the native start event is stronger evidence than coordinator
    prose: bind only when exactly one active task has an awaiting dispatch that
    matches the native task key, or (for hosts reporting ``default``) the
    observed model. Ambiguity fails closed.
    """
    if str(event.get("hook_event_name")) != "SubagentStart":
        return None
    agent_type = str(event.get("agent_type") or "").strip()
    model = str(event.get("model") or "").strip()
    if not agent_type:
        return None
    if db_task_index is None or db_load_task is None:
        return None
    try:
        index = db_task_index(ledger)
    except (OSError, ValueError, TypeError):
        return None
    matches: list[str] = []
    for raw_task_id in index if isinstance(index, dict) else {}:
        task_id = valid_task_id(raw_task_id)
        if not task_id:
            continue
        try:
            loaded = db_load_task(ledger, task_id)
            state = loaded[1] if loaded is not None else {}
        except (OSError, ValueError, TypeError):
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
                if str((attempt.get("spawn_request") or {}).get("task_name") or "") == agent_type
            ]
        if len(candidates) == 1:
            matches.append(task_id)
    return matches[0] if len(matches) == 1 else None


def task_ref(ledger: Path, task_id: str) -> str | None:
    """Resolve the public opaque ref without guessing from a task id."""
    if db_get_global is None:
        return None
    try:
        payload = db_get_global(ledger, "operation_registry", {})
        record = payload.get("tasks", {}).get(task_id) if isinstance(payload, dict) else None
        candidate = record.get("start", {}).get("task_ref") if isinstance(record, dict) else None
        candidate = str(candidate or "")
        return candidate if SAFE_ID_RE.fullmatch(candidate) else None
    except (OSError, ValueError, TypeError):
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
    }


def tool_call_failed(event: dict) -> bool:
    """Treat missing or explicitly error-shaped post-tool results as failures."""
    response = event.get("tool_response")
    if response is None:
        return True
    if isinstance(response, dict):
        return bool(response.get("is_error") or response.get("isError") or response.get("error"))
    return False


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


def context_epoch_for_tool_observation(ledger: Path, task_id: str, event: dict) -> int:
    """Use a durable epoch, rolling it on an explicitly reported compaction."""
    if db_tool_context_epoch is None:
        return 0
    try:
        supplied = event.get("context_epoch", event.get("contextEpoch"))
        if isinstance(supplied, int) and not isinstance(supplied, bool) and supplied >= 0:
            return supplied
        return int(db_tool_context_epoch(ledger, task_id, bump=is_context_recovery(event)))
    except (OSError, ValueError, TypeError):
        return 0


def apply_tool_deduplication(
    event: dict, project: Path, ledger: Path, task_id: str, state: dict,
) -> dict | None:
    """Persist observations and deny only proven duplicate small full reads."""
    if db_record_tool_observation is None:
        return None
    attempt_id = attempt_for_tool_observation(event, state)
    epoch = context_epoch_for_tool_observation(ledger, task_id, event)
    observation = tool_observation(event, project, task_id, attempt_id, epoch)
    if observation is None:
        return None
    hook_name = str(event.get("hook_event_name") or "")
    if hook_name == "PreToolUse" and observation["cacheable"]:
        try:
            already_read = db_find_successful_tool_observation(
                ledger, task_id, attempt_id, epoch, observation["fingerprint"], observation["workspace_generation"],
            )
            if already_read:
                db_mark_tool_observation_duplicate(ledger, task_id, attempt_id, epoch, observation["fingerprint"])
                return {"duplicate": True, "tool_name": observation["tool_name"]}
        except (OSError, ValueError, TypeError):
            return None
    elif hook_name == "PostToolUse":
        result_digest = _bounded_digest(event.get("tool_response"))
        try:
            db_record_tool_observation(
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


def report_publication_context(event: dict) -> str | None:
    if str(event.get("hook_event_name")) != "PostToolUse" or str(event.get("tool_name")) != CORTEX_REPORT_TOOL:
        return None
    result = structured_tool_result(event)
    if not isinstance(result, dict) or result.get("publication_required") is not True:
        return None
    link = str(result.get("report_markdown_link") or "") if isinstance(result, dict) else ""
    if not link or len(link) > 4096 or "\n" in link or not link.startswith("["):
        return None
    completion = result.get("completion_update")
    if not isinstance(completion, dict):
        return None
    summary = str(completion.get("summary") or "").strip()
    next_step = str(completion.get("next") or "").strip()
    if not summary or not next_step or len(summary) > 1000 or len(next_step) > 1000:
        return None
    return (
        "REPORT COMPLETION PUBLICATION REQUIRED: the native subagent durably completed and this is the one allowed "
        "publication. In one main-chat message, briefly explain in the user's language what completed using only "
        f"this bounded summary ({summary}), what happens next using this bounded next-step basis ({next_step}), and "
        f"include this exact report_markdown_link once: {link}. Never publish the link alone or on a later reread."
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
        "CORTEX DISPATCH FAILURE: the native Agent wait had no child target. No worker was spawned. Do not claim "
        "dispatch success, do not advance Cortex, and do not return task success. Invoke the exact pending "
        "spawn_agent dispatch now; if spawn_agent is unavailable or fails to return a child id, stop and report that "
        "host blocker. Never retry an empty wait."
    )


def task_directory(ledger: Path, task_id: str) -> Path:
    """Resolve the registered artifact directory from SQLite, never by scan."""
    if db_task_artifact_path is None:
        return ledger / "tasks" / f"missing-{task_id}"
    try:
        candidate = db_task_artifact_path(ledger, task_id)
        # A task's artifact directory is deliberately lazy.  SQLite owns the
        # path before any projection exists, and a lifecycle event is one of
        # the few operations allowed to materialize it.  Do not require the
        # directory to exist here: that would redirect the hook to a bogus
        # ``missing-*`` path and silently suppress telemetry for new tasks.
        if candidate is not None:
            return reject_symlink_ancestry(candidate, "task directory")
    except (OSError, ValueError):
        pass
    return ledger / "tasks" / f"missing-{task_id}"


def activation(ledger: Path, session_id: str | None) -> dict | None:
    if not session_id or db_get_global is None:
        return None
    try:
        record = db_get_global(ledger, "activations", {}).get(session_id)
        if isinstance(record, dict) and record.get("schema") == SCHEMA and record.get("coordinator") == "main" and record.get("mode") == "main-orchestrator":
            return record
        return None
    except (OSError, ValueError):
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
    """Reassert bounded recovery after a reportless native stop."""
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
    stopped_attempts = [
        item for item in attempts
        if item.get("host_stop_outcome") == "native_worker_stopped_without_report"
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
    inspect = (
        f"Call manage_orchestration(intent='inspect', task_ref={public_task_ref!r}) once"
        if public_task_ref
        else "Call manage_orchestration(intent='inspect') once with the preserved task_ref"
    )
    return (
        f"CORTEX WAIT RECOVERY: attempt {attempt_id!r} stopped without a report and is terminal failed "
        f"(dispatch_ref={dispatch_ref!r}, reason='native_worker_stopped_without_report'). Do not wait on, respawn, "
        f"or follow up the stopped native worker (agent_id={host_agent_id!r}, task_name={host_task_name!r}). "
        f"{inspect}; then submit exactly one result with status='failed', this dispatch_ref, and this reason so Cortex can apply its "
        "unbounded corrective policy and raise effort after repeated failures."
    )


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
        if not task_id and session_id and bind_host_session_from_hook is not None:
            recovered_task_id = pending_task_from_subagent_start(ledger, event)
            recovered_ref = task_ref(ledger, recovered_task_id) if recovered_task_id else None
            if recovered_ref:
                bind_host_session_from_hook(str(project), recovered_ref, session_id)
                task_id = active_task(ledger, session_id)
    except Exception as exc:
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
        print("{}")
        return
    if not task_id:
        print("{}")
        return
    task_dir = task_directory(ledger, task_id)
    try:
        task_dir = reject_symlink_ancestry(task_dir, "task directory")
        if db_load_task is None:
            raise RuntimeError("SQLite ledger runtime is unavailable")
        loaded = db_load_task(ledger, task_id)
        state = loaded[1] if loaded is not None else {}
        if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            raise ValueError("unsupported or mismatched task state")
        if (
            str(event.get("hook_event_name")) == "SessionStart"
            and is_context_recovery(event)
            and db_tool_context_epoch is not None
        ):
            # A host-reported compact/clear is a hard cache boundary even if
            # it does not carry a numeric context epoch itself.
            db_tool_context_epoch(ledger, task_id, bump=True)
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
                    refreshed = db_load_task(ledger, task_id)
                    state = refreshed[1] if refreshed is not None else {}
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
                refreshed = db_load_task(ledger, task_id)
                state = refreshed[1] if refreshed is not None else {}
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
        dedupe = apply_tool_deduplication(event, project, ledger, task_id, state)
        if dedupe and dedupe.get("duplicate") and safe["hook"] == "PreToolUse":
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "CORTEX TOOL DEDUPLICATION: an unchanged small full-file read already succeeded in this "
                        "context epoch. Reuse the prior result; do not retry this read."
                    ),
                }
            }, ensure_ascii=False))
            return
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
                        "Publish a report link only when read_worker_report returns publication_required=true; include "
                        "its completion summary and next-step explanation in the same message, and never republish on reread."
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
