"""Focused regressions for source synchronization and routing-catalog drift."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_github_release_gate_installs_every_direct_python_dependency() -> None:
    workflow = (ROOT / ".github/workflows/cortex.yml").read_text(encoding="utf-8")
    assert '"pytest>=8,<9"' in workflow
    assert '"PyYAML>=6,<7"' in workflow
    assert "if: runner.os == 'macOS'" in workflow
    assert "brew install tmux" in workflow


def test_github_release_gate_runs_the_current_complete_suite() -> None:
    workflow = (ROOT / ".github/workflows/cortex.yml").read_text(encoding="utf-8")
    assert "tests/test_marketplace_release_gate.py" not in workflow
    assert "python -B -m pytest -q\n" in workflow
    assert "PYTHONPATH: plugins/cortex/scripts" in workflow


def test_sync_content_parity_uses_the_canonical_plugin_digest_contract() -> None:
    """Install parity must not duplicate platform-sensitive tree hashing."""
    script = (ROOT / "scripts/sync-cortex.sh").read_text(encoding="utf-8")
    content_matches = script.split("content_matches() {", 1)[1].split(
        "\nwrite_isolated_candidate_receipt() {", 1,
    )[0]
    assert "plugin_tree_digest(installed, manifest)" in content_matches
    assert "manifest.plugin_digest(root)" in content_matches
    assert "def tree_manifest" not in content_matches
    assert "TemporaryDirectory" not in content_matches


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
  staged_manifest="$(find "$CODEX_HOME/.cortex-candidates" -type f -path '*/plugins/cortex/.codex-plugin/plugin.json' -print -quit)"
  version="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$staged_manifest")"
  destination="$CODEX_HOME/plugins/cache/cortex/cortex/$version"
  mkdir -p "$destination"
  staged_plugin="$(dirname "$(dirname "$staged_manifest")")"
  cp -a "$staged_plugin/." "$destination/"
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
        assert re.fullmatch(r"1\.14\.10\+codex\.sha256\.[0-9a-f]{16}", after_version)
        assert before_version == after_version
        staged_versions = list((codex_home / ".cortex-candidates").glob("1.14.10+codex.sha256.*"))
        assert len(staged_versions) == 1
        assert (codex_home / "plugins/cache/cortex/cortex" / staged_versions[0].name).is_dir()
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


def test_sync_rejects_symlinked_candidate_staging_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    real_candidates = tmp_path / "real-candidates"
    real_candidates.mkdir()
    (codex_home / ".cortex-candidates").symlink_to(real_candidates, target_is_directory=True)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": f"{tmp_path}:{environment['PATH']}",
        "CORTEX_PYTHON": sys.executable,
    })
    completed = subprocess.run(
        ["bash", "scripts/sync-cortex.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode != 0
    assert "candidate staging root" in completed.stdout + completed.stderr


def test_release_verifier_normalizes_the_os_temp_alias(tmp_path: Path) -> None:
    physical_temp = tmp_path / "physical-temp"
    physical_temp.mkdir()
    temp_alias = tmp_path / "temp-alias"
    temp_alias.symlink_to(physical_temp, target_is_directory=True)
    environment = os.environ.copy()
    environment.update({
        "TMPDIR": str(temp_alias),
        "TMP": str(temp_alias),
        "TEMP": str(temp_alias),
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    completed = subprocess.run(
        [sys.executable, "-B", "scripts/verify-cortex-release.py", "--mode", "source"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "release validation passed" in completed.stdout


def test_sync_shell_path_rejects_symlinked_installed_version_parent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    real_cache = tmp_path / "real-cache"
    real_cache.mkdir()
    cache_parent = codex_home / "plugins" / "cache" / "cortex"
    cache_parent.parent.mkdir(parents=True)
    cache_parent.symlink_to(real_cache, target_is_directory=True)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2 $3" == "plugin list --json" ]]; then
  printf '{"installed":[{"pluginId":"cortex@cortex","version":"%s"}]}\\n' "$SYNC_EXPECTED_VERSION"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": f"{tmp_path}:{environment['PATH']}",
        "CORTEX_PYTHON": sys.executable,
        "SYNC_EXPECTED_VERSION": json.loads(
            (ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
        )["version"],
    })
    completed = subprocess.run(
        ["bash", "scripts/sync-cortex.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode != 0
    assert "installed candidate version root" in completed.stdout + completed.stderr


def test_sync_shell_path_reuses_an_unchanged_isolated_candidate(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
state="$CODEX_HOME/fake-installed-version"
add_count="$CODEX_HOME/fake-add-count"
if [[ "$1 $2 $3" == "plugin list --json" ]]; then
  if [[ -f "$state" ]]; then
    version="$(<"$state")"
    printf '{"installed":[{"pluginId":"cortex@cortex","version":"%s"}]}\\n' "$version"
  else
    printf '%s\\n' '{"installed":[]}'
  fi
  exit 0
fi
if [[ "$1 $2" == "plugin add" ]]; then
  count=0
  [[ -f "$add_count" ]] && count="$(<"$add_count")"
  printf '%s\\n' "$((count + 1))" >"$add_count"
  staged_manifest="$(find "$CODEX_HOME/.cortex-candidates" -type f -path '*/plugins/cortex/.codex-plugin/plugin.json' -print -quit)"
  version="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$staged_manifest")"
  destination="$CODEX_HOME/plugins/cache/cortex/cortex/$version"
  mkdir -p "$destination"
  staged_plugin="$(dirname "$(dirname "$staged_manifest")")"
  cp -a "$staged_plugin/." "$destination/"
  printf '%s\\n' "$version" >"$state"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": f"{tmp_path}:{environment['PATH']}",
        "CORTEX_PYTHON": sys.executable,
    })
    first = subprocess.run(
        ["bash", "scripts/sync-cortex.sh"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    second = subprocess.run(
        ["bash", "scripts/sync-cortex.sh"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert (codex_home / "fake-add-count").read_text(encoding="utf-8").strip() == "1"
    assert "installed from this repository" in second.stdout


def test_cortex_dev_launches_only_the_stamped_receipted_candidate_and_reuses_it(tmp_path: Path) -> None:
    """The launcher must consume sync's receipt, never rebuild a base cache path."""
    stable_home = tmp_path / "stable-home"
    stable_home.mkdir()
    (stable_home / ".codex").mkdir()
    (stable_home / ".codex/config.toml").write_text(
        '[mcp_servers.codebase_memory]\n'
        'enabled = true\n'
        'command = "/usr/local/bin/codebase-memory-mcp"\n'
        'default_tools_approval_mode = "approve"\n',
        encoding="utf-8",
    )
    stable_config_before = (stable_home / ".codex/config.toml").read_bytes()
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
state="$CODEX_HOME/fake-installed-version"
count="$CODEX_HOME/fake-add-count"
case "${1:-} ${2:-} ${3:-}" in
  "plugin marketplace list") printf '%s\\n' '{"marketplaces":[]}' ; exit 0 ;;
  "plugin marketplace add") exit 0 ;;
  "plugin list --json")
    if [[ -f "$state" ]]; then printf '{"installed":[{"pluginId":"cortex@cortex","version":"%s"}]}\\n' "$(<"$state")"; else printf '%s\\n' '{"installed":[]}' ; fi
    exit 0 ;;
  "plugin add cortex@cortex")
    staged_manifest="$(find "$CODEX_HOME/.cortex-candidates" -type f -path '*/plugins/cortex/.codex-plugin/plugin.json' -print -quit)"
    version="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$staged_manifest")"
    destination="$CODEX_HOME/plugins/cache/cortex/cortex/$version"
    mkdir -p "$destination"
    staged_plugin="$(dirname "$(dirname "$staged_manifest")")"
    cp -a "$staged_plugin/." "$destination/"
    printf '%s\\n' "$version" >"$state"
    old=0; [[ -f "$count" ]] && old="$(<"$count")"; printf '%s\\n' "$((old + 1))" >"$count"
    exit 0 ;;
