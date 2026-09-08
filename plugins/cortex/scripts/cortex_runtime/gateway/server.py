"""aiohttp application runner for the standalone gateway process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import signal
from dataclasses import replace

from aiohttp import web

from .config import ConfigManager, ConfigLoader, write_default_config
from .proxy import create_app
from .mitm import MitmProxy
from .diagnostics import configure_file_logging
from ..runtime.state import GatewayState, RuntimePaths, dependency_identity, payload_digest


async def serve(*, codex_home: Path | None = None, host: str | None = None, port: int | None = None) -> None:
    write_default_config(codex_home)
    manager = ConfigManager(ConfigLoader(codex_home))
    snapshot = manager.load(cold=True)
    bind_host = host or snapshot.host
    bind_port = port or snapshot.port
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("gateway binds only to loopback")
    paths = RuntimePaths(manager.loader.codex_home)
    paths.ensure()
    log_handler = configure_file_logging(paths.log)
    plugin_root = Path(__file__).resolve().parents[3]
    package_file = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        plugin_value = json.loads(package_file.read_text(encoding="utf-8"))
        plugin_version = str(plugin_value.get("version", "unknown"))
        package_digest = payload_digest(plugin_root)
    except (OSError, ValueError):
        plugin_version = "unknown"
        package_digest = "unknown"
    dependency_manifest_digest, dependency_bytes_digest, dependency_digest = dependency_identity(plugin_root, required=True)
    state = GatewayState.new(
        pid=os.getpid(),
        host=bind_host,
        port=bind_port,
        plugin_version=plugin_version,
        package_digest=package_digest,
        upstream=snapshot.upstream,
        policy_revision=snapshot.revision,
        executable=str((plugin_root / "scripts" / "cortex_gateway.py").resolve()),
        plugin_root=plugin_root,
    )
    app = create_app(
        manager,
        identity={
            "instance_id": state.instance_id,
            "plugin_version": plugin_version,
            "package_digest": package_digest,
            "dependency_manifest_digest": dependency_manifest_digest,
            "dependency_bytes_digest": dependency_bytes_digest,
            "dependency_digest": dependency_digest,
            "host": bind_host,
            "port": bind_port,
        },
    )
    # Keep the HTTP health/control listener intact and expose the client-facing
    # HTTPS CONNECT MITM on the adjacent owner-local port. Provider routing
    # uses the real upstream URL plus this explicit proxy endpoint.
    mitm = MitmProxy(host=bind_host, port=0, codex_home=manager.loader.codex_home, manager=manager)
    await mitm.start()
    state = replace(state, mitm_port=mitm.port)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, bind_host, bind_port)
    await site.start()
    state.write(paths)
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def request_stop(*_: object) -> None:
        if not stop.done():
            stop.set_result(None)

    def request_reload(*_: object) -> None:
        try:
            updated = manager.reload()
            if not updated.gateway_enabled:
                # Disabling is a lifecycle transition, not a policy-only
                # reload.  Stop this owned process so a SIGHUP cannot leave a
                # live child forwarding requests under a disabled policy.
                if not stop.done():
                    stop.set_result(None)
            elif not updated.restart_required:
                state.write(replace(state, policy_revision=updated.revision))
        except Exception:
            # The manager retains the last valid immutable snapshot; the
            # health endpoint exposes the sanitized stale status.
            pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            pass
    if hasattr(signal, "SIGHUP"):
        try:
            loop.add_signal_handler(signal.SIGHUP, request_reload)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop
    finally:
        await mitm.close()
        await runner.cleanup()
        log_handler.close()
        for path in (paths.state, paths.pid):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Cortex Model Gateway")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(host=args.host, port=args.port))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
