#!/usr/bin/env python3
"""Build and validate the explicit Cortex source-release candidate."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

PUBLIC_RELEASE_FILES = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "PRIVACY.md",
    "docs/release-readiness.md",
    "docs/project/index.md",
    "docs/project/conventions.md",
    "docs/project/decisions.md",
    "docs/project/gotchas.md",
    "docs/project/storage-classification.md",
    "docs/project/verification.md",
    "docs/features/index.md",
    "docs/features/knowledge-route-contract/index.md",
    "docs/features/lifecycle-telemetry/index.md",
    "docs/features/orchestration-ledger/index.md",
    "docs/features/plugin-packaging/index.md",
    "docs/features/coordinator-communication/index.md",
    ".agents/plugins/marketplace.json",
)
SUPPORT_SCRIPTS = (
    "scripts/cortex-host-preflight.py",
    "scripts/cortex-prompt-lint.py",
    "scripts/cortex_release_candidate.py",
    "scripts/render_cortex_tool_catalog.py",
    "scripts/sync-cortex.sh",
    "scripts/validate-cortex-marketplace.py",
    "scripts/verify-cortex-release.py",
)
PLUGIN_STATIC_FILES = (
    "plugins/cortex/.codex-plugin/plugin.json",
    "plugins/cortex/.mcp.json",
    "plugins/cortex/profiles.json",
    "plugins/cortex/scripts/cortex.py",
)
PLUGIN_SKILLS = (
    "adaptive-pipeline",
    "content-safety",
    "context-compaction",
    "coordinator-communication",
    "cortex-control",
    "documentation-sync",
    "find-skills",
    "knowledge-harvest",
    "orchestrator",
    "output-validation",
    "progress-accounting",
)
ACTIVE_PLUGIN_PYTHON = (
    "plugins/cortex/scripts/cortex.py",
    "plugins/cortex/scripts/cortex_runtime/__init__.py",
    "plugins/cortex/scripts/cortex_runtime/canonical_json.py",
    "plugins/cortex/scripts/cortex_runtime/delegation.py",
    "plugins/cortex/scripts/cortex_runtime/mcp_api.py",
    "plugins/cortex/scripts/cortex_runtime/model_routing.py",
    "plugins/cortex/scripts/cortex_runtime/public_contracts.py",
    "plugins/cortex/scripts/cortex_runtime/routing.py",
    "plugins/cortex/scripts/cortex_runtime/v12_contract.py",
    "plugins/cortex/scripts/cortex_runtime/v12_maintenance.py",
    "plugins/cortex/scripts/cortex_runtime/v12_projections.py",
    "plugins/cortex/scripts/cortex_runtime/v12_service.py",
    "plugins/cortex/scripts/cortex_runtime/v12_store.py",
    "plugins/cortex/scripts/cortex_runtime/worker_message.py",
)
PLUGIN_ROOT = Path("plugins/cortex")
FORBIDDEN_PARTS = frozenset({"__pycache__", ".codex"})
FORBIDDEN_SUFFIXES = frozenset({".pyc", ".pyo"})
RETIRED_PLUGIN_PATHS = frozenset({
    Path("plugins/cortex/hooks"),
    Path("plugins/cortex/scripts/cortex_hook.py"),
    Path("plugins/cortex/scripts/cortex-launcher"),
    Path("plugins/cortex/scripts/cortex_runtime/core"),
    Path("plugins/cortex/scripts/cortex_runtime/record_report"),
})
SECRET_PRONE_DIRECTORIES = frozenset({".aws", ".docker", ".gnupg", ".kube", ".ssh"})
SECRET_PRONE_BASENAMES = frozenset({
    ".env", ".git-credentials", ".netrc", ".npmrc", ".pypirc", ".terraformrc",
    "_netrc", "application_default_credentials.json", "client_secret.json",
    "client-secrets.json", "credentials.json", "credentials.tfrc.json", "id_dsa",
    "id_ecdsa", "id_ed25519", "id_rsa", "secrets.json", "service-account.json",
    "service_account.json",
})
SECRET_PRONE_SUFFIXES = frozenset({".jks", ".kdbx", ".key", ".keystore", ".p12", ".pem", ".pfx"})
PRIVATE_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s`]+/")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)|(?:href|src)=[\"']([^\"']+)[\"']")
PUBLIC_DOCUMENT_SUFFIXES = frozenset({".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})
DOCUMENTED_COMMAND_FILES = (
    Path("README.md"),
    Path("docs/release-readiness.md"),
    Path("docs/project/verification.md"),
)
SCRIPT_PATH_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<explicit>\./)?(?P<path>(?:plugins/cortex/)?scripts/[A-Za-z0-9._/-]+)"
)
PYTHON_INTERPRETER = re.compile(r"(?<![A-Za-z0-9_./-])python(?:3(?:\.\d+)?)?\b")
PYTHON_NO_BYTECODE = "PYTHONDONTWRITEBYTECODE=1"
PYTHON_BYTECODE_FLAG = re.compile(r"(?<![A-Za-z0-9_./-])python(?:3(?:\.\d+)?)?\b\s+-[A-Za-z]*B[A-Za-z]*\b")
PYTEST_MODULE = re.compile(r"(?<![A-Za-z0-9_./-])python(?:3(?:\.\d+)?)?\b.*\s-m\s+pytest\b")
PYTHON_BYTECODE_COMPILER = re.compile(
    r"(?<![A-Za-z0-9_./-])python(?:3(?:\.\d+)?)?\b.*\s-m\s+(?:py_compile|compileall)\b"
)


class CandidateError(RuntimeError):
    """The explicit source-release candidate is incomplete or unsafe."""


@dataclass(frozen=True)
class CandidateManifest:
    """Exact repository-relative regular files copied into one candidate."""

    files: tuple[Path, ...]


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateError(f"{label} must be an object")
    return value


def _safe_relative(raw: str, label: str) -> Path:
    normalized = raw.removeprefix("./")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise CandidateError(f"{label} is not a safe repository-relative path: {raw}")
    return Path(*candidate.parts)


def _require_regular(root: Path, relative: Path) -> Path:
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise CandidateError(f"required candidate file is missing: {relative}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise CandidateError(f"candidate source must be a regular file: {relative}")
    return path


def _plugin_payload_files(root: Path) -> set[Path]:
    """Return a clean complete installable plugin tree, rejecting retired residue."""
    plugin = root / PLUGIN_ROOT
    try:
        mode = plugin.lstat().st_mode
    except OSError as exc:
        raise CandidateError("installable Cortex plugin directory is missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise CandidateError("installable Cortex plugin directory must be a regular directory")

    files: set[Path] = set()
    for base, directories, names in os.walk(plugin, followlinks=False):
        current = Path(base)
        for name in [*directories, *names]:
            path = current / name
            relative = path.relative_to(root)
            if path.is_symlink():
                raise CandidateError(f"installable plugin contains a symlink: {relative}")
            if relative in RETIRED_PLUGIN_PATHS or any(
                retired in relative.parents for retired in RETIRED_PLUGIN_PATHS
            ):
                raise CandidateError(f"installable plugin retains retired V11 residue: {relative}")
            if any(part in FORBIDDEN_PARTS for part in relative.parts):
                raise CandidateError(f"installable plugin contains runtime state: {relative}")
            if path.suffix in FORBIDDEN_SUFFIXES:
                raise CandidateError(f"installable plugin contains Python bytecode: {relative}")
        for name in names:
            path = current / name
            relative = path.relative_to(root)
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise CandidateError(f"installable plugin file is unreadable: {relative}") from exc
            if not stat.S_ISREG(mode):
                raise CandidateError(f"installable plugin has a non-regular payload: {relative}")
            files.add(relative)
    return files


def _skill_resource_files(root: Path) -> set[Path]:
    """Include every regular resource bundled with an explicitly shipped skill."""
    resources: set[Path] = set()
    for name in PLUGIN_SKILLS:
        skill_root = root / PLUGIN_ROOT / "skills" / name
        try:
            mode = skill_root.lstat().st_mode
        except OSError as exc:
            raise CandidateError(f"bundled skill directory is missing: {name}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CandidateError(f"bundled skill directory must be regular: {name}")
        for path in skill_root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise CandidateError(f"bundled skill contains a symlink: {relative}")
            if path.is_file():
                _require_regular(root, relative)
                resources.add(relative)
    return resources


def _module_name_for(root: Path, relative: Path) -> tuple[str, str] | None:
    plugin_scripts = Path("plugins/cortex/scripts")
    support_scripts = Path("scripts")
    if relative.is_relative_to(plugin_scripts):
        base = plugin_scripts
        namespace = "plugin"
    elif relative.is_relative_to(support_scripts):
        base = support_scripts
        namespace = "support"
    else:
        return None
    local = relative.relative_to(base)
    if local.suffix != ".py":
        return None
    parts = list(local.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts), namespace


def _module_file(root: Path, module: str) -> Path | None:
    if not module:
        return None
    parts = module.split(".")
    bases = (
        Path("plugins/cortex/scripts"),
        Path("scripts"),
    )
    for base in bases:
        file_candidate = base.joinpath(*parts).with_suffix(".py")
        package_candidate = base.joinpath(*parts, "__init__.py")
        if (root / file_candidate).is_file():
            return file_candidate
        if (root / package_candidate).is_file():
            return package_candidate
    return None


def _local_namespace(module: str) -> bool:
    if module == "cortex" or module.startswith("cortex_runtime"):
        return True
    support_names = {
        Path(item).stem for item in SUPPORT_SCRIPTS
        if Path(item).suffix == ".py" and "-" not in Path(item).stem
    }
    return module.split(".", 1)[0] in support_names


def _imported_modules(root: Path, relative: Path) -> set[tuple[str, bool]]:
    source = _require_regular(root, relative).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(relative))
    except SyntaxError as exc:
        raise CandidateError(f"invalid Python source in {relative}: {exc}") from exc
    module_info = _module_name_for(root, relative)
    current_module = module_info[0] if module_info else ""
    current_package = (
        current_module if relative.name == "__init__.py"
        else current_module.rpartition(".")[0]
    )
    imported: set[tuple[str, bool]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((alias.name, True) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                try:
                    module = importlib.util.resolve_name("." * node.level + module, current_package)
                except (ImportError, ValueError) as exc:
                    raise CandidateError(f"invalid relative import in {relative}") from exc
            if module:
                imported.add((module, True))
                base = _module_file(root, module)
                if base is not None and base.name == "__init__.py":
                    imported.update(
                        (f"{module}.{alias.name}", True)
                        for alias in node.names if alias.name != "*"
                    )
        elif isinstance(node, ast.Call):
            literal: str | None = None
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                literal = node.args[0].value
            if literal:
                imported.add((literal, True))
    return imported


def _python_import_closure(root: Path, seeds: Iterable[Path]) -> set[Path]:
    discovered = set(seeds)
    pending = [item for item in discovered if item.suffix == ".py"]
    visited: set[Path] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        for module, required in _imported_modules(root, relative):
            resolved = _module_file(root, module)
            if resolved is None:
                if required and _local_namespace(module):
                    raise CandidateError(f"local import {module!r} from {relative} is missing")
                continue
            # Importing a child module also requires every local package init.
            parts = module.split(".")
            required = {resolved}
            for index in range(1, len(parts)):
                package = _module_file(root, ".".join(parts[:index]))
                if package is not None and package.name == "__init__.py":
                    required.add(package)
            for dependency in required:
                if dependency not in discovered:
                    discovered.add(dependency)
                    pending.append(dependency)
    return discovered


def _link_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        value = value[1:value.index(">")]
    else:
        value = value.split(maxsplit=1)[0] if value else ""
    return urllib.parse.unquote(urllib.parse.urlsplit(value).path)


def _markdown_release_closure(root: Path, seeds: Iterable[Path]) -> set[Path]:
    """Include only public documents/assets reachable from release entry docs."""
    discovered = set(seeds)
    pending = [item for item in discovered if item.suffix.lower() == ".md"]
    visited: set[Path] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        source = _require_regular(root, relative).read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(source):
            raw = match.group(1) or match.group(2) or ""
            target = _link_target(raw)
            if not target or target.startswith("/"):
                continue
            resolved = (root / relative.parent / target).resolve()
            try:
                linked = resolved.relative_to(root)
            except ValueError:
                raise CandidateError(f"public document link escapes the repository: {relative} -> {raw}")
            if resolved.is_dir():
                index = resolved / "index.md"
                # Repository navigation may deliberately link a source or
                # asset directory. Only a directory with a Markdown index is
                # a release-document dependency; the source assets themselves
                # are already selected by the explicit package manifest.
                if not index.is_file():
                    continue
                linked = linked / "index.md"
                resolved = index
            if linked.suffix.lower() not in PUBLIC_DOCUMENT_SUFFIXES:
                continue
            if not resolved.is_file():
                raise CandidateError(f"public release link is missing: {relative} -> {linked}")
            if linked not in discovered:
                discovered.add(linked)
                if linked.suffix.lower() == ".md":
                    pending.append(linked)
    return discovered


def source_candidate_manifest(root: Path) -> CandidateManifest:
    """Derive the exact allowlisted candidate from public manifests and imports."""
    root = root.resolve()
    installable_plugin_files = _plugin_payload_files(root)
    files = {
        Path(item)
        for item in (
            *PUBLIC_RELEASE_FILES,
            *SUPPORT_SCRIPTS,
            *PLUGIN_STATIC_FILES,
            *ACTIVE_PLUGIN_PYTHON,
        )
    }
    plugin_manifest = _load_json(root / "plugins/cortex/.codex-plugin/plugin.json", "plugin manifest")
    profiles = _load_json(root / "plugins/cortex/profiles.json", "profile contract")

    interface = plugin_manifest.get("interface")
    if not isinstance(interface, dict) or not isinstance(interface.get("logo"), str):
        raise CandidateError("plugin manifest must declare one logo asset")
    files.add(Path("plugins/cortex") / _safe_relative(str(interface["logo"]), "plugin logo"))

    profile_rows = profiles.get("profiles")
    if not isinstance(profile_rows, list) or not profile_rows:
        raise CandidateError("profile contract must list installable profiles")
    for row in profile_rows:
        if not isinstance(row, dict) or not isinstance(row.get("filename"), str):
            raise CandidateError("profile contract contains an invalid filename")
        relative = _safe_relative(str(row["filename"]), "profile filename")
        if relative.parent != Path(".") or relative.suffix != ".toml":
            raise CandidateError(f"profile filename must be one TOML basename: {relative}")
        files.add(Path("plugins/cortex/agents") / relative)

    files.update(_skill_resource_files(root))
    files = _markdown_release_closure(root, files)
    files = _python_import_closure(root, files)
    for relative in sorted(files):
        _require_regular(root, relative)
    staged_plugin_files = {relative for relative in files if relative.is_relative_to(PLUGIN_ROOT)}
    if installable_plugin_files != staged_plugin_files:
        missing = sorted(installable_plugin_files - staged_plugin_files)
        extra = sorted(staged_plugin_files - installable_plugin_files)
        raise CandidateError(
            "release candidate plugin tree is not an exact installable payload; "
            f"missing={missing}; extra={extra}"
        )
    return CandidateManifest(tuple(sorted(files)))


def _secret_prone(relative: Path) -> bool:
    lowered = tuple(part.lower() for part in relative.parts)
    name = lowered[-1]
    return (
        any(part in SECRET_PRONE_DIRECTORIES for part in lowered[:-1])
        or name in SECRET_PRONE_BASENAMES
        or name.startswith(".env.")
        or any(name.endswith(suffix) for suffix in SECRET_PRONE_SUFFIXES)
        or (
            name.endswith(".json")
            and name.startswith(("client_secret_", "service_account_", "service-account-"))
        )
    )


def _validate_candidate_paths(tree: Path, manifest: CandidateManifest) -> None:
    actual: set[Path] = set()
    for base, directories, files in os.walk(tree, followlinks=False):
        current = Path(base)
        for name in [*directories, *files]:
            path = current / name
            relative = path.relative_to(tree)
            if path.is_symlink():
                raise CandidateError(f"candidate contains a symlink: {relative}")
            if any(part in FORBIDDEN_PARTS for part in relative.parts):
                raise CandidateError(f"candidate contains runtime state: {relative}")
            if path.suffix in FORBIDDEN_SUFFIXES:
                raise CandidateError(f"candidate contains Python bytecode: {relative}")
            if _secret_prone(relative):
                raise CandidateError(f"candidate contains a secret-prone path: {relative}")
        actual.update(
            (current / name).relative_to(tree)
            for name in files
        )
    if actual != set(manifest.files):
        missing = sorted(set(manifest.files) - actual)
        extra = sorted(actual - set(manifest.files))
        raise CandidateError(f"candidate manifest mismatch; missing={missing}; extra={extra}")


def _validate_public_files(tree: Path) -> None:
    for raw in PUBLIC_RELEASE_FILES:
        relative = Path(raw)
        content = _require_regular(tree, relative).read_text(encoding="utf-8")
        if "TODO(release)" in content or "TBD(release)" in content:
            raise CandidateError(f"release placeholder remains in {relative}")
        if PRIVATE_HOME_PATH.search(content):
            raise CandidateError(f"private local home path remains in {relative}")


def _validate_documented_script_commands(tree: Path, manifest: CandidateManifest) -> None:
    """Require packaged command documentation to reference bytecode-free scripts."""
    packaged = set(manifest.files)
    for relative in DOCUMENTED_COMMAND_FILES:
        source = _require_regular(tree, relative).read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), 1):
            if PYTHON_BYTECODE_COMPILER.search(line) is not None:
                raise CandidateError(
                    "documented validation must not use py_compile or compileall because they write bytecode: "
                    f"{relative}:{line_number}"
                )
            uses_python = PYTHON_INTERPRETER.search(line) is not None
            is_python_gate = uses_python and (
                SCRIPT_PATH_REFERENCE.search(line) is not None or PYTEST_MODULE.search(line) is not None
            )
            if is_python_gate and (
                PYTHON_NO_BYTECODE not in line or PYTHON_BYTECODE_FLAG.search(line) is None
            ):
                raise CandidateError(
                    "documented Python validation must use PYTHONDONTWRITEBYTECODE=1 and -B: "
                    f"{relative}:{line_number}"
                )
            for match in SCRIPT_PATH_REFERENCE.finditer(line):
                prefix = line[:match.start()]
                command_context = bool(match.group("explicit")) or re.search(
                    r"\b(?:python(?:3(?:\.\d+)?)?|bash|sh)\b", prefix,
                ) is not None
                if not command_context:
                    continue
                command = Path(match.group("path"))
                if command not in packaged:
                    raise CandidateError(
                        f"documented script command is absent from the candidate: "
                        f"{relative}:{line_number} -> {command}"
                    )
                _require_regular(tree, command)


def validate_candidate_tree(tree: Path, manifest: CandidateManifest | None = None) -> CandidateManifest:
    """Prove the produced candidate is exact, import-closed, and publishable."""
    tree = tree.resolve()
    active = manifest or source_candidate_manifest(tree)
    _validate_candidate_paths(tree, active)
    _validate_public_files(tree)
    _validate_documented_script_commands(tree, active)
    document_closure = _markdown_release_closure(tree, active.files)
    if document_closure != set(active.files):
        raise CandidateError("candidate documentation closure differs from its manifest")
    closure = _python_import_closure(tree, active.files)
    if closure != set(active.files):
        raise CandidateError("candidate Python import closure differs from its manifest")
    validator = tree / "scripts/validate-cortex-marketplace.py"
    catalog_renderer = tree / "scripts/render_cortex_tool_catalog.py"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for command, label in (
        ([sys.executable, "-B", str(validator), "--root", str(tree)], "marketplace validation"),
        ([sys.executable, "-B", str(catalog_renderer), "--root", str(tree), "--check"], "tool catalog validation"),
    ):
        checked = subprocess.run(
            command,
            cwd=tree,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if checked.returncode != 0:
            detail = checked.stdout.strip() or checked.stderr.strip() or f"{label} failed"
            raise CandidateError(detail)
    return active


def build_source_candidate(root: Path, destination: Path) -> CandidateManifest:
    """Copy only exact allowlisted working-tree files into a fresh directory."""
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise CandidateError(f"candidate destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = source_candidate_manifest(root)
    for relative in manifest.files:
        source = _require_regular(root, relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    _validate_candidate_paths(destination, manifest)
    return manifest


def required_head_drift(root: Path, manifest: CandidateManifest) -> list[str]:
    """Return required candidate files that are untracked or differ from HEAD."""
    root = root.resolve()
    drift: list[str] = []
    for relative in manifest.files:
        raw = relative.as_posix()
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", raw],
            cwd=root, text=True, capture_output=True, check=False,
        )
        if tracked.returncode != 0:
            drift.append(f"untracked:{raw}")
            continue
        changed = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", raw],
            cwd=root, check=False,
        )
        if changed.returncode != 0:
            drift.append(f"differs:{raw}")
    return drift


__all__ = [
    "CandidateError", "CandidateManifest", "build_source_candidate",
    "required_head_drift", "source_candidate_manifest", "validate_candidate_tree",
]
