"""Run an interactive LLM spec-builder confirmation flow.

Examples:
    uv run python examples/run_interactive_spec_flow.py
    uv run python examples/run_interactive_spec_flow.py --query "Create a report about this package"
    uv run python examples/run_interactive_spec_flow.py --base-url http://localhost:20128/v1 --model cx/gpt-5.5 --api-key sk-...
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from basic_workflow import (  # noqa: E402
    ExampleEvidenceCollector,
    ExampleIntentAnalyzer,
    ExampleSynthesizer,
)
from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline  # noqa: E402
from data_intelligence_sdk.core.types import (  # noqa: E402
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.datahub import LLMDataHubClusterer  # noqa: E402
from data_intelligence_sdk.registry.engine_registry import (  # noqa: E402
    InMemoryEngineRegistry,
)
from data_intelligence_sdk.runtime.engine_runtime import (  # noqa: E402
    EngineRuntimeContext,
)
from data_intelligence_sdk.runtime.llm_client import (  # noqa: E402
    OpenAICompatibleLLMClient,
)
from data_intelligence_sdk.spec import (  # noqa: E402
    ConsoleSpecConfirmationProvider,
    DefaultClusterSpecBuilder,
    DefaultSpecConfirmation,
    LLMClusterSpecSelector,
    LLMSpecBuilder,
    SpecContextBuilder,
)

DEFAULT_PACKAGE = EXAMPLES_DIR / "data_corpus_package" / "data_corpus_package.json"


class TracingLLMClient:
    """Prints every JSON LLM call used by selector/spec builder."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.call_count = 0

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.call_count += 1
        _print_section(f"LLM Call {self.call_count} Input")
        _print_json({"messages": messages})
        result = self.inner.complete_json(messages)
        _print_section(f"LLM Call {self.call_count} Output")
        _print_json(result)
        return result


class TracingSpecContextBuilder:
    """Prints SpecContextBuilder input and output."""

    def __init__(self, inner: SpecContextBuilder) -> None:
        self.inner = inner

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> object:
        _print_section("SpecContextBuilder Input")
        _print_json(
            {
                "query": query,
                "intent": intent,
                "corpus_package": corpus_package,
                "session_context": session_context,
                "user_context": user_context,
            }
        )
        result = self.inner.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        _print_section("SpecContextBuilder Output")
        _print_json(result)
        return result


class TracingDataSelector:
    """Prints Data Selection Agent input and output."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def select(
        self,
        spec_build_context: object,
        previous_spec: object | None = None,
        user_feedback: str | None = None,
    ) -> object:
        _print_section("LLMDataSelector Input")
        _print_json(
            {
                "spec_build_context": spec_build_context,
                "previous_spec": previous_spec,
                "user_feedback": user_feedback,
            }
        )
        result = self.inner.select(
            spec_build_context,
            previous_spec=previous_spec,
            user_feedback=user_feedback,
        )
        _print_section("LLMDataSelector Output")
        _print_json(result)
        return result


class TracingDataHubClusterer:
    """Prints DataHub clustering input and output."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def cluster(self, corpus_package: DataCorpusPackage) -> object:
        _print_section("DataHubClusterer Input")
        _print_json({"corpus_package": corpus_package})
        result = self.inner.cluster(corpus_package)
        _print_section("DataHubClusterer Output")
        _print_json(result)
        return result


class TracingClusterSpecBuilder:
    """Prints prepared specs for all datahub clusters."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def build_specs(
        self,
        corpus_package: DataCorpusPackage,
        clustering_result: object,
        intent: Intent,
    ) -> list[object]:
        _print_section("ClusterSpecBuilder Input")
        _print_json(
            {
                "corpus_package": corpus_package,
                "clustering_result": clustering_result,
                "intent": intent,
            }
        )
        result = self.inner.build_specs(corpus_package, clustering_result, intent)
        _print_section("ClusterSpecBuilder Output")
        _print_json(result)
        return result


class TracingClusterSpecSelector:
    """Prints cluster spec selection input and output."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def select(self, **kwargs: object) -> object:
        _print_section("ClusterSpecSelector Input")
        _print_json(kwargs)
        result = self.inner.select(**kwargs)
        _print_section("ClusterSpecSelector Output")
        _print_json(result)
        return result


