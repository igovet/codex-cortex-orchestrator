"""Global Cortex Model Gateway configuration and immutable snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import errno
import os
from pathlib import Path
import stat
import tempfile
import threading
import tomllib
from urllib.parse import urlparse

from .defaults import (
    ALLOWED_EFFORTS,
    DEFAULT_EFFORT,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DEFAULT_UPSTREAM,
    MAX_MODEL_LENGTH,
    SCHEMA_VERSION,
)


class ConfigError(ValueError):
    """A safe, user-actionable configuration error."""


def effective_codex_home(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = env.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _assert_safe_home(home: Path, *, create: bool = False) -> Path:
    """Reject symlinked CODEX_HOME/config roots before any write follows them."""
    home = home.expanduser()
    if not home.is_absolute():
        raise ConfigError("CODEX_HOME must be an absolute path")
    current = Path(home.anchor)
    for part in home.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if create:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    # Concurrent proxy startups may create the same home
                    # component; re-lstat below still enforces the safety
                    # checks before any descendant write.
                    pass
                info = current.lstat()
            else:
                continue
        if stat.S_ISLNK(info.st_mode):
            raise ConfigError("CODEX_HOME contains an unsafe symlink")
        if not stat.S_ISDIR(info.st_mode):
            raise ConfigError("CODEX_HOME must contain directories only")
    return home


def config_path(codex_home: Path | None = None) -> Path:
    return (codex_home or effective_codex_home()) / "cortex" / "config.toml"


@dataclass(frozen=True)
class PolicySnapshot:
    revision: int
    loaded_at: str
    router_enabled: bool
    compaction_enabled: bool
    model: str
    effort: str
    gateway_enabled: bool
    listener: tuple[str, int]
    upstream: str
    config_status: str = "valid"
    error_code: str | None = None
    restart_required: bool = False
    fingerprint: str | None = None
    pending_listener: tuple[str, int] | None = None
    pending_upstream: str | None = None

    @property
    def host(self) -> str:
        return self.listener[0]

    @property
    def port(self) -> int:
        return self.listener[1]

    def public_status(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "config_status": self.config_status,
            "restart_required": self.restart_required,
            "gateway_enabled": self.gateway_enabled,
            "router_enabled": self.router_enabled,
            "compaction": {
                "enabled": self.compaction_enabled,
                "model": self.model,
                "reasoning_effort": self.effort,
            },
            "listener": {"host": self.host, "port": self.port},
            "upstream": self.upstream,
            "pending_listener": ({"host": self.pending_listener[0], "port": self.pending_listener[1]} if self.pending_listener else None),
            "pending_upstream": self.pending_upstream,
            "error_code": self.error_code,
        }


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _table(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table")
    return value


def _unknown(table: dict[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"unknown {label} key: {unknown[0]}")


def _validate_upstream(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise ConfigError("gateway.upstream_base_url must be a non-empty URL")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("gateway.upstream_base_url must be an HTTPS URL without credentials or query")
    if parsed.hostname != "chatgpt.com" or parsed.port not in (None, 443):
        raise ConfigError("gateway.upstream_base_url must use the fixed chatgpt.com upstream")
    if parsed.path.rstrip("/") != "/backend-api/codex":
        raise ConfigError("gateway.upstream_base_url must end with /backend-api/codex")
    return raw.rstrip("/")


def _parse(data: dict[str, object], *, path: Path) -> tuple[bool, bool, bool, str, str, str, int, str]:
    _unknown(data, {"schema_version", "gateway", "model_router"}, "root")
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ConfigError("schema_version must be 1")
    gateway = _table(data.get("gateway", {}), "gateway")
    _unknown(gateway, {"enabled", "host", "port", "upstream_base_url"}, "gateway")
    gateway_enabled = _bool(gateway.get("enabled", False), "gateway.enabled")
    host = gateway.get("host", DEFAULT_HOST)
    if not isinstance(host, str) or host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError("gateway.host must be loopback")
    port = gateway.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("gateway.port must be between 1 and 65535")
    upstream = _validate_upstream(gateway.get("upstream_base_url", DEFAULT_UPSTREAM))

    router = _table(data.get("model_router", {}), "model_router")
    _unknown(router, {"enabled", "default", "compaction"}, "model_router")
    router_enabled = _bool(router.get("enabled", False), "model_router.enabled")
    default = _table(router.get("default", {}), "model_router.default")
    _unknown(default, {"mode"}, "model_router.default")
    if default.get("mode", "passthrough") != "passthrough":
        raise ConfigError("model_router.default.mode must be passthrough")
    compaction = _table(router.get("compaction", {}), "model_router.compaction")
    _unknown(compaction, {"enabled", "model", "reasoning_effort"}, "model_router.compaction")
    compaction_enabled = _bool(compaction.get("enabled", False), "model_router.compaction.enabled")
    model = compaction.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not model or len(model) > MAX_MODEL_LENGTH or any(ord(ch) < 32 for ch in model):
        raise ConfigError("model_router.compaction.model must be a bounded non-empty string")
    effort = compaction.get("reasoning_effort", DEFAULT_EFFORT)
    if not isinstance(effort, str) or effort not in ALLOWED_EFFORTS:
        raise ConfigError("model_router.compaction.reasoning_effort is unsupported")
    return gateway_enabled, router_enabled, compaction_enabled, model, effort, host, port, upstream


class ConfigLoader:
    """Read-only loader for the one effective CODEX_HOME configuration."""

    def __init__(self, codex_home: Path | None = None):
        self.codex_home = _assert_safe_home(Path(codex_home or effective_codex_home()))
        self.path = config_path(self.codex_home)

    def _check_path(self, *, create_parent: bool = False) -> None:
        _assert_safe_home(self.codex_home, create=create_parent)
        cortex = self.codex_home / "cortex"
        try:
            info = cortex.lstat()
        except FileNotFoundError:
            if not create_parent:
                return
            try:
                cortex.mkdir(mode=0o700)
            except FileExistsError:
                # Another startup may create the directory concurrently; the
                # lstat below still performs the symlink/type safety check.
                pass
            info = cortex.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ConfigError("CODEX_HOME/cortex is an unsafe configuration root")
        if create_parent:
            os.chmod(cortex, 0o700)

    @staticmethod
    def _validate_file_info(info: os.stat_result) -> None:
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ConfigError("configuration file is an unsafe link or file")

    def _observed_bytes(self) -> tuple[bytes, str] | None:
        """Read and fingerprint one opened inode, never two path lookups."""
        self._check_path()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise ConfigError("configuration file is an unsafe link or file") from exc
        try:
            info = os.fstat(descriptor)
            self._validate_file_info(info)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                data = stream.read()
                final_info = os.fstat(stream.fileno())
            self._validate_file_info(final_info)
            if (
                final_info.st_dev != info.st_dev
                or final_info.st_ino != info.st_ino
                or final_info.st_mtime_ns != info.st_mtime_ns
                or final_info.st_size != info.st_size
            ):
                raise ConfigError("configuration changed during read")
            info = final_info
        finally:
            if descriptor != -1:
                os.close(descriptor)
        fingerprint = hashlib.sha256(
            f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}:{info.st_size}".encode() + data
        ).hexdigest()
        return data, fingerprint

    def is_configured(self) -> bool:
        """Return true only for a safe existing config; reject unsafe paths."""
        observed = self._observed_bytes()
        return observed is not None

    def fingerprint(self) -> str | None:
        observed = self._observed_bytes()
        return observed[1] if observed is not None else None

    def read(self) -> tuple[dict[str, object], str]:
        observed = self._observed_bytes()
        if observed is None:
            raise ConfigError("configuration is not configured")
        raw, fingerprint = observed
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("configuration TOML is invalid") from exc
        return data, fingerprint

    def validate(self) -> dict[str, object]:
        data, _ = self.read()
        _parse(data, path=self.path)
        return data

    def snapshot(self, revision: int = 1, previous: PolicySnapshot | None = None) -> PolicySnapshot:
        data, digest = self.read()
        values = _parse(data, path=self.path)
        current = previous
        requested_listener = (values[5], values[6])
        binding_changed = bool(current and (current.listener != requested_listener or current.upstream != values[7]))
        listener = current.listener if binding_changed else requested_listener
        upstream = current.upstream if binding_changed else values[7]
        return PolicySnapshot(
            revision=revision,
            loaded_at=datetime.now(timezone.utc).isoformat(),
            gateway_enabled=values[0],
            router_enabled=values[1],
            compaction_enabled=values[2],
            model=values[3],
            effort=values[4],
            listener=listener,
            upstream=upstream,
            restart_required=binding_changed,
            # ``digest`` is the descriptor-backed observation used to parse
            # this snapshot.  Do not perform a second path read here: an
            # atomic replacement between read() and fingerprint() could cache
            # enabled policy under a disabled file's fingerprint.
            fingerprint=digest,
            pending_listener=requested_listener if binding_changed else None,
            pending_upstream=values[7] if binding_changed else None,
        )


class ConfigManager:
    """Atomically swap complete immutable snapshots on file changes."""

    def __init__(self, loader: ConfigLoader | None = None):
        self.loader = loader or ConfigLoader()
        self._lock = threading.RLock()
        self._snapshot: PolicySnapshot | None = None
        self._fingerprint: str | None = None
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def load(self, *, cold: bool = True) -> PolicySnapshot:
        with self._lock:
            try:
                snapshot = self.loader.snapshot(
                    revision=(self._snapshot.revision + 1 if self._snapshot else 1),
                    previous=self._snapshot,
                )
            except ConfigError:
                self._last_error = "invalid_configuration"
                if cold or self._snapshot is None:
                    raise
                self._snapshot = replace(self._snapshot, config_status="stale", error_code=self._last_error)
                return self._snapshot
            self._snapshot = snapshot
            # The loader snapshot and this change detector share one exact
            # descriptor-backed observation.  A second path lookup here would
            # reintroduce the read/fingerprint split this manager prevents.
            self._fingerprint = snapshot.fingerprint
            self._last_error = None
            return snapshot

    def snapshot(self) -> PolicySnapshot:
        with self._lock:
            if self._snapshot is None:
                return self.load(cold=True)
            if self.loader.fingerprint() != self._fingerprint:
                return self.load(cold=False)
            return self._snapshot

    def reload(self) -> PolicySnapshot:
        return self.load(cold=self._snapshot is None)


DEFAULT_CONFIG = """schema_version = 1

