"""Explicit provider integration seams; no credentials are read or stored."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import errno
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

from .gateway.defaults import DEFAULT_HOST, DEFAULT_PORT


@dataclass(frozen=True)
class ProviderSettings:
    name: str = "OpenAI"
    gateway_host: str = DEFAULT_HOST
    gateway_port: int = DEFAULT_PORT
    base_url: str | None = None
    wire_api: str = "responses"
    # Credentials may only be routed through a gateway whose process and
    # candidate identity were verified by the supervisor.  The safe default
    # is unauthenticated routing; explicit configuration must opt in with the
    # verification receipt.
    requires_openai_auth: bool = False
    gateway_verified: bool = False
    supports_websockets: bool = False
    remote_compaction_v2: bool | None = False
    feature_gate_validated: bool = False
    context_management: bool | None = None

    def resolved_base_url(self) -> str:
        if self.requires_openai_auth and not self.gateway_verified:
            raise ValueError("authenticated provider routing requires a verified owned gateway")
        if self.gateway_host not in {"127.0.0.1", "localhost", "::1"} or not 1 <= self.gateway_port <= 65535:
            raise ValueError("provider requires a fixed loopback listener")
        authority = f"[{self.gateway_host}]" if ":" in self.gateway_host else self.gateway_host
        expected = f"http://{authority}:{self.gateway_port}/backend-api/codex"
        value = self.base_url or expected
        parsed = urlsplit(value)
        if value.rstrip("/") != expected or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider base_url must target the verified loopback listener")
        if self.remote_compaction_v2 and not self.feature_gate_validated:
            raise ValueError("remote_compaction_v2 requires an explicit validation receipt")
        return value.rstrip("/")


def provider_patch(settings: ProviderSettings = ProviderSettings()) -> str:
    """Return a user-visible patch for an explicit configure action.

    The function intentionally does not read or mutate a Codex config and never
    includes credentials.  A future host-specific updater can consume this
    bounded patch after validating the target Codex version.
    """

    base_url = settings.resolved_base_url()
    return (
        'model_provider = "cortex"\n\n'
        '[features]\n'
        f'remote_compaction_v2 = {str(settings.remote_compaction_v2 and settings.feature_gate_validated).lower()}\n\n'
        '[model_providers.cortex]\n'
        f'name = "{settings.name}"\n'
        f'base_url = "{base_url}"\n'
        f'wire_api = "{settings.wire_api}"\n'
        f'requires_openai_auth = {str(settings.requires_openai_auth).lower()}\n'
        f'supports_websockets = {str(settings.supports_websockets).lower()}\n'
    )


def codex_config_path(codex_home: Path | None = None) -> Path:
    configured_home = os.environ.get("CODEX_HOME")
    home = Path(codex_home or configured_home or (Path.home() / ".codex")).expanduser()
    if not home.is_absolute():
        raise ValueError("CODEX_HOME must be absolute")
    current = Path(home.anchor)
    for part in home.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("CODEX_HOME contains an unsafe symlink")
    return home / "config.toml"


def _safe_config_bytes(path: Path) -> bytes:
    observed = _read_config_observation(path)
    data = observed[0]
    _validate_config_bytes(path, data)
    return data


def _read_config_observation(path: Path) -> tuple[bytes, tuple[int, int, int, int, str] | None]:
    """Read one owner-controlled config inode and return a CAS token."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return b"", None
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return b"", None
        raise ValueError("Codex config is an unsafe link or file") from exc
    try:
        info = os.fstat(descriptor)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Codex config is an unsafe link or file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            data = stream.read()
            final_info = os.fstat(stream.fileno())
        if (
            stat.S_ISLNK(final_info.st_mode)
            or not stat.S_ISREG(final_info.st_mode)
            or final_info.st_nlink != 1
            or final_info.st_uid != os.getuid()
            or final_info.st_mode & 0o077
        ):
            raise ValueError("Codex config is an unsafe link or file")
        if (
            final_info.st_dev != info.st_dev
            or final_info.st_ino != info.st_ino
            or final_info.st_mtime_ns != info.st_mtime_ns
            or final_info.st_size != info.st_size
        ):
            raise ValueError("Codex config changed during read")
        info = final_info
    finally:
        if descriptor != -1:
            os.close(descriptor)
    digest = hashlib.sha256(data).hexdigest()
    token = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size, digest)
    return data, token


