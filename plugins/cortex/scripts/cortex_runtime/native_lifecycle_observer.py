"""Trusted local observation of the native Codex worker lifecycle.

Only SubagentStart and SubagentStop are authoritative host events.  Native
spawn_agent/wait_agent collaboration calls have no usable hook stream in the
installed host and are intentionally not observed here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cortex_runtime import attempt_protocol, ledger_db
from cortex_runtime.host_workspace_binding import bind_session_workspace, workspace_for_session


class _RuntimeProxy:
    """Resolve the executable facade only after module initialization.

    ``cortex`` imports this observer through ``attempt_facade`` while the
    executable module is still being composed.  Importing it at module scope
    therefore creates a cycle (and can expose a partially initialized
    ``cortex`` module).  Hook callbacks run after composition, so a lazy
    attribute lookup preserves the same runtime dependency without importing
    it during module initialization.
    """

    def __getattr__(self, name: str) -> Any:
        import importlib

        return getattr(importlib.import_module("cortex"), name)


runtime = _RuntimeProxy()

PERMISSION_MODES = frozenset({"default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"})
START_FIELDS = frozenset({"agent_id", "agent_type", "cwd", "hook_event_name", "model", "permission_mode", "session_id", "transcript_path", "turn_id"})
STOP_FIELDS = frozenset({"agent_id", "agent_transcript_path", "agent_type", "cwd", "hook_event_name", "last_assistant_message", "model", "permission_mode", "session_id", "stop_hook_active", "transcript_path", "turn_id"})
SESSION_START_FIELDS = frozenset({"cwd", "hook_event_name", "model", "permission_mode", "session_id", "source", "transcript_path"})


class NativeStopCaptureError(RuntimeError):
    """The host must retry because its terminal Stop was not durably captured."""


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _host_envelope_digest(event: Mapping[str, Any], fields: frozenset[str]) -> str:
    """Bind the complete strict host envelope without retaining raw content."""
    payload = [[field, event.get(field)] for field in sorted(fields)]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe(value: object, maximum: int = 512) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= maximum and "\x00" not in value


def _valid_common(event: Mapping[str, Any]) -> bool:
    return all(_safe(event.get(key)) for key in ("cwd", "model", "permission_mode", "session_id", "turn_id")) and event.get("permission_mode") in PERMISSION_MODES and isinstance(event.get("transcript_path"), (str, type(None)))


def _strict_start(value: object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    fields = frozenset(value)
    if fields not in {START_FIELDS, START_FIELDS - {"model"}}:
        return None
    if value.get("hook_event_name") != "SubagentStart":
        return None
    if not all(_safe(value.get(key)) for key in ("cwd", "permission_mode", "session_id", "turn_id")):
        return None
    if value.get("permission_mode") not in PERMISSION_MODES or not isinstance(value.get("transcript_path"), (str, type(None))):
        return None
    # A missing/empty model is retained as negative host evidence.  It must
    # make worker authorization fail terminally instead of looking like a
    # delayed SubagentStart and causing an unchanged retry loop.
    model = value.get("model")
    if model is not None and model != "" and not _safe(model):
        return None
    return value if _safe(value.get("agent_id"), 256) and _safe(value.get("agent_type")) else None


def _strict_stop(value: object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or frozenset(value) != STOP_FIELDS or value.get("hook_event_name") != "SubagentStop" or not _valid_common(value):
        return None
    if not _safe(value.get("agent_id"), 256) or not _safe(value.get("agent_type")) or not isinstance(value.get("stop_hook_active"), bool):
        return None
    if not isinstance(value.get("agent_transcript_path"), (str, type(None))) or not isinstance(value.get("last_assistant_message"), (str, type(None))):
        return None
    return value


def _strict_session_start(value: object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or frozenset(value) != SESSION_START_FIELDS:
        return None
    if value.get("hook_event_name") != "SessionStart":
        return None
    if (
        not all(_safe(value.get(key)) for key in ("cwd", "model", "permission_mode", "session_id"))
        or value.get("permission_mode") not in PERMISSION_MODES
        or not isinstance(value.get("transcript_path"), (str, type(None)))
        or not _safe(value.get("source"), 64)
    ):
        return None
    return value


def _observe_session_start(event: Mapping[str, Any]) -> bool:
    if not bind_session_workspace(event.get("session_id"), event.get("cwd")):
        return False
    root = _existing_root(str(event.get("cwd") or ""))
    if root is None:
        return True
    recorded = ensure_current_host_epoch(
        root,
        str(event.get("session_id") or ""),
        source=str(event.get("source") or ""),
        allow_handoff=str(event.get("source") or "").strip().lower() == "resume",
        hook_owned=True,
    )
    if not isinstance(recorded, Mapping):
        return False
    source = str(event.get("source") or "").strip().lower()
    if source in {"compact", "compaction", "clear", "reset"}:
        return ledger_db.hook_record_native_context_boundary(
            root,
            str(event.get("session_id") or ""),
            recorded,
            source=source,
        )
    return True


def ensure_current_host_epoch(
    root: Path,
    session_id: str,
    *,
    source: str,
    allow_handoff: bool,
    hook_owned: bool = False,
) -> dict[str, Any] | None:
    """Bind the current Codex process, advancing only at a trusted resume."""
    incarnation = _trusted_codex_incarnation(root)
    if incarnation is None or not session_id:
        return None
    prior = (
        ledger_db.hook_get_native_host_epoch(root, session_id)
        if hook_owned
        else ledger_db.get_native_host_epoch(root, session_id)
    )
    prior_fingerprint = str((prior or {}).get("fingerprint") or "") or None
    prior_dead = False
    if prior_fingerprint and prior_fingerprint != incarnation["fingerprint"]:
        if not allow_handoff:
            return None
        prior_dead = not _authenticated_incarnation_is_live(root, prior or {})
        if not prior_dead:
            # A second Codex process must never seize a session while the
            # authenticated previous incarnation is still alive.
            return None
    recorded = ledger_db.hook_advance_native_host_epoch(
        root,
        session_id,
        incarnation,
        source=source,
        prior_fingerprint=prior_fingerprint,
        prior_provably_dead=prior_dead,
        hook_owned=hook_owned,
    )
    return recorded


def _proc_stat(pid: int) -> tuple[int, int] | None:
    """Return (parent pid, start ticks) for one exact Linux process."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        suffix = text[text.rfind(")") + 2 :].split()
        parent_pid = int(suffix[1])
        start_ticks = int(suffix[19])
        return parent_pid, start_ticks
    except (IndexError, OSError, TypeError, ValueError):
        return None


