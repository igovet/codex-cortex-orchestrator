"""Owner-only runtime state and identity helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

BASE_VERSION = "1.15.9"


def _safe_existing(path: Path) -> os.stat_result | None:
    """Return metadata only for a single-link, owner-only regular file."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise OSError(f"unsafe runtime file: {path.name}")
    return info


def _atomic_write(path: Path, data: bytes, *, directory: Path) -> None:
    _safe_existing(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class RuntimePaths:
    codex_home: Path

    @property
    def root(self) -> Path:
        return self.codex_home / "cortex" / "runtime"

    @property
    def lock(self) -> Path:
        return self.root / "gateway.lock"

    @property
    def state(self) -> Path:
        return self.root / "gateway.json"

    @property
    def pid(self) -> Path:
        return self.root / "gateway.pid"

    @property
    def log(self) -> Path:
        return self.root / "gateway.log"

    def ensure(self) -> None:
        if not self.codex_home.is_absolute():
            raise OSError("CODEX_HOME must be absolute")
        current = Path(self.codex_home.anchor)
        for part in self.codex_home.parts[1:]:
            current /= part
            if current.is_symlink():
                raise OSError("CODEX_HOME contains an unsafe symlink")
        for path in (self.codex_home, self.codex_home / "cortex", self.root):
            try:
                info = path.lstat()
            except FileNotFoundError:
                path.mkdir(mode=0o700)
                info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise OSError("runtime directory contains an unsafe symlink")
            os.chmod(path, 0o700)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("runtime directory must be owner-only and owned by the current user")

    def open_lock(self) -> int:
        self.ensure()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("unsafe runtime lock")
            os.fchmod(descriptor, 0o600)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise


@dataclass(frozen=True)
class GatewayState:
    pid: int
    process_start: str
    instance_id: str
    host: str
    port: int
    plugin_version: str
    package_digest: str
    executable: str
    upstream: str
    started_at: str
    policy_revision: int
    phase: str = "ready"
    process_binary: str = ""
    dependency_manifest_digest: str = "unbound"
    dependency_bytes_digest: str = "unbound"
    dependency_digest: str = "unbound"
    # Actual OS-selected HTTPS MITM port; absent in legacy state files.
    mitm_port: int | None = None

    @classmethod
    def new(cls, *, pid: int, host: str, port: int, plugin_version: str, package_digest: str, upstream: str, policy_revision: int, executable: str | None = None, plugin_root: Path | None = None) -> "GatewayState":
        binary = process_executable(pid)
        manifest_digest, bytes_digest, dependency_digest = dependency_identity(plugin_root) if plugin_root is not None else ("unbound", "unbound", "unbound")
        return cls(
            pid=pid,
            process_start=process_identity(pid),
            instance_id=uuid.uuid4().hex,
            host=host,
            port=port,
            plugin_version=plugin_version,
            package_digest=package_digest,
            executable=executable or binary,
            upstream=upstream,
            started_at=str(time.time()),
            policy_revision=policy_revision,
            process_binary=binary,
            dependency_manifest_digest=manifest_digest,
            dependency_bytes_digest=bytes_digest,
            dependency_digest=dependency_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, paths: RuntimePaths) -> None:
        paths.ensure()
        _atomic_write(
            paths.state,
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8"),
            directory=paths.root,
        )
        _atomic_write(paths.pid, str(self.pid).encode("ascii"), directory=paths.root)

    @classmethod
    def read(cls, paths: RuntimePaths) -> "GatewayState | None":
        try:
            paths.ensure()
            _safe_existing(paths.state)
            _safe_existing(paths.pid)
            if _safe_existing(paths.state) is None or _safe_existing(paths.pid) is None:
                return None
            value = json.loads(paths.state.read_text(encoding="utf-8"))
            value.setdefault("mitm_port", value.get("port", 8787) + 1)
            state = cls(**value)
            if state.pid <= 0 or str(state.pid) != paths.pid.read_text(encoding="ascii").strip():
                return None
            if state.process_start == "unknown" or state.executable == "unknown" or state.process_binary == "unknown":
                return None
            return state
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError):
            return None


