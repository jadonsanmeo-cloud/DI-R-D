from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger
from data_intelligence_sdk.scheduled_specs import RecentDocument
from data_intelligence_sdk.scheduled_specs.worker import RecentDocumentSpecWorker


def document(name: str, offset: int) -> RecentDocument:
    return RecentDocument(
        document_id=name,
        organization_id="test-org",
        file_name=f"{name}.pdf",
        source_uri=f"s3://test-org/{name}.pdf",
        ingested_at=datetime(2026, 7, 25, tzinfo=timezone.utc)
        + timedelta(minutes=offset),
    )


class FakeSource:
    def __init__(self, documents: list[RecentDocument]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, int]] = []

    def load_recent(self, *, organization_id: str, limit: int) -> list[RecentDocument]:
        self.calls.append((organization_id, limit))
        return list(self.documents)


class FakePrompt:
    def __init__(self, failing_id: str | None = None) -> None:
        self.failing_id = failing_id
        self.rendered: list[str] = []

    def render(self, item: RecentDocument) -> str:
        self.rendered.append(item.document_id)
        if item.document_id == self.failing_id:
            raise ValueError("raw document content must not enter logs")
        return f"# Spec for {item.document_id}\n"


class FakeStore:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def exists(self, document_id: str) -> bool:
        return document_id in self.files

    def create(self, document_id: str, markdown: str) -> Path:
        if document_id in self.files:
            raise FileExistsError(document_id)
        self.files[document_id] = markdown
        return Path(f"{document_id}.md")


class RecentDocumentSpecWorkerTests(unittest.TestCase):
    def test_creates_only_missing_specs_across_sliding_windows(self) -> None:
        source = FakeSource([document("C", 3), document("B", 2), document("A", 1)])
        prompt = FakePrompt()
        store = FakeStore()
        logger = InMemoryRuntimeLogger()
        worker = RecentDocumentSpecWorker(
            source=source,
            prompt=prompt,
            store=store,
            organization_id="test-org",
            limit=3,
            logger=logger,
        )

        first = worker.run_cycle()
        source.documents = [document("D", 4), document("C", 3), document("B", 2)]
        second = worker.run_cycle()

        self.assertEqual(source.calls, [("test-org", 3), ("test-org", 3)])
        self.assertEqual(prompt.rendered, ["A", "B", "C", "D"])
        self.assertEqual(set(store.files), {"A", "B", "C", "D"})
        self.assertEqual((first.loaded, first.created, first.skipped, first.failed), (3, 3, 0, 0))
        self.assertEqual((second.loaded, second.created, second.skipped, second.failed), (3, 1, 2, 0))

    def test_failure_for_one_document_does_not_block_remaining_documents(self) -> None:
        logger = InMemoryRuntimeLogger()
        store = FakeStore()
        worker = RecentDocumentSpecWorker(
            source=FakeSource([document("C", 3), document("B", 2), document("A", 1)]),
            prompt=FakePrompt(failing_id="B"),
            store=store,
            organization_id="test-org",
            logger=logger,
        )

        result = worker.run_cycle()

        self.assertEqual(set(store.files), {"A", "C"})
        self.assertEqual((result.created, result.failed), (2, 1))
        failed_payload = next(
            payload
            for event, payload in logger.events
            if event == "scheduled_spec.document.failed"
        )
        self.assertEqual(failed_payload, {"document_id": "B", "error_type": "ValueError"})
        self.assertNotIn("raw document content", str(logger.events))


if __name__ == "__main__":
    unittest.main()