def _proc_uid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (IndexError, OSError, ValueError):
        return None
    return None


def _codex_host_pid() -> int | None:
    """Find the same-uid host process that owns this plugin subprocess.

    Native Codex uses a ``codex`` ancestor. Source-mode release validation
    invokes the hook and MCP child directly from one long-lived host process;
    the exact immediate parent is then the shared incarnation boundary.
    """
    expected_uid = int(os.getuid()) if hasattr(os, "getuid") else None
    pid = os.getppid()
    direct_parent = pid
    seen: set[int] = set()
    for _ in range(24):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        stat_record = _proc_stat(pid)
        if stat_record is None:
            return None
        if expected_uid is not None and _proc_uid(pid) != expected_uid:
            return None
        try:
            executable = Path(f"/proc/{pid}/exe").resolve(strict=True).name
            command = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if executable == "codex" and command == "codex":
            return pid
        pid = stat_record[0]
    return direct_parent if _proc_stat(direct_parent) is not None else None


def _boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value if value and len(value) <= 128 and "\x00" not in value else None


def _process_started_at(start_ticks: int) -> str | None:
    try:
        boot_seconds = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
            if line.startswith("btime ")
        )
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        if ticks_per_second <= 0:
            return None
        timestamp = boot_seconds + (int(start_ticks) / ticks_per_second)
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OSError, StopIteration, TypeError, ValueError):
        return None


def _trusted_codex_incarnation(root: Path) -> dict[str, Any] | None:
    pid = _codex_host_pid()
    boot = _boot_id()
    stat_record = _proc_stat(pid) if pid is not None else None
    uid = _proc_uid(pid) if pid is not None else None
    if pid is None or boot is None or stat_record is None or uid is None:
        return None
    start_ticks = stat_record[1]
    started_at = _process_started_at(start_ticks)
    if started_at is None:
        return None
    boot_digest = ledger_db.private_lifecycle_audit_digest(root, "host-boot", boot)
    payload = json.dumps(
        [uid, pid, start_ticks, boot_digest],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "host_uid": uid,
        "host_pid": pid,
        "host_start_ticks": start_ticks,
        "boot_digest": boot_digest,
        "process_started_at": started_at,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": ledger_db.private_lifecycle_audit_digest(
            root, "host-incarnation", payload,
        ),
    }


