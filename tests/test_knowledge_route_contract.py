"""Regression coverage for the Codebase Memory-first knowledge route."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARVEST = ROOT / "plugins/cortex/skills/knowledge-harvest/SKILL.md"
CENSUS = ROOT / "plugins/cortex/skills/knowledge-harvest/references/feature-census.md"
ROUTE_DOC = ROOT / "docs/features/knowledge-route-contract/index.md"
README = ROOT / "README.md"
ORCHESTRATOR = ROOT / "plugins/cortex/skills/orchestrator/SKILL.md"
WORKER_MESSAGE = ROOT / "plugins/cortex/scripts/cortex_runtime/worker_message.py"


def test_codebase_memory_is_preferred_with_single_safe_fallback():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (HARVEST, CENSUS, ROUTE_DOC, README, ORCHESTRATOR, WORKER_MESSAGE)
    )

    normalized = re.sub(r"\s+", " ", text)
    assert normalized.count("preferred") >= 4
    assert text.count("exactly one") >= 2
    assert "bounded limitation" in normalized
    assert "silent" in normalized.lower()
    assert "chain multiple fallback searches" in normalized.lower() or "chained fallback" in normalized.lower()
    assert "not only knowledge harvest" in text
    assert "Before structural project-code discovery" in text
    assert "canonical `project_root` returned in the server-owned assignment context" in normalized
    assert "Do not silently skip an available usable graph" in text


def test_route_docs_do_not_teach_mcp_request_shapes():
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in (HARVEST, CENSUS, ROUTE_DOC)
    )

    for forbidden in ("assignment_ref", "task_ref", "repo_path", "semantic_query"):
        assert forbidden not in text
