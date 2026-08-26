"""Immutable worker briefings with one deliberately small semantic projection."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cortex_runtime.v11_responses import native_dispatch_authority_message

from cortex_runtime.core.runtime_bindings import bind_symbols, bound_symbol
from cortex_runtime.prompt_compiler import compile_v3_briefing


bind_symbols(
    "briefings",
    globals(),
    (
        "PROFILE_EXECUTION_CONTRACTS",
        "PROFILE_INSTRUCTIONS",
        "PROFILES",
        "SHARED_WORKER_CONTRACT",
        "result_contract_is_read_only",
    ),
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
    return native_dispatch_authority_message(worker_authority, (
        f"Cortex worker ({profile}). "
        "Exactly one host-local ALL_TOOLS metadata lookup may resolve only "
        "read_dispatch_briefing; this is the sole pre-briefing exception. ALL_TOOLS entries "
        "provide callable name and description only; do not require, search for, or infer a "
        "local input schema, parameters object, or other metadata. Tool catalog names can be "
        "host-namespaced: match exactly "
        "one callable whose name is `read_dispatch_briefing` or ends with "
        "`__read_dispatch_briefing` (using its Cortex briefing description only to "
        "disambiguate), then invoke `tools[matched_name]`. Never require exact equality "
        "with the bare semantic name and never call `tools.read_dispatch_briefing` unless "
        "that is the matched callable name. Do not invoke an unresolved direct JavaScript "
        "function or inspect another tool before the complete briefing succeeds. FIRST MCP "
        "ACTION: invoke only the resolved Cortex "
        "read_dispatch_briefing callable. Its active public MCP declaration is the sole source "
        "of call fields; use the byte-for-byte server-issued authority supplied above wherever "
        "that declaration requires it. Do not add, omit, rename, or infer a call field from this "
        "briefing; the public MCP tool schema and runtime perform validation. Before "
        "the complete briefing "
        "returns ok=true, make no other MCP/tool or shell call, project read/write, skill "
        "load, or repository discovery. Retry that call only when public recovery is "
        "retryable. If unavailable or nonretryable, return only "
        "`CORTEX_WORKER_BOOTSTRAP_FAILED retryable=false` and stop. Then follow the "
        "briefing mission, scope, acceptance, and verification. Never copy authority into "
        "semantic text, reports, or user-visible output, infer it, or replace it."
    ))


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _package_value(package: dict[str, Any], *segments: str) -> object:
    """Read an internal package value without emitting public schema vocabulary."""
    return package.get("_".join(segments))


def _worker_operation(base_operation: str, action: str) -> str:
    """Resolve one exact public worker operation from canonical routing metadata."""
    contracts = bound_symbol("briefings", "PUBLIC_CONTRACTS")
    matches = [
        name for name, contract in contracts.items()
        if contract.get("audience") == "worker"
        and contract.get("base_operation") == base_operation
        and (contract.get("injected_arguments") or {}).get("action") == action
    ]
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].strip():
        raise ValueError("canonical worker operation routing is invalid")
    return matches[0]


def _terminal_worker_operation(gate: str) -> str:
    """Resolve the exact public terminal operation for this worker gate."""
    action = "governance_closure" if gate == "governance_close" else "submit"
    return _worker_operation("complete_attempt", action)


def host_spawn_prompt(agent: str, package: dict[str, Any]) -> str:
    """Compile the canonical worker briefing without duplicating tool contracts."""
    if agent not in PROFILE_EXECUTION_CONTRACTS:
        raise ValueError("worker profile has no execution contract")

    operation_kind = str(_package_value(package, "operation", "kind") or "").strip()
    profile_operation_kinds = PROFILES.get(agent, {}).get("operation_kinds")
    if (
        operation_kind not in {"inspect", "modify", "verify", "close"}
        or not isinstance(profile_operation_kinds, list)
        or operation_kind not in profile_operation_kinds
    ):
        raise ValueError("compiled worker operation does not match profile capability")
    compiled_read_only = result_contract_is_read_only(package)
    if compiled_read_only != (operation_kind != "modify"):
        raise ValueError("compiled worker operation and native write capability disagree")

    gate = str(package.get("gate") or "")
    completion_operation = _terminal_worker_operation(gate)

    mission = str(package.get("objective") or _package_value(package, "user", "request") or "").strip()
    scope = _text_list(package.get("scope") or _package_value(package, "task", "scope"))
    acceptance = _text_list(
        _package_value(package, "acceptance", "criteria")
        or _package_value(package, "task", "acceptance", "criteria")
    )
    verification = _text_list(
        package.get("verification") or _package_value(package, "task", "verification")
    )
    answered_user_decisions = list(
        _package_value(package, "resolved", "user", "decisions") or []
    )
    predecessor_attempt_results = _text_list(
        package.get("required_report_refs")
    )
    predecessor_operation = (
        _worker_operation("read_worker_result", "read_predecessor")
        if predecessor_attempt_results else ""
    )
    optional_report_count = len(package.get("optional_report_refs") or [])
    report_catalog_operation = (
        _worker_operation("read_worker_result", "list_reports")
        if optional_report_count else ""
    )

    assignment = {
        "mission": mission,
        "operation kind": operation_kind,
        "scope": scope,
        "acceptance": acceptance,
        "verification": verification,
        "answered user decisions": answered_user_decisions,
        "required report references": predecessor_attempt_results,
        "optional report catalog available": optional_report_count > 0,
    }
    recovery_context = package.get("recovery_context")
    if isinstance(recovery_context, dict):
        source_ref = str(package.get("recovery_source_result_ref") or "")
        source_status = str(recovery_context.get("source_result_status") or "")
        if source_status == "available" and not source_ref:
            raise ValueError("technical recovery briefing lost its source result authority")
        if source_status == "absent" and source_ref:
            raise ValueError("result-less technical recovery briefing fabricated a source result")
        assignment["technical recovery"] = {
            "source result status": source_status,
            "source result absence reason": str(
                recovery_context.get("source_result_absence_reason") or ""
            ) or None,
            "prior evaluator disposition": str(
                recovery_context.get("evaluator_disposition") or ""
            ),
            "failure class": str(recovery_context.get("failure_class") or ""),
            "reasons": list(recovery_context.get("evaluator_reasons") or []),
            "recovery stage": str(recovery_context.get("recovery_stage") or ""),
            "missing obligations": list(recovery_context.get("missing_obligations") or []),
            "failure evidence": list(recovery_context.get("failure_evidence") or []),
            "remaining work": str(recovery_context.get("remaining_work") or ""),
            "context digest": str(package.get("recovery_context_digest") or ""),
            "source result digest": str(package.get("recovery_source_result_digest") or "") or None,
            "source result chain count": len(package.get("recovery_chain_result_refs") or []),
        }
        recovery_question_ref = str(package.get("recovery_question_ref") or "")
        if recovery_question_ref:
            assignment["technical recovery"]["required durable question"] = recovery_question_ref
            assignment["technical recovery"]["durable question instruction"] = (
                "Poll this exact answered question with worker_question and consume every answer page "
                "before completing the replacement assignment."
            )
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
    mutation_safety = str(SHARED_WORKER_CONTRACT.get("mutating_patch_safety") or "").strip()
    if not mutation_safety:
        raise ValueError("shared mutating patch safety contract is missing")
    same_operation_recovery = str(
        SHARED_WORKER_CONTRACT.get("same_operation_request_recovery") or ""
    ).strip()
    if not same_operation_recovery:
        raise ValueError("shared same-operation request recovery contract is missing")
    access = "Do not edit project files." if operation_kind != "modify" else (
        "Change only mission artifacts within the assigned scope. " + mutation_safety
    )
    predecessor_reads = (
        " Before any project work, consume every value listed under required report "
        f"references through {predecessor_operation}. Resolve exactly one callable from "
        "ALL_TOOLS whose name equals that exact semantic operation or ends with its "
        "host-namespaced suffix. Before the first invocation, inspect only that matched entry's "
        "current description and host-generated declaration. If either is unavailable, stop "
        "before project access. Construct the invocation solely from that active public MCP "
        "declaration, reuse the exact worker authority that succeeded for the briefing read, "
        "and use the exact current predecessor reference wherever the declaration requires it; "
        "never infer call fields from prose. "
        "Then invoke tools[matched_name] and consume every returned page. If that exact callable "
        "is absent or ambiguous, "
        "stop before project access; do not broaden discovery or search private or runtime paths. "
        "Never substitute embedded projections, shell-"
        "visible Cortex source, installed plugin files, private state, .codex/cortex paths, or "
        "generated report or artifact files for these canonical reads."
        if predecessor_operation else ""
    )
    optional_report_reads = (
        " Older eligible reports are optional context. Page through "
        f"{report_catalog_operation} only when its metadata may help this mission; choose any "
        f"optional opaque report reference needed and consume it through {predecessor_operation}. "
        "Never treat optional catalog entries as required reads."
        if optional_report_count else ""
    )
    return compile_v3_briefing(
        assignment=assignment,
        authority=(
            "The assignment and answered user decisions establish intent. Current source, "
            "tests, executable configuration, and predecessor evidence consumed through its "
            "authorized public operation establish facts."
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
            "returned lifecycle direction explicitly makes the retry safe. After the briefing "
            "succeeds, resolve exactly one callable from ALL_TOOLS whose name equals the required "
            "semantic operation or ends with its host-namespaced suffix, then invoke "
            "tools[matched_name]. Never invent candidate names, guess aliases, or substitute a "
            "different operation. "
            + same_operation_recovery
            + predecessor_reads
            + optional_report_reads
        ),
        output_contract=(
            "Publish one evidence-backed semantic conclusion by resolving and invoking "
            f"{completion_operation} from the active public registry. Its public schema alone "
            "defines the arguments. Do not return terminal prose unless that canonical "
            "submission succeeds. Cortex derives lifecycle identity, provenance, and workspace "
            "observations."
        ),
        stopping=(
            "Continue until the acceptance and verification obligations are resolved. Ask one "
            "complete user question only when a material task decision is required. After "
            "durably publishing that question, end the current native turn with an unambiguous "
            "durable-question marker. Do not begin project work, poll, or submit before "
            "same-child follow-up after the real user answer. After successful submission, "
            "end with a terminal native marker and make no later task-scoped call. The trusted local "
            "SubagentStop observer, not worker prose or wait output, records exact native terminal completion. "
            "A terminal SubagentStop is the result-consumption prerequisite; neither is "
            "cryptographic proof or server attestation. Stop on missing authority, a "
            "nonretryable response, or an explicit terminal direction."
        ),
    )