def _authenticated_incarnation_is_live(root: Path, value: Mapping[str, Any]) -> bool:
    """Return false only when exact private process evidence proves death.

    Permission failures, malformed procfs data, and other unavailable evidence
    remain conservatively live so a new host cannot seize an ambiguous epoch.
    """
    try:
        pid = int(value["host_pid"])
        expected_uid = int(value["host_uid"])
        expected_ticks = int(value["host_start_ticks"])
        boot = _boot_id()
        if boot is None:
            return True
        boot_digest = ledger_db.private_lifecycle_audit_digest(root, "host-boot", boot)
        if not hmac.compare_digest(str(value.get("boot_digest") or ""), boot_digest):
            return False
        try:
            stat_record = _proc_stat(pid)
        except (FileNotFoundError, ProcessLookupError):
            return False
        if stat_record is None:
            # `_proc_stat` deliberately collapses malformed/unreadable procfs;
            # re-open once to distinguish an absent PID from unavailable data.
            try:
                Path(f"/proc/{pid}/stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return False
            except OSError:
                return True
            return True
        observed_uid = _proc_uid(pid)
        if observed_uid is None:
            try:
                Path(f"/proc/{pid}/status").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return False
            except OSError:
                return True
            return True
        # Reused PIDs have a different kernel start time and therefore prove
        # the authenticated prior incarnation is dead. A changed uid with the
        # same PID/start pair is ambiguous and remains fail-closed.
        if stat_record[1] != expected_ticks:
            return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        # Ambiguous evidence preserves the old lease.
        return True


def _existing_root(cwd: str) -> Path | None:
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return None
    try:
        project = Path(cwd).resolve(strict=True)
        root = runtime.ledger_root_path_internal(project, create=False)
        for path, directory in ((root, True), (root / ledger_db.DATABASE_NAME, False), (root / ".state.lock", False)):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode) != directory:
                return None
        return root
    except (OSError, TypeError, ValueError):
        return None


def _attempts_for_agent(root: Path, agent_id: str) -> list[tuple[str, str]]:
    with ledger_db.hook_snapshot(root, timeout_ms=100) as connection:
        if connection is None:
            return []
        # The private worker-session binding is the exact lifecycle authority.
        # Never scan every task state/history inside the bounded hook process.
        return ledger_db.hook_snapshot_worker_bindings(
            connection, agent_id, limit=2,
        )


def _attempt_expected_model(attempt: Mapping[str, Any]) -> str | None:
    spawn_request = (
        attempt.get("spawn_request")
        if isinstance(attempt.get("spawn_request"), Mapping)
        else {}
    )
    values = {
        str(value).strip()
        for value in (
            attempt.get("expected_model"),
            attempt.get("selected_model"),
            spawn_request.get("expected_model"),
        )
        if isinstance(value, str) and value.strip()
    }
    return next(iter(values)) if len(values) == 1 else None


def _awaiting_candidates_for_start(
    root: Path,
    event: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """Find the one dispatch a host Start can identify without model inference."""
    session_id = str(event.get("session_id") or "")
    observed_model = str(event.get("model") or "")
    candidates: list[tuple[str, str]] = []
    with ledger_db.hook_snapshot(root, timeout_ms=100) as connection:
        if connection is None:
            return candidates
        session_candidates = ledger_db.hook_snapshot_awaiting_worker_sessions(
            connection, limit=65,
        )
        # More than the bounded lifecycle frontier is intrinsically
        # ambiguous to a Start envelope that carries no dispatch identity.
        if len(session_candidates) >= 65:
            return candidates
        loaded_tasks: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str] | None] = {}
        for task_id, expected_attempt_id in session_candidates:
            if task_id not in loaded_tasks:
                loaded_tasks[task_id] = ledger_db.hook_snapshot_load_task(connection, task_id)
            loaded = loaded_tasks[task_id]
            if loaded is None:
                continue
            state = loaded[1]
            if str(state.get("coordinator_host_thread_id") or "") != session_id:
                continue
            if (
                state.get("coordinator_native_host_epoch") != event.get("host_epoch")
                or not hmac.compare_digest(
                    str(state.get("coordinator_native_host_epoch_fingerprint") or ""),
                    str(event.get("host_epoch_fingerprint") or ""),
                )
            ):
                continue
            for attempt in state.get("attempts") or []:
                if (
                    not isinstance(attempt, Mapping)
                    or str(attempt.get("attempt_id") or "") != expected_attempt_id
                    or attempt.get("invalidated")
                    or str(attempt.get("status") or "") != "awaiting_host_spawn"
                    or str(attempt.get("dispatch_delivery_status") or "") != "delivered"
                    or str(attempt.get("worker_host_thread_id") or "")
                    or attempt.get("native_host_epoch") != event.get("host_epoch")
                    or not hmac.compare_digest(
                        str(attempt.get("native_host_epoch_fingerprint") or ""),
                        str(event.get("host_epoch_fingerprint") or ""),
                    )
                    or _attempt_expected_model(attempt) != observed_model
                ):
                    continue
                candidates.append((str(task_id), str(attempt.get("attempt_id") or "")))
    return candidates


