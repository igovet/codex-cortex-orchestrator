"""Compile bounded worker context from canonical Cortex state.

This module deliberately has no dependency on the MCP facade, exported files, or
worker-authored transport shape. Callers pass the already-authoritative task,
attempt, receipt, and semantic-result records that they loaded from SQLite.
The result is safe to embed in an immutable dispatch briefing, but is not a
new source of task truth.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


CONTEXT_SCHEMA = "cortex/compiled-worker-context/v1"
MAX_TEXT = 1_200
MAX_OBJECTIVE = 2_400
MAX_ITEMS = 12
MAX_PATHS = 48
MAX_PREDECESSORS = 16


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


def _domain_text(value: object, *, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"canonical {label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"canonical {label} must not be empty")
    if len(text) > limit:
        raise ValueError(f"canonical {label} exceeds its bounded length")
    return text


def _domain_values(value: object, *, label: str, limit: int, item_limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"canonical {label} must be an array of strings")
    values = value
    if len(values) > limit:
        raise ValueError(f"canonical {label} has too many records")
    result: list[str] = []
    for raw in values:
        text = _domain_text(raw, label=label, limit=item_limit)
        if text not in result:
            result.append(text)
    return tuple(result)


def _domain_decisions(value: object) -> tuple[Decision, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("canonical decisions must be an array")
    if len(value) > 8:
        raise ValueError("canonical decisions has too many records")
    decisions: list[Decision] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("canonical decision must be an object")
        # Durable decisions use explicit English canonical fields.  Localized
        # display values are never compiler input.
        question_value = raw.get("question_en")
        answer_value = raw.get("answer_en")
        question = _domain_text(question_value, label="decision question", limit=500)
        answer = _domain_text(answer_value, label="decision answer", limit=800)
        decision = Decision(question=question, answer=answer)
        if decision not in decisions:
            decisions.append(decision)
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
            "question": _text(payload.get("question"), 500),
            "answer": _text(payload.get("answer"), 800),
        }
        compact = {key: value for key, value in item.items() if value}
        if compact and compact not in projected:
            projected.append(compact)
        if len(projected) >= 16:
            break
    return projected


def context_domain_from_canonical(canonical: Mapping[str, Any]) -> ContextDomain:
    """Validate the only accepted raw-to-typed compiler boundary."""
    if not isinstance(canonical, Mapping):
        raise ValueError("canonical context must be an object")
    task = _mapping(canonical.get("task"))
    state = _mapping(canonical.get("state"))
    user_request_value = task.get("user_request_projection") or task.get("user_request")
    intent = TaskIntent(_domain_text(user_request_value, label="task intent", limit=MAX_OBJECTIVE))
    requirements = tuple(
        Requirement(text) for text in _domain_values(
            task.get("requirements") or task.get("task_requirements"),
            label="requirements", limit=MAX_ITEMS, item_limit=600,
        )
    )
    constraints = tuple(
        Constraint(text) for text in _domain_values(
            task.get("constraints") or task.get("task_constraints"),
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
        canonical.get("resolved_user_decisions") or state.get("resolved_user_decisions") or state.get("decisions")
    ))
    for event in _compiler_visible_events(canonical):
        if event.get("event_type") != "decision_resolved":
            continue
        decision = Decision(
            question=_domain_text(event.get("question"), label="decision question", limit=500),
            answer=_domain_text(event.get("answer"), label="decision answer", limit=800),
        )
        if decision not in decisions:
            decisions.append(decision)
    finding_texts: list[str] = []
    for predecessor in _sequence(canonical.get("predecessor_results")):
        source = _mapping(predecessor)
        for raw in [*_sequence(source.get("unresolved_findings")), *_sequence(source.get("findings"))]:
            detail = _mapping(raw)
            candidate = detail.get("summary") or detail.get("message") or raw
            if isinstance(candidate, str):
                text = candidate.strip()[:500]
                if text and text not in finding_texts:
                    finding_texts.append(text)
            if len(finding_texts) >= 8:
                break
        if len(finding_texts) >= 8:
            break
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
    """Return a bounded scalar without interpreting worker-controlled prose."""
    if value is None:
        return ""
    return str(value).strip()[:limit]


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
        if rendered and rendered not in result:
            result.append(rendered)
        if len(result) >= limit:
            break
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
    """Project canonical predecessor results; never embed a second transport."""
    values = _sequence(value)
    facts: list[dict[str, Any]] = []
    for raw in values:
        source = _mapping(raw)
        result_ref = _text(source.get("result_ref"), 128)
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
            "result_ref": result_ref or None,
            "attempt_id": attempt_id or None,
            "gate": _text(source.get("gate") or source.get("phase"), 80) or None,
            "profile": _text(source.get("profile"), 120) or None,
            "semantic_source": "attempt_result",
            "conclusion": summary or None,
            "changed_files": _strings(source.get("changed_files"), limit=24, item_limit=300),
            "checks": _strings(source.get("checks") or source.get("verification"), limit=8, item_limit=500),
            "unresolved_findings": [entry for entry in findings[:8] if entry],
        }
        facts.append({key: value for key, value in item.items() if value not in (None, [], "")})
        if len(facts) >= MAX_PREDECESSORS:
            break
    return facts


class ContextCompiler:
    """Build a target-independent, bounded context from canonical records.

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

        context = {
            "schema": CONTEXT_SCHEMA,
            "task": {
                "user_request": domain.intent.text,
                "requirements": [item.text for item in domain.requirements],
                "constraints": [item.text for item in domain.constraints],
                "acceptance_criteria": [item.text for item in domain.acceptance_criteria],
                "verification_requirements": [item.text for item in domain.verification_requirements],
            },
            "assignment": {
                "attempt_id": _text(attempt.get("attempt_id"), 128) or None,
                "phase": gate or None,
                "profile": profile or None,
                "scope": _strings(attempt.get("task_scope") or task.get("scope") or task.get("task_scope"), limit=MAX_ITEMS, item_limit=500),
                "allowed_paths": _strings(attempt.get("allowed_paths") or task.get("allowed_paths"), limit=MAX_PATHS, item_limit=300),
            },
            "decisions": [
                {"question": item.question, "answer": item.answer}
                for item in domain.decisions
            ],
            "event_transitions": _compiler_visible_events(canonical),
            "findings": [item.summary for item in domain.findings],
            "predecessor_facts": predecessors,
            "predecessor_selection": {
                "available": max(available_predecessors, len(predecessors)),
                "selected": len(predecessors),
                "limit": MAX_PREDECESSORS,
                "truncated": available_predecessors > len(predecessors),
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
        "user_request_projection": _mapping(package.get("user_intent")).get("projection") or package.get("task_user_request"),
        "requirements": package.get("task_requirements"),
        "constraints": package.get("task_constraints") or package.get("constraints"),
        "acceptance_criteria": package.get("task_acceptance_criteria") or package.get("acceptance_criteria"),
        "verification": package.get("task_verification") or package.get("verification"),
        "scope": package.get("task_scope"),
        "allowed_paths": package.get("allowed_paths"),
    }
    attempt = {
        "attempt_id": package.get("attempt_id"),
        "gate": package.get("gate"),
        "profile": profile,
        "allowed_paths": package.get("allowed_paths"),
        "task_scope": package.get("task_scope"),
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
    It retains only bounded canonical fields required by the target profile.
    """
    return ContextCompiler().compile(
        dispatch_canonical_state(package, profile),
        target_profile=profile,
    )


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_empty(item) for key, item in value.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value
