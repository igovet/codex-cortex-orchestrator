"""Explicit Marketplace gateway bootstrap; dormant unless opted in."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .gateway.config import ConfigLoader, write_default_config
from .runtime.supervisor import GatewaySupervisor


def bootstrap() -> None:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    loader = ConfigLoader(home)
    if loader.is_configured():
        data = loader.validate()
        gateway = data.get("gateway", {})
        if isinstance(gateway, dict) and gateway.get("enabled") is False:
            return
    else:
        write_default_config(home)
    target = home / "cortex" / "deps"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.chmod(0o700)
    if target.is_symlink() or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
        raise RuntimeError("Marketplace gateway dependency target is unsafe")
    lock = Path(__file__).resolve().parents[2] / "requirements.lock"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--no-compile", "--upgrade", "--force-reinstall", "--require-hashes", "--target", str(target), "-r", str(lock)],
        check=True,
    )
    os.environ["CORTEX_DEPENDENCY_DIR"] = str(target)
    GatewaySupervisor(codex_home=home).ensure()
