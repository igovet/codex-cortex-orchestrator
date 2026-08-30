#!/usr/bin/env python3
"""Best-effort, sanitized observer for official Codex lifecycle hooks.

This process never blocks, approves, schedules, resumes, or rejects work. It
only writes to the candidate-owned observation generation when the nonce-bound
lease can be validated. Native values are hashed before they leave this
process; transcript paths and prompt contents are never read.
"""
from __future__ import annotations

import json
import os
import re
import sys
import hashlib
import stat
import time
import hashlib
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", "")).resolve()
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))


def _is_native_spawn_tool(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {
        "agent", "collaboration.spawn_agent", "collaborationspawn_agent", "spawn_agent",
    }


def _load() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    event = value if isinstance(value, dict) else {}
    try:
        scripts = str(PLUGIN_ROOT / "scripts")
        if scripts not in sys.path: sys.path.insert(0, scripts)
        from cortex_runtime.raw_diagnostic import append
        append(kind="lifecycle_payload", payload=event)
    except Exception:
        pass
    return event


def _journal_once():
    try:
        from cortex_runtime.event_journal import EventJournal
        from cortex_runtime.observation_generation import verify_lease_record
        code_home = Path(os.environ["CODEX_HOME"]).resolve()
        candidate = Path(os.environ["CORTEX_CANDIDATE_PATH"]).resolve(strict=True)
        lease_path = code_home / ".cortex-mcp-observations" / "lease.json"
        lease = json.loads(lease_path.read_text(encoding="ascii"))
        lease = verify_lease_record(lease, session_nonce=os.environ.get("CORTEX_SESSION_NONCE"), candidate_path=str(candidate), build_id=os.environ.get("CORTEX_BUILD_ID"), fresh=False)
        generation = code_home / ".cortex-mcp-observations" / "generations" / str(lease["generation_id"])
        return EventJournal.from_generation(generation=generation, build_id=str(lease["build_id"]), code_home=code_home), lease
    except Exception:
        return None, None


def _journal():
    """Give the MCP process a short startup window to publish its lease."""
    for attempt in range(10):
        journal, lease = _journal_once()
        if journal is not None and lease is not None:
            return journal, lease
        if attempt < 9:
            time.sleep(0.2)
    return None, None


def _binding_diag(reason: str, **flags: bool) -> None:
    try:
        hook = Path(__file__).resolve(); code_home = hook.parent.parent.parents[4]
        if code_home.name != ".codex": return
        path = code_home / ".cortex-live-binding-diagnostic.json"; tmp = path.with_suffix(".tmp")
        value = {"reason": reason, "event_shape_ok": False, "launch_receipt_ok": False, "cwd_match": False, "profile_match": False, "nonce_match": False, "ancestry_match": False, "tty_check_applicable": False, "tty_match": False, "write_ok": False}
        value.update({key: bool(val) for key, val in flags.items() if key in value})
        tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"); os.chmod(tmp, 0o600); os.replace(tmp, path); os.chmod(path, 0o600)
    except OSError: return


def _bind_root_session(event: dict[str, Any]) -> None:
    if event.get("hook_event_name") != "SessionStart" or event.get("source") != "startup":
        return
    _binding_diag("attempt", event_shape_ok=bool(event.get("session_id") and event.get("cwd") and not event.get("agent_id") and not event.get("parent_session_id")))
    if not isinstance(event.get("session_id"), str) or not isinstance(event.get("cwd"), str) or event.get("agent_id") or event.get("parent_session_id"):
        return
    try:
        hook = Path(__file__).resolve(); candidate = hook.parent.parent; code_home = candidate.parents[4]
        if not (hook.parent.name == "hooks" and candidate.parent.name == "cortex" and candidate.parent.parent.name == "cortex" and candidate.parent.parent.parent.name == "cache" and code_home.name == ".codex"):
            return
        receipt = code_home / ".cortex-live-launch.json"
        if not receipt.is_file() or receipt.is_symlink() or stat.S_IMODE(receipt.stat().st_mode) != 0o600:
            return
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if not value.get("active") or value.get("cwd") != event["cwd"] or value.get("profile_fingerprint") != hashlib.sha256(str(code_home).encode()).hexdigest() or (value.get("tty") and os.isatty(0) and os.ttyname(0) != value.get("tty")):
            return
        pid = int(value.get("pid", 0)); current = os.getpid(); seen = set()
        for _ in range(16):
            if current in seen or current <= 1: return
            seen.add(current)
            if current == pid: break
            status = Path(f"/proc/{current}/status").read_text(encoding="ascii")
            match = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
            if not match: return
            current = int(match.group(1))
        else: return
        binding = code_home / ".cortex-live-binding.json"; tmp = binding.with_suffix(".tmp")
        record = {"session_id": event["session_id"], "source": "cli", "cwd": event["cwd"], "session_nonce": value["session_nonce"], "workdir_fingerprint": hashlib.sha256(event["cwd"].encode()).hexdigest(), "isolated_codex_fingerprint": value["profile_fingerprint"]}
        tmp.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"); os.chmod(tmp, 0o600); os.replace(tmp, binding); os.chmod(binding, 0o600)
        value["captured"] = True; receipt_tmp = receipt.with_suffix(".capture.tmp"); receipt_tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"); os.chmod(receipt_tmp, 0o600); os.replace(receipt_tmp, receipt); os.chmod(receipt, 0o600)
        _binding_diag("captured", event_shape_ok=True, launch_receipt_ok=True, cwd_match=True, profile_match=True, nonce_match=True, ancestry_match=True, tty_match=True, write_ok=True)
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return


def main() -> int:
    event = _load()
    _bind_root_session(event)
    event_name = event.get("hook_event_name")
    if event_name in {"PreToolUse", "PostToolUse"} and _is_native_spawn_tool(event.get("tool_name")):
        # The server renderer owns this marker.  Treat all other Agent calls
        # as unrelated; never retain or inspect their native payload beyond
        # extracting the exact marker token.
        raw = json.dumps(event.get("tool_input"), ensure_ascii=False) if event_name == "PreToolUse" else json.dumps(event.get("tool_response"), ensure_ascii=False)
        marker = re.search(r"(?<![A-Za-z0-9_])dc_[0-9a-f]{32}(?![A-Za-z0-9_])", raw)
        if marker is None:
            return 0
        journal, lease = _journal()
        if journal is None or lease is None:
            return 0
        # Official Codex hooks expose Agent tool_use_id/tool_input and a
        # separate SubagentStart agent_id, but no authenticated relation
        # between those events.  Therefore marker observation is never a
        # native identity binding.  Preserve the honest capability state.
        source = "unavailable" if event_name == "PreToolUse" else "ambiguous"
        journal.emit_lifecycle(event_kind="native_dispatch", source=source, session=event.get("session_id"), turn=event.get("turn_id"), parent=event.get("session_id"), generation=lease.get("generation_id"), dispatch_correlation_marker=marker.group(0), reason=source)
        return 0
    mapping = {
        "SessionStart": "session_start",
        "SessionEnd": "session_end",
        "SubagentStart": "subagent_start",
        "SubagentStop": "subagent_stop",
        "PreCompact": "pre_compact",
        "PostCompact": "post_compact",
    }
    kind = mapping.get(event_name)
    if kind is None:
        return 0
    source = event.get("source") if event_name == "SessionStart" else event.get("trigger")
    if not isinstance(source, str):
        source = "unknown"
    status = None
    if event_name == "SubagentStop":
        # The official event does not prove a resume.  Only classify the
        # explicit stop-hook continuation request; otherwise remain unknown.
        status = "unknown"
    journal, lease = _journal()
    if journal is None or lease is None:
        return 0
    journal.emit_lifecycle(
        event_kind=kind,
        source=source if source in {"startup", "resume", "clear", "compact", "manual", "auto", "unknown"} else "unknown",
        session=event.get("session_id"),
        turn=event.get("turn_id"),
        agent=event.get("agent_id"),
        parent=event.get("session_id"),
        generation=lease.get("generation_id"),
        status=status,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
