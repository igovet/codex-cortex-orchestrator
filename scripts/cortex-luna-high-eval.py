#!/usr/bin/env python3
"""Fixture and optional live evaluation for a Luna-high Cortex parent."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import selectors
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "plugins/cortex/scripts/cortex.py"
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
import cortex  # noqa: E402


LIVE_TIMEOUT_SECONDS = 1800
HEARTBEAT_SECONDS = 15
TERMINATION_GRACE_SECONDS = 10
MAX_STREAM_LINE_BYTES = 64 * 1024
MAX_RETAINED_STREAM_EVENTS = 512
MAX_EMITTED_STREAM_EVENTS = 512
MAX_AUTH_FILE_BYTES = 1024 * 1024
AUTH_ENVIRONMENT_VARIABLES = ("OPENAI_API_KEY", "CODEX_API_KEY")
SAFE_RUNTIME_ENVIRONMENT_VARIABLES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
SAFE_TOOL_NAMES = {
    "start_orchestration", "continue_orchestration", "manage_orchestration",
    "read_worker_report", "record_report", "worker_question", "read_dispatch_briefing",
}
SAFE_NATIVE_TOOL_NAMES = {
    "spawn_agent", "wait", "send_message", "followup_task", "interrupt_agent", "list_agents", "close_agent",
}
SAFE_LEDGER_EVENTS = {
    "delegation", "orchestrate_wave", "worker_report", "attempt", "evidence", "gate",
    "task_started", "task_completed", "task_blocked", "task_resumed",
}
SAFE_GATE_NAMES = {
    "scope", "plan", "discover", "architecture", "implementation", "qa", "security", "performance",
    "accessibility", "ux", "review", "documentation", "close",
}
SAFE_TASK_STATUSES = {"active", "blocked", "completed", "failed", "unknown"}
SAFE_ATTEMPT_STATUSES = {
    "awaiting_host_spawn", "running", "passed", "failed", "blocked", "idle_resumable",
    "stopped_recoverable", "terminated_unavailable", "completed", "unknown",
}
SAFE_SESSION_STATUSES = {
    "awaiting_spawn", "running", "idle_resumable", "stopped_recoverable",
    "terminated_unavailable", "completed", "unknown",
}
SAFE_NATIVE_AGENT_STATUSES = {
    "pending", "running", "completed", "failed", "cancelled", "shut_down", "unknown",
}

RESULT_FAILURE_PATTERNS = (
    ("passed completion requires report_ref", "reportless_success"),
    ("non-success completion requires an explicit reason", "missing_failure_reason"),
    ("advance requires a non-empty completions array", "empty_results"),
    ("advance completion attempt_ids must be unique", "duplicate_attempt_result"),
    ("advance contains attempts outside the active wave", "wrong_attempt_result"),
    ("advance is missing completions for", "missing_attempt_result"),
    ("dispatch_ref", "dispatch_identity"),
    ("unanswered blocking worker question", "open_worker_question"),
    ("report_ref does not belong", "wrong_report_ref"),
    ("report_validation_failed", "report_validation"),
)
NATIVE_TERMINAL_PATTERNS = (
    ("report_validation_failed", "report_validation_failed"),
    ("report_evidence_incomplete", "report_evidence_incomplete"),
    ("report_identity_invalid", "report_identity_invalid"),
    ("report_changed_files_invalid", "artifact_delta_error"),
    ("worker_verification_failed", "test_evidence_error"),
    ("dispatch_briefing_invalid", "dispatch_briefing_error"),
    ("worker_output_language_violation", "output_language_error"),
    ("blocking_question_open", "open_worker_question"),
    ("unresolved_report_questions", "open_worker_question"),
    ("intent_clarification_required", "open_worker_question"),
    ("dispatch briefing", "dispatch_briefing_error"),
    ("briefing digest", "dispatch_briefing_error"),
    ("briefing_digest", "dispatch_briefing_error"),
    ("gate acceptance", "evidence_marker_error"),
    ("gate verification", "evidence_marker_error"),
    ("predecessor review", "evidence_marker_error"),
    ("knowledge reviewed", "evidence_marker_error"),
    ("changed_files", "artifact_delta_error"),
    ("read-only result gate", "artifact_delta_error"),
    ("executed check", "test_evidence_error"),
    ("report.tests", "test_evidence_error"),
    ("mcp", "mcp_access_error"),
    ("permission", "filesystem_access_error"),
    ("unreadable", "filesystem_access_error"),
    ("not found", "filesystem_access_error"),
    ("record_report", "record_report_error"),
)


def configured_codex_home() -> Path:
    """Resolve the current private Codex home without reading its contents."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def copy_private_regular_file(source: Path, destination: Path) -> None:
    """Copy a credential file through no-follow descriptors with strict permissions."""
    source_info = os.lstat(source)
    if (
        not stat.S_ISREG(source_info.st_mode)
        or source_info.st_mode & 0o077
        or source_info.st_size > MAX_AUTH_FILE_BYTES
    ):
        raise RuntimeError("Codex authentication source must be a regular non-symlink file")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | nofollow)
    destination_fd: int | None = None
    completed = False
    try:
        opened_source = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_source.st_mode)
            or opened_source.st_mode & 0o077
            or opened_source.st_size > MAX_AUTH_FILE_BYTES
            or (opened_source.st_dev, opened_source.st_ino) != (source_info.st_dev, source_info.st_ino)
        ):
            raise RuntimeError("Codex authentication source changed while being opened")
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600,
        )
        copied = 0
        while True:
            block = os.read(source_fd, 64 * 1024)
            if not block:
                break
            copied += len(block)
            if copied > MAX_AUTH_FILE_BYTES:
                raise RuntimeError("Codex authentication source exceeds the evaluator size limit")
            remaining = memoryview(block)
            while remaining:
                written = os.write(destination_fd, remaining)
                if written <= 0:
                    raise OSError("unable to write private Codex authentication file")
                remaining = remaining[written:]
        os.fchmod(destination_fd, 0o600)
        completed = True
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)
        if not completed and destination.exists() and not destination.is_symlink():
            destination.unlink()


def remove_private_runtime_home(runtime_home: Path, base: Path) -> None:
    """Remove only the evaluator-created, non-symlink runtime directory."""
    if runtime_home.parent != base or runtime_home.is_symlink():
        raise RuntimeError("refusing to remove an unexpected Codex runtime directory")
    information = os.lstat(runtime_home)
    if not stat.S_ISDIR(information.st_mode):
        raise RuntimeError("refusing to remove a non-directory Codex runtime path")
    shutil.rmtree(runtime_home)