[gateway]
enabled = true
host = \"127.0.0.1\"
port = 8787
upstream_base_url = \"https://chatgpt.com/backend-api/codex\"

[model_router]
enabled = true

[model_router.default]
mode = \"passthrough\"

[model_router.compaction]
enabled = true
model = \"gpt-5.6-luna\"
reasoning_effort = \"medium\"
"""


@contextmanager
def _config_write_lock(path: Path):
    """Serialize bootstrap writers while retaining atomic destination publish."""
    import fcntl

    lock_path = path / ".config.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_default_config_locked(loader: ConfigLoader, *, overwrite: bool) -> Path:
    try:
        existing = loader.path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
        raise ConfigError("configuration file is an unsafe link or file")
    if loader.path.exists() and not overwrite:
        # Idempotent configure must not silently accept an invalid file or
        # replace comments/user settings.  Validate, then leave bytes intact.
        loader.validate()
        return loader.path
    fd, temporary = tempfile.mkstemp(prefix=".config.", dir=loader.path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(DEFAULT_CONFIG)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if overwrite:
            os.replace(temporary, loader.path)
        else:
            # Hard-linking a fully written temporary file creates the missing
            # destination atomically and fails without replacement if another
            # startup wins the race.  This is deliberately create-only.
            try:
                os.link(temporary, loader.path, follow_symlinks=False)
            except FileExistsError:
                loader.validate()
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return loader.path


def write_default_config(codex_home: Path | None = None, *, overwrite: bool = False) -> Path:
    loader = ConfigLoader(codex_home)
    loader._check_path(create_parent=True)
    with _config_write_lock(loader.path.parent):
        # Re-check after acquiring the cross-process lock so a racing writer's
        # valid configuration is preserved without observing its temp link.
        loader._check_path(create_parent=True)
        return _write_default_config_locked(loader, overwrite=overwrite)
