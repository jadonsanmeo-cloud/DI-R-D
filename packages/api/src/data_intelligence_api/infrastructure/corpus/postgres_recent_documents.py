"""Read-only recent-document adapter for the Corpus Service database."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from data_intelligence_sdk.scheduled_specs import RecentDocument


class PostgresRecentDocumentSource:
    def __init__(
        self,
        database_url: str,
        *,
        successful_statuses: Sequence[str] = ("indexed",),
        connection_factory: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not database_url.strip():
            raise ValueError("Corpus database URL is required.")
        statuses = tuple(status.strip() for status in successful_statuses if status.strip())
        if not statuses:
            raise ValueError("At least one successful corpus status is required.")
        self.database_url = _normalize_psycopg_url(database_url)
        self.successful_statuses = statuses
        self.connection_factory = connection_factory

    def load_recent(
        self,
        *,
        organization_id: str,
        limit: int,
    ) -> list[RecentDocument]:
        if limit <= 0:
            raise ValueError("Recent document limit must be positive.")
        if not organization_id.strip():
            raise ValueError("Organization ID is required.")

        with self.connection_factory(
            self.database_url,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        document_id,
                        organization_id,
                        file_name,
                        source_uri,
                        created_at AS ingested_at
                    FROM documents
                    WHERE organization_id = %s
                      AND deleted_at IS NULL
                      AND current_status = ANY(%s)
                    ORDER BY created_at DESC, document_id DESC
                    LIMIT %s
                    """,
                    (organization_id, list(self.successful_statuses), limit),
                )
                rows = cursor.fetchall()

        return [
            RecentDocument(
                document_id=str(row["document_id"]),
                organization_id=str(row["organization_id"]),
                file_name=str(row["file_name"]),
                source_uri=str(row["source_uri"]),
                ingested_at=row["ingested_at"],
            )
            for row in rows
        ]


def _normalize_psycopg_url(database_url: str) -> str:
    sqlalchemy_prefix = "postgresql+psycopg://"
    if database_url.startswith(sqlalchemy_prefix):
        return "postgresql://" + database_url[len(sqlalchemy_prefix) :]
    return database_url