@contextlib.contextmanager
def isolated_codex_runtime(base: Path):
    """Provide an ephemeral 0700 Codex home without loading global configuration."""
    base_info = os.lstat(base)
    if not stat.S_ISDIR(base_info.st_mode) or base.is_symlink():
        raise RuntimeError("evaluator base must be a non-symlink directory")
    runtime_home = Path(tempfile.mkdtemp(prefix="cortex-luna-high-codex-", dir=base))
    os.chmod(runtime_home, 0o700)
    runtime_tmp = runtime_home / "tmp"
    runtime_cache = runtime_home / "cache"
    runtime_config = runtime_home / "config"
    runtime_data = runtime_home / "data"
    runtime_state = runtime_home / "state"
    for directory in (runtime_tmp, runtime_cache, runtime_config, runtime_data, runtime_state):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    environment = {
        name: value
        for name in SAFE_RUNTIME_ENVIRONMENT_VARIABLES
        if (value := os.environ.get(name))
    }
    environment["HOME"] = str(runtime_home)
    environment["CODEX_HOME"] = str(runtime_home)
    environment["TMPDIR"] = str(runtime_tmp)
    environment["XDG_CACHE_HOME"] = str(runtime_cache)
    environment["XDG_CONFIG_HOME"] = str(runtime_config)
    environment["XDG_DATA_HOME"] = str(runtime_data)
    environment["XDG_STATE_HOME"] = str(runtime_state)
    try:
        environment_auth = next(
            (name for name in AUTH_ENVIRONMENT_VARIABLES if os.environ.get(name)), None,
        )
        if environment_auth is not None:
            environment[environment_auth] = os.environ[environment_auth]
        else:
            source = configured_codex_home() / "auth.json"
            copy_private_regular_file(source, runtime_home / "auth.json")
        yield environment
    finally:
        if runtime_home.exists() or runtime_home.is_symlink():
            remove_private_runtime_home(runtime_home, base)


def emit_live_progress(scenario: str, event: str, **metadata: object) -> None:
    """Stream one machine-readable, privacy-preserving live-eval progress event."""
    payload = {"type": "cortex_live_progress", "scenario": scenario, "event": event, **metadata}
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def safe_tool_name(value: object) -> str:
    """Keep only public Cortex lifecycle names from an untrusted host event."""
    candidate = str(value or "").rsplit("__", 1)[-1]
    return candidate if candidate in SAFE_TOOL_NAMES else "other"


def safe_status(value: object, allowed: set[str]) -> str:
    candidate = str(value or "unknown")
    return candidate if candidate in allowed else "unknown"


def structured_ok(value: object) -> bool | None:
    """Read an ``ok`` flag without retaining arbitrary response content."""
    queue = [value]
    visited = 0
    while queue and visited < 32:
        item = queue.pop(0)
        visited += 1
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("ok"), bool):
            return item["ok"]
        for key in ("structuredContent", "structured_content", "result"):
            if key in item:
                queue.append(item[key])
    return None


def classified_result_failure(value: object) -> str | None:
    """Classify a known lifecycle rejection without retaining response text."""
    queue = [value]
    visited = 0
    while queue and visited < 64:
        item = queue.pop(0)
        visited += 1
        if isinstance(item, dict):
            queue.extend(item.values())
        elif isinstance(item, (list, tuple)):
            queue.extend(item)
        elif isinstance(item, str):
            lowered = item.lower()
            for pattern, category in RESULT_FAILURE_PATTERNS:
                if pattern in lowered:
                    return category
    return None


def classified_native_outcome(value: object) -> str | None:
    """Return only a safe durable-result class from native agent state."""
    if not isinstance(value, dict):
        return None
    saw_message = False
    for agent in value.values():
        if not isinstance(agent, dict):
            continue
        message = str(agent.get("message") or "").strip()
        if not message:
            continue
        saw_message = True
        if message.startswith("REPORT_RECORDED report_ref="):
            return "report_recorded"
        if message.startswith("QUESTION_RECORDED question_ref="):
            return "question_recorded"
        lowered = message.lower()
        for pattern, category in NATIVE_TERMINAL_PATTERNS:
            if pattern in lowered:
                return category
    return "other_terminal_message" if saw_message else None


