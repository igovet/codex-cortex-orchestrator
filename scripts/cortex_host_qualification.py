#!/usr/bin/env python3
"""Save passive launch evidence for the exact isolated candidate, never a verdict."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import re
import sys
import tempfile
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
from cortex_runtime.host_boundary import Capability, CodexHostProbe, HostIdentity, canonical, digest
from cortex_candidate_receipt import read_verified_receipt


def cli_version() -> str:
    # Reuse the bounded host command reader rather than introduce an unbounded
    # subprocess capture. A CLI version never establishes Desktop identity.
    spec = importlib.util.spec_from_file_location("qualification_preflight", ROOT / "scripts/cortex-host-preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code, output, failure = module.bounded_command(["codex", "--version"], timeout=10, max_bytes=1024)
    match = re.fullmatch(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[-+.][a-zA-Z0-9.-]+)?)\s*", output)
    return match.group(1) if code == 0 and not failure and match else "unverified"


def capture_launch(owner_home: Path, host: str) -> Path:
    codex_home = owner_home / ".cortex-dev/.codex"
    receipt = read_verified_receipt(source_root=ROOT, owner_home=owner_home,
        isolated_home=owner_home / ".cortex-dev", isolated_codex_home=codex_home)
    config_path = codex_home / "config.toml"
    fd = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024 * 1024:
            raise ValueError("isolated configuration is not a bounded owned file")
        config = tomllib.loads(stream.read(1024 * 1024 + 1).decode("utf-8"))
    # Fingerprint relevant allowlisted settings only; no tokens, environment
    # values, server URLs, full configuration or user prompts are exported.
    agents = config.get("agents", {})
    features = config.get("features", {})
    selected = {
        "default_subagent_model": agents.get("default_subagent_model"),
        "max_threads": agents.get("max_threads"),
        "multi_agent_v2": features.get("multi_agent_v2"),
    }
    config_digest = digest(selected)
    evidence = ("config-sha256:" + config_digest,)
    capabilities = {}
    hook_path = Path(receipt["candidate_path"]) / "hooks/hooks.json"
    fd = os.open(hook_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("candidate hook declaration is not a bounded file")
        hook_payload = json.loads(stream.read(65537))
    hook_events = hook_payload.get("hooks") if isinstance(hook_payload, dict) else None
    supported_events = {"UserPromptSubmit", "SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
                        "Stop", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact"}
    if not isinstance(hook_events, dict) or not hook_events or not set(hook_events) <= supported_events:
        raise ValueError("candidate hook declaration is invalid")
    capabilities["hooks.events"] = Capability("declared", ("payload-sha256:" + receipt["candidate_digest"],),
                                                canonical(sorted(hook_events)))
    if selected["default_subagent_model"] == "gpt-5.6-luna":
        capabilities["models.default"] = Capability("configured", evidence, canonical("gpt-5.6-luna"))
    capacity = selected["max_threads"]
    if type(capacity) is int and capacity > 0:
        capabilities["capacity.configured"] = Capability("configured", evidence, canonical(capacity))
    # A launcher cannot know the future connection/catalogue or infer Desktop
    # engine version from the CLI. Live observers must establish those later.
    identity = HostIdentity(host, cli_version() if host == "cli" else "unverified", "unverified", receipt["candidate_digest"],
                            "unverified", config_digest, "launch-" + uuid.uuid4().hex)
    snapshot = CodexHostProbe.capture(identity, (), capabilities)
    directory = Path(tempfile.mkdtemp(prefix=".host-qualification-", dir=codex_home))
    path = directory / "capabilities.json"
    CodexHostProbe.save(snapshot, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-home", type=Path, required=True)
    parser.add_argument("--host", choices=("cli", "desktop"), required=True)
    args = parser.parse_args()
    print(capture_launch(args.owner_home, args.host))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
