"""Prompt template for LLM-backed execution spec building."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from data_intelligence_sdk.context import SpecBuildContext, SpecContextBuilder
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)


class SpecBuilderPrompt:
    """Builds chat messages for initial and revised execution specs."""

    def __init__(self, context_builder: SpecContextBuilder | None = None) -> None:
        self.context_builder = context_builder or SpecContextBuilder()

    def build_messages(
        self,
        *,
        query: UserQuery | None = None,
        intent: Intent | None = None,
        corpus_package: DataCorpusPackage | None = None,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        spec_build_context: SpecBuildContext | None = None,
        selected_data_context: object | None = None,
    ) -> list[dict[str, str]]:
        return self._messages(
            mode="build",
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
            spec_build_context=spec_build_context,
            selected_data_context=selected_data_context,
        )

    def revise_messages(
        self,
        *,
        previous_spec: ExecutionSpec,
        user_feedback: str,
        query: UserQuery | None = None,
        intent: Intent | None = None,
        corpus_package: DataCorpusPackage | None = None,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        spec_build_context: SpecBuildContext | None = None,
        selected_data_context: object | None = None,
    ) -> list[dict[str, str]]:
        return self._messages(
            mode="revise",
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
            spec_build_context=spec_build_context,
            selected_data_context=selected_data_context,
            previous_spec=previous_spec,
            user_feedback=user_feedback,
        )

    def _messages(
        self,
        *,
        mode: str,
        query: UserQuery | None,
        intent: Intent | None,
        corpus_package: DataCorpusPackage | None,
        session_context: SessionContext | None,
        user_context: UserContext | None,
        spec_build_context: SpecBuildContext | None,
        selected_data_context: object | None,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> list[dict[str, str]]:
        spec_build_context = spec_build_context or self._build_context(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        task = {
            "mode": mode,
            "query": _to_jsonable(spec_build_context.query),
            "intent": spec_build_context.intent,
            "spec_build_context": _to_jsonable(spec_build_context),
            "selected_data_context": _to_jsonable(selected_data_context),
            "corpus_summary": _to_jsonable(spec_build_context.corpus_summary),
            "corpus_package": _to_jsonable(corpus_package),
            "session_context": _to_jsonable(session_context),
            "user_context": _to_jsonable(user_context),
            "previous_spec": _to_jsonable(previous_spec),
            "user_feedback": user_feedback,
        }
        return [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(task, ensure_ascii=True, sort_keys=True),
            },
        ]

    def _build_context(
        self,
        query: UserQuery | None,
        intent: Intent | None,
        corpus_package: DataCorpusPackage | None,
        session_context: SessionContext | None,
        user_context: UserContext | None,
    ) -> SpecBuildContext:
        if query is None or intent is None or corpus_package is None:
            raise ValueError(
                "query, intent, and corpus_package are required when spec_build_context is not provided."
            )
        return self.context_builder.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    return value


_SYSTEM_PROMPT = """You are the Spec Builder agent for a data intelligence pipeline.

Create an ExecutionSpec draft from the provided context. Return only one JSON object.
Do not execute the task, answer the user's question, or include markdown.
Use spec_build_context as the task-local compacted context. It includes query,
intent, corpus_summary, session_brief, user_brief, and deterministic task_hints.
Use corpus_summary as the primary readable map of the available data. Use
corpus_package only as the raw backing context.
If selected_data_context is present, it is a hard boundary, not a suggestion:
- data_requirements must equal selected_data_context.selected_sources;
- scope tables must come only from selected_data_context.selected_tables;
- scope vector collections must come only from selected_data_context.selected_vector_collections;
- columns must come only from selected_data_context.selected_columns;
- do not add metrics, group_by fields, tables, collections, or sources outside selected_data_context unless user_feedback explicitly asks for a revision.

ExecutionSpec JSON contract:
{
  "objective": "string",
  "data_requirements": ["string"],
  "capability_requirements": [
    {
      "name": "string",
      "description": "string or null",
      "input_schema": {},
      "output_schema": {},
      "constraints": {},
      "metadata": {}
    }
  ],
  "constraints": {},
  "engine_hint": "string or null"
}

Field rules:
- objective: rewrite the user request into one clear executable objective.
- data_requirements: include only source refs from corpus_package.sources, corpus_summary.sources, or refs explicitly present in corpus metadata.
- capability_requirements: describe capabilities the engine/runtime must resolve; do not name concrete method calls unless the capability itself is method-specific.
- constraints: structured task constraints such as filters, metrics, group_by, language, output_format, evidence_required, and scope.
- engine_hint: use "report" for report tasks; otherwise use null unless a specific engine is clearly required.

Do not include confirmed. The confirmation component owns that field.
Do not invent data sources, schemas, columns, tables, or collections.
Do not include final answers, calculations, evidence, logs, or execution steps.

Allowed capability names:
- inspect_data
- query_structured_data
- answer_csv_question
- retrieve_documents
- aggregate_data
- filter_data
- generate_report
- summarize_corpus
- answer_question

When mode is revise:
- preserve valid parts of previous_spec;
- only change what user_feedback requests;
- keep the original user query and corpus boundaries in force.

Example output:
{
  "objective": "Calculate total completed revenue from the available sales data.",
  "data_requirements": ["sales.csv"],
  "capability_requirements": [
    {
      "name": "inspect_data",
      "description": "Inspect the available sales data schema and sample rows.",
      "input_schema": {"source": "string"},
      "output_schema": {"columns": "list", "sample_rows": "list"},
      "constraints": {},
      "metadata": {"reason": "Confirm that the revenue and status fields exist."}
    },
    {
      "name": "aggregate_data",
      "description": "Sum the revenue field after applying the requested filters.",
      "input_schema": {"source": "string", "metric": "string", "filters": "object"},
      "output_schema": {"total": "number"},
      "constraints": {"aggregation": "sum", "metric": "revenue", "filters": {"status": "complete"}},
      "metadata": {}
    }
  ],
  "constraints": {
    "metric": "revenue",
    "aggregation": "sum",
    "filters": {"status": "complete"},
    "output_format": "concise_answer",
    "evidence_required": true
  },
  "engine_hint": null
}
"""
