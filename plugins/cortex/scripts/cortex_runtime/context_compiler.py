"""Compile worker context from canonical Cortex state.

This module deliberately has no dependency on the MCP facade, exported files, or
worker-authored transport shape. Callers pass the already-authoritative task,
attempt, receipt, and semantic-result records that they loaded from SQLite.
The result is safe to embed in an immutable dispatch briefing, but is not a
new source of task truth. Prompt volume recommendations are advisory; this
compiler never rejects, clips, or drops canonical content because of bytes.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


CONTEXT_SCHEMA = "cortex/compiled-worker-context/v1"
# These names remain as prompt-authoring hints for callers that want to choose
# a concise presentation. They are not backend validity or storage limits.
MAX_TEXT = 1_200
MAX_OBJECTIVE = 2_400
MAX_ITEMS = 12
MAX_PATHS = 48
MAX_PREDECESSORS = 16
MAX_REQUIREMENT_ITEM = 600


@dataclass(frozen=True)
class TaskIntent:
    """Validated server-owned statement of the task's requested outcome."""

    text: str


@dataclass(frozen=True)
class Requirement:
    text: str


@dataclass(frozen=True)
class Constraint:
    text: str


@dataclass(frozen=True)
class Decision:
    question: str
    answer: str


@dataclass(frozen=True)
class AcceptanceCriterion:
    text: str


@dataclass(frozen=True)
class VerificationRequirement:
    text: str


@dataclass(frozen=True)
class Finding:
    summary: str