class TracingSpecBuilder:
    """Prints Spec Builder input and output around build/revise."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        _print_section("LLMSpecBuilder.build Input")
        _print_json(
            {
                "query": query,
                "intent": intent,
                "corpus_package": corpus_package,
                "session_context": session_context,
                "user_context": user_context,
            }
        )
        result = self.inner.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        _print_section("LLMSpecBuilder.build Output")
        _print_json(result)
        return result

    def revise(
        self,
        *,
        previous_spec: ExecutionSpec,
        user_feedback: str,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        _print_section("LLMSpecBuilder.revise Input")
        _print_json(
            {
                "previous_spec": previous_spec,
                "user_feedback": user_feedback,
                "query": query,
                "intent": intent,
                "corpus_package": corpus_package,
                "session_context": session_context,
                "user_context": user_context,
            }
        )
        result = self.inner.revise(
            previous_spec=previous_spec,
            user_feedback=user_feedback,
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
        )
        _print_section("LLMSpecBuilder.revise Output")
        _print_json(result)
        return result


class TracingSpecConfirmationProvider:
    """Prints confirmation provider request and user decision."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def request_confirmation(self, request: object) -> object:
        _print_section("SpecConfirmation Input")
        _print_json(request)
        result = self.inner.request_confirmation(request)
        _print_section("SpecConfirmation Output")
        _print_json(result)
        return result


class SpecEchoEngine:
    """Demo engine that returns the confirmed spec instead of executing data work."""

    name = "spec_echo"

    def can_handle(self, spec: ExecutionSpec) -> bool:
        del spec
        return True

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del corpus_package, user_context
        _print_section("SpecEchoEngine Input")
        _print_json({"spec": spec})
        runtime.run_context.record_step(
            "confirmed_spec_received",
            inputs={"objective": spec.objective, "confirmed": spec.confirmed},
        )
        output = runtime.run_context.build_output(
            engine_name=self.name,
            result={
                "confirmed_spec": asdict(spec),
                "note": "Spec flow completed. This demo stops before real engine execution.",
            },
        )
        _print_section("SpecEchoEngine Output")
        _print_json(output)
        return output


def _resolve_package_ref(package_path: Path, ref: str) -> str:
    if urlparse(ref).scheme:
        return ref
    path = Path(ref)
    if not path.is_absolute():
        path = package_path.parent / path
    return str(path)


def _load_package_json(path: str) -> DataCorpusPackage:
    package_path = Path(path)
    package_payload = json.loads(package_path.read_text(encoding="utf-8"))

    vectordb_path = _resolve_package_ref(package_path, package_payload["vectordb"])
    db_path = _resolve_package_ref(package_path, package_payload["db"])
    schema_path = _resolve_package_ref(package_path, package_payload["schema"])
    catalog_path = _resolve_package_ref(package_path, package_payload["catalog"])

    schemas = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))

    return DataCorpusPackage(
        sources=[vectordb_path, db_path],
        schemas=schemas,
        metadata={
            "catalog": catalog,
            "package": {
                "vectordb": vectordb_path,
                "db": db_path,
                "schema": schema_path,
                "catalog": catalog_path,
            },
        },
    )


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_to_jsonable(value), indent=2, ensure_ascii=False))


def _print_section(title: str) -> None:
    print("")
    print(f"=== {title} ===")


