"""Idempotent supervisor for independent gateway processes."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.request import urlopen

from ..gateway.config import ConfigError, ConfigLoader, ConfigManager, write_default_config
from .process import gateway_processes, signal_gateway_process, signal_owned, spawn_gateway
from .state import (
    GatewayState,
    RuntimePaths,
    dependency_identity,
    payload_digest,
    process_executable,
    process_identity,
    process_invocation_matches,
    state_matches_process,
)


class GatewaySupervisor:
    def __init__(self, *, codex_home: Path | None = None, plugin_root: Path | None = None, manager: ConfigManager | None = None):
        self.loader = ConfigLoader(codex_home)
        self._external_manager = manager is not None
        self.manager = manager or ConfigManager(self.loader)
        self.codex_home = self.loader.codex_home
        self.plugin_root = plugin_root or Path(__file__).resolve().parents[3]
        self.paths = RuntimePaths(self.codex_home)

    @contextmanager
    def startup_lock(self):
        import fcntl

        descriptor = self.paths.open_lock()
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _healthy(self, state: GatewayState) -> bool:
        if not state_matches_process(state):
            return False
        package_file = self.plugin_root / ".codex-plugin" / "plugin.json"
        try:
            package_bytes = package_file.read_bytes()
            package_digest = payload_digest(self.plugin_root)
            package_version = str(json.loads(package_bytes.decode("utf-8")).get("version", ""))
            dependency_manifest_digest, dependency_bytes_digest, dependency_digest = dependency_identity(self.plugin_root, required=True)
        except (OSError, ValueError):
            return False
        if (
            state.package_digest != package_digest
            or state.plugin_version != package_version
            or state.dependency_manifest_digest != dependency_manifest_digest
            or state.dependency_bytes_digest != dependency_bytes_digest
            or state.dependency_digest != dependency_digest
            or state.upstream == ""
        ):
            return False
        expected_entrypoint = str((self.plugin_root / "scripts" / "cortex_gateway.py").resolve())
        if state.executable != expected_entrypoint:
            return False
        try:
            authority = f"[{state.host}]" if ":" in state.host and not state.host.startswith("[") else state.host
            with urlopen(f"http://{authority}:{state.port}/health", timeout=1) as response:
                value = json.loads(response.read(64 * 1024))
            return (
                value.get("service") == "cortex-model-gateway"
                # A draining process is deliberately refusing new work.  It
                # must never satisfy readiness or be reused for a restart.
                and value.get("status") == "ok"
                and value.get("instance_id") == state.instance_id
                and value.get("package_digest") == state.package_digest
                and value.get("plugin_version") == state.plugin_version
                and value.get("dependency_manifest_digest") == state.dependency_manifest_digest
                and value.get("dependency_bytes_digest") == state.dependency_bytes_digest
                and value.get("dependency_digest") == state.dependency_digest
                and value.get("host") == state.host
                and int(value.get("port", -1)) == state.port
            )
        except Exception:
            return False

    def status(self) -> dict[str, object]:
        try:
            snapshot = self.manager.snapshot()
            config = snapshot.public_status()
        except ConfigError as exc:
            return {"status": "invalid_configuration", "error": str(exc), "code": "invalid_configuration", "CODEX_HOME": str(self.codex_home)}
        state = GatewayState.read(self.paths)
        running = bool(snapshot.gateway_enabled and state and self._healthy(state))
        if running:
            status = "running"
        elif not snapshot.gateway_enabled:
            status = "stopped" if state is None else "not_ready"
        else:
            status = "not_ready"
        return {
            "status": status,
            "CODEX_HOME": str(self.codex_home),
            "runtime": state.to_dict() if state else None,
            **config,
        }

    def _drain_owned(self, state: GatewayState) -> None:
        if not signal_owned(state, signal.SIGTERM):
            raise RuntimeError("owned gateway process changed before drain")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and GatewayState.read(self.paths) is not None:
            time.sleep(0.05)
        if GatewayState.read(self.paths) is not None:
            raise RuntimeError("gateway did not stop before restart")

    @staticmethod
    def _drainable_owned(state: GatewayState) -> bool:
        """Recognize an owned stale child whose cached script was refreshed."""
        if state_matches_process(state):
            return True
        return (
            getattr(state, "pid", None) is not None
            and process_identity(state.pid) == getattr(state, "process_start", None)
            and process_executable(state.pid) == getattr(state, "process_binary", None)
            and process_invocation_matches(state)
        )

    def _drain_conflicting_gateway(self, host: str, port: int) -> None:
        """Drain an older verified Cortex gateway before rebinding its port.

        Marketplace upgrades can leave a gateway from an earlier cached
        payload running without a readable state file in the current profile.
        Only an exact same-user Cortex invocation is eligible; an unrelated
        listener remains untouched and the subsequent readiness failure is
        allowed to abort startup.
        """
        for pid in gateway_processes(host=host, port=port):
            if not signal_gateway_process(pid, host=host, port=port):
                if pid in gateway_processes(host=host, port=port):
                    raise RuntimeError("conflicting Cortex gateway could not be signaled")
                continue
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if pid not in gateway_processes(host=host, port=port):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("conflicting Cortex gateway did not stop before restart")

    def ensure(self, *, wait_seconds: float = 10.0) -> dict[str, object]:
        if not self._external_manager and not self.loader.is_configured():
            # Proxy startup provisions defaults atomically and create-only;
            # then reload the manager so the newly written policy is used.
            write_default_config(self.codex_home)
            self.manager = ConfigManager(self.loader)
        try:
            snapshot = self.manager.snapshot()
        except ConfigError:
            raise
        if not snapshot.gateway_enabled:
            with self.startup_lock():
                state = GatewayState.read(self.paths)
                if state and self._drainable_owned(state):
                    self._drain_owned(state)
            return self.status()
        with self.startup_lock():
            # Keep the entire convergence loop under one startup lock.  A
            # binding change first drains the verified child, then refreshes
            # the manager from the now-current file.  Calling ensure() here
            # would reacquire the same non-reentrant flock and deadlock.
            while True:
                state = GatewayState.read(self.paths)
                requested_listener = snapshot.pending_listener or snapshot.listener
                requested_upstream = snapshot.pending_upstream or snapshot.upstream
                binding_matches = bool(
                    state
                    and state.host == requested_listener[0]
                    and state.port == requested_listener[1]
                    and state.upstream == requested_upstream
                )
                if state and self._healthy(state) and binding_matches:
                    return self.status()
                if snapshot.restart_required:
                    if state and state_matches_process(state):
                        self._drain_owned(state)
                    # A manager retains the previous active binding while
                    # exposing the requested one as pending.  Recreate it
                    # without leaving this lock, so the next snapshot starts
                    # from the requested binding.
                    self.manager = ConfigManager(self.loader)
                    snapshot = self.manager.snapshot()
                    continue
                if state and self._drainable_owned(state):
                    # A stale package/health identity is still an owned
                    # process; drain it before attempting to bind its address.
                    self._drain_owned(state)
                self._drain_conflicting_gateway(requested_listener[0], requested_listener[1])
                process = spawn_gateway(codex_home=self.codex_home, plugin_root=self.plugin_root, host=requested_listener[0], port=requested_listener[1])
                end = time.monotonic() + wait_seconds
                while time.monotonic() < end:
                    state = GatewayState.read(self.paths)
                    if state and state.pid == process.pid and self._healthy(state):
                        return self.status()
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                # A failed readiness check must not leave the newly spawned
                # owned child listening indefinitely.  Drain a verified state
                # file; otherwise terminate the exact Popen child we created.
                state = GatewayState.read(self.paths)
                if state and state.pid == process.pid and state_matches_process(state):
                    self._drain_owned(state)
                elif process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                break
        raise RuntimeError("gateway did not become ready")

    def reload(self) -> dict[str, object]:
        snapshot = self.manager.reload()
        if snapshot.config_status != "valid":
            # Keep the last valid policy and report the rejected edit; never
            # reparse without the previous snapshot or interrupt a live child.
            result = self.status()
            result.update({"reload_status": "rejected", "reload_error": snapshot.error_code or "invalid_configuration"})
            return result
        requested_listener = snapshot.pending_listener or snapshot.listener
        requested_upstream = snapshot.pending_upstream or snapshot.upstream
        state = GatewayState.read(self.paths)
        if state and state_matches_process(state):
            # ``ConfigManager`` deliberately keeps the active snapshot bound
            # to the live child while exposing the requested binding as
            # pending.  Compare the child with those pending values so a
            # listener/upstream edit drains and restarts instead of receiving
            # a SIGHUP that cannot apply the new address.
            binding_changed = state.host != requested_listener[0] or state.port != requested_listener[1] or state.upstream != requested_upstream
            # Disabling is also a lifecycle transition.  Never report a
            # successful reload while an owned child can still forward.
            if binding_changed or not snapshot.gateway_enabled:
                if not signal_owned(state, signal.SIGTERM):
                    result = self.status()
                    result.update({"reload_status": "failed", "reload_error": "owned gateway could not be signaled"})
                    return result
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and GatewayState.read(self.paths) is not None:
                    time.sleep(0.05)
                if GatewayState.read(self.paths) is not None:
                    result = self.status()
                    result.update({"reload_status": "failed", "reload_error": "owned gateway did not drain"})
                    return result
                if binding_changed:
                    self.manager = ConfigManager(self.loader)
                    return self.ensure()
            else:
                if not signal_owned(state, signal.SIGHUP):
                    result = self.status()
                    result.update({"reload_status": "failed", "reload_error": "owned gateway could not be signaled"})
                    return result
        elif snapshot.restart_required:
            # There is no owned child to drain, but the manager may still be
            # carrying the old active binding.  Recreate it before ensure so
            # a first start uses the requested address/upstream.
            self.manager = ConfigManager(self.loader)
            return self.ensure()
        return self.status()

    def stop(self, *, drain: bool = True, deadline: float = 30.0) -> dict[str, object]:
        state = GatewayState.read(self.paths)
        if state and state_matches_process(state):
            signal_owned(state, signal.SIGTERM if drain else signal.SIGKILL)
            end = time.monotonic() + max(0.0, deadline)
            while time.monotonic() < end and GatewayState.read(self.paths) is not None:
                time.sleep(0.05)
        return self.status()
