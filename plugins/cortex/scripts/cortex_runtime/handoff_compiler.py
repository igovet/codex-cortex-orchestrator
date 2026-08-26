"""Target/profile-specific handoff views over canonical semantic state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.context_compiler import ContextCompiler, dispatch_canonical_state


HANDOFF_SCHEMA = "cortex/handoff-projection/v1"
_BACKEND = {"backend_dev", "data_engineer", "debugger", "devops_engineer", "frontend_dev", "fullstack_dev", "mobile_dev", "refactorer"}
_QA = {"qa_engineer", "build_verification"}
_REVIEW = {"code_reviewer", "security_auditor", "performance_engineer"}

def _profile_kind(profile: str) -> str:
    if profile in _BACKEND:
        return "implementation"
    if profile in _QA:
        return "qa"
    if profile in _REVIEW:
        return "review"
    return "general"


def _unique(values: list[str]) -> list[str]:
    return [str(value) for value in values if str(value).strip()]


class HandoffCompiler:
    """Project target-relevant fields without attaching raw worker bodies."""

    def __init__(self, context_compiler: ContextCompiler | None = None) -> None:
        self._context_compiler = context_compiler or ContextCompiler()

    def build(
        self,
        canonical: Mapping[str, Any],
        *,
        target_profile: str,
        target_gate: str | None = None,
    ) -> dict[str, Any]:
        context = self._context_compiler.compile(canonical, target_profile=target_profile, target_gate=target_gate)
        task = context.get("task", {})
        predecessors = context.get("predecessor_facts", [])
        kind = _profile_kind(target_profile)
        common = {
            "schema": HANDOFF_SCHEMA,
            "target": {"profile": target_profile, "kind": kind, "gate": target_gate or context.get("assignment", {}).get("phase")},
            "user_request": task.get("user_request"),
            "server_receipts": context.get("server_receipts", {}),
            "predecessor_selection": context.get("predecessor_selection", {}),
            "predecessor_result_refs": [
                str(item.get("attempt_result_ref")) for item in predecessors
                if isinstance(item, Mapping) and str(item.get("attempt_result_ref") or "").strip()
            ],
        }
        changed = _unique([path for item in predecessors for path in item.get("changed_files", [])])
        checks = _unique([check for item in predecessors for check in item.get("checks", [])])
        unresolved = _unique([finding for item in predecessors for finding in item.get("unresolved_findings", [])])
        conclusions = _unique([item.get("conclusion", "") for item in predecessors])

        if kind == "implementation":
            projection = {
                **common,
                "requirements": task.get("requirements", []),
                "decisions": context.get("decisions", []),
                "assigned_scope": context.get("assignment", {}).get("scope", []),
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "verification_requirements": task.get("verification") or task.get("verification_requirements", []),
                "relevant_predecessor_conclusions": conclusions,
                "known_unresolved_findings": unresolved,
            }
        elif kind == "qa":
            projection = {
                **common,
                "implemented_behavior": conclusions,
                "files_changed": changed,
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "verification_requirements": task.get("verification") or task.get("verification_requirements", []),
                "verification_already_executed": checks,
                "known_unresolved_findings": unresolved,
                "risk_areas": unresolved or changed,
            }
        elif kind == "review":
            projection = {
                **common,
                "change_inventory": changed,
                "requirements_to_review": task.get("requirements", []) + task.get("acceptance_criteria", []),
                "verification_evidence": checks,
                "open_findings": unresolved,
                "decisions_affecting_review": context.get("decisions", []),
            }
        else:
            projection = {**common, "requirements": task.get("requirements", []), "relevant_conclusions": conclusions}
        projection = {key: value for key, value in projection.items() if value not in (None, "", [], {})}
        return projection


def build_handoff(
    canonical: Mapping[str, Any],
    *,
    target_profile: str,
    target_gate: str | None = None,
) -> dict[str, Any]:
    """Convenience functional API for runtime facade integration."""
    return HandoffCompiler().build(
        canonical,
        target_profile=target_profile,
        target_gate=target_gate,
    )


def build_dispatch_handoff(package: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Build the lossless handoff used by immutable dispatch and recovery."""
    canonical = dispatch_canonical_state(package, profile)
    return build_handoff(
        canonical,
        target_profile=profile,
        target_gate=str(package.get("gate") or "") or None,
    )
