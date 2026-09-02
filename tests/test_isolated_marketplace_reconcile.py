"""Regression coverage for isolated Cortex marketplace reconciliation.

The fixture exposes only the native Codex plugin commands used by sync.  It
therefore proves the supported command flow without touching the developer's
real Codex profile or manually editing a marketplace configuration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _write_fake_codex(directory: Path) -> Path:
    executable = directory / "codex"
    executable.write_text(
        r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

state_path = Path(os.environ["CORTEX_RECONCILE_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
state.setdefault("calls", []).append(args)

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

if args == ["plugin", "marketplace", "list", "--json"]:
    save()
    print(json.dumps({"marketplaces": state.get("marketplaces", [])}))
    raise SystemExit(0)
if len(args) == 5 and args[:3] == ["plugin", "marketplace", "remove"] and args[4] == "--json":
    name = args[3]
    state["marketplaces"] = [row for row in state.get("marketplaces", []) if row.get("name") != name]
    save()
    print("{}")
    raise SystemExit(0)
if len(args) == 5 and args[:3] == ["plugin", "marketplace", "add"] and args[4] == "--json":
    root = args[3]
    state.setdefault("marketplaces", []).append({"name": "cortex", "root": root})
    save()
    print("{}")
    raise SystemExit(0)
if args == ["plugin", "list", "--json"]:
    version = state.get("installed_version", "")
    rows = [] if not version else [{"pluginId": "cortex@cortex", "version": version}]
    save()
    print(json.dumps({"installed": rows}))
    raise SystemExit(0)
if len(args) == 4 and args[:2] == ["plugin", "remove"] and args[2] == "cortex@cortex" and args[3] == "--json":
    state["installed_version"] = ""
    save()
    print("{}")
    raise SystemExit(0)
if len(args) == 4 and args[:2] == ["plugin", "add"] and args[2] == "cortex@cortex" and args[3] == "--json":
    candidates = sorted(Path(os.environ["CODEX_HOME"]).glob(".cortex-candidates/*/plugins/cortex/.codex-plugin/plugin.json"))
    if len(candidates) != 1:
        raise SystemExit("expected exactly one staged candidate")
    manifest = candidates[0]
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    destination = Path(os.environ["CODEX_HOME"]) / "plugins/cache/cortex/cortex" / version
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(manifest.parent.parent, destination, dirs_exist_ok=True)
    state["installed_version"] = version
    save()
    print("{}")
    raise SystemExit(0)
save()
raise SystemExit(f"unexpected fake Codex command: {args}")
''',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def _run_sync(tmp_path: Path, state: dict, *, owner: Path | None = None,
              home: Path | None = None, codex_home: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    owner = owner or tmp_path / "owner"
    owner.mkdir(exist_ok=True)
    home = home or owner / ".cortex-dev"
    codex_home = codex_home or home / ".codex"
    if not home.exists():
        home.mkdir()
    if not codex_home.exists():
        codex_home.mkdir()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _write_fake_codex(tmp_path)
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": f"{tmp_path}:{env['PATH']}",
        "CORTEX_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
        "CORTEX_RECONCILE_STATE": str(state_path),
        "CORTEX_ISOLATED_MARKETPLACE_RECONCILE": "1",
        "CORTEX_ISOLATED_DEV_OWNER_HOME": str(owner),
        "CORTEX_ISOLATED_DEV_CODEX_HOME": str(codex_home),
    })
    completed = subprocess.run(
        ["bash", "scripts/sync-cortex.sh"], cwd=ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=60,
    )
    return completed, json.loads(state_path.read_text(encoding="utf-8"))


def _marketplace_calls(state: dict) -> list[list[str]]:
    return [call for call in state.get("calls", []) if call[:2] == ["plugin", "marketplace"]]


def test_stale_cortex_source_is_replaced_without_touching_unrelated_marketplaces(tmp_path: Path) -> None:
    state = {
        "marketplaces": [
            {"name": "cortex", "root": str(tmp_path / "stale-candidate")},
            {"name": "unrelated", "root": str(tmp_path / "unrelated")},
        ],
        "installed_version": "1.14.12+codex.sha256.stale0000000000",
    }
    completed, after = _run_sync(tmp_path, state)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "replaced stale isolated Cortex marketplace candidate" in completed.stdout
    calls = _marketplace_calls(after)
    assert calls[:3] == [
        ["plugin", "marketplace", "list", "--json"],
        ["plugin", "marketplace", "remove", "cortex", "--json"],
        ["plugin", "marketplace", "add", after["marketplaces"][1]["root"], "--json"],
    ]
    unrelated = [row for row in after["marketplaces"] if row["name"] == "unrelated"]
    assert unrelated == [{"name": "unrelated", "root": str(tmp_path / "unrelated")}]
    cortex = [row for row in after["marketplaces"] if row["name"] == "cortex"]
    assert len(cortex) == 1
    assert Path(cortex[0]["root"]).is_relative_to(tmp_path / "owner/.cortex-dev/.codex/.cortex-candidates")
    assert after["installed_version"].startswith("1.14.12+codex.sha256.")


def test_install_rebuilds_when_checkout_manifest_suffix_is_stale(tmp_path: Path) -> None:
    """Install mode must reconcile a generated suffix before marketplace validation."""
    manifest = ROOT / "plugins/cortex/.codex-plugin/plugin.json"
    original = manifest.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["version"] = "1.14.12+codex.sha256.0000000000000000"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        completed, state = _run_sync(
            tmp_path,
            {"marketplaces": [], "installed_version": ""},
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "staged Cortex candidate:" in completed.stdout
        staged = list((tmp_path / "owner/.cortex-dev/.codex/.cortex-candidates").glob("1.14.12+codex.sha256.*"))
        assert len(staged) == 1
        assert not staged[0].name.endswith("0000000000000000")
        assert state["installed_version"] == staged[0].name
    finally:
        manifest.write_text(original, encoding="utf-8")


def test_same_isolated_source_is_reused_and_missing_source_is_registered(tmp_path: Path) -> None:
    state = {"marketplaces": [{"name": "unrelated", "root": str(tmp_path / "other")}], "installed_version": ""}
    first, after_first = _run_sync(tmp_path, state)
    assert first.returncode == 0, first.stdout + first.stderr
    first_calls = _marketplace_calls(after_first)
    assert [call[:3] for call in first_calls] == [
        ["plugin", "marketplace", "list"],
        ["plugin", "marketplace", "add"],
    ]
    second, after_second = _run_sync(tmp_path, after_first)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "isolated Cortex marketplace source is current" in second.stdout
    all_calls = _marketplace_calls(after_second)
    assert all_calls[-1] == ["plugin", "marketplace", "list", "--json"]
    assert sum(call[2] == "remove" for call in all_calls) == 0
    assert sum(call[2] == "add" for call in all_calls) == 1


def test_reconcile_refuses_symlinked_isolated_target_before_native_marketplace_calls(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    actual = tmp_path / "actual-dev-home"
    actual.mkdir()
    (owner / ".cortex-dev").symlink_to(actual, target_is_directory=True)
    completed, after = _run_sync(
        tmp_path, {"marketplaces": [], "installed_version": ""}, owner=owner,
        home=owner / ".cortex-dev", codex_home=owner / ".cortex-dev/.codex",
    )
    assert completed.returncode != 0
    assert "symlink" in completed.stdout + completed.stderr
    assert not _marketplace_calls(after)


def test_reconcile_refuses_main_profile_before_native_marketplace_calls(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    main_codex_home = owner / ".codex"
    main_codex_home.mkdir()
    completed, after = _run_sync(
        tmp_path, {"marketplaces": [], "installed_version": ""}, owner=owner,
        home=owner, codex_home=main_codex_home,
    )
    assert completed.returncode != 0
    assert "exact isolated" in completed.stdout + completed.stderr
    assert not _marketplace_calls(after)
