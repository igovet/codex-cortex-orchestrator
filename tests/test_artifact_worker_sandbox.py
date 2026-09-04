"""Actual workspace sandbox, no model: execute the rendered worker procedure."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cortex_runtime.artifact_fingerprint import archive_path
from test_node_assignment_receipts import node_case


def test_archive_namespace_is_project_and_host_specific(tmp_path):
    first = archive_path(tmp_path / "one", "a" * 64)
    assert first.parent == Path("/tmp").resolve()
    assert first != archive_path(tmp_path / "two", "a" * 64)
    assert first != archive_path(tmp_path / "one", "b" * 64)
    assert not first.exists()  # path derivation is not a filesystem mutation


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("codex") is None,
                    reason="Actual Linux Codex workspace sandbox is required")
def test_rendered_baseline_procedure_runs_in_actual_workspace_sandbox(node_case, tmp_path):
    store, args = node_case
    dispatched, _ = store.open_node_assignment(**args)
    delegation = dispatched["delegation"]
    brief = store._read(lambda c: store._worker_brief(c, store._task(c, args["task_id"]), delegation))
    command = brief["assignment"]["artifact"]["worker_procedure"]["command"]
    archive = Path(command[command.index("--archive-root") + 1])
    assert archive == archive_path(store._codex_home, store.project_hash)
    assert archive.parent == Path("/tmp").resolve() and not archive.exists()
    isolated_home = tmp_path / "sandbox-home"
    isolated_home.mkdir()
    (isolated_home / ".codex").mkdir()
    environment = {**os.environ, "HOME": str(isolated_home),
                   "CODEX_HOME": str(isolated_home / ".codex")}
    launch = [shutil.which("codex"), "sandbox", "-P", ":workspace", "-C", str(store.project_root)]
    try:
        first = subprocess.run([*launch, *command], env=environment, capture_output=True, text=True, timeout=30)
        assert first.returncode == 0, first.stderr
        observed = json.loads(first.stdout)
        assert observed["state"] == "observed"
        second = subprocess.run([*launch, *command, "--compare", observed["fingerprint"]],
                                env=environment, capture_output=True, text=True, timeout=30)
        assert second.returncode == 0, second.stderr
        comparison = json.loads(second.stdout)
        assert comparison["state"] == "observed"
        assert comparison["fingerprint"] == observed["fingerprint"]
        assert comparison["comparisons"][0]["changes"]["count"] == 0
        from cortex_runtime.typed_publications import artifact_schema
        from cortex_runtime.execution_graph import _validate_shape
        _validate_shape(comparison["terminal_observation"], artifact_schema())
        assert set(comparison["terminal_observation"]) == {"method", "start", "end", "changes"}
        assert archive.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in archive.iterdir())
        assert not (store.root / "artifact-manifests").exists()
    finally:
        if archive.is_dir() and not archive.is_symlink():
            shutil.rmtree(archive)
