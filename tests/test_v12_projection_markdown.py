"""Focused checks for the host-private human-view Markdown renderer."""
from __future__ import annotations

from plan_fixtures import ordinary_candidates
import sys
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.markdown_document import (  # noqa: E402
    BulletList,
    Checklist,
    CodeBlock,
    Document,
    Finding,
    Paragraph,
    Section,
    Table,
    render_markdown,
)
from cortex_runtime.report_presenters import render_report  # noqa: E402
from cortex_runtime.v12_projections import _markdown_link, _render_report  # noqa: E402
from cortex_runtime.mcp_api import _public_view  # noqa: E402
from cortex_runtime.execution_graph import GraphError
from test_execution_graph_integrity import graph
from test_graph_ledger import observation
from test_typed_publication_transaction import baseline_content
import pytest


def plan_content():
    return dict(status="completed", summary="Implement the verified product.", scope="Product only.",
                candidates=ordinary_candidates(graph()), artifact=observation(), risks=[], unresolved=[])


def test_plan_renders_every_current_node_edge_check_and_strategy():
    content = plan_content()
    rendered = render_report("plan", content, {"review_policy": "required"})
    assert rendered.startswith("# Implementation Plan")
    assert "**Review policy:** REQUIRED" in rendered
    assert "## Execution dependencies" in rendered
    assert "## Outcome acceptance composition" in rendered
    for node in content["candidates"][0]["graph"]["nodes"]:
        assert "## Node: " + node["key"] in rendered
        for key in ("work", "acceptance", "requires", "provides", "mutation_domains"):
            for value in node[key]:
                assert value in rendered
        for check in node["checks"]:
            assert check["description"] in rendered and check["key"] in rendered
        for edge in node["dependencies"]:
            assert edge["node"] in rendered
        for strategy in node["remediation"]["strategies"]:
            assert "## Repair strategy: " + node["key"] + " / " + strategy["key"] in rendered
            assert strategy["work"][0] in rendered
    assert "Expected check" in rendered
    assert "Observed coverage" not in rendered
    assert "## Finite workflow budgets" in rendered
    assert "## Unresolved items" in rendered
    assert content["artifact"]["end"] in rendered
    assert render_report("plan", content, {"review_policy": "required"}) == rendered


def test_result_displays_only_observed_node_coverage_and_documentation():
    content = baseline_content()
    content["summary"] = "Inspect `src/main.py` and **verify** the boundary."
    content["changes"] = [{"path": "src/main.py", "summary": "Implemented bounded behavior."}]
    rendered = render_report("result", content)
    assert "# Implementation Result" in rendered
    assert content["summary"] in rendered
    assert "## Observed coverage: baseline" in rendered
    assert "## Documentation impact" in rendered
    assert "src/main.py" in rendered
    for row in content["node_coverage"][0]["coverage"]:
        for fact in row["verification"]:
            assert fact["summary"] in rendered
    assert "verification_facts" not in rendered


def test_documentation_has_its_own_sections_not_generic_synthesis():
    result = baseline_content()
    content = {k: v for k, v in result.items() if k not in {"outcome", "changes"}}
    content.update(findings=[{"area": "README", "summary": "Commands match implementation."}],
                   recommendations=["Recheck commands after interface changes."])
    rendered = render_report("documentation", content)
    for value in ("# Documentation Impact", "## Documentation findings", "README",
                  "Commands match implementation.", "## Recommendations",
                  "## Observed coverage: baseline"):
        assert value in rendered
    assert "Additional details" not in rendered


@pytest.mark.parametrize("kind,content", [
    ("progress", {"completed": ["Old progress"]}),
    ("synthesis", {"findings": []}),
    ("plan", {"stages": [], "summary": "Old plan"}),
    ("result", {"schema": "cortex/report/result/v2", "outcome": "Old result"}),
    ("unknown", {"payload": "Arbitrary body"}),
])
def test_obsolete_and_unknown_formats_do_not_get_verified_views(kind, content):
    with pytest.raises(GraphError):
        render_report(kind, content)


def test_report_projection_requires_one_current_body():
    class Store:
        def _read(self, callback):
            return callback(None)
        def _report_chunks(self, connection, report_id):
            return self.chunks
    store = Store()
    report = {"report_id": "private", "report_type": "result", "assembly_state": "finalized"}
    body = baseline_content()
    store.chunks = [{"section": "body", "content": body}]
    assert _render_report(store, report).decode() == render_report("result", body, report)
    for chunks in ([], [{"section": "old", "content": body}], store.chunks * 2):
        store.chunks = chunks
        with pytest.raises(ValueError, match="one immutable body"):
            _render_report(store, report)

class ProjectionMarkdownTests(unittest.TestCase):

    def test_ready_view_exposes_exact_server_link_and_non_ready_view_does_not(self) -> None:
        canonical = "/private/tasks/t_ref/plans/revisions/report-full-canonical-id.md"
        link = _markdown_link("plans/revisions/report-full-canonical-id.md", canonical)
        ready = _public_view({"status": "ready", "path": canonical, "markdown_link": link}, approval=False)
        stale = _public_view({"status": "stale", "path": None, "markdown_link": link}, approval=False)

        self.assertEqual(ready["markdown_link"], f"[Open plan revision]({canonical})")
        self.assertIn("report-full-canonical-id.md", ready["markdown_link"])
        self.assertNotIn("path", ready)
        self.assertEqual(_public_view(ready, approval=False), ready)
        self.assertNotIn("markdown_link", stale)
        self.assertNotIn("path", stale)

        approval = _public_view({
            "status": "ready",
            "path": canonical,
            "markdown_link": link,
            "report_ref": "r_0123456789ab",
            "delegation_ref": "d_0123456789ab",
            "approval_handle": "approval-ready",
        }, approval=True)
        self.assertNotIn("path", approval)
        self.assertEqual(_public_view(approval, approval=True), approval)

    def test_typed_document_owns_sections_while_authored_markdown_is_preserved(self) -> None:
        rendered = render_markdown(Document(
            "Safe ## title",
            status="FINALIZED",
            summary="A paragraph\n## not a heading\n- not a list",
            sections=[Section("Overview", [Paragraph("A *plain* value"), BulletList(["first", "second"])]), Section("Empty", [])],
        ))
        self.assertEqual(len(__import__("re").findall(r"(?m)^# ", rendered)), 1)
        self.assertRegex(rendered, r"(?m)^#{2,6} not a heading$")
        self.assertRegex(rendered, r"(?m)^- not a list$")
        self.assertNotIn("  \n", rendered)
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))
        self.assertNotIn("## Empty", rendered)

    def test_typed_blocks_table_checklist_and_fence_preserve_markdown(self) -> None:
        rendered = render_markdown(Document("Blocks", sections=[Section("Details", [
            Checklist([{"text": "ship", "checked": True}, "review"]),
            Table(["Name", "Value"], [["a|b", "1"]]),
            CodeBlock("```\n## literal", "bash"),
            Finding("Finding", severity="medium", evidence="line"),
        ])]))
        self.assertIn("- [x] ship", rendered)
        self.assertIn("- [ ] review", rendered)
        self.assertIn("a|b", rendered)
        self.assertNotIn("a\\|b", rendered)
        self.assertNotIn("a&#124;b", rendered)
        self.assertIn("````bash", rendered)
        self.assertIn("### Finding", rendered)

if __name__ == "__main__":
    unittest.main()
