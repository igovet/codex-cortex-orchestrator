"""Compile coordinator-authored semantic workers into executable assignments."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from cortex_runtime.verification_contract import required_verification_kinds


_WAVE_REF_PATTERN = re.compile(r"^wave-(0*[1-9][0-9]*)$")


def acceptance_contract_digest(
    acceptance_criteria: Sequence[str],
    verification: Sequence[str],
    *,
    server_acceptance_obligations: Sequence[str] = (),
    server_verification_obligations: Sequence[str] = (),
) -> str:
    """Digest the exact caller/server result-contract boundary without rewriting it."""
    fields = {
        "acceptance_criteria": list(acceptance_criteria),
        "verification": list(verification),
        "server_acceptance_obligations": list(server_acceptance_obligations),
        "server_verification_obligations": list(server_verification_obligations),
    }
    for name, values in fields.items():
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError(f"{name} must contain exact non-empty strings")
    encoded = json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def effective_result_contract(
    acceptance_criteria: Sequence[str],
    verification: Sequence[str],
    *,
    server_acceptance_obligations: Sequence[str] = (),
    server_verification_obligations: Sequence[str] = (),
) -> tuple[list[str], list[str]]:
    """Append server obligations after exact caller items, deduplicating stably."""
    acceptance = list(dict.fromkeys([
        *acceptance_criteria, *server_acceptance_obligations,
    ]))
    checks = list(dict.fromkeys([
        *verification, *server_verification_obligations,
    ]))
    acceptance_contract_digest(
        acceptance_criteria, verification,
        server_acceptance_obligations=server_acceptance_obligations,
        server_verification_obligations=server_verification_obligations,
    )
    return acceptance, checks


def _is_canonical_wave_ref(value: str) -> bool:
    match = _WAVE_REF_PATTERN.fullmatch(value)
    return bool(match and value == f"wave-{int(match.group(1)):02d}")


def compiled_wave_execution_order(
    waves: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Validate immutable occurrence identity and return plan-list order.

    ``wave_index`` is a server-issued occurrence identifier, not a sortable
    execution position. Product-rework insertion may place a newly issued
    occurrence before an older pending occurrence without changing either
    identity. The canonical plan list is therefore the only execution order.
    """
    refs: list[str] = []
    indices: set[int] = set()
    phase_refs: set[str] = set()
    for wave in waves:
        wave_ref = str(wave.get("wave_ref") or "").strip()
        wave_index = wave.get("wave_index")
        phase_ref = str(wave.get("phase_ref") or "").strip()
        match = _WAVE_REF_PATTERN.fullmatch(wave_ref)
        if (
            not _is_canonical_wave_ref(wave_ref)
            or str(wave.get("wave_id") or "") != wave_ref
            or isinstance(wave_index, bool)
            or not isinstance(wave_index, int)
            or wave_index < 1
            or int(match.group(1)) != wave_index
            or wave_ref != f"wave-{wave_index:02d}"
            or phase_ref != f"phase-{wave_index:04d}"
            or wave_ref in refs
            or wave_index in indices
            or phase_ref in phase_refs
        ):
            raise ValueError("compiled plan contains invalid or duplicated wave identity")
        refs.append(wave_ref)
        indices.add(wave_index)
        phase_refs.add(phase_ref)
    return refs


def compiled_wave_execution_position(
    waves: Sequence[Mapping[str, Any]], wave_ref: str,
) -> int:
    """Return the one-based execution position of an immutable occurrence."""
    refs = compiled_wave_execution_order(waves)
    target = str(wave_ref or "").strip()
    if target not in refs:
        raise ValueError("compiled wave is outside the canonical execution order")
    return refs.index(target) + 1


