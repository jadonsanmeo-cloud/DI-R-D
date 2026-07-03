from __future__ import annotations

from typing import Any

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


def _source_summary(sources: list[str]) -> str:
    if not sources:
        return "No sources were provided."
    return "\n".join(f"- {source}" for source in sources)


def _dataset_summary(catalog: dict[str, Any]) -> str:
    datasets = catalog.get("datasets", [])
    if not datasets:
        return "No catalog datasets were provided."
    lines = []
    for dataset in datasets:
        name = dataset.get("name", "unnamed")
        kind = dataset.get("kind", "dataset")
        description = dataset.get("description", "No description provided.")
        lines.append(f"- {name} ({kind}): {description}")
    return "\n".join(lines)


def _schema_summary(schemas: dict[str, Any]) -> str:
    lines = []
    for table_name, table in schemas.get("tables", {}).items():
        columns = ", ".join(table.get("columns", [])) or "no columns listed"
        lines.append(f"- table {table_name}: {columns}")
    for collection_name, collection in schemas.get("vector_collections", {}).items():
        columns = ", ".join(collection.get("columns", [])) or "no columns listed"
        lines.append(f"- vector collection {collection_name}: {columns}")
    return "\n".join(lines) if lines else "No schema metadata was provided."


class ReportEngine:
    name = "report"

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent == "report"

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del user_context
        runtime.run_context.record_step(
            "report_start",
            inputs={
                "objective": spec.objective,
                "sources": corpus_package.sources,
            },
        )

        catalog = corpus_package.metadata.get("catalog", {})
        if not isinstance(catalog, dict):
            catalog = {}
        result = {
            "title": "Data Corpus Report",
            "summary": catalog.get(
                "summary",
                "A basic report generated from the provided data corpus package.",
            ),
            "sections": [
                {"heading": "Sources", "content": _source_summary(corpus_package.sources)},
                {"heading": "Datasets", "content": _dataset_summary(catalog)},
                {"heading": "Schema", "content": _schema_summary(corpus_package.schemas)},
            ],
        }

        return runtime.run_context.build_output(
            engine_name=self.name,
            result=result,
            metadata={
                "sources": corpus_package.sources,
                "report_format": "dict",
            },
        )