def _bind_unambiguous_start(event: Mapping[str, Any], root: Path) -> bool:
    """Bind one exact Start when the coordinator has one matching dispatch.

    Hooks do not expose task names or dispatch capabilities.  Binding is
    therefore legal only when coordinator session, workspace, delivered
    awaiting state, and attested model leave exactly one candidate.  Parallel
    ambiguous starts remain unbound rather than being guessed.
    """
    candidates = _awaiting_candidates_for_start(root, event)
    if len(candidates) != 1:
        return False
    task_id, attempt_id = candidates[0]
    with runtime.state_lock(
        root,
        timeout_seconds=0.20,
        operation="native_start_bind",
        task_id=task_id,
    ):
        locked_candidates = _awaiting_candidates_for_start(root, event)
        if locked_candidates != [(task_id, attempt_id)]:
            # The pre-lock snapshot is only an optimization.  The mutation is
            # authorized exclusively by this recomputed locked snapshot; a
            # concurrently delivered second candidate makes binding
            # ambiguous and therefore fail closed.
            return False
        loaded = runtime.db_load_task(root, task_id)
        if loaded is None:
            return False
        _definition, state, _plan, artifact_dir = loaded
        if str(state.get("coordinator_host_thread_id") or "") != str(event.get("session_id") or ""):
            return False
        attempts = [
            item for item in state.get("attempts") or []
            if isinstance(item, dict)
            and not item.get("invalidated")
            and str(item.get("attempt_id") or "") == attempt_id
        ]
        if len(attempts) != 1:
            return False
        attempt = attempts[0]
        agent_id = str(event.get("agent_id") or "")
        existing = str(attempt.get("worker_host_thread_id") or "")
        if existing:
            return existing == agent_id
        if (
            str(attempt.get("status") or "") != "awaiting_host_spawn"
            or str(attempt.get("dispatch_delivery_status") or "") != "delivered"
            or attempt.get("native_host_epoch") != event.get("host_epoch")
            or not hmac.compare_digest(
                str(attempt.get("native_host_epoch_fingerprint") or ""),
                str(event.get("host_epoch_fingerprint") or ""),
            )
            or _attempt_expected_model(attempt) != str(event.get("model") or "")
            or ledger_db.native_child_binding_exists(
                root, agent_id, task_id=task_id, attempt_id=attempt_id,
            )
        ):
            return False
        observed_at = str(event.get("observed_at") or runtime.now())
        attempt["worker_host_thread_id"] = agent_id
        attempt["worker_host_start_turn_id"] = str(event.get("turn_id") or "")
        attempt["worker_host_start_observed"] = True
        attempt["worker_host_model_attested"] = True
        attempt["worker_host_session_generation"] = 1
        attempt["worker_host_last_seen_at"] = observed_at
        attempt["status"] = "running"
        attempt["lifecycle_status"] = "running"
        attempt["host_resumable"] = True
        claim = (
            attempt.get("worker_authority")
            if isinstance(attempt.get("worker_authority"), Mapping)
            else {}
        )
        # Both repositories join the same re-entrant SQLite unit of work.
        # A failure in task/event persistence rolls the worker-session bind
        # back, and a session failure prevents the task from becoming running.
        # Secondary filesystem projections are explicitly non-authoritative.
        with ledger_db.transaction(root):
            ledger_db.put_worker_session(root, {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "generation": int(claim.get("generation") or 1),
                "host_agent_id": agent_id,
                "host_task_name": "",
                "host_tool": "spawn_agent",
                "status": "running",
                "resumable": True,
                "started_at": observed_at,
            })
            runtime.save_state(
                root / artifact_dir,
                root / artifact_dir / "state.sqlite",
                state,
                "worker_host_thread_bound_by_start",
                "unambiguous trusted native Start bound before the worker's first Cortex call",
            )
        return True


