"""Focused regressions for source synchronization and routing-catalog drift."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _renderer_module():
    path = ROOT / "scripts/render_cortex_tool_catalog.py"
    spec = importlib.util.spec_from_file_location("cortex_catalog_renderer_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_routing_writer_updates_only_the_generated_table_from_profiles() -> None:
    renderer = _renderer_module()
    profiles = json.loads((ROOT / "plugins/cortex/profiles.json").read_text(encoding="utf-8"))
    rows = tuple(
        (
            item["model"],
            item["recommended_effort"],
            item["choose_for"],
        )
        for item in profiles["model_routing"]["recommendations"]
    )
    original = """## Per-delegation model selection

<!-- BEGIN GENERATED CORTEX MODEL ROUTING -->
Keep this explanatory prose and the markers.

| Exact model | Recommended effort | Recommend for |
| --- | --- | --- |
| `gpt-5.6-luna` | `high` | stale guidance |
<!-- END GENERATED CORTEX MODEL ROUTING -->

The surrounding guidance must remain unchanged.
"""

    updated = renderer.update_model_routing(original, rows)

    assert "Keep this explanatory prose and the markers." in updated
    assert "The surrounding guidance must remain unchanged." in updated
    assert renderer.routing_rows(updated) == rows


def test_sync_cleans_plugin_bytecode_before_validation(tmp_path: Path) -> None:
    bytecode = ROOT / "plugins/cortex/scripts/__pycache__"
    bytecode.mkdir(parents=True, exist_ok=True)
    (bytecode / "residue.pyc").write_bytes(b"generated test residue")
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    fake_codex = tmp_path / "codex-cli"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2 $3" == "plugin list --json" ]]; then
  printf '%s\\n' '{"installed":[]}'
  exit 0
fi
if [[ "$1 $2" == "plugin add" ]]; then
  version="$(python3 -B -c 'import json, os; print(json.load(open(os.path.join(os.environ["SYNC_TEST_REPO"], "plugins/cortex/.codex-plugin/plugin.json")))["version"])')"
  destination="$CODEX_HOME/plugins/cache/cortex/cortex/$version"
  mkdir -p "$destination"
  cp -a "$SYNC_TEST_REPO/plugins/cortex/." "$destination/"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "PATH": f"{tmp_path}:{environment['PATH']}",
            "SYNC_TEST_REPO": str(ROOT),
            "CORTEX_PYTHON": sys.executable,
        }
    )
    # The fake executable is named codex so the source workflow exercises its
    # complete install path while all resulting state remains in tmp_path.
    fake_codex.rename(tmp_path / "codex")

    manifest = ROOT / "plugins/cortex/.codex-plugin/plugin.json"
    original_manifest = manifest.read_text(encoding="utf-8")
    before_version = json.loads(original_manifest)["version"]
    try:
        completed = subprocess.run(
            ["bash", "scripts/sync-cortex.sh"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "marketplace validation passed" in completed.stdout
        after_version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        assert re.fullmatch(r"12\.1\.1\+codex\.\d{14}", after_version)
        assert after_version > before_version
        assert (codex_home / "plugins/cache/cortex/cortex" / after_version).is_dir()
        assert not bytecode.exists()
        assert not list((ROOT / "plugins/cortex").rglob("*.pyc"))
    finally:
        # The workflow intentionally refreshes cache metadata in normal mode;
        # restore this shared-checkout fixture after asserting the behavior.
        manifest.write_text(original_manifest, encoding="utf-8")
        # The test's fixture is generated state, and cleanup is limited to
        # this exact path in the shared checkout.
        if bytecode.exists():
            for child in bytecode.iterdir():
                child.unlink()
            bytecode.rmdir()
