"""Task-local context building for execution spec generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)


@dataclass(slots=True)
class CorpusSummary:
    """Readable, compact map of the data corpus for spec planning."""

    package_refs: dict[str, Any] = field(default_factory=dict)
    catalog_summary: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    vector_collections: list[dict[str, Any]] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    raw_files: dict[str, Any] = field(default_factory=dict)
    embedding: dict[str, Any] = field(default_factory=dict)
    database: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionBrief:
    """Task-local view of the current session."""

    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    relevant_constraints: dict[str, Any] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UserBrief:
    """Task-local view of long-lived user context."""

    preferences: dict[str, Any] = field(default_factory=dict)
    relevant_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SpecBuildContext:
    """Context view built specifically for spec generation."""

    query: UserQuery
    intent: Intent
    corpus_summary: CorpusSummary
    session_brief: SessionBrief = field(default_factory=SessionBrief)
    user_brief: UserBrief = field(default_factory=UserBrief)


class SpecContextBuilder:
    """Builds deterministic, task-local context for spec builder agents."""

    def __init__(self, *, max_recent_turns: int = 5, max_history_items: int = 5) -> None:
        self.max_recent_turns = max_recent_turns
        self.max_history_items = max_history_items

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> SpecBuildContext:
        corpus_summary = build_corpus_summary(corpus_package)
        session_brief = self._build_session_brief(session_context)
        user_brief = self._build_user_brief(user_context)
        return SpecBuildContext(
            query=query,
            intent=intent,
            corpus_summary=corpus_summary,
            session_brief=session_brief,
            user_brief=user_brief,
        )

    def _build_session_brief(
        self, session_context: SessionContext | None
    ) -> SessionBrief:
        if session_context is None:
            return SessionBrief()
        constraints = session_context.state.get("constraints", {})
        if not isinstance(constraints, dict):
            constraints = {}
        open_questions = session_context.state.get("open_questions", [])
        if not isinstance(open_questions, list):
            open_questions = []
        return SessionBrief(
            recent_turns=list(session_context.turns[-self.max_recent_turns :]),
            relevant_constraints=constraints,
            open_questions=[str(question) for question in open_questions],
        )

    def _build_user_brief(self, user_context: UserContext | None) -> UserBrief:
        if user_context is None:
            return UserBrief()
        return UserBrief(
            preferences=dict(user_context.preferences),
            relevant_history=list(user_context.history[-self.max_history_items :]),
        )

def build_corpus_summary(corpus_package: DataCorpusPackage) -> CorpusSummary:
    """Create a compact data map from parsed package/schema/catalog metadata."""

    catalog = corpus_package.metadata.get("catalog", {})
    if not isinstance(catalog, dict):
        catalog = {}
    package_refs = corpus_package.metadata.get("package", {})
    if not isinstance(package_refs, dict):
        package_refs = {}
    return CorpusSummary(
        package_refs=package_refs,
        catalog_summary=_optional_str(catalog.get("summary")),
        sources=[
            {"ref": source, "kind": infer_source_kind(source)}
            for source in corpus_package.sources
        ],
        tables=summarize_named_schema_entries(corpus_package.schemas.get("tables", {})),
        vector_collections=summarize_named_schema_entries(
            corpus_package.schemas.get("vector_collections", {})
        ),
        datasets=summarize_catalog_datasets(catalog.get("datasets", [])),
        raw_files=_dict_or_empty(catalog.get("raw_files")),
        embedding=_dict_or_empty(
            catalog.get("embedding") or corpus_package.schemas.get("embedding")
        ),
        database=_dict_or_empty(corpus_package.schemas.get("database")),
    )


def summarize_named_schema_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    entries = []
    for name, payload in value.items():
        if not isinstance(payload, dict):
            entries.append({"name": name})
            continue
        entries.append(
            {
                "name": name,
                "description": payload.get("description"),
                "columns": payload.get("columns", []),
                "primary_key": payload.get("primary_key"),
                "source_file": payload.get("source_file"),
                "source_files": payload.get("source_files", []),
                "storage": payload.get("storage"),
                "embedding_model": payload.get("embedding_model"),
                "embedding_dimensions": payload.get("embedding_dimensions"),
            }
        )
    return entries


def summarize_catalog_datasets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    datasets = []
    for dataset in value:
        if not isinstance(dataset, dict):
            continue
        datasets.append(
            {
                "name": dataset.get("name"),
                "kind": dataset.get("kind"),
                "source": dataset.get("source"),
                "description": dataset.get("description"),
                "raw_file": dataset.get("raw_file"),
                "raw_files": dataset.get("raw_files", []),
            }
        )
    return datasets


def infer_source_kind(source: str) -> str:
    source_lower = str(source).lower()
    if source_lower.endswith(".csv"):
        return "csv_file"
    if source_lower.endswith(".md"):
        return "markdown_document"
    if source_lower.endswith(".json"):
        return "json_file"
    if "schema=vectordb" in source_lower or source_lower.rstrip("/").endswith(
        "/vectordb"
    ):
        return "vector_database"
    if source_lower.startswith(("postgresql://", "postgres://")):
        return "relational_database"
    return "unknown"


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
