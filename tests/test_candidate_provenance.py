"""Black-box checks for isolated candidate identity and runtime provenance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
from cortex_release_candidate import (  # noqa: E402
    CandidateError,
    build_source_candidate,
    candidate_digest,
    plugin_tree_digest,
    _runtime_payload_files,
    source_candidate_manifest,
    validate_candidate_tree,
)
from cortex_runtime.provenance import verify_runtime  # noqa: E402
from cortex_runtime.semantic_registry import OPERATION_NAMES  # noqa: E402
from cortex_payload_manifest import (  # noqa: E402
    RuntimePayloadError,
    ensure_managed_directory,
    validated_managed_directory,
)
from cortex_candidate_receipt import (  # noqa: E402
    CandidateReceiptError,
    RECEIPT_NAME,
    read_verified_receipt,
    write_receipt,
)
from cortex_candidate_location import (  # noqa: E402
    CandidateLocationError,
    from_release_root,
    from_verified_installed_receipt,
)


def _installed_isolated_candidate(tmp_path: Path) -> tuple[Path, Path, Path, Path, str]:
    """Build one real stamped package in the exact isolated cache topology."""
    staged = tmp_path / "staged"
    build_source_candidate(ROOT, staged)
    version = json.loads((staged / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    owner = tmp_path / "owner"
    home = owner / ".cortex-dev"
    codex_home = home / ".codex"
    installed = codex_home / "plugins/cache/cortex/cortex" / version
    installed.parent.mkdir(parents=True)
    shutil.copytree(staged / "plugins/cortex", installed)
    return owner, home, codex_home, installed, version


def _installed_release(tmp_path: Path) -> tuple[Path, Path]:
    """Install the Marketplace payload under a fresh production profile."""
    version = json.loads(
        (ROOT / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    codex_home = tmp_path / "release-home" / ".codex"
    installed = codex_home / "plugins/cache/cortex/cortex" / version
    installed.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "plugins/cortex", installed)
    return codex_home, installed


def test_isolated_candidate_receipt_is_stamped_deterministic_and_owner_only(tmp_path: Path) -> None:
    owner, home, codex_home, installed, version = _installed_isolated_candidate(tmp_path)
    first = write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    receipt_path = codex_home / RECEIPT_NAME
    original = receipt_path.read_bytes()
    second = write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    assert first == second == read_verified_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home,
    )
    assert receipt_path.read_bytes() == original
    assert first["candidate_version"] == version
    assert first["candidate_path"] == str(installed)
    assert first["base_version"] == "1.13.0"
    assert receipt_path.stat().st_mode & 0o077 == 0
    receipt_path.chmod(0o644)
    with pytest.raises(CandidateReceiptError, match="group or other"):
        read_verified_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home)


def test_candidate_receipt_rejects_missing_tampered_cross_target_and_symlink_paths(tmp_path: Path) -> None:
    owner, home, codex_home, installed, version = _installed_isolated_candidate(tmp_path)
    write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    receipt_path = codex_home / RECEIPT_NAME
    receipt_path.unlink()
    with pytest.raises(CandidateReceiptError, match="receipt"):
        read_verified_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home)
    write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    receipt_path.write_bytes(receipt_path.read_bytes().replace(b'"base_version":"1.13.0"', b'"base_version":"9.9.9"'))
    with pytest.raises(CandidateReceiptError, match="receipt"):
        read_verified_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home)
    write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    other_owner = tmp_path / "other-owner"
    other_home = other_owner / ".cortex-dev"
    other_codex = other_home / ".codex"
    other_codex.mkdir(parents=True)
    shutil.copy2(receipt_path, other_codex / RECEIPT_NAME)
    with pytest.raises(CandidateReceiptError, match="different isolated target"):
        read_verified_receipt(source_root=ROOT, owner_home=other_owner, isolated_home=other_home, isolated_codex_home=other_codex)
    receipt_path.unlink()
    receipt_path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(CandidateReceiptError, match="regular file"):
        read_verified_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home)
    receipt_path.unlink()
    write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    cache_plugins = codex_home / "plugins"
    real_plugins = tmp_path / "real-plugins"
    cache_plugins.rename(real_plugins)
    cache_plugins.symlink_to(real_plugins, target_is_directory=True)
    with pytest.raises(CandidateReceiptError, match="symlink"):
        read_verified_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home)
    assert installed.exists()  # the target still exists; only lexical admission rejects it.


def test_candidate_receipt_write_failure_refuses_authorization_after_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, home, codex_home, _installed, version = _installed_isolated_candidate(tmp_path)
    monkeypatch.setenv("CORTEX_TEST_RECEIPT_WRITE_FAIL", "1")
    with pytest.raises(CandidateReceiptError, match="write"):
        write_receipt(
            source_root=ROOT, owner_home=owner, isolated_home=home,
            isolated_codex_home=codex_home, candidate_version=version,
        )
    assert not (codex_home / RECEIPT_NAME).exists()


def test_candidate_location_resolver_has_explicit_release_and_installed_topologies(tmp_path: Path) -> None:
    """Every qualification branch derives server paths from one typed target."""
    release = tmp_path / "release"
    build_source_candidate(ROOT, release)
    checkout = from_release_root(ROOT)
    staged = from_release_root(release)
    assert checkout.server_path == ROOT / "plugins/cortex/scripts/cortex.py"
    assert staged.plugin_root == release / "plugins/cortex"
    assert staged.server_path.is_file()
    assert staged.runtime_package.is_dir()
    with pytest.raises(CandidateLocationError, match="plugin root is missing|server"):
        from_release_root(staged.plugin_root)

    owner, home, codex_home, installed, version = _installed_isolated_candidate(tmp_path)
    receipt = write_receipt(
        source_root=ROOT, owner_home=owner, isolated_home=home,
        isolated_codex_home=codex_home, candidate_version=version,
    )
    selected = from_verified_installed_receipt(receipt, requested_root=installed)
    assert selected.kind == "installed"
    assert selected.plugin_root == installed
    assert selected.server_path == installed / "scripts/cortex.py"
    assert selected.server_path.is_file()
    with pytest.raises(CandidateLocationError, match="disagrees"):
        from_verified_installed_receipt(receipt, requested_root=release)
    with pytest.raises(CandidateLocationError, match="missing|disagrees"):
        from_verified_installed_receipt(receipt, requested_root=installed / "plugins/cortex")


def test_candidate_digest_is_deterministic_and_detects_tampering(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    first = candidate_digest(candidate, manifest)
    assert first == candidate_digest(candidate, manifest)
    target = candidate / manifest.files[0]
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    assert candidate_digest(candidate, manifest) != first


def test_runtime_payload_manifest_stages_filesystem_policy_and_is_importable(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    relative = Path("plugins/cortex/scripts/cortex_runtime/filesystem_policy.py")
    assert relative in manifest.files
    staged = candidate / relative
    assert staged.is_file()
    environment = {**__import__("os").environ, "PYTHONPATH": str(candidate / "plugins/cortex/scripts")}
    environment.pop("CORTEX_SOURCE_MODE", None)
    checked = subprocess.run(
        [sys.executable, "-B", "-c", "import cortex_runtime.filesystem_policy as policy; assert callable(policy.assert_runtime_mutation_conformance)"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_unlisted_runtime_module_fails_closed_at_manifest_boundary(tmp_path: Path) -> None:
    runtime_source = ROOT / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy = tmp_path / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy.parent.mkdir(parents=True)
    shutil.copytree(runtime_source, runtime_copy)
    shutil.copy2(ROOT / "plugins/cortex/scripts/cortex.py", tmp_path / "plugins/cortex/scripts/cortex.py")
    shutil.copy2(ROOT / "plugins/cortex/runtime-payload.json", tmp_path / "plugins/cortex/runtime-payload.json")
    nested = runtime_copy / "nested"
    nested.mkdir()
    (nested / "__init__.py").write_text("\n", encoding="utf-8")
    (nested / "unlisted_runtime_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    try:
        _runtime_payload_files(tmp_path)
    except CandidateError as exc:
        assert "not an exact production Python closure" in str(exc)
        assert "unlisted_runtime_module.py" in str(exc)
    else:
        raise AssertionError("an unlisted production runtime module must fail the candidate gate")


def test_nested_runtime_symlink_and_nonregular_entries_fail_closed(tmp_path: Path) -> None:
    runtime_source = ROOT / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy = tmp_path / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy.parent.mkdir(parents=True)
    shutil.copytree(runtime_source, runtime_copy)
    shutil.copy2(ROOT / "plugins/cortex/scripts/cortex.py", tmp_path / "plugins/cortex/scripts/cortex.py")
    shutil.copy2(ROOT / "plugins/cortex/runtime-payload.json", tmp_path / "plugins/cortex/runtime-payload.json")
    nested = runtime_copy / "nested"
    nested.mkdir()
    (nested / "__init__.py").write_text("\n", encoding="utf-8")
    (nested / "link.py").symlink_to(runtime_copy / "v12_contract.py")
    try:
        _runtime_payload_files(tmp_path)
    except CandidateError as exc:
        assert "unsafe file" in str(exc)
    else:
        raise AssertionError("a nested runtime symlink must fail the candidate gate")
    (nested / "link.py").unlink()
    os.mkfifo(nested / "device.py")
    try:
        _runtime_payload_files(tmp_path)
    except CandidateError as exc:
        assert "unsafe file" in str(exc)
    else:
        raise AssertionError("a nested non-regular runtime entry must fail the candidate gate")


def test_candidate_root_symlink_and_extra_empty_directory_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "candidate"
    link.symlink_to(target, target_is_directory=True)
    try:
        build_source_candidate(ROOT, link)
    except CandidateError as exc:
        assert "candidate root must be a regular directory" in str(exc)
    else:
        raise AssertionError("a symlinked candidate root must fail before staging")
    candidate = tmp_path / "candidate-real"
    manifest = build_source_candidate(ROOT, candidate)
    (candidate / "empty-directory").mkdir()
    try:
        validate_candidate_tree(candidate, manifest)
    except CandidateError as exc:
        assert "directory topology" in str(exc)
    else:
        raise AssertionError("an undeclared empty candidate directory must fail parity")


def test_managed_ancestor_chain_rejects_symlinked_cache_and_nested_ancestors(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    real_cache = home / "real-cache"
    real_cache.mkdir()
    linked_cache = home / ".cortex-candidates"
    linked_cache.symlink_to(real_cache, target_is_directory=True)
    with pytest.raises(RuntimePayloadError, match="symlink"):
        validated_managed_directory(linked_cache / "1.13.0+codex.sha256.abc", "candidate version root", allow_missing=True)
    linked_cache.unlink()
    linked_cache.mkdir()
    real_version_parent = linked_cache / "versions-real"
    real_version_parent.mkdir()
    nested_link = linked_cache / "versions"
    nested_link.symlink_to(real_version_parent, target_is_directory=True)
    with pytest.raises(RuntimePayloadError, match="symlink"):
        validated_managed_directory(nested_link / "candidate", "candidate version root", allow_missing=True)


def test_missing_managed_ancestors_are_created_only_after_safe_validation(tmp_path: Path) -> None:
    target = tmp_path / "codex-home" / ".cortex-candidates" / "1.13.0+codex.sha256.abc"
    created = ensure_managed_directory(target, "candidate version root")
    assert created == target.absolute()
    assert target.is_dir()
    assert validated_managed_directory(target, "candidate version root") == created


def test_marketplace_candidate_parity_rejects_extra_empty_plugin_directory(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    extra = candidate / "plugins/cortex/skills/orchestrator/empty-undeclared"
    extra.mkdir()
    validator = candidate / "scripts/validate-cortex-marketplace.py"
    checked = subprocess.run(
        [sys.executable, "-B", str(validator), "--root", str(candidate), "--candidate"],
        cwd=candidate,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode != 0
    assert "directory topology" in (checked.stdout + checked.stderr)
    # Keep the build manifest exercised in the same test as the marketplace
    # parity failure, proving both paths use the same candidate payload.
    assert manifest.plugin_digest(candidate)


def test_missing_runtime_directory_or_file_and_marketplace_parity_fail_closed(tmp_path: Path) -> None:
    runtime_source = ROOT / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy = tmp_path / "plugins/cortex/scripts/cortex_runtime"
    runtime_copy.parent.mkdir(parents=True)
    shutil.copytree(runtime_source, runtime_copy)
    shutil.copy2(ROOT / "plugins/cortex/scripts/cortex.py", tmp_path / "plugins/cortex/scripts/cortex.py")
    shutil.copy2(ROOT / "plugins/cortex/runtime-payload.json", tmp_path / "plugins/cortex/runtime-payload.json")
    (runtime_copy / "v12_store.py").unlink()
    try:
        _runtime_payload_files(tmp_path)
    except CandidateError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("a missing declared runtime file must fail the candidate gate")
    shutil.rmtree(runtime_copy)
    try:
        _runtime_payload_files(tmp_path)
    except CandidateError as exc:
        assert "runtime package is missing" in str(exc) or "missing" in str(exc)
    else:
        raise AssertionError("a missing runtime directory must fail the candidate gate")


def test_same_source_builds_have_the_same_content_addressed_build_id(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = build_source_candidate(ROOT, first)
    second_manifest = build_source_candidate(ROOT, second)
    assert first_manifest.plugin_digest(first) == second_manifest.plugin_digest(second)
    assert candidate_digest(first, first_manifest) == candidate_digest(second, second_manifest)


def test_installed_plugin_digest_matches_source_payload(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    source = candidate_digest(candidate, manifest)
    installed = candidate / "plugins/cortex"
    assert plugin_tree_digest(installed, manifest) == manifest.plugin_digest(candidate)
    (installed / "unexpected-runtime-file").write_text("tampered", encoding="utf-8")
    try:
        plugin_tree_digest(installed, manifest)
    except CandidateError as exc:
        assert "differs from source manifest" in str(exc)
    else:
        raise AssertionError("an extra installed file must invalidate candidate parity")


def test_installed_plugin_root_symlink_is_rejected(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    installed = candidate / "plugins/cortex"
    moved = tmp_path / "real-plugin"
    installed.rename(moved)
    installed.symlink_to(moved, target_is_directory=True)
    try:
        plugin_tree_digest(installed, manifest)
    except CandidateError as exc:
        assert "root must be a regular directory" in str(exc)
    else:
        raise AssertionError("a symlinked installed candidate root must be rejected")


def test_plugin_only_manifest_accepts_no_repository_marketplace_but_rejects_payload_drift(tmp_path: Path) -> None:
    """Installed caches are plugin-only; delivery metadata stays source-only."""
    candidate = tmp_path / "candidate"
    full = build_source_candidate(ROOT, candidate)
    plugin_manifest = full.installable_plugin_manifest()
    installed = tmp_path / "installed-plugin"
    shutil.copytree(candidate / "plugins/cortex", installed)
    assert not (installed / ".agents/plugins/marketplace.json").exists()
    source_digest = full.plugin_digest(candidate)
    assert plugin_tree_digest(installed, plugin_manifest) == source_digest

    missing = installed / "profiles.json"
    original = missing.read_bytes()
    missing.unlink()
    with pytest.raises(CandidateError, match="missing|differs from source manifest"):
        plugin_tree_digest(installed, plugin_manifest)
    missing.write_bytes(original)

    extra = installed / "unexpected-installable-file"
    extra.write_text("not declared", encoding="utf-8")
    with pytest.raises(CandidateError, match="differs from source manifest"):
        plugin_tree_digest(installed, plugin_manifest)
    extra.unlink()

    target = installed / "profiles.json"
    moved = installed / "profiles-real.json"
    target.rename(moved)
    target.symlink_to(moved)
    with pytest.raises(CandidateError, match="regular file|unsafe path"):
        plugin_tree_digest(installed, plugin_manifest)


def test_exact_candidate_stdio_reports_identity_and_catalog(tmp_path: Path) -> None:
    owner, home, codex_home, installed, version = _installed_isolated_candidate(tmp_path)
    os.chmod(owner, 0o700)
    os.chmod(home, 0o700)
    os.chmod(codex_home, 0o700)
    receipt = write_receipt(source_root=ROOT, owner_home=owner, isolated_home=home, isolated_codex_home=codex_home, candidate_version=version)
    digest = receipt["candidate_digest"]
    server = installed / "scripts/cortex.py"
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "candidate-black-box", "version": "1"},
        },
    }
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/cortex_observation_generation.py"), "--receipt", str(codex_home / RECEIPT_NAME)], capture_output=True, text=True, check=True)
    generation = json.loads(result.stdout)["generation_id"]
    journal_path = codex_home / ".cortex-mcp-observations" / "generations" / generation / "events.jsonl"
    # Mirror the isolated launcher: the MCP child receives the nonce that
    # authenticated the exact pending generation, rather than an unrelated
    # or nonce-less process environment.
    lease = json.loads((codex_home / ".cortex-mcp-observations" / "lease.json").read_text(encoding="ascii"))
    environment = {**__import__("os").environ, "CORTEX_BUILD_ID": "sha256:" + digest,
                    "CORTEX_SOURCE_DIGEST": digest, "CORTEX_CANDIDATE_PATH": str(installed),
                    "HOME": str(home), "CODEX_HOME": str(codex_home),
                    "CORTEX_SESSION_NONCE": lease["nonce"],
                    "PYTHONDONTWRITEBYTECODE": "1"}
    # The candidate process must be self-contained.  A developer checkout
    # leaked through PYTHONPATH would make this test capable of passing while
    # executing source modules instead of the staged candidate.
    environment.pop("PYTHONPATH", None)
    environment.pop("CORTEX_SOURCE_MODE", None)
    process = subprocess.Popen(
        [sys.executable, "-B", str(server)], cwd=tmp_path,
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
    process.stdin.write(json.dumps(listing) + "\n")
    process.stdin.close()
    lines = [json.loads(line) for line in process.stdout if line.strip()]
    assert process.wait(timeout=10) == 0, process.stderr.read() if process.stderr else ""
    initialized = next(item for item in lines if item.get("id") == 1)
    info = initialized["result"]["serverInfo"]
    assert info["buildId"] == "sha256:" + digest
    assert info["sourceDigest"] == digest
    assert info["parityVerified"] is True
    tools = next(item for item in lines if item.get("id") == 2)["result"]["tools"]
    assert len(tools) == len(OPERATION_NAMES)
    observations = [json.loads(line) for line in journal_path.read_text(encoding="ascii").splitlines()]
    ready = [item for item in observations if item["operation"] == "server_ready"]
    assert len(ready) == 1
    assert ready[0]["build_id"] == "sha256:" + digest
    assert ready[0]["catalogue_count"] == len(OPERATION_NAMES)
    assert len(ready[0]["catalogue_digest"]) == 64
    assert all(name not in journal_path.read_text(encoding="ascii") for name in OPERATION_NAMES)


def test_content_addressed_installed_release_stdio_initializes_without_source_mode(tmp_path: Path) -> None:
    """The public content-addressed package must start without a dev override."""
    codex_home, installed = _installed_release(tmp_path)
    home = codex_home.parent
    os.chmod(home, 0o700)
    os.chmod(codex_home, 0o700)
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "release-black-box", "version": "1"},
        },
    }
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    environment = {
        **os.environ, "HOME": str(home), "CODEX_HOME": str(codex_home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    environment.pop("PYTHONPATH", None)
    environment.pop("CORTEX_SOURCE_MODE", None)
    process = subprocess.Popen(
        [sys.executable, "-B", str(installed / "scripts/cortex.py")],
        cwd=tmp_path, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
    process.stdin.write(json.dumps(listing) + "\n")
    process.stdin.close()
    lines = [json.loads(line) for line in process.stdout if line.strip()]
    assert process.wait(timeout=10) == 0, process.stderr.read() if process.stderr else ""
    info = next(item for item in lines if item.get("id") == 1)["result"]["serverInfo"]
    assert info["version"] == "1.13.0"
    assert info["runtimeMode"] == "content_addressed"
    assert info["parityVerified"] is True
    assert info["buildId"] == "sha256:" + info["sourceDigest"]
    tools = next(item for item in lines if item.get("id") == 2)["result"]["tools"]
    assert len(tools) == len(OPERATION_NAMES)


def test_runtime_rejects_spoofed_expectation_and_manifest_suffix(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    manifest = build_source_candidate(ROOT, candidate)
    package = candidate / "plugins/cortex"
    verify_runtime(package, "1.13.0")
    try:
        verify_runtime(package, "1.13.0", {"CORTEX_BUILD_ID": "sha256:" + "0" * 64})
    except RuntimeError as exc:
        assert "disagrees" in str(exc)
    else:
        raise AssertionError("spoofed launcher identity must be rejected")
    plugin_manifest = package / ".codex-plugin/plugin.json"
    value = json.loads(plugin_manifest.read_text(encoding="utf-8"))
    value["version"] = "1.13.0+codex.sha256." + "0" * 16
    plugin_manifest.write_text(json.dumps(value), encoding="utf-8")
    try:
        verify_runtime(package, "1.13.0")
    except RuntimeError as exc:
        assert "suffix" in str(exc)
    else:
        raise AssertionError("wrong build suffix must be rejected")
    source = verify_runtime(package, "1.13.0", allow_source_mode=True)
    assert source["runtime_mode"] == "source"
    assert source["parity_verified"] == "false"


def test_candidate_manifest_is_order_independent_after_in_process_imports(tmp_path: Path) -> None:
    """Collection/import order must not create payload bytecode residue."""
    assert sys.dont_write_bytecode is True
    staged = tmp_path / "candidate"
    build_source_candidate(ROOT, staged)
    assert not any((ROOT / "plugins" / "cortex").rglob("*.pyc"))
    assert not any(path.name == "__pycache__" for path in (ROOT / "plugins" / "cortex").rglob("*"))