def next_compiled_wave_index(
    waves: Sequence[Mapping[str, Any]], *, count: int = 1,
) -> list[int]:
    """Allocate collision-free immutable indices for new occurrences."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("compiled wave allocation count must be positive")
    compiled_wave_execution_order(waves)
    highest = max((int(wave["wave_index"]) for wave in waves), default=0)
    return list(range(highest + 1, highest + count + 1))


def state_occurrence_execution_ranks(state: Mapping[str, Any]) -> dict[str, int]:
    """Return immutable occurrence refs mapped to canonical list positions."""
    occurrences = state.get("orchestration_wave_occurrences")
    if not isinstance(occurrences, Sequence) or isinstance(
        occurrences, (str, bytes, bytearray)
    ):
        raise ValueError("canonical occurrence execution order is unavailable")
    refs: list[str] = []
    for occurrence in occurrences:
        if not isinstance(occurrence, Mapping):
            raise ValueError("canonical occurrence execution order is invalid")
        wave_ref = str(
            occurrence.get("wave_ref") or occurrence.get("wave_id") or ""
        ).strip()
        match = _WAVE_REF_PATTERN.fullmatch(wave_ref)
        wave_index = occurrence.get("wave_index")
        if (
            not _is_canonical_wave_ref(wave_ref)
            or isinstance(wave_index, bool)
            or not isinstance(wave_index, int)
            or int(match.group(1)) != wave_index
            or wave_ref != f"wave-{wave_index:02d}"
            or str(occurrence.get("phase_ref") or "") != f"phase-{wave_index:04d}"
            or wave_ref in refs
        ):
            raise ValueError("canonical occurrence execution identity is invalid")
        refs.append(wave_ref)
    return {wave_ref: position for position, wave_ref in enumerate(refs, 1)}


def resolve_profile_for_operation(
    requested_profile: str,
    operation_kind: str,
    phase_kind: str,
    profiles: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    """Resolve an executable profile from the canonical capability registry."""
    requested = str(requested_profile or "").strip()
    requested_record = profiles.get(requested)
    if isinstance(requested_record, Mapping) and operation_kind in (
        requested_record.get("operation_kinds") or []
    ) and (operation_kind != "modify" or requested_record.get("sandbox") == "workspace-write"):
        return requested, "requested_profile_compatible"
    compatible: list[tuple[str, Mapping[str, Any]]] = []
    for name, record in profiles.items():
        if not isinstance(record, Mapping):
            continue
        capabilities = record.get("operation_kinds")
        if not isinstance(capabilities, list) or operation_kind not in capabilities:
            continue
        if operation_kind == "modify" and record.get("sandbox") != "workspace-write":
            continue
        compatible.append((str(name), record))
    if not compatible:
        raise ValueError(
            f"no profile can execute operation_kind={operation_kind!r} for phase_kind={phase_kind!r}"
        )
    requested_family = (
        str(requested_record.get("capability_family") or "").strip()
        if isinstance(requested_record, Mapping) else ""
    )
    if requested_family:
        same_family = [
            name for name, record in compatible
            if str(record.get("capability_family") or "").strip() == requested_family
        ]
        if len(same_family) == 1:
            return same_family[0], f"required_{operation_kind}_capability"
        if len(same_family) > 1:
            raise ValueError(
                f"profile capability resolution is ambiguous for family={requested_family!r} "
                f"and operation_kind={operation_kind!r}"
            )
    phase_specialists = [
        name for name, record in compatible
        if isinstance(record.get("gates"), list) and phase_kind in record["gates"]
    ]
    if len(phase_specialists) == 1:
        return phase_specialists[0], f"required_{operation_kind}_capability"
    if len(phase_specialists) > 1:
        raise ValueError(
            f"profile capability resolution is ambiguous for phase_kind={phase_kind!r} "
            f"and operation_kind={operation_kind!r}"
        )
    if any(name == "general" for name, _record in compatible):
        return "general", f"required_{operation_kind}_capability"
    if len(compatible) == 1:
        return compatible[0][0], f"required_{operation_kind}_capability"
    raise ValueError(
        f"profile capability resolution is ambiguous for operation_kind={operation_kind!r}"
    )


def resolve_reliability_fallback_profile(
    operation_kind: str,
    profiles: Mapping[str, Mapping[str, Any]],
) -> str:
    """Return the one registry-owned universal recovery profile.

    Reliability recovery is intentionally distinct from ordinary profile
    normalization.  Reusing :func:`resolve_profile_for_operation` with the
    failed specialist would always return that still-compatible specialist,
    preventing the bounded recovery ladder from ever exercising its universal
    fallback.  The canonical profile registry therefore owns one explicit
    fallback marker per operation.  Missing, incompatible, or duplicate
    ownership fails closed instead of guessing from profile names or aliases.
    """
    operation = str(operation_kind or "").strip()
    if operation not in {"inspect", "modify", "verify", "close"}:
        raise ValueError("reliability fallback operation_kind is unsupported")
    candidates: list[str] = []
    for raw_name, record in profiles.items():
        if not isinstance(record, Mapping):
            continue
        fallback_operations = record.get("reliability_fallback_for")
        if fallback_operations is None:
            continue
        if (
            not isinstance(fallback_operations, list)
            or any(
                not isinstance(item, str)
                or item not in {"inspect", "modify", "verify", "close"}
                for item in fallback_operations
            )
            or len(fallback_operations) != len(set(fallback_operations))
        ):
            raise ValueError("profile reliability fallback registry is invalid")
        if operation not in fallback_operations:
            continue
        capabilities = record.get("operation_kinds")
        if not isinstance(capabilities, list) or operation not in capabilities:
            raise ValueError("reliability fallback profile lacks the required operation capability")
        if operation == "modify" and record.get("sandbox") != "workspace-write":
            raise ValueError("reliability fallback profile lacks workspace-write capability")
        candidates.append(str(raw_name))
    if len(candidates) != 1:
        raise ValueError(
            f"reliability fallback profile resolution is "
            f"{'unavailable' if not candidates else 'ambiguous'} "
            f"for operation_kind={operation!r}"
        )
    return candidates[0]


def reliability_recovery_target(
    source: Mapping[str, Any],
    profiles: Mapping[str, Mapping[str, Any]],
    model_efforts: Mapping[str, Sequence[str]],
    recommended_efforts: Mapping[str, str],
) -> dict[str, str] | None:
    """Compile the next unique bounded recovery stage for one occurrence."""
    operation_kind = str(source.get("operation_kind") or "").strip()
    current_model = str(
        source.get("selected_model") or source.get("model") or ""
    ).strip()
    current_profile = str(
        source.get("resolved_profile")
        or source.get("profile")
        or source.get("agent")
        or ""
    ).strip()
    if current_model not in model_efforts:
        raise ValueError("reliability recovery source model is unsupported")
    if not current_profile or current_profile not in profiles:
        raise ValueError("reliability recovery source profile is unavailable")
    fallback_profile = resolve_reliability_fallback_profile(operation_kind, profiles)
    model_order = tuple(model_efforts)
    current_model_index = model_order.index(current_model)
    next_model = (
        model_order[current_model_index + 1]
        if current_model_index + 1 < len(model_order) else None
    )
    if next_model is not None:
        target_profile = current_profile
        stage = "model_escalation_" + next_model.rsplit("-", 1)[-1]
    elif current_profile != fallback_profile:
        target_profile = fallback_profile
        next_model = model_order[-1]
        stage = "universal_profile_fallback"
    else:
        return None
    reliability_effort_order = tuple(dict.fromkeys(
        effort
        for efforts in model_efforts.values()
        for effort in efforts
    ))
    current_effort = str(
        source.get("selected_reasoning_effort")
        or source.get("reasoning_effort")
        or ""
    ).strip()
    if current_effort not in reliability_effort_order:
        raise ValueError("reliability recovery source reasoning effort is unsupported")
    allowed_target_efforts = tuple(model_efforts.get(next_model) or ())
    if not allowed_target_efforts or any(
        effort not in reliability_effort_order for effort in allowed_target_efforts
    ):
        raise ValueError("reliability recovery target has no canonical effort policy")
    if current_effort in allowed_target_efforts:
        target_effort = current_effort
        effort_resolution_reason = "requested_effort_preserved"
    else:
        target_effort = str(recommended_efforts.get(next_model) or "")
        if target_effort not in allowed_target_efforts:
            raise ValueError("reliability recovery target default effort is unavailable")
        effort_resolution_reason = "target_model_default_required"
    return {
        "stage": stage,
        "model": next_model,
        "reasoning_effort": target_effort,
        "effort_resolution_reason": effort_resolution_reason,
        "profile": target_profile,
        "fallback_profile": fallback_profile,
    }


def compile_assignment(
    semantic: Mapping[str, Any],
    *,
    profiles: Mapping[str, Mapping[str, Any]],
    operation_kinds: Mapping[str, Any],
    phase_kind: str,
    phase_ref: str,
    wave_ref: str,
    wave_index: int,
    predecessor_wave_refs: Sequence[str],
    route: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one internally consistent executable assignment or reject it.

    The coordinator remains authoritative for phase semantics, worker profile,
    model, and effort. Cortex issues occurrence identity and verifies that the
    selected profile and native route can actually execute the requested
    operation before any attempt, briefing, or dispatch is persisted.
    """
    canonical_operation_kinds = tuple(str(item) for item in operation_kinds)
    if canonical_operation_kinds != ("inspect", "modify", "verify", "close"):
        raise ValueError("canonical operation_kinds registry is invalid")
    operation_kind = str(semantic.get("operation_kind") or "").strip()
    if operation_kind not in canonical_operation_kinds:
        raise ValueError("worker operation_kind must be inspect, modify, verify, or close")
    requested_profile = str(
        semantic.get("requested_profile") or semantic.get("agent") or semantic.get("profile") or ""
    ).strip()
    profile_name, resolution_reason = resolve_profile_for_operation(
        requested_profile, operation_kind, phase_kind, profiles,
    )
    profile = profiles.get(profile_name)
    if not isinstance(profile, Mapping):
        raise ValueError("compiled assignment profile is unavailable")
    profile_operation_kinds = profile.get("operation_kinds")
    if (
        not isinstance(profile_operation_kinds, list)
        or any(not isinstance(item, str) or item not in canonical_operation_kinds for item in profile_operation_kinds)
        or len(profile_operation_kinds) != len(set(profile_operation_kinds))
    ):
        raise ValueError(f"profile {profile_name!r} has no valid canonical operation_kinds capability")
    objective = str(semantic.get("objective") or "").strip()
    if not objective:
        raise ValueError("compiled assignment objective must be non-empty")
    model = str(semantic.get("model") or "").strip()
    effort = str(semantic.get("reasoning_effort") or "").strip()
    if not model or not effort:
        raise ValueError("compiled assignment requires coordinator-selected model and reasoning_effort")
    if not phase_ref or not wave_ref or wave_index < 1:
        raise ValueError("compiled assignment requires server-issued phase and wave identity")
    predecessors = [str(item).strip() for item in predecessor_wave_refs]
    if (
        any(not _is_canonical_wave_ref(item) for item in predecessors)
        or wave_ref in predecessors
        or len(predecessors) != len(set(predecessors))
    ):
        raise ValueError(
            "compiled assignment predecessor wave refs must be valid, unique, and exclude self"
        )
    can_write = operation_kind == "modify"
    route_read_only = route.get("read_only")
    if not isinstance(route_read_only, bool):
        raise ValueError("compiled assignment route must resolve an explicit read_only capability")
    if can_write and route_read_only:
        raise ValueError("mutating assignment resolved to a read-only native route")
    if not can_write and not route_read_only:
        raise ValueError("non-mutating assignment resolved to a writable native route")
    if can_write and str(profile.get("sandbox") or "") != "workspace-write":
        raise ValueError("mutating assignment profile has no workspace-write capability")
    return {
        **dict(semantic),
        "agent": profile_name,
        "profile": profile_name,
        "requested_profile": requested_profile,
        "resolved_profile": profile_name,
        "resolution_reason": resolution_reason,
        "phase_kind": phase_kind,
        "phase_ref": phase_ref,
        "wave_ref": wave_ref,
        "wave_index": wave_index,
        "operation_kind": operation_kind,
        "predecessor_wave_refs": predecessors,
        "read_only": route_read_only,
        "can_write": can_write,
        "required_verification_kinds": list(
            required_verification_kinds(phase_kind, operation_kind)
        ),
    }
