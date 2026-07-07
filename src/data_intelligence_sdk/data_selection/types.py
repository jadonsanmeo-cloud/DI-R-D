"""Data selection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SelectedDataContext:
    """Data subset selected as relevant for a user task."""

    selected_sources: list[str] = field(default_factory=list)
    selected_tables: list[str] = field(default_factory=list)
    selected_columns: dict[str, list[str]] = field(default_factory=dict)
    selected_vector_collections: list[str] = field(default_factory=list)
    selected_documents: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    confidence: float | None = None
