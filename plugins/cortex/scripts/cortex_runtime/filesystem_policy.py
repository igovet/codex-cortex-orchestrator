"""Fail-closed filesystem-mutation conformance for Cortex runtime source.

The registry is the sole source-level authority for Python filesystem writes.
It is intentionally conservative: a callable that might be an OS/pathlib/
shutil mutator must be a direct, reviewed capability call. Aliasing or storing
such a callable is rejected rather than inferred as safe.
"""
from __future__ import annotations

import ast
from pathlib import Path


_MUTATORS = frozenset({
    "open", "write", "pwrite", "chmod", "fchmod", "chown", "fchown",
    "replace", "unlink", "remove", "rename", "truncate", "ftruncate",
    "mkdir", "makedirs", "rmdir", "link", "symlink", "mkfifo", "mknod",
    "utime", "write_text", "write_bytes", "touch", "move", "copy", "copy2",
    "copyfile", "copytree", "rmtree",
})
_FILESYSTEM_MODULES = frozenset({"os", "os.path", "pathlib", "shutil"})

# Keys are exact qualified functions, not filename/string-literal exemptions.
_CAPABILITIES = {
    "host_boundary.CodexHostProbe.save": "owner-private-exclusive-passive-capability-snapshot",
    "artifact_fingerprint._entry": "bounded-artifact-nofollow-read",
    "artifact_fingerprint._archive_directory": "owner-private-artifact-archive-directory",
    "artifact_fingerprint.save_manifest": "content-addressed-artifact-manifest-create",
    "artifact_fingerprint.load_manifest": "owner-private-artifact-manifest-read",
    "native_observation._write": "signed-native-observation-atomic-write",
    "native_observation.bind_task": "owner-private-native-task-binding-lock",
    "native_observation.record_projection": "owner-private-native-projection-lock",
    "v12_store.V12Store._directory": "private-state-directory",
    "v12_store.V12Store._write_record_locator_rows": "derived-locator-private-mode",
    "v12_store.V12Store._replace_record_locator_sidecar": "derived-locator-atomic-rebuild",
    "v12_store.V12Store._write_task_locator_rows": "derived-task-locator-private-mode",
    "v12_store.V12Store._replace_task_locator_sidecar": "derived-task-locator-atomic-rebuild",
    "v12_store.V12Store._precreate_database": "canonical-database-create",
    "v12_store.V12Store._secure_regular_file": "canonical-database-protection",
    "v12_store.V12Store._sqlite_admission_lock": "per-shard-admission-lease",
    "v12_maintenance._ensure_private_directory": "offline-maintenance-directory",
    "v12_maintenance._create_private_directory": "offline-maintenance-directory",
    "v12_maintenance._create_private_file": "offline-maintenance-file",
    "v12_maintenance._fsync_directory": "offline-maintenance-fsync",
    "v12_maintenance._atomic_private_json": "offline-maintenance-metadata",
    "v12_maintenance._remove_validated_backup_bundle": "offline-validated-backup-retention",
    "v12_maintenance._create_backup": "offline-validated-backup-retention",
    "v12_maintenance.prune_projections": "offline-projection-retention",
    "v12_projections._directory": "private-projection-directory",
    "v12_projections._fsync_parent": "private-projection-fsync",
    "v12_projections._safe_write": "private-projection-atomic-write",
    "event_journal._private_child_directory": "candidate-observation-private-directory",
    "event_journal._private_directory_descriptor": "candidate-observation-root-validation",
    "event_journal.EventJournal._open": "candidate-observation-journal-open",
    "event_journal.EventJournal.emit": "candidate-observation-bounded-append",
    "event_journal.EventJournal.emit_server_ready": "candidate-observation-ready-append",
    "event_journal.EventJournal.emit_lifecycle": "candidate-observation-lifecycle-append",
    "cortex_lifecycle_observer._journal": "candidate-observation-lease-read",
    "observation_generation._private_directory": "candidate-observation-private-directory",
    "observation_generation._locked": "candidate-observation-lock-file",
    "observation_generation._write": "candidate-observation-atomic-record",
    "observation_generation.request_generation": "candidate-observation-intent-update",
    "observation_generation.create_session_intent": "candidate-observation-intent-create",
    "observation_generation.consume_intent": "candidate-observation-lease-atomic-transition",
    "observation_generation.claim_generation": "candidate-observation-lease-claim",
    "observation_generation.write_ready_receipt": "candidate-observation-ready-registration",
    "observation_generation.revoke_session": "candidate-observation-lease-revoke",
    "raw_diagnostic.append": "isolated-raw-diagnostic-bounded-append",
    "audience_attestation.issue_worker_candidate": "owner-private-worker-candidate-attestation",
    "audience_attestation.fresh_worker_candidate_available": "owner-private-worker-candidate-catalogue",
    "audience_attestation.authorize_worker_candidate_call": "owner-private-worker-call-authorization",
    "audience_attestation.revoke_worker_candidate_call": "owner-private-worker-call-revocation",
    "audience_attestation.claim_worker_candidate": "owner-private-worker-candidate-claim",
    "audience_attestation.release_worker_candidate_claim": "owner-private-worker-candidate-release",
    "audience_attestation._private_directory": "owner-private-audience-directory",
    "audience_attestation._key": "owner-private-audience-signing-key",
}


