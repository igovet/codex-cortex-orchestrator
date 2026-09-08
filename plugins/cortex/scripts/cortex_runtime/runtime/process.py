"""Process launch and controlled signal helpers."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

from .state import GatewayState, process_entrypoint, process_executable, process_identity, process_invocation_matches


def _gateway_environment(codex_home: Path) -> dict[str, str]:
    """Build the gateway's narrow environment boundary.

    The gateway needs only its isolated profile and dependency import path.
    In particular, credentials, proxy settings, shell startup state and
    arbitrary host variables must not become ambient child-process state.
    """
    environment = {"CODEX_HOME": str(codex_home)}
    dependency_dir = os.environ.get("CORTEX_DEPENDENCY_DIR")
    if dependency_dir:
        environment["CORTEX_DEPENDENCY_DIR"] = dependency_dir
        # Do not inherit ambient PYTHONPATH entries.  The dependency target is
        # checked against the hash-locked identity before the child starts;
        # the packaged script path is inserted by cortex_gateway.py itself.
        environment["PYTHONPATH"] = dependency_dir
    return environment


def spawn_gateway(*, codex_home: Path, plugin_root: Path, host: str, port: int) -> subprocess.Popen[bytes]:
    entry = plugin_root / "scripts" / "cortex_gateway.py"
    env = _gateway_environment(codex_home)
    return subprocess.Popen(
        [sys.executable, "-B", str(entry), "serve", "--host", host, "--port", str(port)],
        cwd=str(plugin_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def signal_owned(state: GatewayState, sig: int) -> bool:
    if (
        process_identity(state.pid) != state.process_start
        or process_executable(state.pid) != state.process_binary
        # The cached payload may be refreshed while an older owned gateway is
        # still running. Its exact argv entrypoint can then disappear from
        # disk before the supervisor gets a chance to drain it. Invocation,
        # PID-start identity, and executable checks still bind the signal to
        # the recorded child; requiring the old file to remain present would
        # strand the listener and make the new candidate fail readiness.
        or not process_invocation_matches(state)
    ):
        return False
    try:
        os.kill(state.pid, sig)
    except ProcessLookupError:
        return False
    return True
