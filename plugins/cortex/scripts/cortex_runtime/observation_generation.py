"""Exact-session observation intent/lease protocol."""
from __future__ import annotations
import fcntl, hashlib, hmac, json, os, secrets, stat, time
from pathlib import Path
from collections.abc import Mapping
from typing import Any

ROOT_NAME = ".cortex-mcp-observations"
INTENT_NAME = "intent.json"
LEASE_NAME = "lease.json"
REQUEST_NAME = "request.json"
READY_NAME = "ready.json"
EVENTS_NAME = "events.jsonl"
SESSION_NAME = "cortex-v12-smoke"
REQUEST_TTL_NS = 10 * 60 * 1_000_000_000

class ObservationGenerationError(RuntimeError): pass

def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
def _digest(value: Mapping[str, Any]) -> str: return hashlib.sha256(_canonical(value)).hexdigest()
def _nonce_bytes(value: object) -> bytes:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ObservationGenerationError("observation nonce is invalid")
    try: return bytes.fromhex(value)
    except (ValueError, TypeError) as exc: raise ObservationGenerationError("observation nonce is invalid") from exc
def _private_directory(path: Path, *, create: bool = False) -> None:
    if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try: info = path.lstat()
    except OSError as exc: raise ObservationGenerationError("observation directory is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700: raise ObservationGenerationError("observation directory is not owner-only")
def _private_file(path: Path) -> None:
    try: info = path.lstat()
    except OSError as exc: raise ObservationGenerationError("observation file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600: raise ObservationGenerationError("observation file is not owner-only")
def candidate_codex_home(package_root: Path) -> Path:
    root = package_root.absolute()
    try:
        if root.parents[0].name != "cortex" or root.parents[1].name != "cortex" or root.parents[2].name != "cache" or root.parents[3].name != "plugins": raise ObservationGenerationError("candidate package topology is invalid")
        code_home = root.parents[4]; _private_directory(code_home)
        if root != code_home / "plugins" / "cache" / "cortex" / "cortex" / root.name: raise ObservationGenerationError("candidate package is outside isolated cache")
        return code_home
    except (IndexError, OSError) as exc:
        if isinstance(exc, ObservationGenerationError): raise
        raise ObservationGenerationError("candidate runtime root is unavailable") from exc
def _root(code_home: Path) -> Path:
    root = code_home / ROOT_NAME; _private_directory(root, create=True); return root
def _locked(root: Path):
    lock = root / ".lock"; fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600); os.chmod(lock, 0o600); return os.fdopen(fd, "r+")
def _write(path: Path, value: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_canonical(value) + b"\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path); os.chmod(path, 0o600)
def _read(path: Path) -> dict[str, Any]:
    try:
        _private_file(path); value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ObservationGenerationError("observation record is invalid") from exc
    if not isinstance(value, dict): raise ObservationGenerationError("observation record is invalid")
    return value

def verify_lease_record(lease: object, *, session_nonce: str | None = None, candidate_path: str | None = None, build_id: str | None = None, candidate_version: str | None = None, catalogue_count: int | None = None, catalogue_digest: str | None = None, fresh: bool = True, allow_revoked: bool = False) -> dict[str, Any]:
    """Canonical fail-closed lease validation shared by claim and observers."""
    if not isinstance(lease, Mapping): raise ObservationGenerationError("observation lease is invalid")
    required = {"schema_version", "session", "nonce", "generation_id", "build_id", "candidate_version", "candidate_path", "catalogue_count", "catalogue_digest", "created_ns", "state", "processes", "signature"}
    allowed_shapes = (required, required | {"claimed_ns", "active_process_registration"})
    if allow_revoked:
        allowed_shapes = (*allowed_shapes, required | {"claimed_ns", "active_process_registration", "revoked_ns"})
    if set(lease) not in allowed_shapes:
        raise ObservationGenerationError("observation lease shape is invalid")
    value = dict(lease)
    nonce = value.get("nonce")
    generation = value.get("generation_id")
    try:
        if not isinstance(nonce, str) or len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce): raise ValueError
        if not isinstance(generation, str) or len(generation) != 48 or any(c not in "0123456789abcdef" for c in generation): raise ValueError
        signature = value.get("signature")
        if not isinstance(signature, str) or len(signature) != 64: raise ValueError
        expected = hmac.new(bytes.fromhex(nonce), _canonical({k:v for k,v in value.items() if k != "signature"}), hashlib.sha256).hexdigest()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ObservationGenerationError("observation lease identity is invalid") from exc
    if not hmac.compare_digest(signature, expected): raise ObservationGenerationError("observation lease signature is invalid")
    allowed_states = {"pending", "claimed"} | ({"revoked"} if allow_revoked else set())
    if value.get("schema_version") != 2 or value.get("session") != SESSION_NAME or value.get("state") not in allowed_states: raise ObservationGenerationError("observation lease state is invalid")
    if session_nonce is not None and nonce != session_nonce: raise ObservationGenerationError("observation lease nonce does not match session")
    for key, expected_value in (("candidate_path", candidate_path), ("build_id", build_id), ("candidate_version", candidate_version), ("catalogue_count", catalogue_count), ("catalogue_digest", catalogue_digest)):
        if expected_value is not None and value.get(key) != expected_value: raise ObservationGenerationError("observation lease candidate identity does not match")
    created = value.get("created_ns")
    # Freshness bounds only the unclaimed launch handshake. Once the exact
    # nonce-bound candidate has claimed the lease, the ordinary interactive
    # tmux session may legitimately run longer than the handshake TTL. Its
    # authority then ends only through explicit nonce-bound revocation by the
    # live-smoke stop path; retaining the creation-age check here made long
    # real orchestrations lose their event stream before closure.
    pending_is_stale = (
        fresh and value.get("state") == "pending"
        and not (0 <= time.time_ns() - created <= REQUEST_TTL_NS)
    ) if isinstance(created, int) and not isinstance(created, bool) else True
    if isinstance(created, bool) or not isinstance(created, int) or created <= 0 or pending_is_stale:
        raise ObservationGenerationError("observation lease is stale")
    if not isinstance(value.get("processes"), list): raise ObservationGenerationError("observation lease process registration is invalid")
    return value
def request_generation(*, code_home: Path, build_id: str, candidate_version: str, catalogue_count: int, catalogue_digest: str, session_nonce: str | None = None) -> dict[str, Any]:
    _private_directory(code_home); root = _root(code_home); nonce = session_nonce or secrets.token_hex(32)
    if not isinstance(nonce, str) or len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce): raise ObservationGenerationError("session nonce is invalid")
    value = {"schema_version": 2, "session": SESSION_NAME, "nonce": nonce, "generation_id": secrets.token_hex(24), "build_id": build_id, "candidate_version": candidate_version, "catalogue_count": catalogue_count, "catalogue_digest": catalogue_digest, "created_ns": time.time_ns(), "state": "pending"}
    value["signature"] = hmac.new(_nonce_bytes(nonce), _canonical(value), hashlib.sha256).hexdigest()
    with _locked(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX); _write(root / INTENT_NAME, value); fcntl.flock(lock, fcntl.LOCK_UN)
    return value

