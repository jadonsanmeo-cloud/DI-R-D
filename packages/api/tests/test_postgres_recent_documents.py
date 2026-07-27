from __future__ import annotations

import unittest
from datetime import datetime, timezone

from data_intelligence_api.infrastructure.corpus.postgres_recent_documents import (
    PostgresRecentDocumentSource,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: object = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, parameters: object) -> None:
        self.sql = sql
        self.parameters = parameters

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


class PostgresRecentDocumentSourceTests(unittest.TestCase):
    def test_queries_latest_indexed_documents_and_maps_created_at(self) -> None:
        created_at = datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)
        cursor = FakeCursor(
            [
                {
                    "document_id": "doc-123",
                    "organization_id": "test-org",
                    "file_name": "example.pdf",
                    "source_uri": "s3://test-org/example.pdf",
                    "ingested_at": created_at,
                }
            ]
        )
        connection_calls: list[tuple[str, object]] = []

        def connect(database_url: str, **kwargs: object) -> FakeConnection:
            connection_calls.append((database_url, kwargs.get("row_factory")))
            return FakeConnection(cursor)

        source = PostgresRecentDocumentSource(
            "postgresql+psycopg://hidden-credentials/corpus",
            connection_factory=connect,
        )

        documents = source.load_recent(organization_id="test-org", limit=3)

        normalized_sql = " ".join(cursor.sql.split())
        self.assertIn("organization_id = %s", normalized_sql)
        self.assertIn("deleted_at IS NULL", normalized_sql)
        self.assertIn("current_status = ANY(%s)", normalized_sql)
        self.assertIn("ORDER BY created_at DESC, document_id DESC", normalized_sql)
        self.assertIn("LIMIT %s", normalized_sql)
        self.assertEqual(cursor.parameters, ("test-org", ["indexed"], 3))
        self.assertEqual(len(connection_calls), 1)
        self.assertEqual(connection_calls[0][0], "postgresql://hidden-credentials/corpus")
        self.assertEqual(documents[0].document_id, "doc-123")
        self.assertEqual(documents[0].ingested_at, created_at)

    def test_rejects_non_positive_limit_without_connecting(self) -> None:
        connected = False

        def connect(database_url: str, **kwargs: object) -> FakeConnection:
            nonlocal connected
            connected = True
            return FakeConnection(FakeCursor([]))

        source = PostgresRecentDocumentSource(
            "postgresql://hidden-credentials/corpus",
            connection_factory=connect,
        )

        with self.assertRaises(ValueError):
            source.load_recent(organization_id="test-org", limit=0)

        self.assertFalse(connected)


if __name__ == "__main__":
    unittest.main()
