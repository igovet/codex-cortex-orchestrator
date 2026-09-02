#!/usr/bin/env python3
"""Build and validate an explicit Cortex release candidate."""
from __future__ import annotations

import argparse
import os
import subprocess
import tarfile
import tempfile
import sys
from pathlib import Path, PurePosixPath


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from cortex_release_candidate import (
    CandidateError,
    build_source_candidate,
    required_head_drift,
    source_candidate_manifest,
    validate_candidate_tree,
)


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"release validation failed: {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository source tree")
    parser.add_argument(
        "--mode", choices=("source", "head"), default="source",
        help="source validates the exact working-tree candidate; head additionally requires every candidate file to match committed HEAD",
    )
    return parser.parse_args()


def archive_head(root: Path, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=root, capture_output=True, check=False,
    )
    if archive.returncode != 0:
        fail(archive.stderr.decode(errors="replace").strip() or "git archive HEAD failed")
    archive_path = destination / "release.tar"
    archive_path.write_bytes(archive.stdout)
    extracted = destination / "head"
    with tarfile.open(archive_path) as bundle:
        for member in bundle.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or member.issym() or member.islnk():
                fail(f"unsafe archive member: {member.name}")
        bundle.extractall(extracted, filter="data")


def validate_source(root: Path, workspace: Path) -> tuple[int, str]:
    candidate = workspace / "candidate"
    manifest = build_source_candidate(root, candidate)
    validate_candidate_tree(candidate, manifest)
    return len(manifest.files), "working-tree source candidate"


def validate_head(root: Path, workspace: Path) -> tuple[int, str]:
    if not (root / ".git").exists():
        fail(f"not a Git working tree: {root}")
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    if head.returncode != 0:
        fail("a committed HEAD is required for head release verification")
    source_manifest = source_candidate_manifest(root)
    drift = required_head_drift(root, source_manifest)
    if drift:
        preview = ", ".join(drift[:12])
        suffix = " ..." if len(drift) > 12 else ""
        fail(
            "required installable source differs from HEAD; verify --mode source for the "
            f"current candidate or commit the release exactly: {preview}{suffix}"
        )
    archive_head(root, workspace)
    head_root = workspace / "head"
    head_manifest = source_candidate_manifest(head_root)
    if head_manifest.files != source_manifest.files:
        fail("HEAD release manifest differs from the current required installable manifest")
    candidate = workspace / "candidate"
    build_source_candidate(head_root, candidate)
    validate_candidate_tree(candidate, head_manifest)
    return len(head_manifest.files), f"committed HEAD {head.stdout.strip()}"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    try:
        # macOS exposes its system temporary directory through the stable
        # ``/var -> /private/var`` alias. Candidate topology validation is
        # intentionally lexical and rejects symlink traversal, so create the
        # workspace below the physical temp root rather than below that alias.
        # Every candidate component created after this normalization is still
        # checked with lstat and remains fail-closed for symlinks.
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(
            prefix="cortex-release-", dir=temporary_root,
        ) as directory:
            workspace = Path(directory)
            count, label = (
                validate_source(root, workspace)
                if args.mode == "source"
                else validate_head(root, workspace)
            )
    except CandidateError as exc:
        fail(str(exc))
    print(f"release validation passed: {label}; files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
