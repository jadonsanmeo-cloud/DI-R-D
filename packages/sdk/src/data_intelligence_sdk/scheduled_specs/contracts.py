"""Contracts for scheduled report-spec preparation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RecentDocument:
    """Metadata for one recently ingested corpus document."""

    document_id: str
    organization_id: str
    file_name: str
    source_uri: str
    ingested_at: datetime


class RecentDocumentSource(Protocol):
    def load_recent(
        self,
        *,
        organization_id: str,
        limit: int,
    ) -> list[RecentDocument]: ...


class ScheduledSpecStore(Protocol):
    def exists(self, document_id: str) -> bool: ...

    def create(self, document_id: str, markdown: str) -> Path: ...
