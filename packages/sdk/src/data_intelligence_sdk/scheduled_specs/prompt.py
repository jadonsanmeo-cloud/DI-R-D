"""Direct Markdown prompt for scheduled dashboard reports."""

from __future__ import annotations

from data_intelligence_sdk.scheduled_specs.contracts import RecentDocument


class ScheduledReportSpecPrompt:
    """Build a deterministic engine prompt around one seed document."""

    def render(self, document: RecentDocument) -> str:
        return f"""# Scheduled Dashboard Report Spec

## Seed File

- Document ID: `{document.document_id}`
- Name: `{document.file_name}`
- Source URI: `{document.source_uri}`
- Ingested At: `{document.ingested_at.isoformat()}`

## Report Request

Create a dashboard-ready report centered on the seed file.

Retrieve all sufficiently related documents from the available corpus. Determine
relevance from document content and retrieval scores, not ingestion time. Use
the retrieved documents as supporting context.

Clearly distinguish the seed document from supporting documents and cite every supporting document used.

## Expected Report

Produce a concise Markdown report suitable for dashboard display, including:

- executive summary;
- important findings;
- related evidence;
- notable differences or contradictions;
- source citations.
"""