@contextmanager
def _provider_write_lock(directory: Path):
    """Serialize cooperating provider writers with an owner-only lock."""
    lock = directory / ".cortex-provider.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("provider lock is an unsafe link or file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _assert_config_unchanged(path: Path, expected: tuple[bytes, tuple[int, int, int, int, str] | None]) -> None:
    current = _read_config_observation(path)
    if current != expected:
        raise ValueError("Codex config changed during provider update")


def _validate_config_bytes(path: Path, data: bytes) -> None:
    try:
        tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Codex config TOML is invalid; provider patch was not applied") from exc


def _rename_exchange(first: Path, second: Path) -> None:
    """Atomically exchange two directory entries on supported POSIX hosts.

    A normal ``os.replace`` has an unavoidable final TOCTOU window: an
    unrelated same-user writer can replace the config after our last read but
    before the rename.  Linux ``renameat2(RENAME_EXCHANGE)`` and macOS
    ``renamex_np(RENAME_SWAP)`` move the old target into our private temporary
    entry at the same syscall boundary, allowing the caller to compare the
    exact inode which was present at publication.
    """
    if os.name != "posix":
        raise OSError(errno.ENOTSUP, "atomic compare-and-swap is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOTSUP, "atomic compare-and-swap is unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        # <stdio.h>: RENAME_SWAP exchanges two existing names atomically.
        result = renamex_np(os.fsencode(first), os.fsencode(second), 0x00000002)
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "atomic compare-and-swap is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(first), -100, os.fsencode(second), 2)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _publish_config_cas(path: Path, temporary: Path, expected: tuple[bytes, tuple[int, int, int, int, str] | None]) -> None:
    """Publish ``temporary`` only if ``path`` still contains ``expected``."""
    expected_bytes, expected_token = expected
    if expected_token is None:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("Codex config changed during provider update") from exc
        os.unlink(temporary)
        return
    _rename_exchange(temporary, path)
    restored = False
    try:
        observed = _read_config_observation(temporary)
        if observed != (expected_bytes, expected_token):
            # The target was changed before the exchange.  Put that exact
            # newer inode back and leave our candidate in the temp entry.
            _rename_exchange(temporary, path)
            restored = True
            raise ValueError("Codex config changed during provider update")
    except Exception:
        # If validation itself failed, restore the entry whenever the first
        # exchange is still in effect.  A failed restoration is surfaced as a
        # hard error rather than risking an unknown config state.
        if not restored and temporary.exists() and path.exists():
            try:
                _rename_exchange(temporary, path)
            except OSError:
                pass
        raise
    os.unlink(temporary)




_HEADER_PROBE = "__cortex_provider_header_probe__"


def _find_probe(value: object, path: tuple[str, ...] = ()) -> tuple[str, ...] | None:
    if isinstance(value, dict):
        if _HEADER_PROBE in value:
            return path
        for key, nested in value.items():
            found = _find_probe(nested, path + (str(key),))
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_probe(nested, path)
            if found is not None:
                return found
    return None


def _parse_header(line: str) -> tuple[tuple[str, ...], bool] | None:
    """Return TOML header semantics, including quoted and array tables."""
    content = line.rstrip("\r\n")
    stripped = content.lstrip()
    if not stripped.startswith("["):
        return None
    try:
        parsed = tomllib.loads(f"{content}\n{_HEADER_PROBE} = true\n")
    except tomllib.TOMLDecodeError:
        return None
    path = _find_probe(parsed)
    if path is None:
        return None
    return path, stripped.startswith("[[")


def _line_ending(lines: list[str]) -> str:
    return "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"


def _upsert_key(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    newline = _line_ending(lines)
    target = tuple(section.split(".")) if section else None
    start = 0
    end = len(lines)
    if target is None:
        end = next((index for index, line in enumerate(lines) if _parse_header(line)), len(lines))
    else:
        found = None
        for index, line in enumerate(lines):
            header = _parse_header(line)
            if header and header[0] == target:
                if header[1]:
                    raise ValueError(f"provider section [{section}] is an array table")
                found = index
                break
        if found is not None:
            start = found + 1
            end = start
            while end < len(lines) and _parse_header(lines[end]) is None:
                end += 1
        else:
            suffix = "" if not text or text.endswith(("\n", "\r")) else newline
            return text + suffix + f"[{section}]{newline}{key} = {value}{newline}"

    pattern = re.compile(rf"^(\s*)(?P<token>{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')\s*=")
    matching = [index for index in range(start, end) if pattern.match(lines[index])]

    def replacement_for(line: str) -> str:
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else newline
        body = line[:-len(ending)] if len(ending) and line.endswith(ending) else line
        comment = ""
        comment_match = re.search(r"(\s+#.*)$", body)
        if comment_match:
            comment = comment_match.group(1)
        match = pattern.match(body)
        indent = match.group(1) if match else ""
        token = match.group("token") if match else key
        return f"{indent}{token} = {value}{comment}{ending}"

    if matching:
        lines[matching[0]] = replacement_for(lines[matching[0]])
        for index in reversed(matching[1:]):
            del lines[index]
        return "".join(lines)
    if end and not lines[end - 1].endswith(("\n", "\r")):
        lines[end - 1] += newline
    lines.insert(end, f"{key} = {value}{newline}")
    return "".join(lines)


def _compaction_features(settings: ProviderSettings) -> dict[str, bool]:
    values = {}
    if settings.remote_compaction_v2 is not None:
        values["remote_compaction_v2"] = bool(settings.remote_compaction_v2 and settings.feature_gate_validated)
    if settings.context_management is not None:
        values["context_management"] = settings.context_management
    return values


def render_provider_config(original: bytes, settings: ProviderSettings, *, preserved_features: set[str] | None = None) -> bytes:
    """Render a reversible, comment-preserving provider edit and validate it."""
    text = original.decode("utf-8") if original else ""
    text = _upsert_key(text, "", "model_provider", '"cortex"')
    for key, value in _compaction_features(settings).items():
        if key not in (preserved_features or set()):
            text = _upsert_key(text, "features", key, str(value).lower())
    for key, value in (
        ("name", json.dumps(settings.name)),
        ("base_url", json.dumps(settings.resolved_base_url())),
        ("wire_api", json.dumps(settings.wire_api)),
        ("requires_openai_auth", str(settings.requires_openai_auth).lower()),
        ("supports_websockets", str(settings.supports_websockets).lower()),
    ):
        text = _upsert_key(text, "model_providers.cortex", key, value)
    result = text.encode("utf-8")
    try:
        tomllib.loads(result.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("provider patch would produce invalid Codex config") from exc
    return result


def apply_provider_patch(codex_home: Path | None, settings: ProviderSettings, *, managed: bool = False) -> dict[str, str]:
    """Apply an explicit provider edit with a locked, compare-and-swap publish."""
    path = codex_config_path(codex_home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if (
        stat.S_ISLNK(parent_info.st_mode)
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or parent_info.st_mode & 0o077
    ):
        raise ValueError("Codex config directory is an unsafe link or directory")
    backup = path.with_name(path.name + ".cortex-backup")
    with _provider_write_lock(path.parent):
        observed = _read_config_observation(path)
        original, original_token = observed
        _validate_config_bytes(path, original)
        route_path = path.with_name("config.toml.cortex-route.json")
        preserved_features = set()
        feature_journal = {}
        if managed:
            parsed = tomllib.loads(original.decode())
            saved_bytes, _ = _read_config_observation(route_path)
            saved = json.loads(saved_bytes) if saved_bytes else None
            if saved:
                current_fields = parsed.get("model_providers", {}).get("cortex")
                matches_installed = parsed.get("model_provider") == "cortex" and current_fields == saved["installed"]
                matches_pending = (not saved.get("committed", True)
                                   and parsed.get("model_provider") == saved.get("before_selector")
                                   and current_fields == saved.get("before_fields"))
                if not (matches_installed or matches_pending):
                    return {"path": str(path), "status": "user_override"}
            if not saved:
                if parsed.get("model_provider", "openai") != "openai" or "cortex" in parsed.get("model_providers", {}):
                    raise ValueError("automatic Cortex routing requires the OpenAI provider and an unused cortex provider name")
                if parsed.get("openai_base_url") or os.environ.get("OPENAI_BASE_URL"):
                    raise ValueError("automatic Cortex routing cannot replace a custom OpenAI upstream")
            feature_journal = dict((saved or {}).get("features", {}))
            current_features = parsed.get("features", {})
            for key, value in _compaction_features(settings).items():
                current = current_features.get(key)
                prior = feature_journal.get(key)
                if prior and current != prior["installed"] and not (
                    not saved.get("committed", True) and current == prior.get("before")
                ):
                    # A later user choice wins on subsequent connect calls.
                    preserved_features.add(key)
                    continue
                feature_journal[key] = {
                    "previous": prior["previous"] if prior else current,
                    "installed": value,
                    "before": current,
                }
        updated = render_provider_config(original, settings, preserved_features=preserved_features)
        if updated == original:
            if managed and saved and not saved.get("committed", True):
                saved["committed"] = True
                _atomic_private_write(route_path, json.dumps(saved).encode())
            return {"path": str(path), "backup": str(backup), "status": "unchanged"}
        try:
            backup_info = backup.lstat()
        except FileNotFoundError:
            backup_info = None
        if backup_info is not None and (
            stat.S_ISLNK(backup_info.st_mode)
            or not stat.S_ISREG(backup_info.st_mode)
            or backup_info.st_nlink != 1
            or backup_info.st_uid != os.getuid()
            or backup_info.st_mode & 0o077
        ):
            raise ValueError("provider backup is an unsafe link or file")
        if backup_info is None:
            fd, temporary_backup = tempfile.mkstemp(prefix=".cortex-provider-backup.", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_backup, 0o600)
                # Publish the first-version backup create-only.  A concurrent
                # configure must keep whichever complete backup won the race;
                # replacing the destination would destroy that recovery point.
                try:
                    os.link(temporary_backup, backup, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                try:
                    os.unlink(temporary_backup)
                except FileNotFoundError:
                    pass
            try:
                backup_info = backup.lstat()
            except FileNotFoundError as exc:
                raise ValueError("provider backup was not published") from exc
            if (
                stat.S_ISLNK(backup_info.st_mode)
                or not stat.S_ISREG(backup_info.st_mode)
                or backup_info.st_nlink != 1
                or backup_info.st_uid != os.getuid()
                or backup_info.st_mode & 0o077
            ):
                raise ValueError("provider backup is an unsafe link or file")

        if managed:
            installed = tomllib.loads(updated.decode())["model_providers"]["cortex"]
            plugin_keys = [key for key in parsed.get("plugins", {}) if key.startswith("cortex@")]
            journal = {"previous": saved["previous"] if saved else parsed.get("model_provider"), "installed": installed,
                       "plugin_key": saved.get("plugin_key") if saved else plugin_keys[0] if len(plugin_keys) == 1 else None,
                       "before_selector": parsed.get("model_provider"),
                       "features": feature_journal,
                       "before_fields": parsed.get("model_providers", {}).get("cortex"), "committed": False}
            # Publish recovery metadata before routing any credentials. A
            # failed config CAS leaves an inert journal, never a missing undo.
            _atomic_private_write(route_path, json.dumps(journal).encode())

        # Re-open and compare immediately before publication.  The lock
        # serializes cooperating provider writers; the descriptor/content CAS
        # prevents an unrelated same-user editor from being overwritten.
        _assert_config_unchanged(path, (original, original_token))
        fd, temporary = tempfile.mkstemp(prefix=".cortex-provider.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            _assert_config_unchanged(path, (original, original_token))
            _publish_config_cas(path, Path(temporary), (original, original_token))
            if managed:
                journal["committed"] = True
                _atomic_private_write(route_path, json.dumps(journal).encode())
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"path": str(path), "backup": str(backup), "status": "updated"}


def _atomic_private_write(path: Path, data: bytes, *, observed=None) -> None:
    if observed is None:
        observed = _read_config_observation(path)
    fd, name = tempfile.mkstemp(prefix=".cortex-route.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_config_cas(path, Path(name), observed)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _remove_key(text: str, section: str, key: str) -> str:
    target = tuple(section.split(".")) if section else ()
    current = ()
    pattern = re.compile(rf"^\s*(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')\s*=")
    lines = []
    for line in text.splitlines(keepends=True):
        header = _parse_header(line)
        if header:
            current = header[0]
        if current != target or not pattern.match(line):
            lines.append(line)
        elif " #" in line:
            lines.append("#" + line.partition(" #")[2])
    return "".join(lines)


def restore_provider(codex_home: Path | None) -> dict[str, str]:
    """Undo only still-owned route fields, retaining subsequent user edits."""
    path = codex_config_path(codex_home)
    journal_path = path.with_name("config.toml.cortex-route.json")
    if not journal_path.exists() and not journal_path.is_symlink():
        return {"status": "unmanaged"}
    with _provider_write_lock(path.parent):
        saved = json.loads(_read_config_observation(journal_path)[0])
        observed = _read_config_observation(path)
        original = observed[0]
        _validate_config_bytes(path, original)
        parsed = tomllib.loads(original.decode())
        text = original.decode()
        if parsed.get("model_provider") == "cortex":
            if saved["previous"] is None:
                text = _remove_key(text, "", "model_provider")
            else:
                text = _upsert_key(text, "", "model_provider", json.dumps(saved["previous"]))
        current = parsed.get("model_providers", {}).get("cortex", {})
        for key, value in saved["installed"].items():
            before_fields = saved.get("before_fields") or {}
            if key in current and (current[key] == value or (not saved.get("committed", True) and key in before_fields and current[key] == before_fields[key])):
                text = _remove_key(text, "model_providers.cortex", key)
        for key, owned in saved.get("features", {}).items():
            if parsed.get("features", {}).get(key) == owned["installed"]:
                if owned["previous"] is None:
                    text = _remove_key(text, "features", key)
                else:
                    text = _upsert_key(text, "features", key, str(owned["previous"]).lower())
        # Empty generated table headers are safe to remove; retain any table
        # with user fields, and all comments/other TOML bytes.
        lines = text.splitlines(keepends=True)
        for index in range(len(lines) - 1, -1, -1):
            if _parse_header(lines[index]) == (("model_providers", "cortex"), False):
                end = index + 1
                while end < len(lines) and _parse_header(lines[end]) is None:
                    end += 1
                if not any(line.strip() and not line.lstrip().startswith("#") for line in lines[index + 1:end]):
                    del lines[index]
        updated = "".join(lines).encode()
        _validate_config_bytes(path, updated)
        _assert_config_unchanged(path, observed)
        _atomic_private_write(path, updated, observed=observed)
        journal_path.unlink()
    return {"status": "restored", "path": str(path)}


def managed_plugin_disabled(codex_home: Path | None) -> bool:
    """Observe removal/disable of the exact Marketplace entry seen at setup."""
    path = codex_config_path(codex_home)
    journal_path = path.with_name("config.toml.cortex-route.json")
    journal_bytes = _read_config_observation(journal_path)[0]
    if not journal_bytes:
        return False
    key = json.loads(journal_bytes).get("plugin_key")
    if not key:
        return False
    config = tomllib.loads(_safe_config_bytes(path).decode())
    entry = config.get("plugins", {}).get(key)
    return entry is None or entry.get("enabled") is False