def _attribute_parts(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_parts(node.value)
        return None if parent is None else (*parent, node.attr)
    return None


def _module_target(node: ast.AST, module_aliases: dict[str, str]) -> str | None:
    parts = _attribute_parts(node)
    if not parts:
        return None
    mapped = module_aliases.get(parts[0])
    if mapped is None:
        return None
    return ".".join((mapped, *parts[1:]))


def _filesystem_identity(
    node: ast.AST | None,
    *,
    module_aliases: dict[str, str],
    path_names: set[str],
    identity_aliases: dict[str, str],
    return_summaries: dict[str, str],
) -> str | None:
    """Resolve the small set of filesystem constructors/modules we track."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        if node.id in identity_aliases:
            return identity_aliases[node.id]
        if node.id in path_names:
            return "pathlib.Path"
        return module_aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        target = _module_target(node, module_aliases)
        if target is not None:
            return target
        base = _filesystem_identity(
            node.value, module_aliases=module_aliases, path_names=path_names,
            identity_aliases=identity_aliases, return_summaries=return_summaries,
        )
        if base in _FILESYSTEM_MODULES:
            return f"{base}.{node.attr}"
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return return_summaries.get(node.func.id)
    return None


def _is_path_constructor(
    node: ast.AST,
    module_aliases: dict[str, str],
    path_names: set[str],
    identity_aliases: dict[str, str],
    return_summaries: dict[str, str],
) -> bool:
    return _filesystem_identity(
        node, module_aliases=module_aliases, path_names=path_names,
        identity_aliases=identity_aliases, return_summaries=return_summaries,
    ) == "pathlib.Path"


def _filesystem_namespace(node: ast.AST, module_aliases: dict[str, str]) -> bool:
    """Whether an expression names a filesystem module or one of its views."""
    target = _module_target(node, module_aliases)
    return bool(target and any(target == module or target.startswith(module + ".") for module in _FILESYSTEM_MODULES))


def _is_dynamic_filesystem_subscript(node: ast.AST, module_aliases: dict[str, str]) -> bool:
    """Recognize os/pathlib/shutil dictionary chains before call-target exit."""
    if not isinstance(node, ast.Subscript):
        return False
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    return _filesystem_namespace(current, module_aliases)


def _contains_mutator_expression(
    node: ast.AST | None,
    *,
    module_aliases: dict[str, str],
    mutator_aliases: set[str],
) -> bool:
    """Return whether an expression exposes a filesystem mutator callable."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in mutator_aliases
    if isinstance(node, ast.Attribute):
        target = _module_target(node, module_aliases)
        return bool(target and target.rsplit(".", 1)[-1] in _MUTATORS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        name = node.args[1] if len(node.args) > 1 else None
        return bool(
            node.args
            and _module_target(node.args[0], module_aliases) in _FILESYSTEM_MODULES
            and (not isinstance(name, ast.Constant) or name.value in _MUTATORS)
        )
    if isinstance(node, ast.Call):
        return False
    if isinstance(node, ast.Subscript):
        return _is_dynamic_filesystem_subscript(node, module_aliases)
    return any(
        _contains_mutator_expression(child, module_aliases=module_aliases, mutator_aliases=mutator_aliases)
        for child in ast.iter_child_nodes(node)
    )


def _qualified_function(node: ast.AST, parents: dict[ast.AST, ast.AST], module: str) -> str:
    names: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(current.name)
    return ".".join((module, *reversed(names))) if names else f"{module}.<module>"


def _module_paths(runtime_root: Path) -> list[Path]:
    paths = sorted(runtime_root.rglob("*.py"))
    launcher = runtime_root.parent / "cortex.py"
    if launcher.is_file():
        paths.append(launcher)
    return paths


def _assigned_names(node: ast.AST) -> set[str]:
    """Extract only direct local names; attributes remain an escape failure."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_assigned_names(item) for item in node.elts))
    return set()


def _return_summaries(
    tree: ast.AST,
    *,
    module_aliases: dict[str, str],
    path_names: set[str],
) -> dict[str, str]:
    """Compute direct/one-hop helper summaries to a stable conservative set."""
    functions = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    summaries: dict[str, str] = {}
    for _ in range(len(functions) + 1):
        changed = False
        for name, function in functions.items():
            identities = {
                _filesystem_identity(
                    returned.value, module_aliases=module_aliases, path_names=path_names,
                    identity_aliases={}, return_summaries=summaries,
                )
                for returned in ast.walk(function) if isinstance(returned, ast.Return)
            }
            identities.discard(None)
            if len(identities) == 1:
                identity = next(iter(identities))
                if summaries.get(name) != identity:
                    summaries[name] = identity
                    changed = True
        if not changed:
            break
    return summaries


def assert_runtime_mutation_conformance(runtime_root: Path) -> None:
    """Reject any unreviewed filesystem mutation or mutator-callable escape."""
    violations: list[str] = []
    policy_path = Path(__file__).resolve()
    for path in _module_paths(runtime_root):
        if path.resolve() == policy_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = "cortex" if path.name == "cortex.py" else ".".join(path.relative_to(runtime_root).with_suffix("").parts)
        module_aliases: dict[str, str] = {}
        mutator_aliases: set[str] = set()
        path_names: set[str] = {"Path"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _FILESYSTEM_MODULES:
                        module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module in _FILESYSTEM_MODULES:
                for alias in node.names:
                    name = alias.asname or alias.name
                    if alias.name in _MUTATORS:
                        mutator_aliases.add(name)
                    elif node.module == "pathlib" and alias.name == "Path":
                        path_names.add(name)
                    elif alias.name == "path":
                        module_aliases[name] = "os.path"
        return_summaries = _return_summaries(tree, module_aliases=module_aliases, path_names=path_names)
        identity_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                identity = _filesystem_identity(
                    getattr(node, "value", None), module_aliases=module_aliases, path_names=path_names,
                    identity_aliases=identity_aliases, return_summaries=return_summaries,
                )
                if identity is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                    for target in targets:
                        identity_aliases.update({name: identity for name in _assigned_names(target)})
                if _contains_mutator_expression(
                    getattr(node, "value", None), module_aliases=module_aliases, mutator_aliases=mutator_aliases,
                ):
                    violations.append(f"{module}:mutator-callable-storage")
                    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                    for target in targets:
                        mutator_aliases.update(_assigned_names(target))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                defaults = [
                    *getattr(node.args, "defaults", ()),
                    *(value for value in getattr(node.args, "kw_defaults", ()) if value is not None),
                ]
                if any(
                    _contains_mutator_expression(value, module_aliases=module_aliases, mutator_aliases=mutator_aliases)
                    for value in defaults
                ):
                    violations.append(f"{module}:mutator-callable-default")
                if any(
                    _contains_mutator_expression(decorator, module_aliases=module_aliases, mutator_aliases=mutator_aliases)
                    for decorator in getattr(node, "decorator_list", ())
                ):
                    violations.append(f"{module}:mutator-callable-decorator")
            elif isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
                if _contains_mutator_expression(
                    getattr(node, "value", None), module_aliases=module_aliases, mutator_aliases=mutator_aliases,
                ):
                    violations.append(f"{module}:mutator-callable-export")

        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
            capability = _qualified_function(call, parents, module)
            func = call.func
            if _is_dynamic_filesystem_subscript(func, module_aliases):
                violations.append(f"{capability}:dynamic-subscript")
                continue
            if any(
                _contains_mutator_expression(argument, module_aliases=module_aliases, mutator_aliases=mutator_aliases)
                for argument in (*call.args, *(keyword.value for keyword in call.keywords))
            ):
                violations.append(f"{capability}:mutator-callable-callback")
            if isinstance(func, ast.Name):
                if func.id in mutator_aliases or func.id == "open":
                    if capability not in _CAPABILITIES:
                        violations.append(f"{capability}:{func.id}")
                elif func.id == "getattr" and call.args:
                    target = _module_target(call.args[0], module_aliases)
                    name = call.args[1] if len(call.args) > 1 else None
                    if target in _FILESYSTEM_MODULES and (
                        not isinstance(name, ast.Constant) or name.value in _MUTATORS
                    ):
                        violations.append(f"{capability}:dynamic-lookup")
                continue
            if not isinstance(func, ast.Attribute):
                continue
            target = _module_target(func, module_aliases)
            if target is None:
                base_identity = _filesystem_identity(
                    func.value, module_aliases=module_aliases, path_names=path_names,
                    identity_aliases=identity_aliases, return_summaries=return_summaries,
                )
                if base_identity in _FILESYSTEM_MODULES:
                    target = f"{base_identity}.{func.attr}"
            path_call = isinstance(func.value, ast.Call) and _is_path_constructor(
                func.value.func, module_aliases, path_names, identity_aliases, return_summaries,
            )
            if (target and target.rsplit(".", 1)[-1] in _MUTATORS) or (path_call and func.attr in _MUTATORS):
                if capability not in _CAPABILITIES:
                    violations.append(f"{capability}:{func.attr}")
    if violations:
        raise AssertionError("unregistered filesystem mutation capability: " + ", ".join(sorted(set(violations))))