def _print_corpus_summary(corpus_package: DataCorpusPackage) -> None:
    package = corpus_package.metadata.get("package", {})
    catalog = corpus_package.metadata.get("catalog", {})
    if not isinstance(package, dict):
        package = {}
    if not isinstance(catalog, dict):
        catalog = {}

    print("Package refs:")
    for name in ("schema", "catalog", "db", "vectordb"):
        if name in package:
            print(f"- {name}: {package[name]}")

    catalog_summary = catalog.get("summary")
    if catalog_summary:
        print("")
        print("Catalog summary:")
        print(catalog_summary)

    tables = corpus_package.schemas.get("tables", {})
    if isinstance(tables, dict) and tables:
        print("")
        print("Tables from schema.json:")
        for table_name, table in tables.items():
            if not isinstance(table, dict):
                print(f"- {table_name}")
                continue
            columns = ", ".join(str(column) for column in table.get("columns", []))
            description = table.get("description", "No description.")
            print(f"- {table_name}: {columns}")
            print(f"  {description}")

    vector_collections = corpus_package.schemas.get("vector_collections", {})
    if isinstance(vector_collections, dict) and vector_collections:
        print("")
        print("Vector collections from schema.json:")
        for collection_name, collection in vector_collections.items():
            if not isinstance(collection, dict):
                print(f"- {collection_name}")
                continue
            columns = ", ".join(str(column) for column in collection.get("columns", []))
            description = collection.get("description", "No description.")
            print(f"- {collection_name}: {columns}")
            print(f"  {description}")

    datasets = catalog.get("datasets", [])
    if isinstance(datasets, list) and datasets:
        print("")
        print("Datasets from catalog.json:")
        for dataset in datasets:
            if not isinstance(dataset, dict):
                continue
            print(
                f"- {dataset.get('name', 'unnamed')} "
                f"({dataset.get('kind', 'dataset')}): "
                f"{dataset.get('description', 'No description.')}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the interactive spec-builder confirmation flow."
    )
    parser.add_argument(
        "--package",
        default=str(DEFAULT_PACKAGE),
        help="Path to a data_corpus_package.json manifest.",
    )
    parser.add_argument(
        "--query",
        default="Summarize this data corpus package.",
        help="User query to turn into an ExecutionSpec.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the base URL from the configured OpenRouter model.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Override the API key from the configured OpenRouter model.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the configured OpenRouter model name.",
    )
    args = parser.parse_args()

    corpus_package = _load_package_json(args.package)
    raw_llm_client = OpenAICompatibleLLMClient(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    llm_client = TracingLLMClient(raw_llm_client)
    datahub_clusterer = TracingDataHubClusterer(LLMDataHubClusterer(llm_client))
    cluster_spec_builder = TracingClusterSpecBuilder(DefaultClusterSpecBuilder())
    cluster_spec_selector = TracingClusterSpecSelector(
        LLMClusterSpecSelector(llm_client)
    )
    spec_builder = TracingSpecBuilder(
        LLMSpecBuilder(
            llm_client,
            datahub_clusterer=datahub_clusterer,
            cluster_spec_builder=cluster_spec_builder,
            cluster_spec_selector=cluster_spec_selector,
        )
    )
    spec_confirmation = DefaultSpecConfirmation(
        TracingSpecConfirmationProvider(ConsoleSpecConfirmationProvider())
    )
    engine = SpecEchoEngine()
    registry = InMemoryEngineRegistry(fallback_engine=engine)
    registry.register(engine)
    pipeline = DataIntelligencePipeline(
        intent_analyzer=ExampleIntentAnalyzer(),
        spec_builder=spec_builder,
        spec_confirmation=spec_confirmation,
        engine_registry=registry,
        evidence_collector=ExampleEvidenceCollector(),
        synthesizer=ExampleSynthesizer(),
    )

    print("=== Data Corpus Package ===")
    print(f"Package: {args.package}")
    print("Sources:")
    for source in corpus_package.sources:
        print(f"- {source}")
    print("")
    print("=== Loaded Schema/Catalog Summary ===")
    _print_corpus_summary(corpus_package)
    print("")
    print("=== User Query ===")
    print(args.query)
    print("")
    print("=== Flow ===")
    print(
        "DataHubClusterer -> ClusterSpecBuilder -> "
        "ClusterSpecSelector -> LLMSpecBuilder -> SpecConfirmation"
    )

    response = pipeline.run(UserQuery(args.query), corpus_package)

    print("")
    print("=== Final Demo Output ===")
    _print_json(response.answer)
    if response.evidence is not None:
        print("")
        print("=== Evidence Trace ===")
        _print_json(asdict(response.evidence))


if __name__ == "__main__":
    main()