@dataclass(frozen=True)
class ContextDomain:
    """Typed canonical boundary consumed by ContextCompiler.

    The dispatch adapter creates these records from the task's durable state.
    This intentionally keeps arbitrary worker payloads on the far side of the
    boundary: a worker result contributes only selected semantic findings.
    """

    intent: TaskIntent
    requirements: tuple[Requirement, ...]
    constraints: tuple[Constraint, ...]
    decisions: tuple[Decision, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    verification_requirements: tuple[VerificationRequirement, ...]
    findings: tuple[Finding, ...]


def _domain_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"canonical {label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"canonical {label} must not be empty")
    return text


def _durable_question_text(value: object, *, label: str) -> str:
    """Accept one stored question/answer exactly as SQLite returned it.

    Unlike task metadata, a durable question pair is ordinary user text.  Its
    boundary whitespace and Unicode code points are part of the user's answer
    and must not be normalized, translated, truncated, or treated as a legacy
    display field while compiling a replacement worker's context.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"canonical {label} must be a non-empty string")
    return value


def _domain_values(value: object, *, label: str, limit: int, item_limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"canonical {label} must be an array of strings")
    values = value
    # Do not use a prompt cardinality limit as a ledger validity limit.  The
    # returned domain is projected later with a source reference.
    del limit
    return tuple(_domain_text(raw, label=label) for raw in values)


def _validated_requirement(value: object) -> str:
    """Validate one current canonical requirement record.

    Requirement records are accepted only in the current normalized string
    shape.  In particular, this boundary does not preserve or reinterpret
    pre-canonical row text (including boundary whitespace) as a migration.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("canonical requirements must be normalized non-empty strings")
    return value


def _domain_requirements(value: object) -> tuple[str, ...]:
    """Return every canonical requirement as one unchanged typed record."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("canonical requirements must be an array of strings")
    records: list[str] = []
    for raw in value:
        text = _validated_requirement(raw)
        # A requirement is a durable semantic unit, not a prompt-sized
        # fragment.  The scoped immutable briefing reader handles volume, so
        # a successor must receive this exact record rather than a server-side
        # split projection.
        records.append(text)
    return tuple(records)


def _domain_decisions(value: object) -> tuple[Decision, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("canonical decisions must be an array")
    decisions: list[Decision] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("canonical decision must be an object")
        question_value = raw.get("question_text")
        answer_value = raw.get("answer_text")
        question = _durable_question_text(question_value, label="decision question_text")
        answer = _durable_question_text(answer_value, label="decision answer_text")
        decisions.append(Decision(question=question, answer=answer))
    return tuple(decisions)


def _compiler_visible_events(canonical: Mapping[str, Any]) -> list[dict[str, str]]:
    """Project only server-owned question/decision transitions for context."""
    raw_events: list[object] = list(_sequence(canonical.get("attempt_events")))
    for predecessor in _sequence(canonical.get("predecessor_results")):
        raw_events.extend(_sequence(_mapping(predecessor).get("semantic_events")))
    projected: list[dict[str, str]] = []
    for raw in raw_events:
        event = _mapping(raw)
        if event.get("actor") != "cortex" or event.get("event_type") not in {
            "question_created", "question_answered", "decision_resolved",
        }:
            continue
        payload = _mapping(event.get("payload"))
        item = {
            "event_type": _text(event.get("event_type"), 64),
            "question_ref": _text(payload.get("question_ref"), 128),
            "question_text": payload.get("question_text") if isinstance(payload.get("question_text"), str) else "",
            "answer_text": payload.get("answer_text") if isinstance(payload.get("answer_text"), str) else "",
        }
        compact = {key: value for key, value in item.items() if value}
        if compact:
            projected.append(compact)
    return projected


def context_domain_from_canonical(canonical: Mapping[str, Any]) -> ContextDomain:
    """Validate the only accepted raw-to-typed compiler boundary."""
    if not isinstance(canonical, Mapping):
        raise ValueError("canonical context must be an object")
    task = _mapping(canonical.get("task"))
    state = _mapping(canonical.get("state"))
    user_request_value = task.get("user_request")
    intent = TaskIntent(_domain_text(user_request_value, label="task intent"))
    requirements = tuple(
        Requirement(text) for text in _domain_requirements(
            task.get("requirements")
        )
    )
    constraints = tuple(
        Constraint(text) for text in _domain_values(
            task.get("constraints"),
            label="constraints", limit=MAX_ITEMS, item_limit=700,
        )
    )
    acceptance_criteria = tuple(
        AcceptanceCriterion(text) for text in _domain_values(
            task.get("acceptance_criteria"), label="acceptance criteria", limit=MAX_ITEMS, item_limit=700,
        )
    )
    verification_requirements = tuple(
        VerificationRequirement(text) for text in _domain_values(
            task.get("verification") or task.get("verification_requirements"),
            label="verification requirements", limit=MAX_ITEMS, item_limit=700,
        )
    )
    decisions = list(_domain_decisions(
        canonical.get("resolved_user_decisions")
    ))
    for event in _compiler_visible_events(canonical):
        if event.get("event_type") != "decision_resolved":
            continue
        decisions.append(Decision(
            question=_durable_question_text(event.get("question_text"), label="decision question_text"),
            answer=_durable_question_text(event.get("answer_text"), label="decision answer_text"),
        ))
    finding_texts: list[str] = []
    for predecessor in _sequence(canonical.get("predecessor_results")):
        source = _mapping(predecessor)
        for raw in [*_sequence(source.get("unresolved_findings")), *_sequence(source.get("findings"))]:
            detail = _mapping(raw)
            candidate = detail.get("summary") or detail.get("message") or raw
            if isinstance(candidate, str):
                text = candidate.strip()
                if text:
                    finding_texts.append(text)
    return ContextDomain(
        intent=intent,
        requirements=requirements,
        constraints=constraints,
        decisions=tuple(decisions),
        acceptance_criteria=acceptance_criteria,
        verification_requirements=verification_requirements,
        findings=tuple(Finding(summary=text) for text in finding_texts),
    )


def _text(value: object, limit: int = MAX_TEXT) -> str:
    """Return a complete scalar without interpreting worker-controlled prose."""
    if value is None:
        return ""
    return _utf8_prefix(str(value).strip(), limit)


def _utf8_prefix(value: str, maximum_bytes: int) -> str:
    """Keep complete canonical text; limits are prompt guidance only."""
    del maximum_bytes
    return value


def _project_texts(
    values: Sequence[str],
    *,
    item_bytes: int,
    item_limit: int,
    source: Mapping[str, Any] | None,
) -> tuple[list[str], dict[str, Any] | None]:
    """Return every complete text item without a backend transport budget."""
    del item_bytes, item_limit, source
    return list(values), None


def _strings(value: object, *, limit: int = MAX_ITEMS, item_limit: int = MAX_TEXT) -> list[str]:
    values: Sequence[object]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = ()
    result: list[str] = []
    for item in values:
        rendered = _text(item, item_limit)
        if rendered:
            result.append(rendered)
    del limit
    return result


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _nonnegative_int(value: object, default: int) -> int:
    """Return a safe count from a server-owned selection record."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _bounded_facts(value: object) -> list[dict[str, Any]]:
    """Project canonical predecessor results without a content-volume gate."""
    values = _sequence(value)
    facts: list[dict[str, Any]] = []
    for raw in values:
        source = _mapping(raw)
        result_ref = _text(source.get("attempt_result_ref"), 128)
        attempt_id = _text(source.get("attempt_id") or source.get("producer_attempt_id"), 128)
        summary = _text(source.get("summary") or source.get("conclusion"), MAX_TEXT)
        if not (result_ref or attempt_id or summary):
            continue
        finding_values = [*_sequence(source.get("unresolved_findings")), *_sequence(source.get("findings"))]
        findings: list[str] = []
        for item in finding_values:
            fact = _mapping(item)
            findings.append(_text(fact.get("summary") or fact.get("message") or item, 500))
        item = {
            "attempt_result_ref": result_ref or None,
            "attempt_id": attempt_id or None,
            "gate": _text(source.get("gate") or source.get("phase"), 80) or None,
            "profile": _text(source.get("profile"), 120) or None,
            "semantic_source": "attempt_result",
            "conclusion": summary or None,
            "changed_files": _strings(source.get("changed_files"), limit=24, item_limit=300),
            "checks": _strings(source.get("checks") or source.get("verification"), limit=8, item_limit=500),
            "unresolved_findings": [entry for entry in findings if entry],
        }
        facts.append({key: value for key, value in item.items() if value not in (None, [], "")})
    return facts


class ContextCompiler:
    """Build a target-independent context from canonical records.

    ``canonical`` is intentionally a small adapter boundary.  The runtime
    should populate it from SQLite/task state, not from an arbitrary previous
    worker payload. In particular, ``predecessor_results`` must be semantic
    server records selected after receipt validation.
    """

    def compile(self, canonical: Mapping[str, Any], *, target_profile: str, target_gate: str | None = None) -> dict[str, Any]:
        if not isinstance(canonical, Mapping):
            raise ValueError("canonical context must be an object")
        domain = context_domain_from_canonical(canonical)
        task = _mapping(canonical.get("task"))
        attempt = _mapping(canonical.get("attempt"))
        gate = _text(target_gate or attempt.get("gate") or canonical.get("gate"), 80)
        profile = _text(target_profile or attempt.get("profile"), 120)
        predecessors = _bounded_facts(canonical.get("predecessor_results"))
        selection = _mapping(canonical.get("predecessor_selection"))
        available_predecessors = _nonnegative_int(selection.get("available"), len(predecessors))
        receipts = _mapping(canonical.get("read_receipts"))
        briefing_receipt = _mapping(receipts.get("briefing"))
        predecessor_receipts = receipts.get("predecessors")
        receipt_refs = _strings(predecessor_receipts, limit=MAX_PREDECESSORS, item_limit=128)
        contract = _mapping(task.get("task_contract") or canonical.get("task_contract"))
        requirements, requirements_projection = _project_texts(
            [item.text for item in domain.requirements],
            item_bytes=MAX_REQUIREMENT_ITEM, item_limit=MAX_ITEMS, source=contract,
        )
        constraints, constraints_projection = _project_texts(
            [item.text for item in domain.constraints],
            item_bytes=700, item_limit=MAX_ITEMS, source=contract,
        )
        acceptance, acceptance_projection = _project_texts(
            [item.text for item in domain.acceptance_criteria],
            item_bytes=700, item_limit=MAX_ITEMS, source=contract,
        )
        verification, verification_projection = _project_texts(
            [item.text for item in domain.verification_requirements],
            item_bytes=700, item_limit=MAX_ITEMS, source=contract,
        )
        intent = _utf8_prefix(domain.intent.text, MAX_OBJECTIVE)
        intent_projection: dict[str, Any] | None = None
        if intent != domain.intent.text:
            intent_projection = {
                "total_bytes": len(domain.intent.text.encode("utf-8")),
                "selected_bytes": len(intent.encode("utf-8")),
                "truncated": True,
            }
            for key in ("artifact_ref", "digest_sha256", "artifact_path", "byte_size"):
                if contract.get(key) not in (None, ""):
                    intent_projection[key] = contract[key]
        task_projection = {
            key: value for key, value in {
                "user_request": intent_projection,
                "requirements": requirements_projection,
                "constraints": constraints_projection,
                "acceptance_criteria": acceptance_projection,
                "verification_requirements": verification_projection,
            }.items() if value
        }
        decision_values = list(domain.decisions)
        rendered_decisions = [
            {
                "question": _utf8_prefix(item.question, 500),
                "answer": _utf8_prefix(item.answer, 800),
            }
            for item in decision_values
        ]

        context = {
            "schema": CONTEXT_SCHEMA,
            "task": {
                "user_request": intent,
                "requirements": requirements,
                "constraints": constraints,
                "acceptance_criteria": acceptance,
                "verification_requirements": verification,
                "projection": task_projection or None,
            },
            "assignment": {
                "attempt_id": _text(attempt.get("attempt_id"), 128) or None,
                "phase": gate or None,
                "profile": profile or None,
                "scope": _strings(attempt.get("scope") or task.get("scope"), limit=MAX_ITEMS, item_limit=500),
                "allowed_paths": _strings(attempt.get("allowed_paths") or task.get("allowed_paths"), limit=MAX_PATHS, item_limit=300),
            },
            "decisions": rendered_decisions,
            "event_transitions": _compiler_visible_events(canonical),
            "findings": [item.summary for item in domain.findings],
            "predecessor_facts": predecessors,
            "predecessor_selection": {
                "available": max(available_predecessors, len(predecessors)),
                "selected": len(predecessors),
                "truncated": False,
            },
            "server_receipts": {
                "briefing_read": bool(briefing_receipt),
                "predecessor_result_refs_read": receipt_refs,
            },
        }
        return _drop_empty(context)


def dispatch_canonical_state(package: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Adapt one durable dispatch package to the compiler's canonical seam.

    ``predecessor_results`` is populated by the production dispatch service
    from AttemptResult/AttemptEvent rows. It contains no worker-authored body
    and accepts no obsolete result-registry fallback.
    """
    task = {
        "user_request": package.get("user_request"),
        "requirements": package.get("requirements"),
        "constraints": package.get("constraints"),
        "acceptance_criteria": package.get("acceptance_criteria"),
        "verification": package.get("verification"),
        "scope": package.get("scope"),
        "allowed_paths": package.get("allowed_paths"),
        # This descriptor is deliberately metadata only.  The complete task
        # contract is an immutable artifact; compilers must never recover it
        # by reading a ledger row or treating a compact prompt as canonical.
        "task_contract": _mapping(package.get("task_contract")),
    }
    attempt = {
        "attempt_id": package.get("attempt_id"),
        "gate": package.get("gate"),
        "profile": profile,
        "allowed_paths": package.get("allowed_paths"),
        "scope": package.get("scope"),
    }
    return {
        "task": task,
        "attempt": attempt,
        "state": _mapping(package.get("canonical_state")),
        "resolved_user_decisions": package.get("resolved_user_decisions"),
        "predecessor_results": package.get("predecessor_results"),
        "predecessor_selection": _mapping(package.get("predecessor_selection")),
        "read_receipts": _mapping(package.get("read_receipts")),
    }


def compile_dispatch_context(package: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Compile the dispatch package through the canonical state seam.

    This is the single assembly call used by ``briefings.host_spawn_prompt``.
    It preserves every canonical field required by the target profile.
    """
    return ContextCompiler().compile(
        dispatch_canonical_state(package, profile),
        target_profile=profile,
    )


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_empty(item) for key, item in value.items() if item not in ("", [], {})}
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value
