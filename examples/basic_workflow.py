"""Example workflow wiring for the Data Intelligence SDK.

This module intentionally lives outside ``src/data_intelligence_sdk`` because
concrete analyzer/spec/evidence/synthesis behavior belongs to consuming apps.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    EngineOutput,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.engines.report import ReportEngine
from data_intelligence_sdk.methods import register_csv_methods, register_vector_methods
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.interfaces import InMemoryInterfaceRegistry
from data_intelligence_sdk.runtime.method_hub import MethodHub


def _as_result_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"result": value}


class ExampleIntentAnalyzer:
    def analyze(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> Intent:
        del session_context, user_context
        text = query.text.lower()
        if any(
            term in text
            for term in ("report", "dashboard", "write up", "write-up", "briefing")
        ):
            return "report"
        if any(
            str(source).lower().endswith(".csv") for source in corpus_package.sources
        ) or any(
            term in text
            for term in (
                "data",
                "csv",
                "file",
                "row",
                "rows",
                "count",
                "sum",
                "total",
                "average",
                "revenue",
                "document",
                "documents",
                "search",
                "retrieve",
                "retrieval",
            )
        ):
            return "reason"
        return "unknown"


class ExampleSpecBuilder:
    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        del session_context, user_context
        capability_names = [
            "inspect_data",
            "filter_data",
            "aggregate_data",
            "answer_question",
        ]
        if any(
            str(source).lower().endswith(".csv") for source in corpus_package.sources
        ):
            capability_names.append("answer_csv_question")
        return ExecutionSpec(
            intent=intent,
            objective=query.text,
            data_requirements=list(corpus_package.sources),
            capability_requirements=[
                CapabilityRequirement(name=name) for name in capability_names
            ],
        )


class ExampleSpecConfirmation:
    def confirm(
        self,
        spec: ExecutionSpec,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        del session_context, user_context
        spec.confirmed = True
        return spec


class ExampleEvidenceCollector:
    def collect(self, spec: ExecutionSpec, output: EngineOutput) -> EvidenceBundle:
        metadata = output.metadata
        return EvidenceBundle(
            sources=list(metadata.get("sources", spec.data_requirements)),
            observations=list(metadata.get("observations", [])),
            steps=list(output.trace.steps),
            method_calls=list(output.trace.method_calls),
            interface_defs=list(metadata.get("interface_defs", [])),
            sandbox_results=[
                _as_result_dict(result)
                for result in metadata.get("sandbox_results", [])
            ],
            artifact_refs=list(output.trace.artifact_refs),
            log_refs=list(output.trace.log_refs),
        )


class ExampleSynthesizer:
    def synthesize(
        self, spec: ExecutionSpec, output: EngineOutput, evidence: EvidenceBundle
    ) -> FinalResponse:
        del spec
        return FinalResponse(
            answer=str(output.result),
            evidence=evidence,
            metadata={"engine_name": output.engine_name},
        )


def create_example_pipeline(
    *,
    engine: object | None = None,
    llm: object | None = None,
    model: str | None = None,
    api_key: str | None = None,
    config_path: str | Path | None = None,
    config_manager: ConfigManager | None = None,
    allow_method_generation: bool = True,
    method_hub: MethodHub | None = None,
    interface_registry: object | None = None,
    interface_builder: object | None = None,
    sandbox_executor: object | None = None,
) -> DataIntelligencePipeline:
    method_hub = method_hub or MethodHub()
    if not method_hub.list_methods():
        register_csv_methods(method_hub)
        register_vector_methods(method_hub)
    if engine is None:
        if llm is not None:
            engine = GeneralPurposeEngine(llm=llm)
        else:
            engine = GeneralPurposeEngine(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=config_manager,
                allow_method_generation=allow_method_generation,
            )
    interface_registry = interface_registry or InMemoryInterfaceRegistry()
    registry = InMemoryEngineRegistry(fallback_engine=engine)
    return DataIntelligencePipeline(
        intent_analyzer=ExampleIntentAnalyzer(),
        spec_builder=ExampleSpecBuilder(),
        spec_confirmation=ExampleSpecConfirmation(),
        engine_registry=registry,
        evidence_collector=ExampleEvidenceCollector(),
        synthesizer=ExampleSynthesizer(),
        method_hub=method_hub,
        interface_registry=interface_registry,
        interface_builder=interface_builder,
        sandbox_executor=sandbox_executor,
    )


def create_report_pipeline(
    *,
    method_hub: MethodHub | None = None,
    interface_registry: object | None = None,
) -> DataIntelligencePipeline:
    """Create an offline report-generation pipeline for examples."""

    return create_example_pipeline(
        engine=ReportEngine(),
        method_hub=method_hub,
        interface_registry=interface_registry,
    )