def _observe_start(event: Mapping[str, Any], root: Path) -> bool:
    bound_workspace = workspace_for_session(event.get("session_id"))
    if bound_workspace is None:
        return False
    try:
        if Path(str(event["cwd"])).resolve(strict=True) != bound_workspace:
            return False
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    observed_model = event.get("model")
    epoch = ledger_db.hook_get_native_host_epoch(root, str(event.get("session_id") or ""))
    incarnation = _trusted_codex_incarnation(root)
    if (
        not isinstance(epoch, Mapping)
        or not isinstance(incarnation, Mapping)
        or not hmac.compare_digest(
            str(epoch.get("fingerprint") or ""), str(incarnation.get("fingerprint") or ""),
        )
    ):
        return False
    document = {
        "schema": ledger_db.NATIVE_HOST_START_SCHEMA,
        "agent_id": str(event["agent_id"]),
        "session_id": str(event["session_id"]),
        "turn_id": str(event["turn_id"]),
        "agent_type": str(event["agent_type"]),
        "model": str(observed_model) if _safe(observed_model) else None,
        "host_epoch": int(epoch["epoch"]),
        "host_epoch_fingerprint": str(epoch["fingerprint"]),
        "observed_at": runtime.now(),
    }
    outcome = ledger_db.hook_compare_insert_native_start(root, document)
    if outcome not in {"inserted", "same"}:
        return False
    # Persist the exact workspace-bound Start even if the short task snapshot
    # is temporarily busy.  This evidence alone authorizes nothing: binding
    # below still requires a unique coordinator-session/model/delivered
    # dispatch match.  Worker authorization can therefore observe the Start
    # on an immediate retry instead of losing it to snapshot timing.
    # Do not wait for a worker MCP call when that match is already unique. A
    # zero-successful-call child can now be associated with its Stop and enter
    # deterministic recovery.
    _bind_unambiguous_start(document, root)
    return True


def _save_reconciled_stop(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    stop_key: str,
    attempt: dict[str, Any],
    event_name: str,
    detail: str,
    outcome: str,
) -> None:
    """Atomically save task projection before marking one inbox row consumed."""
    runtime.save_state(task_dir, task_dir / "state.sqlite", state, event_name, detail)
    receipt = {
        "schema": ledger_db.NATIVE_HOST_STOP_RECEIPT_SCHEMA,
        "stop_key_digest": _digest(stop_key),
        "task_id": str(state.get("task_id") or ""),
        "attempt_id": str(attempt.get("attempt_id") or ""),
        "outcome": outcome,
        "reconciled_at": runtime.now(),
    }
    receipt_outcome = ledger_db.put_native_host_stop_receipt(root, stop_key, receipt)
    if receipt_outcome not in {"inserted", "same"}:
        raise RuntimeError("native child stop reconciliation receipt conflicts")