def create_session_intent(*, code_home: Path, session_nonce: str) -> dict[str, Any]:
    """Create the pre-launch intent; candidate refresh fills its identity."""
    _private_directory(code_home); root = _root(code_home)
    if not isinstance(session_nonce, str) or len(session_nonce) != 64 or any(c not in "0123456789abcdef" for c in session_nonce):
        raise ObservationGenerationError("session nonce is invalid")
    value = {"schema_version": 2, "session": SESSION_NAME, "nonce": session_nonce, "created_ns": time.time_ns(), "state": "created"}
    value["signature"] = hmac.new(_nonce_bytes(session_nonce), _canonical(value), hashlib.sha256).hexdigest()
    with _locked(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX); _write(root / INTENT_NAME, value); fcntl.flock(lock, fcntl.LOCK_UN)
    return value
def consume_intent(*, code_home: Path, package_root: Path, build_id: str, candidate_version: str, catalogue_count: int, catalogue_digest: str, session_nonce: str) -> dict[str, Any]:
    root = _root(code_home)
    with _locked(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX); intent = _read(root / INTENT_NAME)
        base_required = {"schema_version", "session", "nonce", "created_ns", "state", "signature"}
        pending_required = base_required | {"generation_id", "build_id", "candidate_version", "catalogue_count", "catalogue_digest"}
        if (intent.get("state") == "created" and set(intent) != base_required) or (intent.get("state") == "pending" and set(intent) != pending_required) or intent.get("state") not in {"created", "pending"} or intent.get("schema_version") != 2:
            raise ObservationGenerationError("observation intent shape is invalid")
        if intent.get("session") != SESSION_NAME or intent.get("nonce") != session_nonce or intent.get("build_id") != build_id or intent.get("candidate_version") != candidate_version or intent.get("catalogue_count") != catalogue_count or intent.get("catalogue_digest") != catalogue_digest:
            if intent.get("state") != "created":
                raise ObservationGenerationError("observation intent does not match candidate")
        nonce = _nonce_bytes(session_nonce)
        generation = intent.get("generation_id") or secrets.token_hex(24)
        created = intent.get("created_ns")
        if not isinstance(generation, str) or len(generation) != 48 or any(c not in "0123456789abcdef" for c in generation):
            raise ObservationGenerationError("observation intent generation is invalid")
        if isinstance(created, bool) or not isinstance(created, int) or created <= 0 or not (0 <= time.time_ns() - created <= REQUEST_TTL_NS):
            raise ObservationGenerationError("observation intent is stale")
        unsigned = {k:v for k,v in intent.items() if k != "signature"}
        if not isinstance(intent.get("signature"), str) or not hmac.compare_digest(intent["signature"], hmac.new(nonce, _canonical(unsigned), hashlib.sha256).hexdigest()): raise ObservationGenerationError("observation intent signature is invalid")
        lease = {"schema_version": 2, "session": SESSION_NAME, "nonce": session_nonce, "generation_id": generation, "build_id": build_id, "candidate_version": candidate_version, "candidate_path": str(package_root.absolute()), "catalogue_count": catalogue_count, "catalogue_digest": catalogue_digest, "created_ns": intent["created_ns"], "state": "pending", "processes": []}
        lease["signature"] = hmac.new(nonce, _canonical(lease), hashlib.sha256).hexdigest(); _write(root / LEASE_NAME, lease)
        intent["state"] = "consumed"; _write(root / INTENT_NAME, intent); fcntl.flock(lock, fcntl.LOCK_UN)
    return lease
