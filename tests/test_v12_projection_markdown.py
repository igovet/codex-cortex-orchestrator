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
        self.assertIn("pages/1\\-2\\.md", rendered)
        self.assertIn("\\# heading &lt;script&gt;alert\\('x'\\)&lt;/script&gt;", rendered)

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

        self.assertIn("# Report: r\\_test", rendered)
        self.assertIn("## Content", rendered)
        self.assertIn("- **summary:** \\- injected list  ", rendered)
        self.assertIn("\\#\\# injected heading", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertNotIn('"summary"', rendered)


if __name__ == "__main__":
    unittest.main()
