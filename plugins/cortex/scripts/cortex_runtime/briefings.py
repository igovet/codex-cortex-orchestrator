"""Immutable worker briefings with one deliberately small semantic projection."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols
from cortex_runtime.prompt_compiler import compile_v3_briefing


bind_symbols(
    "briefings",
    globals(),
    ("PROFILE_EXECUTION_CONTRACTS", "PROFILE_INSTRUCTIONS", "result_contract_is_read_only"),
)


def dispatch_briefing_review_marker(briefing_digest: str) -> str:
    digest = str(briefing_digest or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("dispatch briefing digest is invalid")
    return f"Dispatch briefing reviewed: {digest}"


def codebase_memory_project_key_from_root(project_root: object) -> str:
    """Mirror Codebase Memory's cbm_project_name_from_path for a canonical root."""
    raw = str(project_root or "")
    if not raw or all(character in "/\\:" for character in raw):
        return "root"
    try:
        path = str(Path(raw).resolve(strict=True))
    except (OSError, RuntimeError):
        path = raw
    path = path.replace("\\", "/")
    mapped: list[str] = []
    for byte in path.encode("utf-8"):
        if (
            ord("a") <= byte <= ord("z")
            or ord("A") <= byte <= ord("Z")
            or ord("0") <= byte <= ord("9")
            or byte in (ord("."), ord("_"), ord("-"))
        ):
            mapped.append(chr(byte))
        elif byte >= 0x80:
            mapped.append(f"{byte:02x}")
        else:
            mapped.append("-")
    collapsed: list[str] = []
    for character in mapped:
        previous = collapsed[-1] if collapsed else ""
        if (character == "-" and previous == "-") or (character == "." and previous == "."):
            continue
        collapsed.append(character)
    key = "".join(collapsed).lstrip(".-").rstrip("-") or "root"
    if len(key) <= 200:
        return key
    digest = 2166136261
    for byte in key.encode("ascii"):
        digest ^= byte
        digest = (digest * 16777619) & 0xFFFFFFFF
    return f"{key[:191]}-{digest:08x}"


def host_spawn_bootstrap(
    profile: str,
    briefing_path: Path,
    briefing_digest: str,
    worker_authority: str,
    task_id: str,
    attempt_id: str,
    project_root: Path,
    *,
    intent_path: str | None = None,
    intent_digest: str | None = None,
    plan_unit_path: str | None = None,
    plan_unit_digest: str | None = None,
    task_contract_path: str | None = None,
    task_contract_digest: str | None = None,
) -> str:
    """Return the minimal native worker bootstrap."""
    del briefing_path, briefing_digest, task_id, attempt_id, project_root
    del intent_path, intent_digest, plan_unit_path, plan_unit_digest
    del task_contract_path, task_contract_digest
    return (
        f"Cortex worker ({profile}). Opaque worker authority: {worker_authority}. "
        "Read the complete immutable briefing before project work. Use only the "
        "active public tool contracts and never infer or replace authority."
    )


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _package_value(package: dict[str, Any], *segments: str) -> object:
    """Read an internal package value without emitting public schema vocabulary."""
    return package.get("_".join(segments))


def host_spawn_prompt(agent: str, package: dict[str, Any]) -> str:
    """Compile the canonical worker briefing without duplicating tool contracts."""
    if agent not in PROFILE_EXECUTION_CONTRACTS:
        raise ValueError("worker profile has no execution contract")

    mission = str(package.get("objective") or _package_value(package, "user", "request") or "").strip()
    scope = _text_list(package.get("scope") or _package_value(package, "task", "scope"))
    allowed_paths = _text_list(_package_value(package, "allowed", "paths"))
    acceptance = _text_list(
        _package_value(package, "acceptance", "criteria")
        or _package_value(package, "task", "acceptance", "criteria")
    )
    verification = _text_list(
        package.get("verification") or _package_value(package, "task", "verification")
    )
    predecessor_evidence = list(_package_value(package, "resolved", "user", "decisions") or [])
    predecessor_evidence.extend(
        _text_list(_package_value(package, "context", "result", "refs"))
    )

    assignment = {
        "mission": mission,
        "scope": scope,
        "allowed paths": allowed_paths,
        "acceptance": acceptance,
        "verification": verification,
        "predecessor evidence": predecessor_evidence,
    }
    execution = PROFILE_EXECUTION_CONTRACTS[agent]
    role_delta = "\n".join((
        "Role execution contract:",
        "Inputs: " + execution["inputs"],
        "Project artifacts: " + execution["project_artifacts"],
        "Completion: " + execution["completion"],
        "",
        "Profile playbook:",
        PROFILE_INSTRUCTIONS[agent],
    ))
    access = "Do not edit project files." if result_contract_is_read_only(package) else (
        "Change only mission artifacts inside the allowed paths."
    )
    return compile_v3_briefing(
        assignment=assignment,
        authority=(
            "The assignment and answered user decisions establish intent. Current source, "
            "tests, executable configuration, and granted predecessor evidence establish facts."
        ),
        hard_constraints=(
            f"Work only the assigned mission and scope. {access} Do not subdelegate, expose "
            "sensitive data, address the user directly, or perform external, destructive, "
            "privileged, production, or paid actions without explicit authority."
        ),
        role_delta=role_delta,
        tool_protocol=(
            "Use the active public tool registry. Read the briefing completely before work, "
            "follow server pagination, preserve opaque authority, and retry only when the "
            "returned lifecycle direction explicitly makes the retry safe."
        ),
        output_contract=(
            "Publish one evidence-backed semantic conclusion through the current completion "
            "tool. Cortex derives lifecycle identity, provenance, and workspace observations."
        ),
        stopping=(
            "Continue until the acceptance and verification obligations are resolved. Ask one "
            "complete user question only when a material task decision is required. After "
            "durably publishing that question, end the current native turn with an unambiguous "
            "durable-question marker. Do not begin project work, poll, or submit before "
            "same-child follow-up after the real user answer. Stop on missing authority, a "
            "nonretryable response, or an explicit terminal direction."
        ),
    )
