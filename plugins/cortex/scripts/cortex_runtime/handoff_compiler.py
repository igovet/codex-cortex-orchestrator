"""Target/profile-specific handoff projections over canonical semantic state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.context_compiler import ContextCompiler, dispatch_canonical_state


HANDOFF_SCHEMA = "cortex/handoff-projection/v1"
_BACKEND = {"backend_dev", "data_engineer", "debugger", "devops_engineer", "frontend_dev", "fullstack_dev", "mobile_dev", "refactorer"}
_QA = {"qa_engineer", "build_verification"}
_REVIEW = {"code_reviewer", "security_auditor", "performance_engineer"}

# The complete compiler is useful for non-prompt consumers.  Native dispatch
# has a stricter transport budget, so it receives a deliberately small
# profile-specific subset through ``compact=True`` below.
_COMPACT_CONCLUSIONS = 2
_COMPACT_CONCLUSION_CHARS = 220
_COMPACT_PATHS = 8
_COMPACT_PATH_CHARS = 100
_COMPACT_CHECKS = 2
_COMPACT_CHECK_CHARS = 140
_COMPACT_FINDINGS = 2
_COMPACT_FINDING_CHARS = 150


def _utf8_prefix(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


def _profile_kind(profile: str) -> str:
    if profile in _BACKEND:
        return "implementation"
    if profile in _QA:
        return "qa"
    if profile in _REVIEW:
        return "review"
    return "general"


def _unique(values: list[str], *, limit: int) -> list[str]:
    answer: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in answer:
            answer.append(value)
        if len(answer) >= limit:
            break
    return answer


def _compact_strings(values: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return _unique([_utf8_prefix(str(value).strip(), item_limit) for value in values], limit=limit)


def _compact_selection(value: object) -> dict[str, Any]:
    """Keep bounded result selection evidence without duplicating result refs."""
    selection = value if isinstance(value, Mapping) else {}
    compact = {
        "available": selection.get("available"),
        "selected": selection.get("selected"),
        "limit": selection.get("limit"),
        "truncated": bool(selection.get("truncated")),
    }
    return {key: value for key, value in compact.items() if value not in (None, "", 0, False)}


def _compact_projection(projection: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Keep the real immutable briefing below its transport budget.

    Static task intent, scope, and acceptance fields already have canonical
    assignment slots in the briefing.  This compact handoff therefore carries
    only the role-relevant *successor facts* and their bounded provenance.
    """
    common = {
        "schema": projection["schema"],
        "target": projection["target"],
        "predecessor_selection": _compact_selection(projection.get("predecessor_selection")),
        # The compact fact list is a non-authoritative prompt projection.  A
        # successor can retrieve complete canonical predecessor results using
        # these exact refs rather than treating shortened prose as truth.
        "predecessor_result_refs": _compact_strings(
            projection.get("predecessor_result_refs"), limit=16, item_limit=128,
        ),
    }
    if kind == "implementation":
        compact = {
            **common,
            "relevant_predecessor_conclusions": _compact_strings(
                projection.get("relevant_predecessor_conclusions"),
                limit=_COMPACT_CONCLUSIONS,
                item_limit=_COMPACT_CONCLUSION_CHARS,
            ),
            "known_unresolved_findings": _compact_strings(
                projection.get("known_unresolved_findings"),
                limit=_COMPACT_FINDINGS,
                item_limit=_COMPACT_FINDING_CHARS,
            ),
        }
    elif kind == "qa":
        compact = {
            **common,
            "implemented_behavior": _compact_strings(
                projection.get("implemented_behavior"),
                limit=_COMPACT_CONCLUSIONS,
                item_limit=_COMPACT_CONCLUSION_CHARS,
            ),
            "files_changed": _compact_strings(
                projection.get("files_changed"), limit=_COMPACT_PATHS, item_limit=_COMPACT_PATH_CHARS,
            ),
            "verification_already_executed": _compact_strings(
                projection.get("verification_already_executed"),
                limit=_COMPACT_CHECKS,
                item_limit=_COMPACT_CHECK_CHARS,
            ),
            "known_unresolved_findings": _compact_strings(
                projection.get("known_unresolved_findings"),
                limit=_COMPACT_FINDINGS,
                item_limit=_COMPACT_FINDING_CHARS,
            ),
        }
    elif kind == "review":
        compact = {
            **common,
            "change_inventory": _compact_strings(
                projection.get("change_inventory"), limit=_COMPACT_PATHS, item_limit=_COMPACT_PATH_CHARS,
            ),
            "verification_evidence": _compact_strings(
                projection.get("verification_evidence"),
                limit=_COMPACT_CHECKS,
                item_limit=_COMPACT_CHECK_CHARS,
            ),
            "open_findings": _compact_strings(
                projection.get("open_findings"), limit=_COMPACT_FINDINGS, item_limit=_COMPACT_FINDING_CHARS,
            ),
        }
    else:
        compact = {
            **common,
            "relevant_conclusions": _compact_strings(
                projection.get("relevant_conclusions"),
                limit=_COMPACT_CONCLUSIONS,
                item_limit=_COMPACT_CONCLUSION_CHARS,
            ),
        }
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


class HandoffCompiler:
    """Project only the fields a target role needs; never attach raw reports."""

    def __init__(self, context_compiler: ContextCompiler | None = None) -> None:
        self._context_compiler = context_compiler or ContextCompiler()

    def build(
        self,
        canonical: Mapping[str, Any],
        *,
        target_profile: str,
        target_gate: str | None = None,
        compact: bool = False,
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
        changed = _unique([path for item in predecessors for path in item.get("changed_files", [])], limit=64)
        checks = _unique([check for item in predecessors for check in item.get("checks", [])], limit=24)
        unresolved = _unique([finding for item in predecessors for finding in item.get("unresolved_findings", [])], limit=24)
        conclusions = _unique([item.get("conclusion", "") for item in predecessors], limit=16)

        if kind == "implementation":
            projection = {
                **common,
                "requirements": task.get("requirements", []),
                "decisions": context.get("decisions", []),
                "assigned_scope": context.get("assignment", {}).get("scope", []),
                "allowed_paths": context.get("assignment", {}).get("allowed_paths", []),
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "verification_requirements": task.get("verification_requirements", []),
                "relevant_predecessor_conclusions": conclusions,
                "known_unresolved_findings": unresolved,
            }
        elif kind == "qa":
            projection = {
                **common,
                "implemented_behavior": conclusions,
                "files_changed": changed,
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "verification_requirements": task.get("verification_requirements", []),
                "verification_already_executed": checks,
                "known_unresolved_findings": unresolved,
                "risk_areas": unresolved or changed[:12],
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
        return _compact_projection(projection, kind=kind) if compact else projection


def build_handoff(
    canonical: Mapping[str, Any],
    *,
    target_profile: str,
    target_gate: str | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    """Convenience functional API for runtime facade integration."""
    return HandoffCompiler().build(
        canonical,
        target_profile=target_profile,
        target_gate=target_gate,
        compact=compact,
    )


def build_dispatch_handoff(package: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Build the bounded handoff used by immutable dispatch and recovery."""
    canonical = dispatch_canonical_state(package, profile)
    return build_handoff(
        canonical,
        target_profile=profile,
        target_gate=str(package.get("gate") or "") or None,
        compact=True,
    )
