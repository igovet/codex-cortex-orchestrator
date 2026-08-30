"""Focused checks for the host-private human-view Markdown renderer."""
from __future__ import annotations

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
from cortex_runtime.report_presenters import merge_report_payloads, render_report  # noqa: E402
from cortex_runtime.v12_projections import _inert, _markdown_link, _render_report  # noqa: E402
from cortex_runtime.mcp_api import _public_view  # noqa: E402


class ProjectionMarkdownTests(unittest.TestCase):
    def test_structured_values_are_readable_markdown_not_embedded_json(self) -> None:
        rendered = _inert({
            "pages": [{"path": "pages/1-2.md", "events": 2}],
            "message": "# heading <script>alert('x')</script>",
        })

        self.assertNotIn("<pre>", rendered)
        self.assertNotIn("</pre>", rendered)
        self.assertNotIn('"pages"', rendered)
        self.assertIn("- **pages:**", rendered)
        self.assertIn("pages/1-2.md", rendered)
        self.assertIn("# heading &lt;script&gt;alert('x')&lt;/script&gt;", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("pages/1\\-2\\.md", rendered)
        self.assertNotIn("\n# heading", rendered)

    def test_report_content_is_markdown_and_untrusted_text_is_inert(self) -> None:
        class Store:
            def _read(self, callback):
                return callback(None)

            def _report_chunks(self, _connection, _report_id):
                return [{"section": "User *section*", "chunk_index": 0, "content": {
                    "summary": "- injected list\n## injected heading",
                }}]

            def _compact_report(self, report):
                return {"report_id": report["report_id"], "content": "opaque"}

        report = {
            "report_id": "r_test",
            "assembly_state": "finalized",
            "report_type": "plan",
            "status": "completed",
            "review_policy": "informational",
        }
        rendered = _render_report(Store(), report).decode("utf-8")

        self.assertIn("# Implementation Plan", rendered)
        self.assertIn("**Status:** FINALIZED", rendered)
        self.assertIn("**Review policy:** INFORMATIONAL", rendered)
        self.assertIn("\\- injected list", rendered)
        self.assertIn("\\## injected heading", rendered)
        self.assertNotRegex(rendered, r"(?m)^## injected heading$")
        self.assertEqual(len(__import__("re").findall(r"(?m)^# ", rendered)), 1)
        self.assertNotIn("<pre>", rendered)
        self.assertNotIn('"summary"', rendered)
        self.assertNotIn("r_test", rendered)

    def test_profile_names_and_identifiers_remain_readable(self) -> None:
        rendered = _inert({
            "delegation_id": "delegation-fde6f5fc-abcdef",
            "native_task_name": "planner_2",
            "model": "gpt-5.6-luna",
        })

        self.assertIn("**delegation_id:** delegation-fde6f5fc-abcdef", rendered)
        self.assertIn("**native_task_name:** planner_2", rendered)
        self.assertIn("**model:** gpt-5.6-luna", rendered)
        self.assertNotIn("delegation\\_id", rendered)
        self.assertNotIn("planner\\_2", rendered)

    def test_multiline_instructions_preserve_readable_markdown(self) -> None:
        rendered = _inert({"instructions": "Trusted policy:\n- Keep identifiers readable.\n- Do not add slash escapes."})

        self.assertIn("Trusted policy: - Keep identifiers readable. - Do not add slash escapes.", rendered)
        self.assertNotIn("\\- Keep identifiers", rendered)

    def test_plan_body_uses_headings_for_structured_work_and_lists_for_checks(self) -> None:
        class Store:
            def _read(self, callback):
                return callback(None)

            def _report_chunks(self, _connection, _report_id):
                return [{
                    "section": "plan",
                    "chunk_index": 0,
                    "content": {
                        "implementation_work_breakdown": [{
                            "stage": "Build",
                            "owner": "backend_dev",
                            "work": "Implement the API",
                            "acceptance": "The focused test passes",
                        }],
                        "ordered_verification": ["Run unit tests", "Run the release gate"],
                        "test_acceptance_matrix": [{
                            "test": "projection rendering",
                            "acceptance": "No JSON dump is emitted",
                        }],
                        "observed_baseline": {
                            "branch": "feature/rendering",
                            "evidence": "Focused regression test",
                        },
                    },
                }]

        rendered = _render_report(Store(), {"report_id": "r_plan", "assembly_state": "finalized", "report_type": "plan"}).decode("utf-8")

        self.assertIn("## Implementation stages", rendered)
        self.assertIn("### Stage 1 — Build", rendered)
        self.assertIn("**Owner:** backend_dev", rendered)
        self.assertIn("## Verification", rendered)
        self.assertIn("1. Run unit tests", rendered)
        self.assertIn("2. Run the release gate", rendered)
        self.assertIn("- Test Acceptance Matrix: test: projection rendering; acceptance: No JSON dump is emitted", rendered)
        self.assertIn("**Observed Baseline:** branch: feature/rendering; evidence: Focused regression test", rendered)
        self.assertNotIn("####", rendered)
        self.assertNotIn("\n-\n", rendered)
        self.assertNotIn("\n  #", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertNotIn('"implementation_work_breakdown"', rendered)
        self.assertNotIn("&lt;", rendered)
        self.assertNotIn("  \n", rendered)

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

    def test_typed_document_owns_hierarchy_and_spacing(self) -> None:
        rendered = render_markdown(Document(
            "Safe ## title",
            status="FINALIZED",
            summary="A paragraph\n## not a heading\n- not a list",
            sections=[Section("Overview", [Paragraph("A *plain* value"), BulletList(["first", "second"])]), Section("Empty", [])],
        ))
        self.assertEqual(len(__import__("re").findall(r"(?m)^# ", rendered)), 1)
        self.assertNotRegex(rendered, r"(?m)^#{2,6} not a heading$")
        self.assertNotRegex(rendered, r"(?m)^- not a list$")
        self.assertNotIn("  \n", rendered)
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))
        self.assertNotIn("## Empty", rendered)

    def test_inline_emphasis_and_null_chunks_cannot_change_structure(self) -> None:
        rendered = render_markdown(Document(
            "Safe",
            sections=[Section("Details", [Paragraph("**untrusted** __formatting__")])],
        ))
        self.assertNotIn("**untrusted**", rendered)
        self.assertNotIn("__formatting__", rendered)
        self.assertEqual(merge_report_payloads([None, {"checks": ["one"]}]), {"checks": ["one"]})

    def test_report_types_have_distinct_fixed_sections(self) -> None:
        report = {"assembly_state": "finalized", "status": "completed"}
        progress = render_report(report_type="progress", content={"completed": ["A"], "active": ["B"], "blocked": ["C"], "next": ["D"]}, report={**report, "report_type": "progress"})
        result = render_report(report_type="result", content={"outcome": "Done", "changes": ["A"], "checks": ["test"]}, report={**report, "report_type": "result"})
        self.assertLess(progress.index("## Completed"), progress.index("## Active"))
        self.assertLess(progress.index("## Active"), progress.index("## Blocked"))
        self.assertLess(result.index("## Outcome"), result.index("## Changes"))
        self.assertIn("## Checks", result)
        self.assertNotIn("## Completed", result)

    def test_canonical_source_text_is_inert_and_not_duplicated(self) -> None:
        source = "Пользовательский текст — unchanged"
        rendered = render_report(
            report_type="plan",
            content={
                "schema": "cortex/report/plan/v1", "summary": "English summary",
                "scope": [], "stages": [], "verification": [], "source_text": source,
            },
            report={"report_type": "plan", "assembly_state": "finalized", "status": "completed"},
        )
        self.assertIn("## Source material", rendered)
        self.assertEqual(rendered.count(source), 1)
        self.assertNotIn("source_text_en", rendered)
        self.assertNotIn("source_text_ru", rendered)

    def test_review_envelope_and_hostile_values_are_safe(self) -> None:
        content = {
            "schema": "cortex/report-view/v1",
            "presentation_kind": "code_review",
            "title": "Review",
            "summary": "Readable summary",
            "sections": [{
                "title": "Findings",
                "blocks": [{"type": "finding", "title": "Danger", "severity": "high", "location": "src/app.py:1", "evidence": "## hostile\n```oops```"}],
            }],
        }
        rendered = render_report(report_type="synthesis", content=content, report={"report_type": "synthesis", "assembly_state": "finalized", "status": "completed"})
        self.assertIn("## Findings", rendered)
        self.assertIn("### Danger", rendered)
        self.assertIn("**Severity:** high", rendered)
        self.assertNotRegex(rendered, r"(?m)^## hostile$")
        self.assertNotRegex(rendered, r"(?m)^```oops```$")
        self.assertEqual(len(__import__("re").findall(r"(?m)^# ", rendered)), 1)

    def test_chunk_labels_do_not_create_sections_and_merge_is_deterministic(self) -> None:
        chunks = [
            {"section": "first", "content": {"summary": "same", "checks": ["one"]}},
            {"section": "injected ## heading", "content": {"checks": ["two"], "unknown": "value"}},
        ]
        class Store:
            def _read(self, callback):
                return callback(None)

            def _report_chunks(self, _connection, _report_id):
                return chunks

        rendered = _render_report(Store(), {"report_id": "r_chunk", "assembly_state": "finalized", "report_type": "result", "status": "completed"}).decode()
        single = render_report(report_type="result", content=merge_report_payloads([item["content"] for item in chunks]), report={"report_type": "result", "assembly_state": "finalized", "status": "completed"})
        self.assertEqual(rendered, single)
        self.assertNotIn("injected ## heading", rendered)
        self.assertIn("## Checks", rendered)
        self.assertIn("## Additional details", rendered)

    def test_typed_blocks_table_checklist_and_fence_are_safe(self) -> None:
        rendered = render_markdown(Document("Blocks", sections=[Section("Details", [
            Checklist([{"text": "ship", "checked": True}, "review"]),
            Table(["Name", "Value"], [["a|b", "1"]]),
            CodeBlock("```\n## literal", "bash"),
            Finding("Finding", severity="medium", evidence="line"),
        ])]))
        self.assertIn("- [x] ship", rendered)
        self.assertIn("- [ ] review", rendered)
        self.assertIn("a\\|b", rendered)
        self.assertIn("````bash", rendered)
        self.assertIn("### Finding", rendered)

    def test_golden_presentations_are_stable(self) -> None:
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "markdown"
        report = {"assembly_state": "finalized", "status": "completed"}
        cases = {
            "plan": (
                "plan",
                {"summary": "Renderer-owned plan.", "scope": "Build stable views.", "stages": [{"stage": "Presentation layer", "work": ["Typed blocks"], "acceptance": ["One H1"]}], "dependencies": ["Explorer evidence"], "verification": ["Run focused tests"], "risks": ["Legacy content"], "decisions_needed": ["Approve rollout"], "definition_of_done": ["Golden snapshots"]},
            ),
            "progress": (
                "progress",
                {"summary": "Renderer is in progress.", "completed": ["Document model"], "active": ["Presenter templates"], "next": ["Run QA"], "current_checks": ["Projection unit tests"], "changed_risks": ["Legacy Markdown is sanitized"]},
            ),
            "result": (
                "result",
                {"outcome": "Typed views are available.", "changes": ["Added presenters"], "verified_behavior": ["Chunk labels remain invisible"], "checks": ["Unit tests"], "deviations": ["No live smoke"], "limitations": ["Old custom Markdown is rendered safely"], "residual_risk": ["Some legacy semantics are generic"], "next_actions": ["Run docs verification"]},
            ),
            "synthesis": (
                "synthesis",
                {"schema": "cortex/report-view/v1", "presentation_kind": "discovery", "title": "Discovery synthesis", "summary": "Mapped projection ownership.", "scope": "Presentation code", "observed_baseline": ["Recursive headings"], "dependencies": ["V12 store"], "recommendations": ["Use typed blocks"], "coverage": "Focused source and test audit"},
            ),
            "code-review": (
                "synthesis",
                {"schema": "cortex/report-view/v1", "presentation_kind": "code_review", "title": "Code review", "summary": "One finding remains.", "findings": [{"title": "Unsafe Markdown projection", "severity": "high", "location": "v12_projections.py", "impact": "Report text can change hierarchy.", "evidence": "Heading injection reproduces.", "recommendation": "Use typed presenters.", "coverage": "Focused fixture", "residual_risk": "Legacy reports use fallback.", "conclusion": "Fix required"}], "coverage": "Renderer tests", "residual_risk": "None after fix", "conclusion": "Ready"},
            ),
            "legacy-fallback": (
                "future",
                {"schema": "unknown", "legacy_key": "value", "nested": {"message": "## hostile"}, "items": ["one", "two"]},
            ),
        }
        for name, (report_type, content) in cases.items():
            rendered = render_report(report_type=report_type, content=content, report={**report, "report_type": report_type})
            self.assertEqual(rendered, (fixture_root / f"{name}.md").read_text(encoding="utf-8"), name)

    def test_v2_contract_evidence_has_dedicated_safe_sections(self) -> None:
        common = {
            "contract_coverage": [{"item_ref": "o_0123456789ab", "status": "complete", "verification": ["Focused test"]}],
            "deviations": ["No migration was needed."],
            "unresolved": ["Live smoke remains with verification."],
            "risks": ["One residual risk."],
            "verification": ["Run focused suite."],
        }
        cases = {
            "result": {"schema": "cortex/report/result/v2", "summary": "Result v2.", "outcome": "implemented", "changes": [], **common},
            "plan": {"schema": "cortex/report/plan/v2", "summary": "Plan v2.", "scope": [], "stages": [], **common},
            "synthesis": {"schema": "cortex/report/synthesis/v2", "summary": "Synthesis v2.", "findings": [], "recommendations": [], **common},
        }
        for report_type, content in cases.items():
            rendered = render_report(report_type=report_type, content=content, report={"report_type": report_type, "assembly_state": "finalized", "status": "completed"})
            self.assertIn("## Contract coverage", rendered)
            self.assertIn("## Unresolved items", rendered)
            self.assertIn("o_0123456789ab", rendered)
            self.assertNotIn("## Additional details", rendered)


if __name__ == "__main__":
    unittest.main()