def _reconcile_stop_for_task(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    stop_key: str,
    event: Mapping[str, Any],
) -> bool:
    """Reconcile one captured Stop into its exact task/attempt occurrence."""
    if str(state.get("coordinator_host_thread_id") or "") != str(event.get("session_id") or ""):
        return False
    if (
        state.get("coordinator_native_host_epoch") != event.get("host_epoch")
        or not hmac.compare_digest(
            str(state.get("coordinator_native_host_epoch_fingerprint") or ""),
            str(event.get("host_epoch_fingerprint") or ""),
        )
    ):
        return False
    attempts = [
        item for item in state.get("attempts") or []
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("worker_host_thread_id") or "") == str(event.get("agent_id") or "")
        and item.get("native_host_epoch") == event.get("host_epoch")
        and hmac.compare_digest(
            str(item.get("native_host_epoch_fingerprint") or ""),
            str(event.get("host_epoch_fingerprint") or ""),
        )
    ]
    if not attempts:
        # A Start can be durably captured while its 200ms eager task binding is
        # busy. Cold replay must join that stored Start before consuming the
        # later Stop; otherwise a zero-call child would remain unidentifiable
        # forever even though both host boundaries are durable.
        start = ledger_db.get_latest_native_host_start(
            root, str(event.get("agent_id") or ""),
        )
        candidates = _awaiting_candidates_for_start(root, start) if isinstance(start, Mapping) else []
        current_candidates = [
            item for item in state.get("attempts") or []
            if isinstance(item, dict)
            and not item.get("invalidated")
            and str(item.get("status") or "") == "awaiting_host_spawn"
            and str(item.get("dispatch_delivery_status") or "") == "delivered"
            and not str(item.get("worker_host_thread_id") or "")
            and item.get("native_host_epoch") == start.get("host_epoch")
            and hmac.compare_digest(
                str(item.get("native_host_epoch_fingerprint") or ""),
                str(start.get("host_epoch_fingerprint") or ""),
            )
            and _attempt_expected_model(item) == str((start or {}).get("model") or "")
        ]
        if (
            isinstance(start, Mapping)
            and str(start.get("session_id") or "") == str(event.get("session_id") or "")
            and start.get("host_epoch") == event.get("host_epoch")
            and hmac.compare_digest(
                str(start.get("host_epoch_fingerprint") or ""),
                str(event.get("host_epoch_fingerprint") or ""),
            )
            and len(current_candidates) == 1
            and candidates == [(
                str(state.get("task_id") or ""),
                str(current_candidates[0].get("attempt_id") or ""),
            )]
            and not ledger_db.native_child_binding_exists(
                root,
                str(event.get("agent_id") or ""),
                task_id=str(state.get("task_id") or ""),
                attempt_id=str(current_candidates[0].get("attempt_id") or ""),
            )
        ):
            attempt = current_candidates[0]
            observed_at = str(start.get("observed_at") or runtime.now())
            claim = attempt.get("worker_authority") if isinstance(attempt.get("worker_authority"), Mapping) else {}
            attempt.update({
                "worker_host_thread_id": str(event.get("agent_id") or ""),
                "worker_host_start_turn_id": str(start.get("turn_id") or ""),
                "worker_host_start_observed": True,
                "worker_host_model_attested": True,
                "worker_host_session_generation": 1,
                "worker_host_last_seen_at": observed_at,
                "status": "running",
                "lifecycle_status": "running",
                "host_resumable": True,
            })
            ledger_db.put_worker_session(root, {
                "task_id": str(state.get("task_id") or ""),
                "attempt_id": str(attempt.get("attempt_id") or ""),
                "generation": int(claim.get("generation") or 1),
                "host_agent_id": str(event.get("agent_id") or ""),
                "host_task_name": "",
                "host_tool": "spawn_agent",
                "status": "running",
                "resumable": True,
                "started_at": observed_at,
            })
            attempts = [attempt]
    if len(attempts) != 1:
        return False
    attempt = attempts[0]
    task_id = str(state.get("task_id") or "")
    attempt_id = str(attempt.get("attempt_id") or "")
    turn_digest = _digest(event.get("turn_id"))
    result_ref = str(attempt.get("attempt_result_ref") or "")
    canonical = attempt_protocol.get_attempt_result(
        root, task_id=task_id, attempt_id=attempt_id,
    )

    if result_ref and isinstance(canonical, dict) and str(canonical.get("result_ref") or "") == result_ref:
        existing = attempt.get("native_terminal_stop")
        if isinstance(existing, Mapping) and existing.get("observed") is True:
            if str(existing.get("result_digest") or "") != _digest(result_ref):
                raise RuntimeError("native child stop conflicts with terminal result identity")
            outcome = "terminal_replay"
        else:
            sequence = int(state.get("native_lifecycle_sequence") or 0) + 1
            state["native_lifecycle_sequence"] = sequence
            attempt["native_terminal_stop"] = {
                "observed": True,
                "sequence": sequence,
                "result_digest": _digest(result_ref),
                "agent_digest": _digest(event.get("agent_id")),
                "session_digest": _digest(event.get("session_id")),
                "turn_digest": turn_digest,
                "observed_at": str(event.get("observed_at") or runtime.now()),
            }
            attempt.pop("native_incomplete_stop_evidence", None)
            outcome = "terminal_result"

        # Evaluation is derived and retryable; terminal Stop capture is not.
        # A transient evaluator failure therefore leaves an explicit retry bit
        # while the terminal occurrence and its inbox consumption still commit.
        try:
            from cortex_runtime.assignment_evaluator import persist_assignment_evaluation

            persist_assignment_evaluation(root, state, attempt)
            attempt.pop("native_stop_evaluation_pending", None)
        except (OSError, RuntimeError, TypeError, ValueError):
            attempt["native_stop_evaluation_pending"] = True
        _save_reconciled_stop(
            root,
            task_dir,
            state,
            stop_key,
            attempt,
            "native_terminal_stop",
            "durable native Stop inbox reconciled with the exact canonical result",
            outcome,
        )
        return True

    previous = attempt.get("native_incomplete_stop_evidence")
    if not (
        isinstance(previous, Mapping)
        and previous.get("observed") is True
        and str(previous.get("turn_digest") or "") == turn_digest
    ):
        sequence = int(state.get("native_lifecycle_sequence") or 0) + 1
        state["native_lifecycle_sequence"] = sequence
        observed_at = str(event.get("observed_at") or runtime.now())
        attempt["native_incomplete_stop_evidence"] = {
            "observed": True,
            "sequence": sequence,
            "session_generation": int(attempt.get("worker_host_session_generation") or 1),
            "agent_digest": _digest(event.get("agent_id")),
            "session_digest": _digest(event.get("session_id")),
            "turn_digest": turn_digest,
            "observed_at": observed_at,
        }
        attempt["host_stopped_at"] = observed_at

    try:
        from cortex_runtime.questions import permitted_question_categories

        open_questions, _has_more = ledger_db.page_durable_questions(
            root,
            task_id,
            offset=0,
            limit=1,
            attempt_id=attempt_id,
            status="open",
            categories=permitted_question_categories(),
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        open_questions = []
    if open_questions:
        attempt["status"] = "waiting_question"
        attempt["lifecycle_status"] = "paused_awaiting_user"
        attempt["host_stop_outcome"] = "awaiting_user"
        attempt["host_resumable"] = True
        state["status"] = "needs_input"
        event_name = "native_question_stop"
        detail = "durable native Stop inbox paused on an exact durable user question"
        outcome = "awaiting_user"
    else:
        try:
            pending_repair = ledger_db.get_pending_repair_escrow(
                root, task_id=task_id, attempt_id=attempt_id,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            pending_repair = None
        attempt["host_stop_outcome"] = (
            "repair_resume_required"
            if isinstance(pending_repair, Mapping)
            else "native_worker_stopped_without_result_pending_recovery"
        )
        event_name = "native_incomplete_stop"
        detail = "durable native Stop inbox has no canonical result"
        outcome = "repair_resume_required" if isinstance(pending_repair, Mapping) else "resultless"
    _save_reconciled_stop(
        root, task_dir, state, stop_key, attempt, event_name, detail, outcome,
    )
    return True


def reconcile_native_stop_inbox(root: Path, task_dir: Path, state: dict[str, Any]) -> int:
    """Consume every captured Stop that belongs to this locked task snapshot."""
    reconciled = 0
    for stop_key, event in ledger_db.pending_native_host_stops(root):
        if _reconcile_stop_for_task(root, task_dir, state, stop_key, event):
            reconciled += 1

    # Evaluation can fail independently after the irreplaceable Stop was
    # captured. Retry it on every locked lifecycle read until it is persisted.
    evaluation_changed = False
    for attempt in state.get("attempts") or []:
        if not isinstance(attempt, dict) or attempt.get("native_stop_evaluation_pending") is not True:
            continue
        result_ref = str(attempt.get("attempt_result_ref") or "")
        stop = attempt.get("native_terminal_stop")
        if not result_ref or not isinstance(stop, Mapping) or stop.get("observed") is not True:
            continue
        try:
            from cortex_runtime.assignment_evaluator import persist_assignment_evaluation

            persist_assignment_evaluation(root, state, attempt)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        attempt.pop("native_stop_evaluation_pending", None)
        evaluation_changed = True
    if evaluation_changed:
        runtime.save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "native_stop_evaluation_reconciled",
            "retried derived assignment evaluation after durable terminal Stop capture",
        )
    return reconciled


def _observe_stop(event: Mapping[str, Any], root: Path) -> bool:
    epoch = ledger_db.hook_get_native_host_epoch(root, str(event.get("session_id") or ""))
    incarnation = _trusted_codex_incarnation(root)
    if (
        not isinstance(epoch, Mapping)
        or not isinstance(incarnation, Mapping)
        or not hmac.compare_digest(
            str(epoch.get("fingerprint") or ""), str(incarnation.get("fingerprint") or ""),
        )
    ):
        raise NativeStopCaptureError("native host epoch is unavailable")
    document = {
        "schema": ledger_db.NATIVE_HOST_STOP_SCHEMA,
        "agent_id": str(event["agent_id"]),
        "session_id": str(event["session_id"]),
        "turn_id": str(event["turn_id"]),
        "agent_type": str(event["agent_type"]),
        "model": str(event["model"]),
        "permission_mode": str(event["permission_mode"]),
        "stop_hook_active": bool(event["stop_hook_active"]),
        "host_epoch": int(epoch["epoch"]),
        "host_epoch_fingerprint": str(epoch["fingerprint"]),
        "host_envelope_digest": _host_envelope_digest(event, STOP_FIELDS),
        "observed_at": runtime.now(),
    }
    capture = ledger_db.hook_compare_insert_native_stop(root, document)
    if capture not in {"inserted", "same"}:
        raise NativeStopCaptureError("native terminal Stop capture is unavailable")

    # Reconciliation is deliberately best-effort here. The durable inbox is
    # the acknowledgement boundary; any state-lock, SQLite-read, evaluator,
    # or process failure is retried by coordinator lifecycle entry points.
    try:
        matches = _attempts_for_agent(root, str(event["agent_id"]))
        if not matches:
            start = ledger_db.get_latest_native_host_start(root, str(event["agent_id"]))
            if (
                isinstance(start, Mapping)
                and str(start.get("session_id") or "") == str(event.get("session_id") or "")
            ):
                _bind_unambiguous_start(start, root)
                matches = _attempts_for_agent(root, str(event["agent_id"]))
        if len(matches) == 1:
            task_id, _attempt_id = matches[0]
            with runtime.state_lock(
                root,
                timeout_seconds=0.20,
                operation="native_terminal_stop_reconcile",
                task_id=task_id,
            ):
                loaded = runtime.db_load_task(root, task_id)
                if loaded is not None:
                    _definition, state, _plan, artifact_dir = loaded
                    stop_key = ledger_db.native_host_stop_key(
                        str(document["agent_id"]),
                        str(document["session_id"]),
                        str(document["turn_id"]),
                    )
                    _reconcile_stop_for_task(
                        root, root / artifact_dir, state, stop_key, document,
                    )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return True


def promote_incomplete_stop(attempt: dict[str, Any], state: dict[str, Any], result_ref: str) -> bool:
    """Promote exact Stop evidence after a raced canonical result is saved."""
    existing = attempt.get("native_terminal_stop")
    if (
        isinstance(existing, Mapping)
        and existing.get("observed") is True
        and isinstance(result_ref, str)
        and result_ref
        and str(existing.get("result_digest") or "") == _digest(result_ref)
    ):
        return True
    evidence = attempt.get("native_incomplete_stop_evidence")
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("observed") is not True
        or not str(attempt.get("worker_host_thread_id") or "")
        or not isinstance(result_ref, str)
        or not result_ref
    ):
        return False
    # A Stop from a prior paused/repaired turn cannot attest a result produced
    # by a later same-child follow-up.  Wait for that resumed turn's own exact
    # SubagentStop instead of promoting stale evidence.
    resumed_generation = int(attempt.get("worker_host_session_generation") or 1)
    evidence_generation = int(evidence.get("session_generation") or 1)
    if evidence_generation != resumed_generation:
        return False
    sequence = int(state.get("native_lifecycle_sequence") or 0) + 1
    state["native_lifecycle_sequence"] = sequence
    attempt["native_terminal_stop"] = {
        "observed": True,
        "sequence": sequence,
        "result_digest": _digest(result_ref),
        "agent_digest": str(evidence.get("agent_digest") or ""),
        "session_digest": str(evidence.get("session_digest") or ""),
        "turn_digest": str(evidence.get("turn_digest") or ""),
        "observed_at": str(evidence.get("observed_at") or runtime.now()),
        "promoted_at": runtime.now(),
    }
    attempt.pop("native_incomplete_stop_evidence", None)
    return True


def observe(value: object) -> bool:
    event_name = value.get("hook_event_name") if isinstance(value, Mapping) else None
    if event_name == "SessionStart":
        event = _strict_session_start(value)
        return _observe_session_start(event) if event is not None else False
    event = _strict_start(value) if event_name == "SubagentStart" else _strict_stop(value) if event_name == "SubagentStop" else None
    if event is None:
        return False
    root = _existing_root(str(event.get("cwd") or ""))
    if root is None:
        # The native hook is global. A valid Stop outside an activated Cortex
        # ledger has no Cortex durability obligation and must remain fail-open.
        return event_name == "SubagentStop"
    if event_name == "SubagentStop":
        # Capture failures must cross the hook boundary so Codex retries the
        # only terminal host event instead of accepting a lossy success.
        return _observe_stop(event, root)
    try:
        return _observe_start(event, root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
