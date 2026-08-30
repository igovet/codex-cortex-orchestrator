"""Temporary isolated live-diagnostic sink; disabled unless explicitly enabled."""
from __future__ import annotations
import json, os, stat, time
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORD = 256 * 1024

def enabled() -> bool:
    return os.environ.get("CORTEX_RAW_DIAGNOSTIC") == "1"

def append(*, kind: str, payload: object) -> str:
    if os.environ.get("CORTEX_RAW_DIAGNOSTIC") != "1": return "disabled"
    if not isinstance(kind, str) or not kind: return "serialization_failed"
    home = Path(os.environ.get("HOME", "")); code = Path(os.environ.get("CODEX_HOME", ""))
    # Codex's stdio MCP launcher may omit CODEX_HOME while setting cwd to the
    # verified candidate. Derive it only from this packaged module's fixed
    # candidate topology; never from an ambient or caller-supplied path.
    if not code.is_absolute():
        candidate = Path(__file__).resolve().parents[2]
        if candidate.name and candidate.parent.name == "cortex" and candidate.parent.parent.name == "cache":
            code = candidate.parents[3]
    if not home.is_absolute() and code.is_absolute(): home = code.parent
    # cortex-dev exports HOME as the isolated `.cortex-dev` directory itself.
    # Validate that exact launcher relationship without depending on a user
    # or machine-specific absolute path; stable HOME/.codex is rejected.
    if not home.is_absolute() or not code.is_absolute() or home.name != ".cortex-dev" or code != home / ".codex": return "guard_rejected"
    try:
        if home.is_symlink() or code.is_symlink() or not home.is_dir() or not code.is_dir(): return "guard_rejected"
        if home.stat().st_uid != os.getuid() or code.stat().st_uid != os.getuid(): return "guard_rejected"
        if stat.S_IMODE(home.stat().st_mode) != 0o700 or stat.S_IMODE(code.stat().st_mode) != 0o700: return "guard_rejected"
    except OSError:
        return "guard_rejected"
    try:
        root = code / ".cortex-raw-diagnostic"
        root.mkdir(mode=0o700, parents=True, exist_ok=True); os.chmod(root, 0o700)
        if root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700: return "guard_rejected"
        path = root / "events.jsonl"
        if path.exists() and (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()): return "guard_rejected"
        record = {"ts_ns": time.time_ns(), "pid": os.getpid(), "kind": kind, "outcome": "written", "payload": payload}
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RECORD: encoded = json.dumps({"ts_ns": time.time_ns(), "pid": os.getpid(), "kind": kind, "outcome": "written", "payload": "bounded"}, separators=(",", ":")).encode() + b"\n"
        with open(path, "ab", buffering=0) as stream:
            os.chmod(path, 0o600); stream.write(encoded)
        if path.stat().st_size > MAX_BYTES:
            data = path.read_bytes()[-MAX_BYTES:]
            start = data.find(b"\n") + 1
            tmp = root / ".events.tmp"
            with open(tmp, "wb") as stream: stream.write(data[start:]); os.chmod(tmp, 0o600)
            os.replace(tmp, path); os.chmod(path, 0o600)
        return "written"
    except (OSError, ValueError, TypeError):
        return "write_failed"