def claim_generation(*, package_root: Path, build_id: str, candidate_version: str, catalogue_count: int, catalogue_digest: str, session_nonce: str | None = None) -> tuple[Path, dict[str, Any]]:
    code_home = candidate_codex_home(package_root); root = _root(code_home)
    with _locked(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lease = verify_lease_record(_read(root / LEASE_NAME), session_nonce=session_nonce, candidate_path=str(package_root.absolute()), build_id=build_id, candidate_version=candidate_version, catalogue_count=catalogue_count, catalogue_digest=catalogue_digest)
        if session_nonce is None:
            raise ObservationGenerationError("observation lease requires session nonce")
        generation = root / "generations" / str(lease["generation_id"]); _private_directory(generation.parent, create=True)
        _private_directory(generation, create=True)
        if lease.get("state") == "pending":
            _write(generation / REQUEST_NAME, lease)
            lease["state"] = "claimed"; lease["claimed_ns"] = time.time_ns()
        # A restart is allowed to reuse the same exact generation.  The
        # process registration is server-owned and is appended atomically;
        # repeated initialization by one PID reuses its registration.
        processes = lease.get("processes")
        if not isinstance(processes, list): processes = []
        registration = next((item for item in processes if isinstance(item, Mapping) and item.get("pid") == os.getpid()), None)
        if not isinstance(registration, Mapping):
            registration = {"pid": os.getpid(), "registration": secrets.token_hex(16), "registered_ns": time.time_ns()}
            processes.append(registration)
        lease["processes"] = processes; lease["active_process_registration"] = dict(registration)
        unsigned = {k:v for k,v in lease.items() if k != "signature"}
        lease["signature"] = hmac.new(bytes.fromhex(str(lease["nonce"])), _canonical(unsigned), hashlib.sha256).hexdigest()
        _write(root / LEASE_NAME, lease); _write(generation / REQUEST_NAME, lease); fcntl.flock(lock, fcntl.LOCK_UN)
    return generation, lease
def write_ready_receipt(generation: Path, *, build_id: str, catalogue_count: int, catalogue_digest: str) -> None:
    request = _read(generation / REQUEST_NAME)
    if request.get("build_id") != build_id or request.get("catalogue_count") != catalogue_count or request.get("catalogue_digest") != catalogue_digest: raise ObservationGenerationError("ready receipt does not match lease")
    active = request.get("active_process_registration")
    registration = active.get("registration") if isinstance(active, Mapping) and isinstance(active.get("registration"), str) else secrets.token_hex(16)
    value = {"schema_version": 2, "session": request["session"], "nonce": request["nonce"], "generation_id": request["generation_id"], "lease_signature": request["signature"], "build_id": build_id, "catalogue_count": catalogue_count, "catalogue_digest": catalogue_digest, "process_id": os.getpid(), "process_registration": registration}
    ready = generation / READY_NAME
    if ready.exists():
        try:
            prior = _read(ready)
            if prior.get("process_id") == os.getpid() and prior.get("process_registration") == registration:
                return
        except (OSError, ObservationGenerationError, UnicodeError, json.JSONDecodeError):
            pass
    _write(ready if not ready.exists() else generation / f"ready-{os.getpid()}-{registration}.json", value)

def revoke_session(*, code_home: Path, session_nonce: str) -> None:
    root = _root(code_home)
    with _locked(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: lease = _read(root / LEASE_NAME)
        except (OSError, ObservationGenerationError): lease = None
        if lease is not None and lease.get("session") == SESSION_NAME and lease.get("nonce") == session_nonce:
            lease["state"] = "revoked"; lease["revoked_ns"] = time.time_ns()
            unsigned = {key: value for key, value in lease.items() if key != "signature"}
            lease["signature"] = hmac.new(bytes.fromhex(str(lease["nonce"])), _canonical(unsigned), hashlib.sha256).hexdigest()
            _write(root / LEASE_NAME, lease)
        try: intent = _read(root / INTENT_NAME)
        except (OSError, ObservationGenerationError): intent = None
        if intent is not None and intent.get("session") == SESSION_NAME and intent.get("nonce") == session_nonce:
            intent["state"] = "revoked"; _write(root / INTENT_NAME, intent)
        fcntl.flock(lock, fcntl.LOCK_UN)
