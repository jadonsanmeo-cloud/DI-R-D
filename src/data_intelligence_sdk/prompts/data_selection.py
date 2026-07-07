"""Prompt template for selecting task-relevant data."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from data_intelligence_sdk.context import SpecBuildContext
from data_intelligence_sdk.core.types import ExecutionSpec


class DataSelectionPrompt:
    """Builds chat messages for the data selection agent."""

    def select_messages(
        self,
        spec_build_context: SpecBuildContext,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "spec_build_context": asdict(spec_build_context),
                        "previous_spec": _to_jsonable(previous_spec),
                        "user_feedback": user_feedback,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        ]


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    return value


_SYSTEM_PROMPT = """You are the Data Selection agent for a data intelligence pipeline.

Choose the smallest relevant subset of data for the current user task. Use only
data described in spec_build_context.corpus_summary. Do not execute the task and
do not answer the user's question.
When user_feedback is present, treat it as the latest instruction. Use
previous_spec to understand what is being revised, and select data for the
revised task rather than the original query alone.
Revision feedback overrides the original query for the parts it changes. If the
original query asked for A and B, but user_feedback says B is no longer needed,
select only A and omit B from every selected_* field.

SelectedDataContext JSON contract:
{
  "selected_sources": ["string"],
  "selected_tables": ["string"],
  "selected_columns": {"table_or_collection": ["string"]},
  "selected_vector_collections": ["string"],
  "selected_documents": ["string"],
  "reasons": ["string"],
  "missing_information": ["string"],
  "confidence": 0.0
}

Selection rules:
- Select sources, tables, columns, vector collections, and documents only if they
  exist in spec_build_context.corpus_summary.
- Prefer task_hints when they are present, but verify them against corpus_summary.
- Include a short reason for each important selection.
- Put ambiguities or missing details in missing_information.
- Use confidence between 0 and 1.

Example output:
{
  "selected_sources": [
    "postgresql://demo/db",
    "postgresql://demo/db?schema=vectordb"
  ],
  "selected_tables": ["orders"],
  "selected_columns": {
    "orders": ["order_id", "customer_id", "country", "status", "revenue"],
    "document_chunks": ["chunk_id", "document_id", "content", "metadata"]
  },
  "selected_vector_collections": ["document_chunks"],
  "selected_documents": [],
  "reasons": [
    "The user explicitly asked about orders.",
    "The user explicitly asked about document chunks."
  ],
  "missing_information": [],
  "confidence": 0.95
}

Revision example:
- Original query: "Create a report about A and B."
- user_feedback: "B is no longer needed."
- Correct behavior: select only A. Do not keep B just because it appeared in the
  original query or previous_spec.
"""
