#!/usr/bin/env python3
"""Authoritative isolated candidate-delivery receipt.

The stamped plugin cache directory is selected by Codex during installation.
Neither a caller nor the live-dev launcher is allowed to rediscover it from a
base release version.  The supported isolated sync flow writes this receipt
only after the exact installed package has passed source/candidate parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


RECEIPT_NAME = ".cortex-candidate-receipt.json"
SCHEMA_VERSION = 1
BASE_VERSION = "1.14.16"
_STAMPED_VERSION = re.compile(r"^1\.14\.16\+codex\.sha256\.([0-9a-f]{16})$")
_RECEIPT_FIELDS = frozenset({
    "schema_version", "isolated_home", "isolated_codex_home",
    "candidate_version", "candidate_path", "source_digest",
    "candidate_digest", "build_id", "base_version", "parity_verified", "receipt_sha256",
    "skill_records",
})


class CandidateReceiptError(RuntimeError):
    """The isolated candidate receipt cannot safely authorize a launch."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _lexical(path: str | Path, label: str, *, allow_missing: bool = False) -> Path:
    # Import from the source checkout rather than duplicate the lstat ancestry
    # policy.  The helper itself is a support script, not runtime payload.
    from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory

    try:
        raw = Path(path)
        if not raw.is_absolute() or any(part in {".", ".."} for part in raw.parts):
            raise CandidateReceiptError(f"{label} must be an absolute lexical path")
        return validated_managed_directory(raw, label, allow_missing=allow_missing)
    except RuntimePayloadError as exc:
        raise CandidateReceiptError(str(exc)) from None


