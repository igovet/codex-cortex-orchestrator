"""Focused checks for the host-private human-view Markdown renderer."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_projections import _inert, _render_report  # noqa: E402


class ProjectionMarkdownTests(unittest.TestCase):
    def test_structured_values_are_readable_markdown_not_embedded_json(self) -> None:
        rendered = _inert({
            "pages": [{"path": "pages/1-2.md", "events": 2}],
            "message": "# heading <script>alert('x')</script>",
        })

        self.assertNotIn("<pre>", rendered)
        self.assertNotIn("</pre>", rendered)
        self.assertNotIn('"pages"', rendered)
        self.assertIn("- **pages**", rendered)
        self.assertIn("pages/1-2.md", rendered)
        self.assertIn("# heading <script>alert('x')</script>", rendered)
        self.assertNotIn("&lt;script&gt;", rendered)
        self.assertNotIn("pages/1\\-2\\.md", rendered)
        self.assertNotIn("\\", rendered)

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

        report = {"report_id": "r_test", "assembly_state": "finalized"}
        rendered = _render_report(Store(), report).decode("utf-8")

        self.assertIn("# Report", rendered)
        self.assertIn("**Status:** FINALIZED", rendered)
        self.assertIn("## User *section*", rendered)
        self.assertIn("### Summary\n\n- injected list  \n## injected heading", rendered)
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

        self.assertIn("Trusted policy:  \n  - Keep identifiers readable.  \n  - Do not add slash escapes.", rendered)
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
                    },
                }]

        rendered = _render_report(Store(), {"report_id": "r_plan", "assembly_state": "finalized", "report_type": "plan"}).decode("utf-8")

        self.assertIn("### Implementation Work Breakdown", rendered)
        self.assertIn("#### Stage 1 — Build", rendered)
        self.assertIn("- **Owner:** backend_dev", rendered)
        self.assertIn("### Ordered Verification", rendered)
        self.assertIn("1. Run unit tests", rendered)
        self.assertIn("2. Run the release gate", rendered)
        self.assertIn("### Test Acceptance Matrix", rendered)
        self.assertIn("#### projection rendering", rendered)
        self.assertIn("- **Acceptance:** No JSON dump is emitted", rendered)
        self.assertNotIn("\n-\n", rendered)
        self.assertNotIn("\n  #", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertNotIn('"implementation_work_breakdown"', rendered)
        self.assertNotIn("&lt;", rendered)
        self.assertNotIn("\\", rendered)


if __name__ == "__main__":
    unittest.main()
