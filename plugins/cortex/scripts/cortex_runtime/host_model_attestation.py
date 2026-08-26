"""Fail-closed attestation of the native Codex subagent default model.

The bundled Cortex routing policy describes which models Cortex supports; it
is not evidence about the currently running host.  Luna is special because the
native ``spawn_agent`` wire can select it only by omitting ``model``.  This
module therefore reads the current host-owned Codex configuration on every
attestation and returns only a bounded status/model pair to the runtime.

No configuration content, path, exception text, or unrelated setting crosses
this boundary.
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path


MAX_CODEX_CONFIG_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HostDefaultModelAttestation:
    """A privacy-bounded observation of ``agents.default_subagent_model``."""

    status: str
    model: str | None = None

    @property
    def attested(self) -> bool:
        return self.status == "attested" and bool(self.model)


def _configured_codex_home() -> Path | None:
    raw = os.environ.get("CODEX_HOME")
    if raw is None:
        home = os.environ.get("HOME")
        if not isinstance(home, str) or not home.strip():
            return None
        raw = str(Path(home) / ".codex")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    return path


def _safe_regular_config(path: Path) -> str | None:
    """Return a bounded failure status when the config path is not trustworthy."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except (FileNotFoundError, OSError):
            return "config_missing" if current == path else "config_unreadable"
        if stat.S_ISLNK(info.st_mode):
            return "config_unreadable"
    try:
        info = path.stat()
    except OSError:
        return "config_unreadable"
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CODEX_CONFIG_BYTES:
        return "config_unreadable"
    return None


def attest_host_default_model() -> HostDefaultModelAttestation:
    """Read the effective persisted host default without exposing raw config."""
    codex_home = _configured_codex_home()
    if codex_home is None:
        return HostDefaultModelAttestation("codex_home_unavailable")
    config_path = codex_home / "config.toml"
    unsafe = _safe_regular_config(config_path)
    if unsafe is not None:
        return HostDefaultModelAttestation(unsafe)
    try:
        raw = config_path.read_bytes()
        if len(raw) > MAX_CODEX_CONFIG_BYTES:
            return HostDefaultModelAttestation("config_unreadable")
        payload = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return HostDefaultModelAttestation("config_invalid")
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return HostDefaultModelAttestation("default_missing")
    model = agents.get("default_subagent_model")
    if not isinstance(model, str) or not model.strip():
        return HostDefaultModelAttestation("default_invalid")
    return HostDefaultModelAttestation("attested", model.strip())


__all__ = ["HostDefaultModelAttestation", "attest_host_default_model"]
