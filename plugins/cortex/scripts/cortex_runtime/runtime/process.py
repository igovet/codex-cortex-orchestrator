"""Process launch and controlled signal helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from .state import GatewayState, process_argv, process_entrypoint, process_executable, process_identity, process_invocation_matches


def _is_cortex_entrypoint(path: Path) -> bool:
    """Accept only a packaged Cortex gateway script as a restart target."""
    try:
        path = path.resolve(strict=True)
        if path.name != "cortex_gateway.py" or path.parent.name != "scripts":
            return False
        manifest = path.parent.parent / ".codex-plugin" / "plugin.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        return value.get("name") == "cortex"
    except (OSError, ValueError, TypeError):
        return False


def _same_codex_home(pid: int, codex_home: Path) -> bool:
    try:
        expected = b"CODEX_HOME=" + os.fsencode(codex_home)
        with open(f"/proc/{pid}/environ", "rb") as stream:
            return expected in stream.read(1024 * 1024).split(b"\0")
    except OSError:
        return False


def gateway_processes(*, host: str, port: int, codex_home: Path) -> list[int]:
    """Find same-user Cortex gateway processes bound to the requested command.

    The caller uses this only after a bind/readiness conflict.  Matching the
    complete argv and a real Cortex package manifest prevents a generic
    process using the same port from being treated as ours.
    """
    result: list[int] = []
    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        try:
            info = entry.stat()
            if info.st_uid != os.getuid() or pid == os.getpid() or not _same_codex_home(pid, codex_home):
                continue
            args = process_argv(pid)
            if len(args) != 8 or args[1] != "-B" or args[3:] != ["serve", "--host", host, "--port", str(port)]:
                continue
            executable = process_executable(pid)
            if executable == "unknown" or os.path.realpath(args[0]) != executable:
                continue
            script = Path(args[2])
            if not script.is_absolute() or not _is_cortex_entrypoint(script):
                continue
            result.append(pid)
        except (FileNotFoundError, OSError, ValueError):
            # Processes may disappear while /proc is being inspected.
            continue
    return result


def signal_gateway_process(pid: int, *, host: str, port: int, codex_home: Path, sig: int = signal.SIGTERM) -> bool:
    """Signal a verified Cortex gateway process without following PID reuse."""
    try:
        if pid <= 0 or pid == os.getpid():
            return False
        entry = Path("/proc") / str(pid)
        info = entry.stat()
        if info.st_uid != os.getuid() or not _same_codex_home(pid, codex_home):
            return False
        args = process_argv(pid)
        if len(args) != 8 or args[1] != "-B" or args[3:] != ["serve", "--host", host, "--port", str(port)]:
            return False
        executable = process_executable(pid)
        if executable == "unknown" or os.path.realpath(args[0]) != executable:
            return False
        if not Path(args[2]).is_absolute() or not _is_cortex_entrypoint(Path(args[2])):
            return False
        os.kill(pid, sig)
        return True
    except (FileNotFoundError, OSError, ValueError):
        return False


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
