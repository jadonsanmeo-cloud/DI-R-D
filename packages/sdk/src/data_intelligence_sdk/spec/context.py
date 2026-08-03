"""Task-local context building for execution spec generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)

if TYPE_CHECKING:
    from data_intelligence_sdk.intent import IntentAnalysis


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
class TaskHints:
    """Deterministic hints extracted for spec planning."""

    mentioned_tables: list[str] = field(default_factory=list)
    mentioned_columns: list[str] = field(default_factory=list)
    mentioned_vector_collections: list[str] = field(default_factory=list)
    mentioned_datasets: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    output_type: str | None = None
    language: str | None = None


@dataclass(slots=True)
class SpecBuildContext:
    """Context view built specifically for spec generation."""

    query: UserQuery
    intent: Intent
    corpus_summary: CorpusSummary
    intent_analysis: IntentAnalysis | None = None
    session_brief: SessionBrief = field(default_factory=SessionBrief)
    user_brief: UserBrief = field(default_factory=UserBrief)


class SpecContextBuilder:
    """Builds deterministic, task-local context for spec builder agents."""

    def __init__(
        self, *, max_recent_turns: int = 5, max_history_items: int = 5
    ) -> None:
        self.max_recent_turns = max_recent_turns
        self.max_history_items = max_history_items

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        intent_analysis: IntentAnalysis | None = None,
    ) -> SpecBuildContext:
        corpus_summary = build_corpus_summary(corpus_package)
        session_brief = self._build_session_brief(session_context)
        user_brief = self._build_user_brief(user_context)
        return SpecBuildContext(
            query=query,
            intent=intent,
            corpus_summary=corpus_summary,
            intent_analysis=intent_analysis,
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

    def _build_task_hints(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_summary: CorpusSummary,
        user_brief: UserBrief,
    ) -> TaskHints:
        query_text = query.text.lower()
        mentioned_tables = _mentioned_names(query_text, corpus_summary.tables)
        mentioned_vector_collections = _mentioned_names(
            query_text, corpus_summary.vector_collections
        )
        mentioned_datasets = _mentioned_names(query_text, corpus_summary.datasets)
        mentioned_columns = _mentioned_columns(
            query_text, corpus_summary.tables + corpus_summary.vector_collections
        )
        metrics = _extract_metrics(query_text, mentioned_columns)
        output_type = _infer_output_type(query_text, intent)
        language = _infer_language(query, user_brief)
        return TaskHints(
            mentioned_tables=mentioned_tables,
            mentioned_columns=mentioned_columns,
            mentioned_vector_collections=mentioned_vector_collections,
            mentioned_datasets=mentioned_datasets,
            metrics=metrics,
            output_type=output_type,
            language=language,
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


def _mentioned_names(query_text: str, entries: list[dict[str, Any]]) -> list[str]:
    names = []
    for entry in entries:
        name = entry.get("name")
        if isinstance(name, str) and _name_matches_query(name, query_text):
            names.append(name)
    return names


def _mentioned_columns(query_text: str, entries: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for entry in entries:
        value = entry.get("columns", [])
        if not isinstance(value, list):
            continue
        for column in value:
            column_text = str(column)
            if column_text.lower() in query_text and column_text not in columns:
                columns.append(column_text)
    return columns


def _name_matches_query(name: str, query_text: str) -> bool:
    normalized_name = name.lower()
    spaced_name = normalized_name.replace("_", " ").replace("-", " ")
    return normalized_name in query_text or spaced_name in query_text


def _extract_metrics(query_text: str, mentioned_columns: list[str]) -> list[str]:
    # Metric identity comes from the available corpus schema. Do not inject a
    # fixed English or business vocabulary into every request; the Spec Builder
    # can preserve an unbound metric request in its structured requirements when
    # the schema has not established a concrete field yet.
    return [
        column for column in mentioned_columns if column.lower() in query_text
    ]


def _infer_output_type(query_text: str, intent: Intent) -> str | None:
    if intent == "report" or "report" in query_text or "briefing" in query_text:
        return "report"
    if "dashboard" in query_text:
        return "dashboard"
    if "table" in query_text:
        return "table"
    return None


def _infer_language(query: UserQuery, user_brief: UserBrief) -> str | None:
    metadata_language = query.metadata.get("language")
    if isinstance(metadata_language, str):
        return metadata_language
    preference_language = user_brief.preferences.get("language")
    if isinstance(preference_language, str):
        return preference_language
    preferred_language = user_brief.preferences.get("preferred_language")
    if isinstance(preferred_language, str):
        return preferred_language
    return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
