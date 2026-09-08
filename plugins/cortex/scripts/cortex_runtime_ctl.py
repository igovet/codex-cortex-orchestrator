"""Explicit administrative commands for the independent Cortex Gateway."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cortex_runtime.gateway.config import ConfigError, effective_codex_home, write_default_config  # noqa: E402
from cortex_runtime.provider import ProviderSettings, apply_provider_patch, codex_config_path, provider_patch, _safe_config_bytes  # noqa: E402
from cortex_runtime.runtime.supervisor import GatewaySupervisor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cortex Model Gateway control")
    parser.add_argument("command", choices=("configure", "config-check", "ensure", "status", "reload", "stop"))
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--provider", action="store_true", help="print the explicit Codex provider patch")
    parser.add_argument("--replace", action="store_true", help="explicitly replace an existing config")
    args = parser.parse_args(argv)
    effective_home = args.codex_home or effective_codex_home()
    if args.command == "configure":
        provider_settings: ProviderSettings | None = None
        try:
            if args.provider:
                # Preflight the user Codex config before changing the gateway
                # config so malformed/link-backed settings fail without a
                # provider mutation.
                _safe_config_bytes(codex_config_path(effective_home))
                try:
                    configured = GatewaySupervisor(codex_home=effective_home).manager.load(cold=True)
                    provider_settings = ProviderSettings(gateway_host=configured.host, gateway_port=configured.port)
                except ConfigError:
                    provider_settings = ProviderSettings()
            path = write_default_config(effective_home, overwrite=args.replace)
            if provider_settings is not None:
                provider_result = apply_provider_patch(effective_home, provider_settings)
        except (ConfigError, OSError, ValueError) as exc:
            print(json.dumps({"status": "error", "error": str(exc), "code": "configuration_conflict"}, sort_keys=True))
            return 2
        result: dict[str, object] = {"status": "configured", "path": str(path)}
        if provider_settings is not None:
            result["provider_patch"] = provider_patch(
                provider_settings
            )
            result["provider"] = provider_result
        print(json.dumps(result, sort_keys=True))
        return 0
    supervisor = GatewaySupervisor(codex_home=effective_home)
    try:
        if args.command == "config-check":
            snapshot = supervisor.manager.load(cold=True)
            result = {"status": "valid", "CODEX_HOME": str(supervisor.codex_home), **snapshot.public_status()}
        elif args.command == "ensure":
            result = supervisor.ensure()
        elif args.command == "status":
            result = supervisor.status()
        elif args.command == "reload":
            result = supervisor.reload()
        else:
            result = supervisor.stop()
        if args.command == "ensure" and args.provider:
            snapshot = supervisor.manager.snapshot()
            # The supervisor has just verified the owned gateway. Tell Codex
            # to attach its OpenAI/OAuth auth material; the proxy itself never
            # stores or logs those credentials.
            settings = ProviderSettings(
                gateway_host=snapshot.host,
                gateway_port=snapshot.port,
                requires_openai_auth=True,
                gateway_verified=True,
            )
            result = dict(result)
            result["provider"] = apply_provider_patch(effective_home, settings)
    except (ConfigError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc), "code": "gateway_control_error"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    # ``reload`` reports a structured failure when the positively owned child
    # cannot be signaled or does not drain.  Preserve that diagnostic JSON but
    # make the administrative command fail for shell callers as well.
    if args.command == "reload" and result.get("reload_status") == "failed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
