"""Hash and validate the frozen Phase 2 fixture manifest."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TASK_IDS = [f"F-0{i}" for i in range(1, 7)]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def tree_sha256(path: Path) -> str:
    rows = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = item.relative_to(path).as_posix()
        rows.append(rel.encode() + b"\0" + sha256_file(item).encode() + b"\n")
    return sha256_bytes(b"".join(rows))


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build_manifest() -> dict:
    rows = []
    for task_id in TASK_IDS:
        prompt = ROOT / "prompts" / f"{task_id}.txt"
        tree = ROOT / "trees" / task_id
        rows.append({
            "task_id": task_id,
            "family": {
                "F-01": "factual-investigation",
                "F-02": "bug-fix-regression",
                "F-03": "multi-file-feature",
                "F-04": "test-authoring",
                "F-05": "documentation-api-correction",
                "F-06": "security-sensitive-change",
            }[task_id],
            "prompt_path": f"prompts/{task_id}.txt",
            "fixture_tree_path": f"trees/{task_id}",
            "oracle_path": "oracle.py",
            "reset_path": "reset.py",
            "prompt_sha256": sha256_file(prompt),
            "fixture_tree_sha256": tree_sha256(tree),
            "oracle_sha256": sha256_file(ROOT / "oracle.py"),
            "reset_sha256": sha256_file(ROOT / "reset.py"),
            "protected_paths": ["USER-NOTE.txt"],
            "oracle_command": "python3 oracle.py TASK_ID WORKTREE",
            "reset_command": "python3 reset.py --task TASK_ID --destination WORKTREE",
            "reset_policy": "Destination must be absent; reset copies the tree byte-for-byte and never overwrites.",
        })
    return {
        "suite_version": "phase2-cli-v1",
        "manifest_version": "phase2-cli-v1-manifest",
        "hash_algorithm": "sha256",
        "tree_hash_definition": "sha256 of sorted relative-path NUL file-sha256 newline records",
        "families": rows,
        "ledger_schema_path": "ledger_schema.json",
        "ledger_schema_sha256": sha256_file(ROOT / "ledger_schema.json"),
        "condition_independent_identity": ["suite_version", "task_id", "prompt_sha256", "fixture_tree_sha256", "oracle_sha256", "reset_sha256"],
        "protected_paths": "USER-NOTE.txt must remain byte-for-byte unchanged in every task.",
        "mapping_policy": "No arm, condition, randomization label, or baseline/candidate mapping is stored here.",
    }


def validate_manifest(manifest: dict) -> list[str]:
    """Return errors unless *manifest* is the canonical frozen bundle.

    Hashing paths named by the manifest is insufficient: a caller could point a
    family at another family's prompt and recalculate that row's hash. The
    generated manifest is the source of truth for task IDs, family meaning,
    paths, policies, and the hashes of those canonical artifacts.
    """
    if not isinstance(manifest, dict):
        return ["manifest_type"]
    errors = []
    if any(key in manifest for key in ("arm", "condition", "baseline", "candidate", "randomization")):
        errors.append("forbidden_mapping_field")
    try:
        expected = build_manifest()
    except (OSError, TypeError, ValueError) as exc:
        return [f"canonical_bundle:{type(exc).__name__}"]

    if manifest.get("suite_version") != expected["suite_version"]:
        errors.append("suite_version")
    families = manifest.get("families")
    if not isinstance(families, list) or len(families) != len(expected["families"]):
        errors.append("family_count")
        families = families if isinstance(families, list) else []
    for index, expected_row in enumerate(expected["families"]):
        actual_row = families[index] if index < len(families) else None
        if actual_row != expected_row:
            errors.append(f"{expected_row['task_id']}:canonical_identity")

    for key in ("manifest_version", "hash_algorithm", "tree_hash_definition",
                "ledger_schema_path", "ledger_schema_sha256",
                "condition_independent_identity", "protected_paths", "mapping_policy"):
        if manifest.get(key) != expected.get(key):
            errors.append(key)
    return errors


if __name__ == "__main__":
    path = ROOT / "manifest.json"
    manifest = json.loads(path.read_text())
    errors = validate_manifest(manifest)
    print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    raise SystemExit(0 if not errors else 1)
