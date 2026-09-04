"""Exact source anchors and structural extraction audit, not semantic approval.

The source is immutable user text. Offsets are Unicode codepoint positions,
not UTF-8 byte positions. Only the ledger supplies source text; callers select
positions, never provide a second purported copy of the source excerpt.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Iterable


class SourceCoverageError(ValueError):
    """A source mapping cannot establish the claimed extraction coverage."""


@dataclass(frozen=True)
class SourceAnchor:
    source_digest: str
    start: int
    end: int


@dataclass(frozen=True)
class ExtractionLink:
    """Server-resolved requirement/criterion identity and its source anchor."""
    obligation: str
    criterion: str | None
    anchor: SourceAnchor


def source_digest(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise SourceCoverageError("source_empty")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def anchor(source: str, start: int, end: int) -> SourceAnchor:
    digest = source_digest(source)
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source)):
        raise SourceCoverageError("source_range_invalid")
    if not source[start:end].strip():
        raise SourceCoverageError("source_range_empty")
    return SourceAnchor(digest, start, end)


def excerpt(source: str, selection: SourceAnchor) -> str:
    if not isinstance(selection, SourceAnchor):
        raise SourceCoverageError("source_anchor_invalid")
    measured = anchor(source, selection.start, selection.end)
    if measured != selection:
        raise SourceCoverageError("source_anchor_stale")
    return source[selection.start:selection.end]


@dataclass(frozen=True)
class ExtractionAudit:
    source_digest: str
    mapping_digest: str
    uncovered: tuple[tuple[int, int], ...]
    unmapped: tuple[tuple[str, str | None], ...]

    @property
    def structurally_complete(self) -> bool:
        # Intentionally not named complete/approved: one whole-request link can
        # cover all characters while its semantic interpretation still omits work.
        return not self.uncovered and not self.unmapped


def audit(source: str, obligations: Iterable[tuple[str, str | None]],
          links: Iterable[ExtractionLink]) -> ExtractionAudit:
    """Account for every non-whitespace character and every registered subject.

    This pure check grants no execution or closure authority. An independent
    semantic extraction review must be bound to this exact mapping digest plus
    the ledger's immutable requirement/criterion-content digest before admission.
    """
    digest = source_digest(source)
    subjects = tuple(obligations)
    if not subjects or len(set(subjects)) != len(subjects):
        raise SourceCoverageError("source_subjects_invalid")
    if any(not isinstance(name, str) or not name or
           (criterion is not None and (not isinstance(criterion, str) or not criterion))
           for name, criterion in subjects):
        raise SourceCoverageError("source_subjects_invalid")
    required = set(subjects)
    if any((name, None) not in required for name, criterion in subjects if criterion is not None):
        raise SourceCoverageError("criterion_parent_missing")
    mapped = set()
    ranges = set()
    records = set()
    for link in links:
        subject = (link.obligation, link.criterion)
        if subject not in required:
            raise SourceCoverageError("source_subject_unknown")
        excerpt(source, link.anchor)
        mapped.add(subject)
        ranges.add((link.anchor.start, link.anchor.end))
        records.add((link.obligation, link.criterion, link.anchor.start, link.anchor.end))
    uncovered = []
    # Merge intervals before scanning gaps: no input-sized boolean allocation
    # and no quadratic walk when many criteria cite the same passage.
    position = 0
    for start, end in sorted(ranges):
        if start > position and source[position:start].strip():
            uncovered.append((position, start))
        position = max(position, end)
    if source[position:].strip():
        uncovered.append((position, len(source)))
    ordered = sorted(records, key=lambda row: (row[0], row[1] or "", row[2], row[3]))
    encoded = json.dumps({"source": digest, "subjects": sorted(subjects, key=lambda s: (s[0], s[1] or "")),
                          "links": ordered}, ensure_ascii=False, separators=(",", ":"))
    return ExtractionAudit(digest, hashlib.sha256(encoded.encode()).hexdigest(),
                           tuple(uncovered), tuple(s for s in subjects if s not in mapped))
