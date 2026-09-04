"""Source package parity checks for Codex CLI and Desktop host surfaces."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/cortex-host-preflight.py"
PLUGIN_ROOT = ROOT / "plugins/cortex"


def _module():
    spec = importlib.util.spec_from_file_location(
        "cortex_host_preflight_plugin_contract", PREFLIGHT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copied_hooks(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    shutil.copytree(PLUGIN_ROOT / "hooks", root / "hooks")
    return root


def test_source_plugin_matches_the_preflight_mcp_and_hook_contract() -> None:
    module = _module()
    actual_mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    assert actual_mcp == module.EXPECTED_MCP
    assert module.hook_contract_failure(PLUGIN_ROOT) is None
    result, version = module.inspect_plugin(PLUGIN_ROOT)
    assert result["status"] == "PASS", result
    assert version == json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]


def test_every_packaged_hook_uses_the_shared_python3_no_bytecode_contract() -> None:
    payload = json.loads(
        (PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8")
    )
    commands = [
        callback["command"]
        for declarations in payload["hooks"].values()
        for declaration in declarations
        for callback in declaration["hooks"]
    ]
    assert commands
    assert all(command.startswith("python3 -B ") for command in commands)
    assert all("/usr/bin/python3" not in command for command in commands)


def test_preflight_rejects_an_absolute_system_python_hook(tmp_path: Path) -> None:
    module = _module()
    root = _copied_hooks(tmp_path)
    path = root / "hooks/hooks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] = (
        '/usr/bin/python3 -B "$PLUGIN_ROOT/hooks/cortex_activation.py"'
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "must not hard-code /usr/bin/python3" in module.hook_contract_failure(root)


def test_preflight_rejects_a_hook_without_no_bytecode_mode(tmp_path: Path) -> None:
    module = _module()
    root = _copied_hooks(tmp_path)
    path = root / "hooks/hooks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["hooks"]["Stop"][0]["hooks"][0]["command"] = (
        'python3 "$PLUGIN_ROOT/hooks/cortex_activation.py"'
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "shared python3 -B contract" in module.hook_contract_failure(root)


def test_preflight_requires_the_exact_native_agent_matcher(tmp_path: Path) -> None:
    module = _module()
    root = _copied_hooks(tmp_path)
    path = root / "hooks/hooks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for declaration in payload["hooks"]["PreToolUse"]:
        if declaration.get("matcher") == "^Agent$":
            declaration["matcher"] = "Agent"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "exact native Agent lifecycle matcher" in module.hook_contract_failure(root)


def test_passive_snapshot_does_not_claim_native_capabilities(tmp_path, monkeypatch, capsys):
    module = _module()
    passed = {"name": "fixture", "status": "PASS", "detail": "fixture"}
    monkeypatch.setattr(module, "resolve_python", lambda: (passed, None))
    monkeypatch.setattr(module, "inspect_codex", lambda: passed)
    monkeypatch.setattr(module, "inspect_plugin", lambda root: (passed, "fixture-version"))
    monkeypatch.setattr(module, "package_digest", lambda root: "fixture-digest")
    monkeypatch.setattr(module, "inspect_cache", lambda *args: passed)
    monkeypatch.setattr(module, "inspect_registration", lambda *args: passed)
    monkeypatch.setattr(module, "inspect_mcp_config", lambda *args: passed)
    output = tmp_path / "host.json"
    monkeypatch.setattr(module.sys, "argv", ["preflight", "--json", "--host", "desktop", "--capability-output", str(output)])
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["capabilities"]["qualification"] == "unverified"
    snapshot = json.loads(output.read_text())["snapshot"]
    assert snapshot["identity"]["host"] == "desktop"
    assert snapshot["tools"] == []
    assert all(value["state"] == "unverified" for _, value in snapshot["capabilities"])
