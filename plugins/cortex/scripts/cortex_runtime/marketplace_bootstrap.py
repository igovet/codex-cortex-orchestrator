"""Marketplace bootstrap for the owned gateway and local Codex provider."""
from __future__ import annotations

import os
import json
import hashlib
from importlib.metadata import distributions
from pathlib import Path
import subprocess
import sys

from .gateway.config import ConfigLoader, write_default_config
from .runtime.supervisor import GatewaySupervisor
from .runtime.state import dependency_identity
from .provider import _atomic_private_write, _read_config_observation


def verified_dependencies(plugin: Path, target: Path) -> tuple[str, str, str]:
    identity = dependency_identity(plugin, target, required=True)
    normalize = lambda name: name.lower().replace("-", "_")
    expected = dict(line.strip().split("==") for line in (plugin / "requirements.txt").read_text().splitlines() if line.strip() and not line.startswith("#"))
    actual = {normalize(dist.metadata["Name"]): dist.version for dist in distributions(path=[str(target)])}
    if actual != {normalize(name): version for name, version in expected.items()}:
        raise RuntimeError("Marketplace gateway dependency versions do not match the pinned package")
    return identity


def bootstrap() -> None:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    loader = ConfigLoader(home)
    if loader.is_configured():
        data = loader.validate()
        gateway = data.get("gateway", {})
        if isinstance(gateway, dict) and gateway.get("enabled") is False:
            GatewaySupervisor(codex_home=home).connect()
            return
    else:
        write_default_config(home)
    plugin = Path(__file__).resolve().parents[2]
    manifest_digest = hashlib.sha256((plugin / "requirements.lock").read_bytes()).hexdigest()
    supplied = os.environ.get("CORTEX_DEPENDENCY_DIR")
    target = Path(supplied) if supplied else home / "cortex" / "deps" / manifest_digest
    supervisor = GatewaySupervisor(codex_home=home)
    if supplied:
        # The isolated launcher has already installed and verified this exact
        # tree. MCP startup must retain it, including for native workers.
        verified_dependencies(plugin, target)
    else:
        with supervisor.startup_lock():
            # Each lock revision has a separate immutable install tree; a
            # Marketplace upgrade cannot rewrite imports used by an old child.
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.parent.is_symlink() or target.parent.stat().st_uid != os.getuid() or target.parent.stat().st_mode & 0o077:
                raise RuntimeError("Marketplace gateway dependency parent is unsafe")
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.is_symlink() or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
                raise RuntimeError("Marketplace gateway dependency target is unsafe")
            receipt = home / "cortex" / f"dependencies-{manifest_digest}.json"
            saved = _read_config_observation(receipt)[0]
            if saved:
                if list(verified_dependencies(plugin, target)) != json.loads(saved):
                    raise RuntimeError("Marketplace gateway dependency identity changed")
            else:
                # Validate descendants before pip can write through a link.
                dependency_identity(plugin, target, required=True)
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                     "--no-compile", "--upgrade", "--force-reinstall", "--require-hashes", "--target", str(target), "-r", str(plugin / "requirements.lock")],
                    check=True,
                    # MCP stdout remains a JSON-RPC stream.
                    stdout=subprocess.DEVNULL,
                )
                _atomic_private_write(receipt, json.dumps(verified_dependencies(plugin, target)).encode())
    os.environ["CORTEX_DEPENDENCY_DIR"] = str(target)
    supervisor.connect()
