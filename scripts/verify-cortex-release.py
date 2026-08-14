#!/usr/bin/env python3
"""Validate the tracked Cortex release tree without using local runtime state."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-cortex-marketplace.py"
FORBIDDEN_PARTS = {"__pycache__"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
SECRET_PRONE_DIRECTORIES = {".aws", ".docker", ".gnupg", ".kube", ".ssh"}
SECRET_PRONE_BASENAMES = {
    ".env",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".terraformrc",
    "_netrc",
    "application_default_credentials.json",
    "client_secret.json",
    "client-secrets.json",
    "credentials.json",
    "credentials.tfrc.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "service-account.json",
    "service_account.json",
}
SECRET_PRONE_SUFFIXES = {".jks", ".kdbx", ".key", ".keystore", ".p12", ".pem", ".pfx"}
REQUIRED_PUBLIC_FILES = (
    Path("README.md"),
    Path("LICENSE"),
    Path("CHANGELOG.md"),
    Path("SECURITY.md"),
    Path("PRIVACY.md"),
    Path("docs/release-readiness.md"),
    Path(".agents/plugins/marketplace.json"),
    Path("plugins/cortex/.codex-plugin/plugin.json"),
)
PRIVATE_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s`]+/")


def fail(message: str) -> None:
    raise SystemExit(f"release validation failed: {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root containing the tracked release")
    parser.add_argument("--require-tracked", action="store_true", help="fail instead of skipping when no Git commit is available")
    return parser.parse_args()


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)


def is_secret_prone(relative: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in relative.parts)
    name = lowered_parts[-1]
    if any(part in SECRET_PRONE_DIRECTORIES for part in lowered_parts[:-1]):
        return True
    if name in SECRET_PRONE_BASENAMES or name.startswith(".env."):
        return True
    if any(name.endswith(suffix) for suffix in SECRET_PRONE_SUFFIXES):
        return True
    return (
        (name.startswith("client_secret_") or name.startswith("service_account_") or name.startswith("service-account-"))
        and name.endswith(".json")
    )


def reject_runtime_and_symlinks(tree: Path) -> None:
    for base, directory_names, file_names in os.walk(tree, followlinks=False):
        current = Path(base)
        for name in [*directory_names, *file_names]:
            path = current / name
            relative = path.relative_to(tree)
            if path.is_symlink():
                fail(f"tracked release must not contain symlinks: {relative}")
            if "marketplace" in relative.parts:
                fail(f"retired nested marketplace artifact is tracked: {relative}")
            if ".codex" in relative.parts or any(part in FORBIDDEN_PARTS for part in relative.parts):
                fail(f"runtime state is tracked: {relative}")
            if path.suffix in FORBIDDEN_SUFFIXES:
                fail(f"Python bytecode is tracked: {relative}")
            if is_secret_prone(relative):
                fail(f"secret-prone path is tracked: {relative}")


def reject_release_placeholders(tree: Path) -> None:
    for relative in REQUIRED_PUBLIC_FILES:
        path = tree / relative
        if not path.is_file():
            fail(f"required release file is missing: {relative}")
        content = path.read_text(encoding="utf-8")
        if "TODO(release)" in content or "TBD(release)" in content:
            fail(f"release placeholder remains in {relative}")
        if PRIVATE_HOME_PATH.search(content):
            fail(f"private local home path remains in public release file: {relative}")


def validate_tree(tree: Path) -> None:
    reject_runtime_and_symlinks(tree)
    reject_release_placeholders(tree)
    checked = run([sys.executable, str(VALIDATOR), "--root", str(tree)], ROOT)
    if checked.returncode != 0:
        fail(checked.stdout.strip() or checked.stderr.strip() or "marketplace validation failed")


def archive_head(root: Path, destination: Path) -> None:
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=root, capture_output=True, check=False)
    if archive.returncode != 0:
        fail(archive.stderr.decode(errors="replace").strip() or "git archive HEAD failed")
    archive_path = destination / "release.tar"
    archive_path.write_bytes(archive.stdout)
    with tarfile.open(archive_path) as bundle:
        members = bundle.getmembers()
        for member in members:
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or member.issym() or member.islnk():
                fail(f"unsafe archive member: {member.name}")
        bundle.extractall(destination / "tree", filter="data")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not (root / ".git").exists():
        fail(f"not a Git working tree: {root}")
    head = run(["git", "rev-parse", "--verify", "HEAD"], root)
    if head.returncode != 0:
        if args.require_tracked:
            fail("a committed HEAD is required for a tracked-release archive check")
        print("release validation: SKIP (no committed HEAD for tracked-release archive)")
        return 0
    with tempfile.TemporaryDirectory(prefix="cortex-release-") as directory:
        workspace = Path(directory)
        archive_head(root, workspace)
        validate_tree(workspace / "tree")
    print(f"release validation passed: tracked archive {head.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
