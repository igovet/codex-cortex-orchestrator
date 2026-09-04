"""Source accounting never treats an LLM summary as original text."""
import pytest

from cortex_runtime.source_coverage import (
    SourceCoverageError, ExtractionLink, anchor, audit, excerpt,
)


def test_exact_unicode_source_excerpt_and_stale_rejection():
    original = "  Привет 🌍\nНе удалять данные.\n"
    selected = anchor(original, 2, len(original) - 1)
    assert excerpt(original, selected) == "Привет 🌍\nНе удалять данные."
    with pytest.raises(SourceCoverageError, match="stale"):
        excerpt(original.replace("Не", "Да"), selected)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 100), (2, 2), (True, 2), (0, False)])
def test_invalid_offsets_rejected(start, end):
    with pytest.raises(SourceCoverageError, match="range_invalid"):
        anchor("request", start, end)


def test_missing_source_sentence_and_criterion_are_visible():
    text = "Build UI. Preserve data."
    result = audit(text, [("ui", None), ("ui", "keyboard"), ("data", None)], [
        ExtractionLink("ui", None, anchor(text, 0, 9)),
    ])
    assert not result.structurally_complete
    assert [text[a:b].strip() for a, b in result.uncovered] == ["Preserve data."]
    assert result.unmapped == (("ui", "keyboard"), ("data", None))


def test_all_characters_mapped_does_not_mean_semantic_approval():
    text = "Build UI. Preserve data."
    link = ExtractionLink("ui", None, anchor(text, 0, len(text)))
    result = audit(text, [("ui", None)], [link])
    assert result.structurally_complete
    assert not hasattr(result, "approved")
    assert not hasattr(result, "complete")


def test_audit_is_order_independent_and_duplicate_links_add_no_evidence():
    text = "Build UI."
    span = anchor(text, 0, len(text))
    subjects = [("ui", None), ("ui", "keyboard")]
    links = [ExtractionLink(name, criterion, span) for name, criterion in subjects]
    result = audit(text, subjects, links)
    assert result.structurally_complete
    assert result.mapping_digest == audit(text, reversed(subjects), links[::-1] + links).mapping_digest
    assert result.mapping_digest != audit(text, [("ui", None)], links[:1]).mapping_digest


def test_cannot_map_unregistered_or_orphaned_criterion():
    text = "Build UI."
    with pytest.raises(SourceCoverageError, match="subject_unknown"):
        audit(text, [("ui", None)], [ExtractionLink("other", None, anchor(text, 0, len(text)))])
    with pytest.raises(SourceCoverageError, match="parent_missing"):
        audit(text, [("ui", "keyboard")], [])


def test_overlapping_ranges_preserve_later_gap():
    text = "abcdef NEED"
    result = audit(text, [("r", None)], [
        ExtractionLink("r", None, anchor(text, 0, 6)),
        ExtractionLink("r", None, anchor(text, 1, 3)),
    ])
    assert result.uncovered == ((6, len(text)),)