esac
printf 'fake ordinary codex candidate=%s build=%s\\n' "${CORTEX_CANDIDATE_PATH:-missing}" "${CORTEX_BUILD_ID:-missing}"
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(stable_home),
        "PATH": f"{tmp_path}:{environment['PATH']}",
        "CORTEX_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    environment.pop("CODEX_HOME", None)
    first = subprocess.run(
        ["bash", "scripts/cortex-dev"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=90,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    isolated_codex = stable_home / ".cortex-dev/.codex"
    receipt = json.loads((isolated_codex / ".cortex-candidate-receipt.json").read_text(encoding="utf-8"))
    stamped = receipt["candidate_version"]
    assert re.fullmatch(r"1\.14\.10\+codex\.sha256\.[0-9a-f]{16}", stamped)
    assert receipt["candidate_path"] == str(isolated_codex / "plugins/cache/cortex/cortex" / stamped)
    assert f"Cortex candidate version={stamped}" in first.stdout
    assert f"Cortex candidate path={receipt['candidate_path']}" in first.stdout
    assert "Cortex candidate receipt=" in first.stdout
    assert f"fake ordinary codex candidate={receipt['candidate_path']}" in first.stdout
    isolated_config = (isolated_codex / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.codebase_memory]" in isolated_config
    assert "/usr/local/bin/codebase-memory-mcp" in isolated_config
    isolated_payload = tomllib.loads(isolated_config)
    assert isolated_payload["mcp_servers"]["codebase_memory"]["env"] == {"HOME": str(stable_home.resolve())}
    assert (stable_home / ".codex/config.toml").read_bytes() == stable_config_before
    # The base semantic version is permitted as display metadata only.  Its
    # unstamped cache directory must never be selected or printed as a path.
    assert f"/plugins/cache/cortex/cortex/1.14.10\n" not in first.stdout
    first_receipt = (isolated_codex / ".cortex-candidate-receipt.json").read_bytes()
    second = subprocess.run(
        ["bash", "scripts/cortex-dev"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=90,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert (isolated_codex / ".cortex-candidate-receipt.json").read_bytes() == first_receipt
    assert (isolated_codex / "fake-add-count").read_text(encoding="utf-8").strip() == "1"


def test_cortex_dev_fails_closed_when_codebase_memory_is_not_configured(tmp_path: Path) -> None:
    stable_home = tmp_path / "stable-home"
    stable_home.mkdir()
    environment = os.environ.copy()
    environment.update({
        "HOME": str(stable_home),
        "PATH": environment["PATH"],
        "CORTEX_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    environment.pop("CODEX_HOME", None)
    completed = subprocess.run(
        ["bash", "scripts/cortex-dev"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert completed.returncode != 0
    assert "Codebase Memory MCP is not configured" in completed.stderr


def test_cortex_dev_does_not_copy_inline_codebase_memory_environment_values(tmp_path: Path) -> None:
    stable_home = tmp_path / "stable-home"
    (stable_home / ".codex").mkdir(parents=True)
    (stable_home / ".codex/config.toml").write_text(
        '[mcp_servers.codebase_memory]\n'
        'enabled = true\n'
        'command = "/usr/local/bin/codebase-memory-mcp"\n'
        '[mcp_servers.codebase_memory.env]\n'
        'TOKEN = "secret-that-must-not-be-copied"\n',
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({
        "HOME": str(stable_home),
        "PATH": environment["PATH"],
        "CORTEX_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    environment.pop("CODEX_HOME", None)
    completed = subprocess.run(
        ["bash", "scripts/cortex-dev"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "credential-bearing configuration" in combined
    assert "secret-that-must-not-be-copied" not in combined


def test_cortex_dev_fails_closed_when_codebase_memory_is_disabled(tmp_path: Path) -> None:
    stable_home = tmp_path / "stable-home"
    (stable_home / ".codex").mkdir(parents=True)
    (stable_home / ".codex/config.toml").write_text(
        '[mcp_servers.codebase_memory]\n'
        'enabled = false\n'
        'command = "/usr/local/bin/codebase-memory-mcp"\n',
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({
        "HOME": str(stable_home),
        "PATH": environment["PATH"],
        "CORTEX_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    environment.pop("CODEX_HOME", None)
    completed = subprocess.run(
        ["bash", "scripts/cortex-dev"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert completed.returncode != 0
    assert "Codebase Memory MCP is disabled" in completed.stderr


def test_isolated_sync_fails_when_installed_candidate_cannot_commit_its_receipt(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    home = owner / ".cortex-dev"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-} ${2:-} ${3:-}" in
  "plugin marketplace list") printf '%s\\n' '{"marketplaces":[]}' ; exit 0 ;;
  "plugin marketplace add") exit 0 ;;
  "plugin list --json") printf '%s\\n' '{"installed":[]}' ; exit 0 ;;
  "plugin add cortex@cortex")
    manifest="$(find "$CODEX_HOME/.cortex-candidates" -type f -path '*/plugins/cortex/.codex-plugin/plugin.json' -print -quit)"
    version="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$manifest")"
    destination="$CODEX_HOME/plugins/cache/cortex/cortex/$version"; mkdir -p "$destination"
    cp -a "$(dirname "$(dirname "$manifest")")/." "$destination/"; exit 0 ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home), "CODEX_HOME": str(codex_home), "PATH": f"{tmp_path}:{environment['PATH']}",
        "CORTEX_PYTHON": sys.executable, "CORTEX_ISOLATED_MARKETPLACE_RECONCILE": "1",
        "CORTEX_ISOLATED_DEV_OWNER_HOME": str(owner), "CORTEX_ISOLATED_DEV_CODEX_HOME": str(codex_home),
        "CORTEX_TEST_RECEIPT_WRITE_FAIL": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    completed = subprocess.run(
        ["bash", "scripts/sync-cortex.sh"], cwd=ROOT, env=environment,
        text=True, capture_output=True, check=False, timeout=90,
    )
    assert completed.returncode != 0
    assert "receipt was not committed" in completed.stdout + completed.stderr
    assert list((codex_home / "plugins/cache/cortex/cortex").glob("1.14.10+codex.sha256.*"))
    assert not (codex_home / ".cortex-candidate-receipt.json").exists()
