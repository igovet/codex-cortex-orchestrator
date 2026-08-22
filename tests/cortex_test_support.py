"""Private host-store setup shared by Cortex runtime test suites.

Runtime code must fail closed when the developer's real ``~/.codex`` is not
private.  Tests that exercise a real Cortex ledger therefore opt into an
isolated, mode-0700 host store instead of weakening that production check.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


HOST_CONTROL_STORE_ENV = "CORTEX_HOST_STATE_DIR"
_RESTORED_ENV_KEYS = (HOST_CONTROL_STORE_ENV, "CORTEX_ROOT")


class HostPrivateControlStoreTestMixin:
    """Give one test case a private, subprocess-inherited host control store."""

    host_state_dir: Path
    _previous_cortex_host_environment: dict[str, str | None]

    def set_up_host_private_control_store(self) -> Path:
        # Keep this outside the caller's temporary project.  Several suites
        # intentionally use their temporary-directory root as project_root,
        # and a host store nested below it must be rejected by the production
        # workspace-containment guard.
        self._cortex_host_store_temp = tempfile.TemporaryDirectory(
            prefix="cortex-host-private-test-"
        )
        host_state_dir = Path(self._cortex_host_store_temp.name) / "host-private-cortex"
        host_state_dir.mkdir(mode=0o700, exist_ok=True)
        host_state_dir.chmod(0o700)
        self._previous_cortex_host_environment = {
            key: os.environ.get(key) for key in _RESTORED_ENV_KEYS
        }
        os.environ[HOST_CONTROL_STORE_ENV] = str(host_state_dir)
        # Test execution must not inherit an unrelated caller's unsupported
        # test override. Individual rejection tests can still patch it.
        os.environ.pop("CORTEX_ROOT", None)
        self.host_state_dir = host_state_dir
        return host_state_dir

    def tear_down_host_private_control_store(self) -> None:
        previous = getattr(self, "_previous_cortex_host_environment", None)
        if not isinstance(previous, dict):
            return
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        host_store_temp = getattr(self, "_cortex_host_store_temp", None)
        if host_store_temp is not None:
            host_store_temp.cleanup()
