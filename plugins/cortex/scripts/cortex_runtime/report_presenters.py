"""Report-specific presentation templates for V12 human-readable views.

Reports are persisted as opaque JSON.  Presenters interpret only known
semantic fields and turn them into a :mod:`markdown_document` tree.  Unknown,
legacy, malformed, or future content is always rendered through the safe
heading-free fallback; it can never alter the canonical report lifecycle or
make ``submit_report`` fail.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from cortex_runtime.markdown_document import (
    Block,
    BulletList,
    Callout,
    Checklist,
    CodeBlock,
    Document,
    Finding,
    KeyValue,
    OrderedSteps,
    Paragraph,
    Section,
    Table,
    legacy_lines,
    plain_text,
)


REPORT_VIEW_SCHEMA = "cortex/report-view/v1"
PRESENTATION_KINDS = (
    "discovery",
    "code_review",
    "security_audit",
    "verification",
    "architecture",
    "closure",
)

_MISSING = object()


def _sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    return True


def _normal_key(value: object) -> str:
    return plain_text(value).strip().lower().replace("-", "_").replace(" ", "_")


def _lookup(mapping: Mapping[str, Any], *names: str, default: object = _MISSING) -> object:
    normalized = {_normal_key(key): value for key, value in mapping.items()}
    for name in names:
        value = normalized.get(_normal_key(name), _MISSING)
        if value is not _MISSING and _nonempty(value):
            return value
    return default


def _display(value: object) -> str:
    if isinstance(value, Mapping):
        values = [f"{plain_text(key)}: {_display(item)}" for key, item in value.items() if _nonempty(item)]
        return "; ".join(values) or "(empty)"
    if _sequence(value):
        values = [_display(item) for item in value if _nonempty(item)]
        return "; ".join(values) or "(empty)"
    return plain_text(value).strip() or "(empty)"


def _items(value: object) -> list[object]:
    if value is _MISSING or value is None:
        return []
    if _sequence(value):
        return [item for item in value if _nonempty(item)]
    if isinstance(value, Mapping):
        return [value]
    return [value] if _nonempty(value) else []


def _blocks(value: object, *, style: str = "bullets") -> list[Block]:
    """Turn one semantic value into typed, heading-free blocks."""
    if value is _MISSING or not _nonempty(value):
        return []
    if isinstance(value, Mapping):
        blocks: list[Block] = []
        for key, item in value.items():
            if not _nonempty(item):
                continue
            label = _label(key)
            if _sequence(item):
                children = [_display(child) for child in item if _nonempty(child)]
                if children:
                    blocks.append(BulletList([f"{label}: {child}" for child in children]))
            else:
                blocks.append(KeyValue(label, _display(item) if isinstance(item, Mapping) else item))
        return blocks
    if _sequence(value):
        if style == "ordered":
            return [OrderedSteps(list(value))]
        if style == "checklist":
            return [Checklist(list(value))]
        return [BulletList(list(value))]
    return [Paragraph(value)]


def _label(value: object) -> str:
    text = plain_text(value).strip().replace("_", " ").replace("-", " ")
    return text.title() if text else "Detail"


def _summary_value(content: Mapping[str, Any], envelope: "ReportEnvelope | None") -> object | None:
    if envelope is not None and _nonempty(envelope.summary):
        return envelope.summary
    value = _lookup(content, "summary", "executive_summary", "overview")
    return None if value is _MISSING else value


def _title_value(content: Mapping[str, Any], envelope: "ReportEnvelope | None", default: str) -> str:
    value = envelope.title if envelope is not None else _lookup(content, "title")
    text = plain_text(value if value is not _MISSING and value is not None else default).strip()
    return text or default


def _report_status(report: Mapping[str, Any]) -> str:
    assembly = plain_text(report.get("assembly_state") or "unknown").strip().upper()
    lifecycle = plain_text(report.get("status") or "").strip().upper()
    if assembly == "ABORTED":
        return "ABORTED — NOT FINAL EVIDENCE"
    if lifecycle:
        return f"{assembly} — {lifecycle}"
    return assembly


def _document(
    title: str,
    report: Mapping[str, Any],
    *,
    summary: object | None = None,
    sections: Sequence[Section] = (),
    metadata: Sequence[KeyValue] = (),
) -> Document:
    values = list(metadata)
    if report.get("review_policy") is not None:
        values.append(KeyValue("Review policy", plain_text(report.get("review_policy")).upper()))
    return Document(title, status=_report_status(report), summary=summary, sections=sections, metadata=values)


def _section(title: str, blocks: Sequence[Block]) -> Section | None:
    section = Section(title, blocks)
    return None if section.is_empty() else section


def _append(sections: list[Section], title: str, blocks: Sequence[Block]) -> None:
    value = _section(title, blocks)
    if value is not None:
        sections.append(value)


def _append_contract_evidence(
    sections: list[Section], data: Mapping[str, Any], known: set[str], *, include_standard: bool = True,
    include_deviations: bool = True,
) -> None:
    """Render canonical v2 outcome evidence as named, inert typed sections.

    V2 reports deliberately carry the same evidence shape across result, plan,
    and synthesis reports.  Keeping the rendering here prevents a valid v2
    payload from falling through to a generic JSON-like "details" section and
    makes legacy v1 payloads unchanged when the fields are absent.
    """
    fields: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("Contract coverage", ("contract_coverage",), "bullets"),
        ("Deviations", ("deviations",), "bullets"),
        ("Unresolved items", ("unresolved", "unresolved_items"), "bullets"),
        ("Risks", ("risks",), "bullets"),
        ("Verification", ("verification",), "ordered"),
    )
    if not include_standard:
        fields = fields[:3]
    if not include_deviations:
        fields = tuple(field for field in fields if field[0] != "Deviations")
    for title, names, style in fields:
        value = _lookup(data, *names, default=_MISSING)
        if value is not _MISSING:
            _append(sections, title, _blocks(value, style=style))
            known.update(_normal_key(name) for name in names)


def _finding(item: object, index: int, *, prefix: str = "Finding") -> Finding | None:
    if isinstance(item, Mapping):
        title_value = _lookup(item, "title", "name", "key", "id", "stage", default=f"{prefix} {index}")
        title = _display(title_value)
        if prefix == "Stage" and _lookup(item, "stage", default=_MISSING) is not _MISSING:
            title = f"Stage {index} — {title}"
        known = {
            "title", "name", "key", "id", "stage", "severity", "location", "path", "file",
            "impact", "evidence", "observed", "verification", "recommendation", "next", "action",
            "coverage", "residual_risk", "risk", "conclusion", "disposition", "result",
        }
        details = {
            _label(key): value
            for key, value in item.items()
            if _normal_key(key) not in known and _nonempty(value)
        }
        return Finding(
            title=title or f"{prefix} {index}",
            severity=None if _lookup(item, "severity", default=_MISSING) is _MISSING else _lookup(item, "severity"),
            location=None if _lookup(item, "location", "path", "file", default=_MISSING) is _MISSING else _lookup(item, "location", "path", "file"),
            impact=None if _lookup(item, "impact", default=_MISSING) is _MISSING else _lookup(item, "impact"),
            evidence=None if _lookup(item, "evidence", "observed", "verification", default=_MISSING) is _MISSING else _lookup(item, "evidence", "observed", "verification"),
            recommendation=None if _lookup(item, "recommendation", "next", "action", default=_MISSING) is _MISSING else _lookup(item, "recommendation", "next", "action"),
            coverage=None if _lookup(item, "coverage", default=_MISSING) is _MISSING else _lookup(item, "coverage"),
            residual_risk=None if _lookup(item, "residual_risk", "risk", default=_MISSING) is _MISSING else _lookup(item, "residual_risk", "risk"),
            conclusion=None if _lookup(item, "conclusion", "disposition", "result", default=_MISSING) is _MISSING else _lookup(item, "conclusion", "disposition", "result"),
            details=details,
        )
    if _nonempty(item):
        return Finding(title=f"{prefix} {index}", evidence=item)
    return None


def _findings(value: object, *, prefix: str = "Finding") -> list[Block]:
    result: list[Block] = []
    for index, item in enumerate(_items(value), start=1):
        finding = _finding(item, index, prefix=prefix)
        if finding is not None:
            result.append(finding)
    return result


def _stages(value: object) -> list[Block]:
    if isinstance(value, Mapping):
        value = [value]
    if not _sequence(value):
        return _blocks(value)
    result: list[Block] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, Mapping):
            stage = _finding(item, index, prefix="Stage")
            if stage is not None:
                result.append(stage)
        elif _nonempty(item):
            result.append(Paragraph(item))
    return result


def _known_remainder(content: Mapping[str, Any], known: set[str]) -> Mapping[str, Any]:
    return {key: value for key, value in content.items() if _normal_key(key) not in known and _nonempty(value)}


@dataclass(frozen=True)
class ReportEnvelope:
    """A validated optional renderer-only report presentation envelope."""

    presentation_kind: str | None
    title: object | None
    summary: object | None
    sections: tuple[Section, ...]


def _typed_block(value: object) -> Block | None:
    if isinstance(value, str):
        return Paragraph(value)
    if not isinstance(value, Mapping):
        return None
    kind = _normal_key(value.get("type", value.get("block_type", "")))
    if kind in {"paragraph", "text", "prose"}:
        return Paragraph(value.get("text", value.get("value", "")))
    if kind in {"key_value", "keyvalue", "metadata"}:
        return KeyValue(value.get("label", value.get("key", "Detail")), value.get("value", value.get("text", "")))
    if kind in {"bullet_list", "bullets", "list"}:
        return BulletList(_items(value.get("items", value.get("values", ()))))
    if kind in {"ordered_steps", "ordered", "steps"}:
        return OrderedSteps(_items(value.get("items", value.get("values", ()))))
    if kind in {"checklist", "checks"}:
        return Checklist(_items(value.get("items", value.get("values", ()))))
    if kind in {"table", "grid"}:
        columns = value.get("columns", value.get("headers", ()))
        rows = value.get("rows", ())
        return Table(columns if _sequence(columns) else (), rows if _sequence(rows) else ())
    if kind in {"code_block", "code", "command"}:
        return CodeBlock(value.get("code", value.get("text", value.get("value", ""))), value.get("language"))
    if kind in {"callout", "notice"}:
        return Callout(value.get("text", value.get("value", "")), value.get("label"))
    if kind in {"finding", "review_finding"}:
        return _finding(value, 1)
    return None


def _typed_sections(value: object) -> tuple[Section, ...] | None:
    if not _sequence(value):
        return None
    sections: list[Section] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            return None
        title = item.get("title", item.get("name", f"Section {index}"))
        raw_blocks = item.get("blocks")
        if raw_blocks is None:
            # A section may use a compact typed-field form.  It is still
            # parsed as explicit block types, never as arbitrary JSON keys.
            raw_blocks = []
            for key, kind in (
                ("paragraphs", "paragraph"),
                ("text", "paragraph"),
                ("key_values", "key_value"),
                ("bullets", "bullet_list"),
                ("items", "bullet_list"),
                ("steps", "ordered_steps"),
                ("checklist", "checklist"),
                ("table", "table"),
                ("code", "code_block"),
                ("callout", "callout"),
                ("findings", "finding"),
            ):
                candidate = item.get(key, _MISSING)
                if candidate is _MISSING:
                    continue
                if kind == "finding" and _sequence(candidate):
                    raw_blocks.extend({"type": kind, **(entry if isinstance(entry, Mapping) else {"evidence": entry})} for entry in candidate)
                elif kind in {"paragraph", "key_value"} and _sequence(candidate):
                    raw_blocks.extend({"type": kind, "text": entry} for entry in candidate)
                else:
                    raw_blocks.append({"type": kind, "items": candidate} if kind in {"bullet_list", "ordered_steps", "checklist"} else {"type": kind, "value": candidate})
        if not _sequence(raw_blocks):
            return None
        blocks: list[Block] = []
        for raw in raw_blocks:
            block = _typed_block(raw)
            if block is not None:
                blocks.append(block)
        sections.append(Section(title, blocks))
    return tuple(sections)


def parse_report_envelope(content: object) -> ReportEnvelope | None:
    """Parse only the optional renderer envelope; return ``None`` on fallback."""
    if not isinstance(content, Mapping) or content.get("schema") != REPORT_VIEW_SCHEMA:
        return None
    raw_kind = content.get("presentation_kind", content.get("kind"))
    kind = None if raw_kind is None else _normal_key(raw_kind)
    if kind not in {None, *PRESENTATION_KINDS, "implementation_plan", "implementation_result", "progress", "result"}:
        return None
    sections = _typed_sections(content.get("sections", ()))
    if sections is None:
        return None
    return ReportEnvelope(
        presentation_kind=kind,
        title=content.get("title"),
        summary=content.get("summary"),
        sections=sections,
    )


def merge_report_payloads(contents: Sequence[object]) -> object:
    """Combine immutable chunks into one logical payload before presentation.

    Mapping keys merge recursively, lists concatenate in chunk order, equal
    scalar duplicates collapse, and conflicting scalar values remain visible
    as an ordered list.  Chunk labels are intentionally ignored.
    """
    # Empty chunks are valid storage artifacts during compatibility/migration
    # and should not force an otherwise typed payload through the generic
    # list fallback.
    values = [value for value in contents if value is not None]
    if not values:
        return None
    if len(values) == 1:
        return values[0]

    def merge(left: object, right: object) -> object:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            merged: dict[str, Any] = dict(left)
            for key, value in right.items():
                if key not in merged:
                    merged[key] = value
                else:
                    merged[key] = merge(merged[key], value)
            return merged
        if _sequence(left) and _sequence(right):
            return list(left) + list(right)
        if left == right:
            return left
        if _sequence(left):
            return list(left) + [right]
        return [left, right]

    if all(isinstance(item, Mapping) for item in values):
        result: object = values[0]
        for value in values[1:]:
            result = merge(result, value)
        return result
    if all(_sequence(item) for item in values):
        result_list: list[object] = []
        for value in values:
            result_list.extend(value)  # type: ignore[arg-type]
        return result_list
    if all(isinstance(item, str) for item in values):
        return "\n".join(str(item) for item in values)
    return values


normalize_report_payload = merge_report_payloads


class GenericFallbackPresenter:
    """Safe legacy renderer that never derives headings from arbitrary keys."""

    def present(self, content: object, report: Mapping[str, Any] | None = None) -> Document:
        header = report or {}
        report_type = plain_text(header.get("report_type") or "report").strip().title()
        title = "Plan" if report_type.lower() == "plan" else report_type or "Report"
        blocks: list[Block] = []
        if isinstance(content, Mapping):
            for key, value in content.items():
                if not _nonempty(value):
                    continue
                label = _label(key)
                if _sequence(value):
                    blocks.append(BulletList([f"{label}: {_display(item)}" for item in value if _nonempty(item)]))
                else:
                    blocks.append(KeyValue(label, _display(value) if isinstance(value, Mapping) else value))
        elif _sequence(content):
            blocks.append(BulletList(list(content)))
        elif _nonempty(content):
            blocks.append(Paragraph(content))
        sections = [] if not blocks else [Section("Content", blocks)]
        return _document(title, header, sections=sections)


class PlanPresenter:
    def present(self, content: object, report: Mapping[str, Any]) -> Document:
        envelope = parse_report_envelope(content)
        if envelope is not None and envelope.sections:
            title = _title_value(content, envelope, "Implementation Plan")
            return _document(title, report, summary=_summary_value(content, envelope), sections=envelope.sections)
        data = content if isinstance(content, Mapping) else {}
        summary = _summary_value(data, envelope)
        sections: list[Section] = []
        known: set[str] = {"schema", "presentation_kind", "kind", "title", "summary", "outcome", "overview"}
        approval = _lookup(data, "approval_status", "review_status", "approval", "decision", default=_MISSING)
        if approval is not _MISSING:
            _append(sections, "Approval & status", _blocks(approval))
            known.update({"approval_status", "review_status", "approval", "decision"})
        scope = _lookup(data, "goal", "objective", "scope", "boundaries", "requirements_and_boundaries", default=_MISSING)
        if scope is not _MISSING:
            _append(sections, "Goal & scope", _blocks(scope))
            known.update({"goal", "objective", "scope", "boundaries", "requirements_and_boundaries"})
        stages = _lookup(data, "stages", "implementation_work_breakdown", "work_breakdown", "work_items", "implementation", default=_MISSING)
        if stages is not _MISSING:
            _append(sections, "Implementation stages", _stages(stages))
            known.update({"stages", "implementation_work_breakdown", "work_breakdown", "work_items", "implementation"})
        dependencies = _lookup(data, "dependencies", "prerequisites", default=_MISSING)
        if dependencies is not _MISSING:
            _append(sections, "Dependencies", _blocks(dependencies))
            known.update({"dependencies", "prerequisites"})
        verification = _lookup(data, "verification", "ordered_verification", "verification_plan", "checks", default=_MISSING)
        if verification is not _MISSING:
            _append(sections, "Verification", _blocks(verification, style="ordered"))
            known.update({"verification", "ordered_verification", "verification_plan", "checks"})
        risks = _lookup(data, "risks", "contradictions_and_risks", "risk_factors", "safeguards", default=_MISSING)
        if risks is not _MISSING:
            _append(sections, "Risks & safeguards", _blocks(risks))
            known.update({"risks", "contradictions_and_risks", "risk_factors", "safeguards"})
        decisions = _lookup(data, "decisions_needed", "user_decisions", "questions", "decisions", default=_MISSING)
        if decisions is not _MISSING:
            _append(sections, "Decisions needed", _blocks(decisions, style="checklist" if _sequence(decisions) else "bullets"))
            known.update({"decisions_needed", "user_decisions", "questions", "decisions"})
        done = _lookup(data, "definition_of_done", "definition", "acceptance", "acceptance_criteria", default=_MISSING)
        if done is not _MISSING:
            _append(sections, "Definition of done", _blocks(done, style="checklist" if _sequence(done) else "bullets"))
            known.update({"definition_of_done", "definition", "acceptance", "acceptance_criteria"})
        details = _lookup(data, "technical_details", "technical", default=_MISSING)
        if details is not _MISSING:
            _append(sections, "Technical details", _blocks(details))
            known.update({"technical_details", "technical"})
        _append_contract_evidence(sections, data, known, include_standard=False)
        remainder = _known_remainder(data, known)
        if remainder:
            _append(sections, "Technical details", _blocks(remainder))
        return _document(_title_value(data, envelope, "Implementation Plan"), report, summary=summary, sections=sections)


class ProgressPresenter:
    def present(self, content: object, report: Mapping[str, Any]) -> Document:
        envelope = parse_report_envelope(content)
        if envelope is not None and envelope.sections:
            return _document(_title_value(content, envelope, "Progress"), report, summary=_summary_value(content, envelope), sections=envelope.sections)
        data = content if isinstance(content, Mapping) else {}
        summary = _summary_value(data, envelope)
        sections: list[Section] = []
        fields = (
            ("Completed", ("completed", "done"), "bullets"),
            ("Active", ("active", "in_progress", "current"), "bullets"),
            ("Blocked", ("blocked", "blockers"), "bullets"),
            ("Next", ("next", "next_steps", "upcoming"), "bullets"),
            ("Current checks", ("current_checks", "checks", "verification"), "ordered"),
            ("Changed risks", ("changed_risks", "risks", "residual_risk"), "bullets"),
        )
        known = {"schema", "presentation_kind", "kind", "title", "summary", "overview", "outcome"}
        for title, names, style in fields:
            value = _lookup(data, *names, default=_MISSING)
            if value is not _MISSING:
                _append(sections, title, _blocks(value, style=style))
                known.update(_normal_key(name) for name in names)
        remainder = _known_remainder(data, known)
        if remainder:
            _append(sections, "Additional context", _blocks(remainder))
        return _document(_title_value(data, envelope, "Progress"), report, summary=summary, sections=sections)


class ResultPresenter:
    def present(self, content: object, report: Mapping[str, Any]) -> Document:
        envelope = parse_report_envelope(content)
        if envelope is not None and envelope.sections:
            return _document(_title_value(content, envelope, "Result"), report, summary=_summary_value(content, envelope), sections=envelope.sections)
        data = content if isinstance(content, Mapping) else {}
        summary = _summary_value(data, envelope)
        sections: list[Section] = []
        fields = (
            ("Outcome", ("outcome", "result", "summary"), "paragraph"),
            ("Changes", ("changes", "changed", "files", "surfaces"), "bullets"),
            ("Verified behavior", ("verified_behavior", "behavior", "acceptance", "evidence"), "bullets"),
            ("Checks", ("checks", "verification", "tests"), "ordered"),
            ("Deviations", ("deviations", "deviation", "plan_delta"), "bullets"),
            ("Known limitations", ("limitations", "known_limitations"), "bullets"),
            ("Residual risk", ("residual_risk", "risks"), "bullets"),
            ("Next actions", ("next_actions", "next", "follow_ups"), "bullets"),
        )
        known = {"schema", "presentation_kind", "kind", "title", "summary", "overview"}
        for title, names, style in fields:
            value = _lookup(data, *names, default=_MISSING)
            if value is not _MISSING:
                _append(sections, title, _blocks(value, style=style))
                known.update(_normal_key(name) for name in names)
        _append_contract_evidence(sections, data, known, include_standard=False, include_deviations=False)
        remainder = _known_remainder(data, known)
        if remainder:
            _append(sections, "Additional details", _blocks(remainder))
        return _document(_title_value(data, envelope, "Result"), report, summary=summary, sections=sections)


class SynthesisPresenter:
    """Dispatch ``synthesis`` reports to stable discovery/review templates."""

    def present(self, content: object, report: Mapping[str, Any]) -> Document:
        envelope = parse_report_envelope(content)
        data = content if isinstance(content, Mapping) else {}
        kind = envelope.presentation_kind if envelope is not None else _lookup(data, "presentation_kind", "kind", default="discovery")
        kind = _normal_key(kind)
        if envelope is not None and envelope.sections:
            return _document(_title_value(content, envelope, _synthesis_title(kind)), report, summary=_summary_value(data, envelope), sections=envelope.sections)
        if kind == "code_review":
            return self._review(data, report, "Code review")
        if kind == "security_audit":
            return self._review(data, report, "Security audit")
        if kind == "verification":
            return self._verification(data, report)
        if kind == "architecture":
            return self._architecture(data, report)
        if kind == "closure":
            return self._closure(data, report)
        return self._discovery(data, report)

    def _discovery(self, data: Mapping[str, Any], report: Mapping[str, Any]) -> Document:
        sections: list[Section] = []
        summary = _summary_value(data, None)
        _append(sections, "Scope", _blocks(_lookup(data, "scope", "question", "objective", default=_MISSING)))
        _append(sections, "Observed baseline", _blocks(_lookup(data, "observed_baseline", "findings", "evidence", default=_MISSING)))
        _append(sections, "Dependencies", _blocks(_lookup(data, "dependencies", "related_surfaces", default=_MISSING)))
        _append(sections, "Recommendations", _blocks(_lookup(data, "recommendations", "next", "next_actions", default=_MISSING)))
        _append(sections, "Coverage", _blocks(_lookup(data, "coverage", default=_MISSING)))
        _append_contract_evidence(sections, data, set())
        return _document(_title_value(data, None, "Discovery synthesis"), report, summary=summary, sections=sections)

    def _review(self, data: Mapping[str, Any], report: Mapping[str, Any], title: str) -> Document:
        sections: list[Section] = []
        _append(sections, "Executive summary", _blocks(_lookup(data, "executive_summary", "summary", "overview", default=_MISSING)))
        findings = _lookup(data, "findings", "issues", "observations", default=_MISSING)
        _append(sections, "Findings", _findings(findings, prefix="Finding"))
        _append(sections, "Coverage", _blocks(_lookup(data, "coverage", "checks", default=_MISSING)))
        _append(sections, "Residual risk", _blocks(_lookup(data, "residual_risk", "risks", default=_MISSING)))
        _append(sections, "Conclusion", _blocks(_lookup(data, "conclusion", "disposition", "result", default=_MISSING)))
        # Review templates own an explicit Executive summary section.  Avoid
        # repeating the same prose in the document-level summary slot.
        return _document(_title_value(data, None, title), report, sections=sections)

    def _verification(self, data: Mapping[str, Any], report: Mapping[str, Any]) -> Document:
        sections: list[Section] = []
        summary = _summary_value(data, None)
        _append(sections, "Verification scope", _blocks(_lookup(data, "scope", "objective", default=_MISSING)))
        _append(sections, "Checks", _blocks(_lookup(data, "checks", "verification", "tests", default=_MISSING), style="ordered"))
        _append(sections, "Observed results", _blocks(_lookup(data, "results", "observations", "evidence", default=_MISSING)))
        _append(sections, "Limitations", _blocks(_lookup(data, "limitations", "unrun", default=_MISSING)))
        _append(sections, "Conclusion", _blocks(_lookup(data, "conclusion", "result", default=_MISSING)))
        return _document(_title_value(data, None, "Verification"), report, summary=summary, sections=sections)

    def _architecture(self, data: Mapping[str, Any], report: Mapping[str, Any]) -> Document:
        sections: list[Section] = []
        summary = _summary_value(data, None)
        _append(sections, "Context", _blocks(_lookup(data, "context", "scope", default=_MISSING)))
        _append(sections, "Components", _blocks(_lookup(data, "components", "modules", default=_MISSING)))
        _append(sections, "Relationships", _blocks(_lookup(data, "relationships", "dependencies", "data_flow", default=_MISSING)))
        _append(sections, "Trade-offs", _blocks(_lookup(data, "tradeoffs", "risks", default=_MISSING)))
        _append(sections, "Recommendation", _blocks(_lookup(data, "recommendation", "next", default=_MISSING)))
        return _document(_title_value(data, None, "Architecture synthesis"), report, summary=summary, sections=sections)

    def _closure(self, data: Mapping[str, Any], report: Mapping[str, Any]) -> Document:
        sections: list[Section] = []
        summary = _summary_value(data, None)
        _append(sections, "Outcome", _blocks(_lookup(data, "outcome", "result", "summary", default=_MISSING)))
        _append(sections, "Evidence", _blocks(_lookup(data, "evidence", "verified", default=_MISSING)))
        _append(sections, "Residual risk", _blocks(_lookup(data, "residual_risk", "risks", default=_MISSING)))
        _append(sections, "Follow-ups", _blocks(_lookup(data, "follow_ups", "next_actions", default=_MISSING)))
        return _document(_title_value(data, None, "Closure evidence"), report, summary=summary, sections=sections)


def _synthesis_title(kind: str) -> str:
    return {
        "code_review": "Code review",
        "security_audit": "Security audit",
        "verification": "Verification",
        "architecture": "Architecture synthesis",
        "closure": "Closure evidence",
        "discovery": "Discovery synthesis",
    }.get(kind, "Synthesis")


_GENERIC = GenericFallbackPresenter()
_PRESENTERS = {
    "plan": PlanPresenter(),
    "progress": ProgressPresenter(),
    "result": ResultPresenter(),
    "synthesis": SynthesisPresenter(),
}


def present_report(report_type: object, content: object, report: Mapping[str, Any] | None = None) -> Document:
    """Return a deterministic typed document; presentation errors fall back."""
    metadata = report or {"report_type": report_type, "assembly_state": "unknown"}
    kind = plain_text(report_type).strip().lower()
    presenter = _PRESENTERS.get(kind)
    if presenter is None:
        return _GENERIC.present(content, metadata)
    try:
        source_text = content.get("source_text") if isinstance(content, Mapping) else None
        presentation_content = (
            {key: value for key, value in content.items() if key != "source_text"}
            if isinstance(content, Mapping) and isinstance(source_text, str)
            else content
        )
        document = presenter.present(presentation_content, metadata)
        # Canonical user source material is one explicitly labeled inert value.
        # It is never translated, normalized into worker prose, or duplicated.
        if isinstance(source_text, str):
            return Document(
                document.title,
                status=document.status,
                summary=document.summary,
                sections=(*document.sections, Section("Source material", [CodeBlock(source_text)])),
                metadata=document.metadata,
            )
        return document
    except Exception:
        # A malformed future envelope must never make a canonical report
        # unavailable.  Keep this guard intentionally broad: this is a
        # best-effort derived view, not a validation gate.
        return _GENERIC.present(content, metadata)


def render_report(report_type: object, content: object, report: Mapping[str, Any] | None = None) -> str:
    return present_report(report_type=report_type, content=content, report=report).render()


def fallback_lines(content: object) -> list[str]:
    """Compatibility helper for the historic ``_inert`` projection function."""
    return legacy_lines(content)


__all__ = [
    "GenericFallbackPresenter",
    "PRESENTATION_KINDS",
    "PlanPresenter",
    "ProgressPresenter",
    "REPORT_VIEW_SCHEMA",
    "ReportEnvelope",
    "ResultPresenter",
    "SynthesisPresenter",
    "fallback_lines",
    "merge_report_payloads",
    "normalize_report_payload",
    "parse_report_envelope",
    "present_report",
    "render_report",
]