def process_identity(pid: int) -> str:
    """Return a PID-reuse-resistant Linux process start identity."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return stat.rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, OSError, IndexError):
        return "unknown"


def process_executable(pid: int) -> str:
    try:
        return str(Path(f"/proc/{pid}/exe").resolve())
    except (FileNotFoundError, OSError):
        return "unknown"


def process_entrypoint(pid: int) -> str:
    """Return the script in the real Python entrypoint argv position."""
    try:
        args = process_argv(pid)
        if len(args) < 3 or args[1] != "-B":
            return "unknown"
        candidate = Path(args[2])
        if candidate.is_absolute() and candidate.name == "cortex_gateway.py" and candidate.is_file():
            return str(candidate.resolve())
    except (FileNotFoundError, OSError, ValueError):
        pass
    return "unknown"


def process_argv(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    return [os.fsdecode(value) for value in raw if value]


def process_invocation_matches(state: GatewayState) -> bool:
    try:
        args = process_argv(state.pid)
        if len(args) != 8:
            return False
        executable = process_executable(state.pid)
        return (
            os.path.realpath(args[0]) == executable
            and args[1:] == [
                "-B",
                state.executable,
                "serve",
                "--host",
                state.host,
                "--port",
                str(state.port),
            ]
        )
    except (FileNotFoundError, OSError, ValueError):
        return False


def payload_digest(plugin_root: Path) -> str:
    """Compute the same normalized digest used by the package stamper."""
    digest = hashlib.sha256()
    raw_root = Path(plugin_root)
    if not raw_root.is_absolute():
        raise ValueError("payload root must be absolute")
    current = Path(raw_root.anchor)
    for part in raw_root.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink in payload path")
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise ValueError("payload root must be a real directory")
    root = raw_root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in payload")
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise ValueError("bytecode in payload")
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if relative == ".codex-plugin/plugin.json":
            value = json.loads(data)
            value["version"] = BASE_VERSION
            data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        digest.update(relative.encode() + b"\0" + str(len(data)).encode() + b"\0")
        digest.update(data)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("dependency target contains a symlink")
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise ValueError("dependency target contains bytecode")
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest.update(relative.encode() + b"\0" + str(len(data)).encode() + b"\0")
        digest.update(data)
    return digest.hexdigest()


def dependency_identity(plugin_root: Path | None, target: Path | None = None, *, required: bool = False) -> tuple[str, str, str]:
    """Return lock-manifest, installed-byte, and combined dependency identity."""
    if plugin_root is None:
        return "unbound", "unbound", "unbound"
    manifest = plugin_root / "requirements.lock"
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    raw_target = target or (Path(os.environ["CORTEX_DEPENDENCY_DIR"]) if os.environ.get("CORTEX_DEPENDENCY_DIR") else (Path(os.environ["CODEX_HOME"]) / "cortex" / "deps" / manifest_digest if os.environ.get("CODEX_HOME") else None))
    if raw_target is None:
        if required:
            raise ValueError("CORTEX_DEPENDENCY_DIR is required for the isolated gateway")
        bytes_digest = "unbound"
    else:
        target_path = raw_target.expanduser()
        if not target_path.is_absolute() or target_path.is_symlink():
            raise ValueError("dependency target must be an absolute non-symlink directory")
        current = Path(target_path.anchor)
        for part in target_path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError("dependency target contains an unsafe symlink path")
        info = target_path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("dependency target must be owner-only and owned by the current user")
        bytes_digest = _tree_digest(target_path)
    combined = hashlib.sha256(f"{manifest_digest}\0{bytes_digest}".encode("ascii")).hexdigest()
    return manifest_digest, bytes_digest, combined


def state_matches_process(state: GatewayState) -> bool:
    try:
        proc = Path(f"/proc/{state.pid}").lstat()
        return (
            state.pid > 0
            and proc.st_uid == os.getuid()
            and state.process_start != "unknown"
            and state.executable != "unknown"
            and state.process_binary != "unknown"
            and process_identity(state.pid) == state.process_start
            and process_executable(state.pid) == state.process_binary
            and process_entrypoint(state.pid) == state.executable
            and process_invocation_matches(state)
        )
    except (FileNotFoundError, OSError):
        return False