def _regular_owner_only(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CandidateReceiptError(f"{label} is missing or unreadable: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CandidateReceiptError(f"{label} must be an owner-only regular file")
    if info.st_uid != os.geteuid():
        raise CandidateReceiptError(f"{label} must be owned by the current isolated-profile user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise CandidateReceiptError(f"{label} must not be readable or writable by group or other users")


def _read_receipt_json(path: Path) -> object:
    """Read one owner-private regular receipt through an O_NOFOLLOW fd."""
    _regular_owner_only(path, "candidate receipt")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except OSError as exc:
        raise CandidateReceiptError(f"candidate receipt is missing or unreadable: {exc}") from None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateReceiptError(f"candidate receipt is invalid: {exc}") from None


def _isolation(
    *, owner_home: str | Path, isolated_home: str | Path, isolated_codex_home: str | Path,
) -> tuple[Path, Path, Path]:
    owner = _lexical(owner_home, "isolated owner HOME")
    home = _lexical(isolated_home, "isolated HOME")
    codex_home = _lexical(isolated_codex_home, "isolated CODEX_HOME")
    expected_home = owner / ".cortex-dev"
    if home != expected_home or codex_home != expected_home / ".codex":
        raise CandidateReceiptError("receipt is allowed only for the exact isolated HOME/.cortex-dev/.codex target")
    return owner, home, codex_home


def _candidate_path(codex_home: Path, version: str) -> Path:
    if not _STAMPED_VERSION.fullmatch(version):
        raise CandidateReceiptError("candidate version is not the required content-addressed 1.14.16 stamp")
    # This is a validation relation, not candidate discovery.  The only
    # selected path is the byte-for-byte path persisted in the receipt.
    expected = codex_home / "plugins" / "cache" / "cortex" / "cortex" / version
    return _lexical(expected, "installed candidate version root")


def _source_manifest(source_root: Path):
    sys.path.insert(0, str(source_root / "scripts"))
    from cortex_release_candidate import source_candidate_manifest

    return source_candidate_manifest(source_root)


def _candidate_identity(source_root: Path, candidate: Path, version: str) -> tuple[str, str]:
    manifest = _source_manifest(source_root)
    source_digest = manifest.plugin_digest(source_root)
    sys.path.insert(0, str(source_root / "scripts"))
    from cortex_release_candidate import CandidateError, plugin_tree_digest

    try:
        candidate_digest = plugin_tree_digest(candidate, manifest)
    except CandidateError as exc:
        raise CandidateReceiptError(f"candidate parity validation failed: {exc}") from None
    if candidate_digest != source_digest:
        raise CandidateReceiptError("candidate/source digest mismatch")
    manifest_path = candidate / ".codex-plugin" / "plugin.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateReceiptError(f"installed candidate manifest is invalid: {exc}") from None
    if not isinstance(payload, dict) or payload.get("version") != version:
        raise CandidateReceiptError("installed candidate manifest version disagrees with receipt")
    return source_digest, candidate_digest


def _receipt_path(codex_home: Path) -> Path:
    return codex_home / RECEIPT_NAME


def _validate_receipt_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
        raise CandidateReceiptError("candidate receipt has an unsupported schema")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("base_version") != BASE_VERSION:
        raise CandidateReceiptError("candidate receipt schema or base version is invalid")
    for field in _RECEIPT_FIELDS - {"schema_version", "parity_verified", "skill_records"}:
        if not isinstance(value.get(field), str) or not value[field]:
            raise CandidateReceiptError(f"candidate receipt field is invalid: {field}")
    if value.get("parity_verified") is not True:
        raise CandidateReceiptError("candidate receipt does not attest verified parity")
    records = value.get("skill_records")
    if not isinstance(records, list) or len(records) > 1024:
        raise CandidateReceiptError("candidate receipt skill manifest is invalid")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise CandidateReceiptError("candidate receipt skill manifest entry is invalid")
        path, digest = record["path"], record["sha256"]
        if (not isinstance(path, str) or len(path) > 256 or not path.startswith("skills/")
                or ".." in Path(path).parts or path in seen
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise CandidateReceiptError("candidate receipt skill manifest entry is invalid")
        seen.add(path)
    supplied = value["receipt_sha256"]
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if supplied != _digest(unsigned):
        raise CandidateReceiptError("candidate receipt digest mismatch")
    return dict(value)


def write_receipt(
    *, source_root: str | Path, owner_home: str | Path, isolated_home: str | Path,
    isolated_codex_home: str | Path, candidate_version: str,
) -> dict[str, Any]:
    """Create/replace the only launcher-authoritative isolated receipt."""
    root = _lexical(source_root, "repository root")
    _, home, codex_home = _isolation(
        owner_home=owner_home, isolated_home=isolated_home, isolated_codex_home=isolated_codex_home,
    )
    candidate = _candidate_path(codex_home, candidate_version)
    source_digest, candidate_digest = _candidate_identity(root, candidate, candidate_version)
    skills = candidate / "skills"
    skill_records = []
    for path in sorted(skills.rglob("*")):
        if path.is_symlink():
            raise CandidateReceiptError("candidate skill payload contains an unsafe entry")
        if not path.is_file():
            continue
        skill_records.append({"path": path.relative_to(candidate).as_posix(),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "isolated_home": str(home),
        "isolated_codex_home": str(codex_home),
        "candidate_version": candidate_version,
        "candidate_path": str(candidate),
        "source_digest": source_digest,
        "candidate_digest": candidate_digest,
        "build_id": "sha256:" + source_digest,
        "base_version": BASE_VERSION,
        "parity_verified": True,
        "skill_records": skill_records,
    }
    value["receipt_sha256"] = _digest(value)
    path = _receipt_path(codex_home)
    if path.exists() or path.is_symlink():
        _regular_owner_only(path, "existing candidate receipt")
    if os.environ.get("CORTEX_TEST_RECEIPT_WRITE_FAIL") == "1":
        raise CandidateReceiptError("candidate receipt write was intentionally refused for regression coverage")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{RECEIPT_NAME}.", dir=str(codex_home))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(codex_home, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    _regular_owner_only(path, "candidate receipt")
    return value


def read_runtime_verified_receipt(
    *, source_root: str | Path, owner_home: str | Path, isolated_home: str | Path,
    isolated_codex_home: str | Path,
) -> dict[str, Any]:
    """Verify the immutable installed candidate without reading its source checkout.

    This is the only post-launch receipt verifier.  It deliberately validates
    the stamped receipt claims and the installed payload tree, not whether a
    developer's mutable checkout still matches the candidate issued earlier.
    """
    root = _lexical(source_root, "repository root")
    _, home, codex_home = _isolation(
        owner_home=owner_home, isolated_home=isolated_home, isolated_codex_home=isolated_codex_home,
    )
    path = _receipt_path(codex_home)
    _regular_owner_only(path, "candidate receipt")
    try:
        value = _validate_receipt_payload(_read_receipt_json(path))
    except CandidateReceiptError:
        raise
    if value["isolated_home"] != str(home) or value["isolated_codex_home"] != str(codex_home):
        raise CandidateReceiptError("candidate receipt belongs to a different isolated target")
    candidate = _candidate_path(codex_home, value["candidate_version"])
    if value["candidate_path"] != str(candidate):
        raise CandidateReceiptError("candidate receipt path is outside the exact managed stamped cache location")
    # Reuse the installed runtime's content algorithm; it validates recursive
    # payload topology and rejects symlinks/non-regular entries.  `root` is
    # retained only as the trusted support-code location, never scanned.
    import sys
    runtime_scripts = str(root / "plugins" / "cortex" / "scripts")
    if runtime_scripts not in sys.path:
        sys.path.insert(0, runtime_scripts)
    from cortex_runtime.provenance import package_digest
    installed_digest = package_digest(candidate)
    if value["candidate_digest"] != installed_digest or value["source_digest"] != installed_digest:
        raise CandidateReceiptError("candidate receipt digest does not match installed payload")
    if value["build_id"] != "sha256:" + installed_digest:
        raise CandidateReceiptError("candidate receipt build ID does not match installed payload")
    return value


def read_verified_receipt(
    *, source_root: str | Path, owner_home: str | Path, isolated_home: str | Path,
    isolated_codex_home: str | Path,
) -> dict[str, Any]:
    """Read the exact receipt and verify every launch-relevant relation."""
    root = _lexical(source_root, "repository root")
    _, home, codex_home = _isolation(
        owner_home=owner_home, isolated_home=isolated_home, isolated_codex_home=isolated_codex_home,
    )
    path = _receipt_path(codex_home)
    _regular_owner_only(path, "candidate receipt")
    try:
        value = _validate_receipt_payload(_read_receipt_json(path))
    except CandidateReceiptError:
        raise
    if value["isolated_home"] != str(home) or value["isolated_codex_home"] != str(codex_home):
        raise CandidateReceiptError("candidate receipt belongs to a different isolated target")
    candidate_path = Path(value["candidate_path"])
    if not candidate_path.is_absolute() or any(part in {".", ".."} for part in candidate_path.parts):
        raise CandidateReceiptError("candidate receipt path is not lexical")
    expected = _candidate_path(codex_home, value["candidate_version"])
    if str(candidate_path) != str(expected):
        raise CandidateReceiptError("candidate receipt path is outside the exact managed stamped cache location")
    source_digest, candidate_digest = _candidate_identity(root, candidate_path, value["candidate_version"])
    if value["source_digest"] != source_digest or value["candidate_digest"] != candidate_digest:
        raise CandidateReceiptError("candidate receipt digest does not match current source or installed candidate")
    if value["build_id"] != "sha256:" + source_digest:
        raise CandidateReceiptError("candidate receipt build ID does not match source digest")
    return value


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("write", "verify"):
        item = sub.add_parser(command)
        item.add_argument("--source-root", required=True)
        item.add_argument("--owner-home", required=True)
        item.add_argument("--isolated-home", required=True)
        item.add_argument("--isolated-codex-home", required=True)
        if command == "write":
            item.add_argument("--candidate-version", required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        common = {
            "source_root": args.source_root,
            "owner_home": args.owner_home,
            "isolated_home": args.isolated_home,
            "isolated_codex_home": args.isolated_codex_home,
        }
        if args.command == "write":
            value = write_receipt(**common, candidate_version=args.candidate_version)
        else:
            value = read_verified_receipt(**common)
    except CandidateReceiptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