def sanitize_codex_stream_line(line: str) -> dict[str, object]:
    """Classify a Codex JSON event while never returning its text or arguments."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"event": "host_output", "format": "non_json"}
    if not isinstance(payload, dict):
        return {"event": "host_output", "format": "other"}
    item = payload.get("item")
    if not isinstance(item, dict):
        event_type = str(payload.get("type") or "")
        if event_type in {"thread.started", "thread.completed"}:
            return {"event": "parent_started" if event_type.endswith("started") else "parent_completed"}
        if event_type.startswith("turn."):
            return {"event": "parent_turn", "status": "started" if event_type.endswith("started") else "completed"}
        return {"event": "host_event"}
    item_type = str(item.get("type") or "")
    if item_type == "collab_tool_call":
        tool = str(item.get("tool") or "")
        safe_native: dict[str, object] = {
            "event": "native_tool_call",
            "tool": tool if tool in SAFE_NATIVE_TOOL_NAMES else "other",
            "status": safe_status(item.get("status"), {"started", "in_progress", "completed", "failed"}),
        }
        states = item.get("agents_states")
        native_outcome = classified_native_outcome(states)
        if native_outcome is not None:
            safe_native["outcome"] = native_outcome
        if isinstance(states, dict):
            statuses: dict[str, int] = {}
            for agent in states.values():
                if not isinstance(agent, dict):
                    continue
                status = safe_status(agent.get("status"), SAFE_NATIVE_AGENT_STATUSES)
                statuses[status] = statuses.get(status, 0) + 1
            if statuses:
                safe_native["agent_statuses"] = statuses
        return safe_native
    if item_type not in {"mcp_tool_call", "tool_call"}:
        if item_type in {"agent_message", "reasoning", "message"}:
            return {"event": "parent_activity", "kind": "model"}
        if item_type in {"command_execution", "function_call"}:
            return {"event": "parent_activity", "kind": "host_tool"}
        return {"event": "parent_activity", "kind": "other"}
    status = str(item.get("status") or "unknown")
    result = item.get("result")
    safe: dict[str, object] = {
        "event": "cortex_mcp_call",
        "tool": safe_tool_name(item.get("tool") or item.get("name")),
        "status": status if status in {"started", "in_progress", "completed", "failed"} else "unknown",
    }
    if safe["status"] in {"completed", "failed"}:
        ok = structured_ok(result)
        if ok is not None:
            safe["ok"] = ok
        if ok is False:
            failure_class = classified_result_failure(result)
            if failure_class is not None:
                safe["failure_class"] = failure_class
    return safe


def safe_ledger_progress(project: Path) -> dict[str, object] | None:
    """Return bounded aggregate lifecycle state from a source-mode SQLite ledger."""
    database = project / ".codex" / "cortex" / "cortex.db"
    if not database.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        rows = connection.execute("SELECT status, state_json FROM tasks").fetchall()
        task_statuses: dict[str, int] = {}
        attempt_statuses: dict[str, int] = {}
        gates: dict[str, int] = {}
        for status, state_json in rows:
            task_status = safe_status(status, SAFE_TASK_STATUSES)
            task_statuses[task_status] = task_statuses.get(task_status, 0) + 1
            try:
                state = json.loads(str(state_json))
            except (TypeError, json.JSONDecodeError):
                state = {}
            for attempt in state.get("attempts", []) if isinstance(state, dict) else []:
                if not isinstance(attempt, dict):
                    continue
                attempt_status = safe_status(attempt.get("status"), SAFE_ATTEMPT_STATUSES)
                attempt_statuses[attempt_status] = attempt_statuses.get(attempt_status, 0) + 1
                gate = str(attempt.get("gate") or "")
                if gate in SAFE_GATE_NAMES:
                    gates[gate] = gates.get(gate, 0) + 1
        report_count = connection.execute(
            "SELECT COUNT(*) FROM logical_artifacts WHERE kind='worker_report'"
        ).fetchone()[0]
        session_rows = connection.execute(
            "SELECT status, COUNT(*) FROM worker_sessions GROUP BY status"
        ).fetchall()
        sessions: dict[str, int] = {}
        for status, count in session_rows:
            session_status = safe_status(status, SAFE_SESSION_STATUSES)
            sessions[session_status] = sessions.get(session_status, 0) + int(count)
        latest = connection.execute(
            "SELECT event FROM ledger_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        latest_event = str(latest[0]) if latest and str(latest[0]) in SAFE_LEDGER_EVENTS else "none"
        return {
            "tasks": len(rows), "task_statuses": task_statuses,
            "attempt_statuses": attempt_statuses, "gates": gates,
            "worker_reports": int(report_count), "worker_sessions": sessions,
            "latest_ledger_event": latest_event,
        }
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        if connection is not None:
            connection.close()


def terminate_process_group(process: subprocess.Popen[str], scenario: str, reason: str) -> None:
    """Terminate and reap a live evaluator process group without leaking descendants."""
    process_group = process.pid
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        return
    emit_live_progress(scenario, "termination_requested", reason=reason)
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        emit_live_progress(scenario, "termination_escalated", reason=reason)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    emit_live_progress(scenario, "terminated", reason=reason, exit_code=process.returncode)


def run_live_command(
    command: list[str], project: Path, scenario: str, *, timeout_seconds: int,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    """Stream sanitized Codex progress while supervising its complete process group."""
    started = time.monotonic()
    last_activity = started
    last_activity_kind = "launch"
    events: list[dict[str, object]] = []
    dropped_events = 0
    emitted_stream_events = 0
    suppression_emitted = False
    interrupted: dict[str, int | None] = {"signal": None}

    def request_termination(signum: int, _frame: object) -> None:
        interrupted["signal"] = signum

    previous_term = signal.signal(signal.SIGTERM, request_termination)
    previous_int = signal.signal(signal.SIGINT, request_termination)
    process: subprocess.Popen[str] | None = None
    selector: selectors.BaseSelector | None = None
    next_heartbeat = started + HEARTBEAT_SECONDS
    last_ledger_signature = ""
    termination_reason: str | None = None

    def retain_event(event: dict[str, object]) -> None:
        nonlocal dropped_events
        if len(events) == MAX_RETAINED_STREAM_EVENTS:
            events.pop(0)
            dropped_events += 1
        events.append(event)

    def emit_stream_event(event: dict[str, object], elapsed_seconds: int) -> None:
        nonlocal emitted_stream_events, suppression_emitted
        if emitted_stream_events < MAX_EMITTED_STREAM_EVENTS:
            emitted_stream_events += 1
            emit_live_progress(scenario, str(event["event"]), elapsed_seconds=elapsed_seconds, **{
                key: value for key, value in event.items() if key != "event"
            })
            return
        if not suppression_emitted:
            suppression_emitted = True
            emit_live_progress(scenario, "host_events_suppressed", elapsed_seconds=elapsed_seconds)

    def handle_stream_line(source: str, line: str) -> None:
        """Retain and emit only a bounded, sanitized representation of one line."""
        nonlocal last_activity, last_activity_kind
        now = time.monotonic()
        if source == "stdout":
            safe_event = (
                {"event": "host_output", "format": "oversized"}
                if len(line.encode("utf-8", errors="ignore")) > MAX_STREAM_LINE_BYTES
                else sanitize_codex_stream_line(line)
            )
            retain_event(safe_event)
            last_activity, last_activity_kind = now, str(safe_event.get("event") or "host")
            emit_stream_event(safe_event, int(now - started))
            return
        # Preserve observability without exposing arbitrary host diagnostics.
        last_activity, last_activity_kind = now, "stderr"
        safe_event = {"event": "host_stderr"}
        retain_event(safe_event)
        emit_stream_event(safe_event, int(now - started))

    try:
        process = subprocess.Popen(
            command, cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=1, start_new_session=True, env=environment,
        )
        emit_live_progress(scenario, "parent_started", elapsed_seconds=0)
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if interrupted["signal"] is not None:
                termination_reason = f"signal_{interrupted['signal']}"
            elif now - started >= timeout_seconds:
                termination_reason = "timeout"
            if termination_reason:
                terminate_process_group(process, scenario, termination_reason)
                break
            ledger = safe_ledger_progress(project)
            if ledger is not None:
                signature = json.dumps(ledger, sort_keys=True, separators=(",", ":"))
                if signature != last_ledger_signature:
                    last_ledger_signature = signature
                    last_activity, last_activity_kind = now, "ledger"
                    emit_live_progress(scenario, "ledger_progress", elapsed_seconds=int(now - started), **ledger)
            if now >= next_heartbeat:
                emit_live_progress(
                    scenario, "heartbeat", elapsed_seconds=int(now - started),
                    last_activity=last_activity_kind, last_activity_seconds_ago=int(now - last_activity),
                    process_running=process.poll() is None,
                )
                next_heartbeat = now + HEARTBEAT_SECONDS
            for key, _mask in selector.select(timeout=1):
                stream = key.fileobj
                line = stream.readline(MAX_STREAM_LINE_BYTES + 1)
                if not line:
                    selector.unregister(stream)
                    continue
                handle_stream_line(str(key.data), line)
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
    finally:
        try:
            if selector is not None:
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                selector.close()
            if process is not None:
                # The direct Codex parent can exit before an inherited pipe holder.
                # Always probe and clean the known session/process group first.
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    process_group_alive = False
                else:
                    process_group_alive = True
                if process_group_alive or process.poll() is None:
                    terminate_process_group(process, scenario, termination_reason or "cleanup")
                # Process-group cleanup closes descendants' inherited descriptors. Drain
                # the remaining buffered lines before closing our own file objects so
                # their sanitized lifecycle events are not silently lost on timeout.
                for stream, source in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    if stream is None or stream.closed:
                        continue
                    while True:
                        line = stream.readline(MAX_STREAM_LINE_BYTES + 1)
                        if not line:
                            break
                        handle_stream_line(source, line)
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            # The handlers remain installed until process-group and pipe cleanup
            # finishes, preventing a signal from skipping the reap/close sequence.
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    if process is None:
        raise RuntimeError("live evaluator failed to create its parent process")
    elapsed = int(time.monotonic() - started)
    emit_live_progress(
        scenario, "parent_finished", elapsed_seconds=elapsed, exit_code=process.returncode,
        termination=termination_reason,
    )
    return {
        "returncode": process.returncode, "events": events, "elapsed_seconds": elapsed,
        "termination": termination_reason, "dropped_stream_events": dropped_events,
    }


def workspace_summary(project: Path) -> dict[str, object]:
    """Describe the actual fixture workspace without treating it as a blocker."""
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return {"modified": [], "untracked": [], "staged": [], "committed": "not_required"}
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
            continue
        if len(line) < 4:
            continue
        index, worktree, path = line[0], line[1], line[3:]
        if index != " ":
            staged.append(path)
        if worktree != " ":
            modified.append(path)
    has_changes = bool(modified or untracked or staged)
    return {
        "modified": sorted(modified),
        "untracked": sorted(untracked),
        "staged": sorted(staged),
        "committed": False if has_changes else "not_required",
    }


def passing_closure(project: Path, gate: str) -> dict[str, object]:
    """Build a valid no-blocker closure for deterministic fixture reports."""
    return {
        "decision": "pass",
        "findings": [],
        "verification": {
            "executed": [f"Deterministic Luna-high fixture verification completed for the {gate} gate."],
            "not_executed": [],
            "required_missing": [],
            "limitations": [],
        },
        "workspace": workspace_summary(project),
    }


def canonical_artifacts(
    ledger: Path, task_id: str, *, kind: str | None = None
) -> list[dict[str, object]]:
    """List canonical SQLite artifacts without relying on local projections."""
    artifacts: list[dict[str, object]] = []
    offset = 0
    while True:
        page, next_offset = cortex.db_list_artifacts(
            ledger, task_id, kind=kind, offset=offset, page_size=100,
        )
        artifacts.extend(page)
        if next_offset is None:
            return artifacts
        offset = next_offset


def report(label: str, project: Path, changed_files: list[str] | None = None) -> dict[str, object]:
    return {
        "summary": label, "findings": [], "questions": [], "changed_files": changed_files or [],
        "tests": [{
            "command": "python3 -c 'print(\"luna-high fixture\")'",
            "cwd": str(project),
            "exit_code": 0,
            "evidence": "The deterministic fixture command printed luna-high fixture and exited zero.",
        }],
        "evidence": [label], "uncertainty": [],
    }


def task(user_request: str, complexity: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "user_request": user_request,
        "acceptance_criteria": ["The requested fixture lifecycle completes with a verified handoff."],
        "verification": ["Run and record the deterministic fixture check through the close gate."],
    }
    if complexity is not None:
        value["complexity"] = complexity
    return value


def planning(label: str) -> dict[str, object]:
    return {
        "overview": f"Deterministic work breakdown for {label}.",
        "work_packages": [{
            "id": "fixture_core",
            "title": "Complete fixture lifecycle",
            "objective": "Exercise the current durable report and close contract.",
            "allowed_paths": ["."],
            "microtasks": [{
                "id": "fixture_verify",
                "title": "Verify fixture lifecycle",
                "objective": "Record a complete, reproducible fixture result.",
                "profile": "backend_dev",
                "allowed_paths": ["."],
                "acceptance_criteria": ["The fixture result is recorded."],
                "verification": ["Run the deterministic fixture command."],
            }],
        }],
    }


def scoping(label: str) -> dict[str, object]:
    return {
        "overview": f"Deterministic discovery brief for {label}.",
        "context_files": [],
        "discovery_domains": [{
            "id": "fixture_runtime",
            "title": "Fixture runtime",
            "objective": "Trace the deterministic fixture lifecycle and evidence boundaries.",
            "paths": ["."],
            "context": ["Current source, tests, and executable fixture configuration are authoritative."],
            "depends_on": [],
            "acceptance_criteria": ["The discovery report maps the complete fixture lifecycle."],
            "verification": ["Confirm the lifecycle against current fixture source and tests."],
        }],
    }


def finish(project: Path, current: dict[str, object]) -> dict[str, object]:
    if not current.get("ok"):
        raise AssertionError(current)
    while current.get("outcome") != "completed":
        if current.get("outcome") == "awaiting_plan_approval":
            prompt = cortex.manage_orchestration({
                "project_root": str(project),
                "task_ref": current["task_ref"],
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
            interaction = prompt.get("plan_approval_interaction") or {}
            approve = next((action for action in interaction.get("actions", []) if action.get("id") == "approve"), None)
            if not isinstance(approve, dict) or not isinstance(approve.get("arguments"), dict):
                raise AssertionError(f"plan approval interaction omitted its Approve action: {prompt}")
            current = cortex.manage_orchestration(approve["arguments"])
            if not current.get("ok"):
                raise AssertionError(current)
            continue
        dispatches = current.get("dispatches") or []
        parallel = len(dispatches) > 1
        ledger = cortex.ledger_root({"project_root": str(project)})
        registry = cortex._operation_registry(ledger)
        task_id = next(
            candidate for candidate, record in registry["tasks"].items()
            if record.get("start", {}).get("task_ref") == current["task_ref"]
        )
        task_dir, state, _ = cortex._v3_task_state(ledger, task_id)
        task_definition = cortex.load_task_definition(task_dir, state)
        active_attempts = [
            item for item in state["attempts"]
            if item.get("status") not in cortex.TERMINAL_ATTEMPT_STATUSES
            and item.get("gate") in cortex.active_gates(state)
        ][-len(dispatches):]
        results = []
        for worker, (dispatch, attempt) in enumerate(zip(dispatches, active_attempts), 1):
            label = f"step {current['step']} worker {worker}"
            changed_files: list[str] = []
            if attempt.get("gate") == "implementation":
                (project / "result.md").write_text("Verified Luna-high fixture result.\n", encoding="utf-8")
                changed_files = ["result.md"]
            worker_report = report(label, project, changed_files)
            evidence = worker_report["evidence"]
            predecessor_reports = list(attempt.get("context_report_ids") or [])
            if predecessor_reports:
                evidence.append("Predecessor review: " + ", ".join(predecessor_reports))
            for index, _criterion in enumerate(attempt.get("acceptance_criteria") or [], 1):
                evidence.append(f"Gate acceptance {index}: PASS - Deterministic fixture lifecycle produced the required recorded result.")
            for index, _criterion in enumerate(attempt.get("verification") or [], 1):
                evidence.append(f"Gate verification {index}: PASS - Exact deterministic fixture command completed with exit code zero.")
            if attempt.get("gate") == "close":
                for index, _criterion in enumerate(task_definition.get("acceptance_criteria") or [], 1):
                    evidence.append(f"Task acceptance {index}: PASS - Completed fixture lifecycle produced a durable verified handoff.")
                for index, _criterion in enumerate(task_definition.get("verification") or [], 1):
                    evidence.append(f"Task verification {index}: PASS - Final deterministic fixture check completed with exit code zero.")
            evidence.append("Dispatch briefing reviewed: " + str(attempt["briefing_digest"]))
            publication: dict[str, object] = {
                "project_root": str(project),
                "task_id": state["task_id"],
                "attempt_id": attempt["attempt_id"],
                "profile": dispatch["profile"],
                "report": worker_report,
            }
            if attempt.get("gate") in {"review", "close"}:
                publication["closure"] = passing_closure(project, str(attempt["gate"]))
            if attempt.get("gate") == "plan":
                publication["planning"] = planning(label)
            if attempt.get("gate") == "scope":
                publication["scoping"] = scoping(label)
            published = cortex.publish_worker_report(publication)
            if not published.get("ok"):
                raise AssertionError(published)
            value: dict[str, object] = {"report_ref": published["report_ref"]}
            if parallel:
                value["worker"] = worker
            results.append(value)
        current = cortex.continue_orchestration({
            "project_root": str(project), "task_ref": current["task_ref"],
            "step": current["step"], "results": results,
        })
        if not current.get("ok"):
            raise AssertionError(current)
    return current


def fixture_eval(base: Path) -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []

    sequential = base / "sequential"
    sequential.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(sequential), "task": task("sequential Luna fixture"),
    })
    completed = finish(sequential, current)
    scenarios.append({"name": "automatic_sequential", "outcome": completed["outcome"]})

    parallel = base / "parallel"
    parallel.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(parallel),
        "task": {**task("parallel Luna fixture", "standard"), "plan_approval": "auto"},
        "waves": [{"workers": [{"phase": "research"}, {"phase": "discover", "profile": "explorer"}]}],
    })
    if len(current.get("dispatches") or []) != 2:
        raise AssertionError("parallel fixture did not return two relative worker slots")
    completed = finish(parallel, current)
    scenarios.append({"name": "compact_parallel", "outcome": completed["outcome"]})

    blocked = base / "blocked"
    blocked.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(blocked),
        "task": {**task("blocked resume Luna fixture", "C2"), "plan_approval": "auto"},
        "waves": [{"workers": [{"phase": "discover"}]}],
    })
    blocked_result = cortex.continue_orchestration({
        "project_root": str(blocked), "task_ref": current["task_ref"], "step": current["step"],
        "results": [{
            "status": "blocked",
            "reason": "fixture dependency unavailable",
            "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
        }],
    })
    if blocked_result.get("outcome") != "blocked":
        raise AssertionError(blocked_result)
    resumed = cortex.manage_orchestration({
        "project_root": str(blocked), "task_ref": current["task_ref"],
        "intent": "resume", "reason": "fixture dependency restored",
    })
    completed = finish(blocked, resumed)
    scenarios.append({"name": "blocked_resume", "outcome": completed["outcome"]})

    for project in (sequential, parallel, blocked):
        task_dir = next((project / ".codex/cortex/tasks").iterdir())
        state = cortex.load_task_state_for_artifact(task_dir)
        ledger = cortex.ledger_root({"project_root": str(project)})
        report_artifacts = canonical_artifacts(ledger, state["task_id"], kind="worker_report")
        report_records = [
            json.loads(cortex.db_read_artifact_content(ledger, state["task_id"], str(item["artifact_ref"])))
            for item in report_artifacts
        ]
        closure_records = [
            item for item in report_records if item.get("gate") in {"review", "close"}
        ]
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        snapshot_cleanup = state.get("manifest_snapshot_cleanup") or {}
        if (
            state.get("status") != "completed"
            or not close_evidence
            or not state.get("handoff_created")
            or snapshot_cleanup.get("status") != "completed"
            or not report_records
            or any(set(item.get("report", {})) != {
                "summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty",
            } for item in report_records)
            or any(
                not isinstance(item.get("closure"), dict)
                or item["closure"].get("decision") != "pass"
                or item["closure"].get("findings") != []
                or item["closure"].get("verification", {}).get("required_missing") != []
                for item in closure_records
            )
            or any(
                cortex.db_get_manifest_snapshot(
                    cortex.ledger_root({"project_root": str(project)}), str(reference)
                ) is not None
                for reference in [
                    state.get("initial_manifest_ref"),
                    *[
                        item.get("result_baseline_ref")
                        for item in state.get("attempts", [])
                        if isinstance(item, dict)
                    ],
                ]
                if reference
            )
        ):
            raise AssertionError(f"{project.name} lacks close evidence or handoff")
    return scenarios


def live_prompt(scenario: str, project: Path, source_task_ref: str | None = None) -> str:
    if scenario == "follow_up_partial":
        return (
            "Use only the public Cortex tools for this isolated partial smoke test. "
            f"The exact project_root is {project}. The completed source task_ref is {source_task_ref!r}. "
            "Call manage_orchestration exactly once with intent=follow_up, that task_ref, and payload exactly "
            "{\"user_request\":\"Correct the fixture result because the completed task produced the wrong behavior.\","
            "\"complexity\":\"C1\",\"acceptance_criteria\":[\"The linked corrective task is created without "
            "mutating the completed source task.\"],\"verification\":[\"Inspect the returned follow-up linkage and "
            "the unchanged source-task state.\"],\"plan_approval\":\"auto\"}. "
            "Do not call start_orchestration, continue_orchestration, or any private Cortex tool. Do not execute the returned "
            "worker dispatch: this test must stop after Cortex has created the linked corrective task. You may inspect that new task "
            "once with manage_orchestration to confirm it is awaiting its first worker."
        )
    report_field_names = ", ".join(cortex.REPORT_FIELDS)
    report_contract = f"exactly {len(cortex.REPORT_FIELDS)} report fields: {report_field_names}"
    common = (
        "Use the Cortex MCP public tools to complete this isolated task. "
        "You are the parent orchestrator. The exact task contract is the content inside <cortex_task_contract>; "
        "do not copy any surrounding host metadata into the task. Call start_orchestration exactly once with that contract, "
        "and use one continue_orchestration per wave; "
        f"never call orchestrate or any private Cortex tool. Execute every native dispatch; workers must persist {report_contract} with record_report and return only report_ref plus a short summary. "
        f"For every review or close dispatch, record_report must include both a top-level gate_result and a separate compatible top-level closure sibling: decision=pass only when there are no open blockers, findings=[], verification with executed/not_executed/required_missing/limitations arrays (required_missing=[] only after required checks ran), and workspace with modified/untracked/staged arrays plus committed true, false, or not_required. Their decision, findings, verification, and workspace values must match. Never place gate_result or closure inside the strict {len(cortex.REPORT_FIELDS)}-key report. "
        "Read every ref with read_worker_report and advance with report_ref. After a durable report was read and no "
        "question or follow-up remains for that child, close the completed native child with close_agent when that "
        "host tool is available, before dispatching a later wave; never close a running or question-paused child. "
        "Before every new spawn, FIRST close every known leftover completed child only after its durable report was "
        "read or its exact failed result was accepted by Cortex. If recovery may have missed a terminal child, use "
        "list_agents defensively and apply the same rule; THEN spawn. Do not close active or question-paused children. "
        "Treat a native child as successful only "
        "when its final response starts with REPORT_RECORDED and the referenced report was read successfully. If a "
        "stopped child returns anything else and no durable report exists, call continue_orchestration once for that "
        "current wave with status=failed, the exact dispatch_ref from the dispatch, and the child's exact bounded "
        "failure text as reason; never submit an empty result or a reportless success, and let Cortex issue any "
        "bounded retry dispatch. Finish only after close evidence and handoff. Do not ask for manual argument "
        "corrections. "
        f"The exact project_root is {project}. "
    )
    if scenario == "automatic_sequential":
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md and append exactly 'Verified note: README heading is Luna high Cortex fixture.' as one new line to result.md, creating the file if absent.\","
            "\"complexity\":\"C2\","
            "\"acceptance_criteria\":[\"README.md is inspected and its heading is confirmed as Luna high Cortex fixture.\","
            "\"result.md contains exactly one appended line: Verified note: README heading is Luna high Cortex fixture.\","
            "\"The final handoff identifies the changed file and includes evidence that the append was verified.\"],"
            "\"verification\":[\"Read README.md and confirm its heading, then read result.md and confirm the exact appended line.\","
            "\"Inspect the resulting diff or equivalent file evidence to verify only result.md received the intended line.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract>"
        )
    if scenario == "compact_parallel":
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md, then create result.md containing exactly one line: Parallel discovery fixture completed.\","
            "\"complexity\":\"C1\","
            "\"acceptance_criteria\":[\"Two independent discovery workers inspect README.md in the first wave.\","
            "\"result.md contains exactly one line: Parallel discovery fixture completed.\","
            "\"Close evidence and the final handoff verify the intended file change.\"],"
            "\"verification\":[\"Read README.md and both discovery reports before implementation.\","
            "\"Read result.md and inspect the resulting diff or equivalent file evidence.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract> "
            "Call start_orchestration exactly once with that exact task and these exact waves: "
            "[{\"workers\":[{\"phase\":\"discover\",\"profile\":\"explorer\",\"objective\":\"Confirm the README heading and relevant context.\"},"
            "{\"phase\":\"discover\",\"profile\":\"explorer\",\"objective\":\"Independently verify the required result.md content and constraints.\"}]},"
            "{\"workers\":[{\"phase\":\"implementation\"}]},{\"workers\":[{\"phase\":\"review\"}]},"
            "{\"workers\":[{\"phase\":\"documentation\"}]},{\"workers\":[{\"phase\":\"close\"}]}]."
        )
    if scenario == "planner_work_breakdown":
        return common + (
            "Exercise the Planner work-breakdown contract deterministically. "
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md, then create result.md containing exactly one line: Planner fixture completed.\","
            "\"complexity\":\"C2\",\"acceptance_criteria\":[\"README.md is inspected.\","
            "\"result.md contains exactly one line: Planner fixture completed.\",\"A final handoff is created.\"],"
            "\"verification\":[\"Read README.md and result.md and verify the exact result.md content.\"],"
            "\"plan_approval\":\"required\"}"
            "</cortex_task_contract> "
            "Call start_orchestration exactly once with that exact task and waves exactly "
            "[{\"workers\":[{\"phase\":\"discover\"}]},{\"workers\":[{\"phase\":\"plan\"}]},"
            "{\"workers\":[{\"phase\":\"implementation\"}]},{\"workers\":[{\"phase\":\"qa\"}]},"
            "{\"workers\":[{\"phase\":\"review\"}]},{\"workers\":[{\"phase\":\"documentation\"}]},"
            "{\"workers\":[{\"phase\":\"close\"}]}]. Complete discovery before the singleton final Planner. "
            "The Planner must read its supplied discovery report and publish the strict report plus this exact planning sibling: "
            "{\"overview\":\"Inspect the source before producing and verifying the requested result.\","
            "\"work_packages\":[{\"id\":\"inspect_source\",\"title\":\"Inspect source\","
            "\"objective\":\"Inspect README.md.\",\"allowed_paths\":[\"README.md\"],\"microtasks\":[{"
            "\"id\":\"read_readme\",\"title\":\"Read README\",\"objective\":\"Inspect README.md.\","
            "\"profile\":\"explorer\",\"allowed_paths\":[\"README.md\"],"
            "\"acceptance_criteria\":[\"README.md is inspected.\"],\"verification\":[\"Read README.md.\"]}]},{"
            "\"id\":\"deliver_result\",\"title\":\"Deliver result\",\"objective\":\"Create and verify result.md.\","
            "\"depends_on\":[\"inspect_source\"],\"allowed_paths\":[\"result.md\"],\"microtasks\":[{"
            "\"id\":\"write_result\",\"title\":\"Write result\",\"objective\":\"Create the exact result.md line.\","
            "\"profile\":\"general\",\"allowed_paths\":[\"result.md\"],"
            "\"acceptance_criteria\":[\"result.md contains exactly the required Planner fixture line.\"],"
            "\"verification\":[\"Read result.md and compare its exact content.\"]}]}]}. "
            "Do not add, remove, rename, or reorder packages or microtasks. Read the plan report, close the completed "
            "Planner child, then call "
            "continue_orchestration with that report_ref. Only after it returns outcome=awaiting_plan_approval and "
            "plan_review, call manage_orchestration intent=plan_approval with decision=prompt, then submit only the "
            "embedded Approve action arguments; never call approval before that continue. The user pre-authorized this fixture. Then run implementation, qa, review, "
            "documentation, and close in the returned order. Implementation creates result.md. Do not bypass approval "
            "or edit .codex/cortex."
        )
    return common + (
        "Exercise a deterministic future-wave reassessment without manufacturing a blocker. "
        "<cortex_task_contract>"
        "{\"user_request\":\"Inspect README.md, then create result.md containing exactly one line: Reassessment fixture completed.\","
        "\"complexity\":\"C2\","
        "\"acceptance_criteria\":[\"README.md is inspected before result.md is created.\","
        "\"result.md contains exactly one line: Reassessment fixture completed.\","
        "\"The final handoff identifies result.md and the verification evidence.\"],"
        "\"verification\":[\"Read README.md and result.md, then verify the exact result.md content.\","
        "\"Inspect the resulting diff or equivalent file evidence.\"],"
        "\"plan_approval\":\"auto\"}"
        "</cortex_task_contract> "
        "Call start_orchestration with that exact task and these exact initial waves: "
        "[{\"workers\":[{\"phase\":\"discover\"}]},{\"workers\":[{\"phase\":\"documentation\"}]},"
        "{\"workers\":[{\"phase\":\"review\"}]},{\"workers\":[{\"phase\":\"close\"}]}]. "
        "After the discover report is read and its completed child is closed, call continue_orchestration for that "
        "wave with its exact task_ref, step, and report_ref result plus future_waves exactly "
        "[{\"workers\":[{\"phase\":\"implementation\"}]},{\"workers\":[{\"phase\":\"documentation\"}]},"
        "{\"workers\":[{\"phase\":\"review\"}]},{\"workers\":[{\"phase\":\"close\"}]}] and reason exactly "
        "'Discovery confirms result.md must be created, so add implementation before documentation.' Do not set "
        "rework. This one replacement must create the reassessment evidence; after it, follow the returned pipeline "
        "normally and do not replace future waves again."
    )


def live_eval(
    base: Path, scenarios: tuple[str, ...] | None = None, *, timeout_seconds: int = LIVE_TIMEOUT_SECONDS,
    retain_failure_metadata: bool = False,
) -> list[dict[str, object]]:
    codex = shutil.which("codex")
    if not codex:
        return [{"status": "SKIP", "reason": "codex runtime unavailable; no live evidence"}]
    results: list[dict[str, object]] = []
    for scenario in scenarios or ("automatic_sequential", "compact_parallel", "blocked_resume", "planner_work_breakdown"):
        project = base / f"live-{scenario}"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / "README.md").write_text("# Luna high Cortex fixture\n", encoding="utf-8")
        source_task_ref = None
        source_snapshot: tuple[dict[str, object], dict[str, object]] | None = None
        if scenario == "follow_up_partial":
            source = cortex.start_orchestration({
                "project_root": str(project),
                "task": task("Complete a deterministic source fixture before follow-up testing.", "C1"),
            })
            completed_source = finish(project, source)
            if completed_source.get("outcome") != "completed":
                raise AssertionError(completed_source)
            source_task_ref = str(source["task_ref"])
            source_dir = next((project / ".codex/cortex/tasks").iterdir())
            source_snapshot = (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            )
        command = [
            codex, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox", "-C", str(project),
            "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="high"',
            "-c", f'mcp_servers.cortex.command="{sys.executable}"',
            "-c", f'mcp_servers.cortex.args=["{SERVER}"]',
            live_prompt(scenario, project, source_task_ref),
        ]
        with isolated_codex_runtime(base) as isolated_environment:
            streamed = run_live_command(
                command, project, scenario, timeout_seconds=timeout_seconds,
                environment=isolated_environment,
            )
        events = list(streamed["events"])
        tool_names: list[str] = []
        completed_tool_names: list[str] = []
        native_tool_names: list[str] = []
        completed_native_tool_names: list[str] = []
        failed_public_calls: list[str] = []
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "cortex_mcp_call":
                continue
            name = event.get("tool")
            if isinstance(name, str):
                tool_names.append(name)
                if event.get("status") == "completed":
                    completed_tool_names.append(name)
                    if event.get("ok") is False:
                        failed_public_calls.append(name)
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "native_tool_call":
                continue
            name = event.get("tool")
            if isinstance(name, str):
                native_tool_names.append(name)
                if event.get("status") == "completed":
                    completed_native_tool_names.append(name)
        task_dirs = list((project / ".codex/cortex/tasks").glob("*"))
        task_dir = task_dirs[0] if len(task_dirs) == 1 else None
        if scenario == "follow_up_partial":
            task_dir = next(
                (path for path in task_dirs if isinstance(cortex.load_task_definition(path).get("follow_up"), dict)),
                None,
            )
        state = cortex.load_task_state_for_artifact(task_dir) if task_dir else {}
        ledger = cortex.ledger_root({"project_root": str(project)})
        report_artifacts = canonical_artifacts(ledger, str(state.get("task_id") or ""), kind="worker_report") if task_dir else []
        report_records = [
            json.loads(cortex.db_read_artifact_content(ledger, str(state["task_id"]), str(item["artifact_ref"])))
            for item in report_artifacts
        ]
        report_keys = set(cortex.REPORT_FIELDS)
        strict_reports = bool(report_records) and all(
            set(record.get("report", {})) == report_keys
            for record in report_records
        )
        closures_valid = all(
            isinstance(record.get("closure"), dict)
            and record["closure"].get("decision") == "pass"
            and record["closure"].get("findings") == []
            and record["closure"].get("verification", {}).get("required_missing") == []
            and set(record["closure"].get("workspace", {})) == {"modified", "untracked", "staged", "committed"}
            for record in report_records if record.get("gate") in {"review", "close"}
        )
        attempts_by_wave: dict[str, set[str]] = {}
        for attempt in state.get("attempts", []):
            if attempt.get("invalidated"):
                continue
            attempts_by_wave.setdefault(str(attempt.get("orchestration_wave_id") or ""), set()).add(str(attempt.get("gate") or ""))
        parallel_exercised = any(len(gates) > 1 for gates in attempts_by_wave.values())
        adaptive_exercised = bool(state.get("resume_events")) or len(state.get("reassessment_receipts", [])) > 1 or bool(state.get("pipeline_changes"))
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        planning_manifest = cortex.current_planning_manifest(task_dir) if task_dir else {}
        checks = {
            "process_ok": streamed["returncode"] == 0,
            "used_start": "start_orchestration" in tool_names,
            "used_continue": "continue_orchestration" in tool_names,
            "avoided_private_tools": "orchestrate" not in tool_names,
            "single_task": len(task_dirs) == 1,
            "completed": state.get("status") == "completed",
            "close_evidence": close_evidence,
            "handoff": bool(state.get("handoff_created")),
            "manifest_snapshots_cleaned": (
                (state.get("manifest_snapshot_cleanup") or {}).get("status") == "completed"
                and bool(task_dir)
                and not any(
                    cortex.db_get_manifest_snapshot(
                        cortex.ledger_root({"project_root": str(project)}), str(reference)
                    ) is not None
                    for reference in [
                        state.get("initial_manifest_ref"),
                        *[
                            item.get("result_baseline_ref")
                            for item in state.get("attempts", [])
                            if isinstance(item, dict)
                        ],
                    ]
                    if reference
                )
            ),
            "strict_worker_reports": strict_reports,
            "review_close_closures": closures_valid,
            "no_failed_public_calls": not failed_public_calls,
            "one_start": completed_tool_names.count("start_orchestration") == 1,
            "native_dispatch_exercised": "spawn_agent" in completed_native_tool_names,
            "native_wait_exercised": "wait" in completed_native_tool_names,
            "native_cleanup_exercised": "close_agent" in completed_native_tool_names,
        }
        if scenario == "compact_parallel":
            checks["parallel_wave_exercised"] = parallel_exercised
        if scenario == "blocked_resume":
            checks["resume_or_reassessment_exercised"] = adaptive_exercised
        if scenario == "planner_work_breakdown":
            package_artifacts = planning_manifest.get("work_packages") if isinstance(planning_manifest, dict) else []
            checks["plan_approval_exercised"] = state.get("plan_approval", {}).get("status") == "approved"
            checks["planning_manifest"] = (
                planning_manifest.get("schema") == "cortex/planning/v1"
                and len(package_artifacts) >= 2
                and all(
                    isinstance(package, dict)
                    and cortex.db_get_artifact_for_export_path(
                        ledger, str(state["task_id"]), str(package.get("artifact_path") or ""),
                    ) is not None
                    for package in package_artifacts
                )
            )
        if scenario == "follow_up_partial":
            source_dir = next((path for path in task_dirs if path != task_dir), None)
            corrective_task = cortex.load_task_definition(task_dir) if task_dir else {}
            source_unchanged = bool(source_dir and source_snapshot and (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            ) == source_snapshot)
            checks = {
                "process_ok": streamed["returncode"] == 0,
                "used_follow_up": "manage_orchestration" in tool_names,
                "avoided_start_and_continue": "start_orchestration" not in tool_names and "continue_orchestration" not in tool_names,
                "avoided_private_tools": "orchestrate" not in tool_names,
                "created_one_linked_corrective_task": len(task_dirs) == 2 and task_dir is not None,
                "source_unchanged": source_unchanged,
                "corrective_task_active": state.get("status") == "active",
                "follow_up_link": corrective_task.get("follow_up", {}).get("source_task_ref") == source_task_ref,
                "first_corrective_dispatch_prepared": bool(state.get("attempts")) and state["attempts"][0].get("gate") in {"plan", "discover"},
                "no_failed_public_calls": not failed_public_calls,
            }
        passed = all(checks.values())
        results.append({
            "scenario": scenario, "status": "PASS" if passed else "FAIL",
            "launch_model": "gpt-5.6-luna", "launch_reasoning_effort": "high",
            "exit_code": streamed["returncode"], "elapsed_seconds": streamed["elapsed_seconds"],
            "termination": streamed["termination"], "dropped_stream_events": streamed["dropped_stream_events"],
            "tool_names": tool_names,
            "native_tool_names": native_tool_names,
            "checks": checks, "failed_public_calls": failed_public_calls,
        })
        if not passed:
            if retain_failure_metadata:
                failure_dir = Path(tempfile.mkdtemp(prefix="cortex-luna-high-failure-"))
                # Keep only sanitized progress metadata. Copying the temporary
                # project or raw Codex events could preserve prompts, report
                # text, paths, or host diagnostics beyond this opt-in.
                (failure_dir / "progress.json").write_text(
                    json.dumps({"result": results[-1], "events": events[-100:]}, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                results[-1]["failure_artifacts"] = str(failure_dir)
            else:
                results[-1]["failure_metadata"] = "not_retained"
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="run the four real gpt-5.6-luna high parent scenarios")
    parser.add_argument(
        "--scenario", choices=("automatic_sequential", "compact_parallel", "blocked_resume", "planner_work_breakdown", "follow_up_partial"),
        help="run one live scenario for diagnosis; the default release run still requires all four",
    )
    parser.add_argument(
        "--live-timeout-seconds", type=int, default=LIVE_TIMEOUT_SECONDS,
        help="bound each live source-mode scenario and terminate its complete process group on expiry",
    )
    parser.add_argument(
        "--retain-failure-metadata", action="store_true",
        help="opt in to retaining bounded sanitized live-failure metadata under /tmp",
    )
    args = parser.parse_args()
    if args.live_timeout_seconds < 10 or args.live_timeout_seconds > 7200:
        parser.error("--live-timeout-seconds must be between 10 and 7200")
    with tempfile.TemporaryDirectory(prefix="cortex-luna-high-") as directory:
        base = Path(directory)
        fixtures = fixture_eval(base)
        if args.live or os.environ.get("CORTEX_RUN_LIVE_LUNA") == "1":
            live = live_eval(
                base, (args.scenario,) if args.scenario else None,
                timeout_seconds=args.live_timeout_seconds,
                retain_failure_metadata=args.retain_failure_metadata,
            )
        else:
            live = [{"status": "SKIP", "reason": "live flag not supplied; no live release evidence"}]
    successful = all(item.get("status") in {"PASS", "SKIP"} for item in live)
    print(json.dumps({"status": "PASS" if successful else "FAIL", "fixtures": fixtures, "live": live}, sort_keys=True))
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
