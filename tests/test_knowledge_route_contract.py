"""Regression coverage for the Codebase Memory-first knowledge route."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARVEST = ROOT / "plugins/cortex/skills/knowledge-harvest/SKILL.md"
CENSUS = ROOT / "plugins/cortex/skills/knowledge-harvest/references/feature-census.md"
ROUTE_DOC = ROOT / "docs/features/knowledge-route-contract/index.md"
README = ROOT / "README.md"


def test_codebase_memory_is_mandatory_first_route_with_single_evidence_fallback():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (HARVEST, CENSUS, ROUTE_DOC, README)
    )

    normalized = re.sub(r"\s+", " ", text)
    assert normalized.count("mandatory first route") >= 3
    assert text.count("exactly one") >= 2
    assert "concrete graph limitation" in normalized
    assert "silent" in normalized.lower()
    assert "chained fallback" in normalized.lower()


def test_route_docs_do_not_teach_mcp_request_shapes():
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in (HARVEST, CENSUS, ROUTE_DOC)
    )

    for forbidden in ("assignment_ref", "task_ref", "repo_path", "semantic_query"):
        assert forbidden not in text
