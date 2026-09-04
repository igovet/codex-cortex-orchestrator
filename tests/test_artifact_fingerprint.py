"""Worker fingerprints detect content, index, metadata and boundary changes."""
import subprocess

import pytest

from cortex_runtime.artifact_fingerprint import FingerprintError, changed_paths, observe, save_manifest, load_manifest


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def snapshot(root, method="path_manifest_v1", paths=("src",)):
    return observe(root, method=method, artifact_paths=paths)


def test_non_git_content_permissions_deletion_and_absence(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = src / "a.txt"
    path.write_text("first")
    first = snapshot(tmp_path)
    assert snapshot(tmp_path)["fingerprint"] == first["fingerprint"]
    path.write_text("second")
    second = snapshot(tmp_path)
    assert first["fingerprint"] != second["fingerprint"]
    commitment = changed_paths(first, second, mutation_domains=["src"])
    assert commitment["samples"] == ["src/a.txt"]
    assert commitment["within_domains"]
    assert not changed_paths(first, second, mutation_domains=["other"])["within_domains"]
    path.chmod(0o700)
    third = snapshot(tmp_path)
    assert third["fingerprint"] != second["fingerprint"]
    path.unlink()
    assert snapshot(tmp_path)["fingerprint"] != third["fingerprint"]
    absent = snapshot(tmp_path, paths=["not-created"])
    assert absent["entries"] == [{"path": "not-created", "kind": "absent"}]


def test_symlink_is_hashed_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private"
    secret.write_text("never emitted")
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").symlink_to(outside, target_is_directory=True)
    before = snapshot(root)
    assert before["entries"][0]["kind"] == "symlink"
    secret.write_text("different private content")
    assert snapshot(root)["fingerprint"] == before["fingerprint"]
    with pytest.raises(FingerprintError, match="parent is a symlink"):
        snapshot(root, paths=["src/private"])


@pytest.mark.parametrize("path", ["../escape", "/absolute", ".git/config", "bad\x00name", "src//file", "src/./file"])
def test_invalid_boundary_rejected(tmp_path, path):
    with pytest.raises(FingerprintError):
        snapshot(tmp_path, paths=[path])


def test_git_tracks_unborn_head_index_unstaged_and_ignored_declared_files(tmp_path):
    git(tmp_path, "init", "-q")
    src = tmp_path / "src"
    src.mkdir()
    file = src / "a"
    file.write_text("a")
    (tmp_path / ".gitignore").write_text("src/ignored\n")
    before = snapshot(tmp_path, "git_content_v1")
    git(tmp_path, "add", "src/a")
    staged = snapshot(tmp_path, "git_content_v1")
    assert before["fingerprint"] != staged["fingerprint"]
    assert changed_paths(before, staged, mutation_domains=["src"])["samples"] == ["src/a"]
    file.write_text("unstaged")
    unstaged = snapshot(tmp_path, "git_content_v1")
    assert staged["fingerprint"] != unstaged["fingerprint"]
    (src / "ignored").write_text("generated output")
    ignored = snapshot(tmp_path, "git_content_v1")
    assert ignored["fingerprint"] != unstaged["fingerprint"]
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    assert snapshot(tmp_path, "git_content_v1")["fingerprint"] != ignored["fingerprint"]


def test_non_git_does_not_pretend_git_observation_succeeded(tmp_path):
    with pytest.raises(FingerprintError, match="Git artifact capability unavailable"):
        snapshot(tmp_path, "git_content_v1")


@pytest.mark.parametrize("method", ["path_manifest_v1", "git_content_v1"])
def test_manifest_roundtrip_recomputes_independent_changed_paths(tmp_path, method):
    project = tmp_path / "project"
    project.mkdir()
    if method == "git_content_v1":
        git(project, "init", "-q")
    (project / "src").mkdir()
    path = project / "src/a"
    path.write_text("before")
    before = snapshot(project, method)
    archive = tmp_path / "private-manifests"
    fingerprint = save_manifest(project, archive, before)
    assert save_manifest(project, archive, before) == fingerprint
    path.write_text("after")
    after = snapshot(project, method)
    restored = load_manifest(project, archive, fingerprint)
    assert restored == before
    assert changed_paths(restored, after, mutation_domains=["src"]) == changed_paths(before, after, mutation_domains=["src"])
    assert archive.stat().st_mode & 0o777 == 0o700
    stored = archive / (fingerprint + ".json")
    assert stored.stat().st_mode & 0o777 == 0o600
    assert "before" not in stored.read_text()
    assert snapshot(project, method)["fingerprint"] == after["fingerprint"]


def test_manifest_rejects_tampering_and_in_project_archive(tmp_path):
    import json
    project = tmp_path / "project"
    project.mkdir()
    before = snapshot(project)
    with pytest.raises(FingerprintError, match="outside the project"):
        save_manifest(project, project / "archive", before)
    archive = tmp_path / "manifests"
    fingerprint = save_manifest(project, archive, before)
    stored = archive / (fingerprint + ".json")
    tampered = json.loads(stored.read_text())
    tampered["paths"] = ["other"]
    stored.write_text(json.dumps(tampered))
    with pytest.raises(FingerprintError, match="commitment mismatch"):
        load_manifest(project, archive, fingerprint)
    with pytest.raises(FingerprintError, match="commitment mismatch"):
        save_manifest(project, archive, before)


def test_boundary_is_part_of_the_fingerprint_even_for_identical_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a").write_text("same")
    first = snapshot(tmp_path, paths=["src"])
    second = snapshot(tmp_path, paths=["src", "src/a"])
    assert first["entries"] == second["entries"]
    assert first["fingerprint"] != second["fingerprint"]
    with pytest.raises(FingerprintError, match="boundary changed"):
        changed_paths(first, second, mutation_domains=["."])


def test_head_only_change_cannot_claim_empty_path_change(tmp_path):
    git(tmp_path, "init", "-q")
    before = snapshot(tmp_path, "git_content_v1")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture")
    after = snapshot(tmp_path, "git_content_v1")
    changed = changed_paths(before, after, mutation_domains=["."])
    assert changed["samples"] == [".git/HEAD"]
    assert not changed["within_domains"]


def test_worker_executable_persists_and_compares_without_printing_manifest(tmp_path):
    import json
    from pathlib import Path
    import cortex_runtime.artifact_fingerprint as module
    project = tmp_path / "project"
    project.mkdir()
    path = project / "example"
    path.write_text("private file contents")
    command = ["python3", str(Path(module.__file__)), "--project-root", str(project),
        "--archive-root", str(tmp_path / "archive"), "--method", "auto", "--artifact-path", "."]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    result = json.loads(first.stdout)
    assert result["state"] == "observed"
    assert result["method"] == "path_manifest_v1"
    assert "private file contents" not in first.stdout
    path.write_text("changed")
    second = subprocess.run([*command, "--compare", result["fingerprint"], "--mutation-domain", "."], check=True, capture_output=True, text=True)
    observed = json.loads(second.stdout)
    assert "terminal_observation" not in result
    interval = observed["terminal_observation"]
    assert set(interval) == {"method", "start", "end", "changes"}
    assert interval["start"] == result["fingerprint"]
    assert interval["end"] == observed["fingerprint"]
    assert interval["changes"] == observed["comparisons"][0]["changes"]
    assert observed["comparisons"][0]["changes"]["samples"] == ["example"]
    assert observed["comparisons"][0]["changes"]["within_domains"]
    unavailable = subprocess.run([*command, "--compare", "f" * 64], check=True, capture_output=True, text=True)
    assert json.loads(unavailable.stdout)["state"] == "unavailable"
    assert not unavailable.stderr
