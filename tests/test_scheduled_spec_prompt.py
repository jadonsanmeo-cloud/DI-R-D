from __future__ import annotations

import unittest
from datetime import datetime, timezone

from data_intelligence_sdk.scheduled_specs import (
    RecentDocument,
    ScheduledReportSpecPrompt,
)


class ScheduledReportSpecPromptTests(unittest.TestCase):
    def test_renders_direct_markdown_prompt_for_one_seed_document(self) -> None:
        document = RecentDocument(
            document_id="doc-123",
            organization_id="test-org",
            file_name="example.pdf",
            source_uri="s3://test-org/example.pdf",
            ingested_at=datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc),
        )

        markdown = ScheduledReportSpecPrompt().render(document)

        self.assertTrue(markdown.startswith("# Scheduled Dashboard Report Spec\n"))
        self.assertIn("`doc-123`", markdown)
        self.assertIn("`example.pdf`", markdown)
        self.assertIn("`s3://test-org/example.pdf`", markdown)
        self.assertIn("`2026-07-25T10:30:00+00:00`", markdown)
        self.assertIn("Retrieve all sufficiently related documents", markdown)
        self.assertIn("document content and retrieval scores", markdown)
        self.assertIn("dashboard display", markdown)
        self.assertIn("cite every supporting document", markdown)
        self.assertNotIn("```json", markdown)
        self.assertNotIn("capability_requirements", markdown)
        self.assertNotIn("data_requirements", markdown)
        self.assertNotIn("ExecutionSpec", markdown)


if __name__ == "__main__":
    unittest.main()
