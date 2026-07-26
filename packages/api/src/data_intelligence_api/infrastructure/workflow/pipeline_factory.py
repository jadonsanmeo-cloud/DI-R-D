"""Application workflow wiring around the Data Intelligence SDK.

Concrete analyzer/spec/evidence/synthesis behavior belongs to the API application.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
from typing import Any
from uuid import UUID

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
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.deep_agent_sandbox import (
    DeepAgentSandboxSession,
    SandboxSessionProvider,
)
from data_intelligence_sdk.runtime.interfaces import InMemoryInterfaceRegistry
from data_intelligence_sdk.runtime.llm_client import LLMClient, OpenAICompatibleLLMClient
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient
from data_intelligence_sdk.sandbox.artifacts import FilesystemArtifactStore
from data_intelligence_sdk.spec import LLMSpecBuilder
from data_intelligence_sdk.spec.markdown_builder import LLMMarkdownSpecBuilder
from data_intelligence_api.infrastructure.intent import AxiomIntentServiceAnalyzer


def _as_result_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"result": value}


class _AxiomSandboxProvider:
    """Provision and stage one AXIOM sandbox per pipeline request."""

    def __init__(
        self,
        client: object,
        *,
        workspace_id: UUID,
        cleanup: bool,
    ) -> None:
        self.client = client
        self.workspace_id = workspace_id
        self.cleanup = cleanup

    @contextmanager
    def open(self, corpus_package: DataCorpusPackage):
        sandbox = self.client.create_sandbox(self.workspace_id)
        try:
            sandbox.wait_until_ready()
            source_paths = self._stage_sources(sandbox, corpus_package)
            yield DeepAgentSandboxSession(
                sandbox=sandbox,
                source_paths=source_paths,
            )
        finally:
            if self.cleanup:
                with suppress(Exception):
                    sandbox.delete()

    @staticmethod
    def _stage_sources(
        sandbox: object,
        corpus_package: DataCorpusPackage,
    ) -> dict[str, str]:
        source_paths: dict[str, str] = {}
        used_names: set[str] = set()
        for index, source in enumerate(corpus_package.sources):
            source_text = str(source)
            host_path = Path(source_text)
            if not host_path.is_file():
                raise ValueError(
                    "The sandbox runtime currently requires local source files: "
                    f"{source_text}"
                )
            filename = host_path.name
            if filename in used_names:
                filename = f"{index}_{filename}"
            used_names.add(filename)
            relative_path = f"input/{filename}"
            sandbox.write(relative_path, host_path.read_bytes())
            source_paths[source_text] = f"/workspace/{relative_path}"
        return source_paths


def _configure_axiom_sandbox_provider(
    *,
    config_manager: object,
) -> SandboxSessionProvider:
    """Build the request-scoped AXIOM sandbox provider."""

    settings = config_manager.sandbox_settings()
    if not settings.enabled:
        raise RuntimeError("AXIOM sandbox configuration is disabled.")
    if not settings.workspace_id:
        raise ValueError(
            "SANDBOX_WORKSPACE_ID is required when SANDBOX_ENABLED=true."
        )

    try:
        from axiom_sandbox_client import SandboxClient
    except ImportError as exc:
        raise RuntimeError(
            "AXIOM sandbox integration is enabled but axiom-sandbox-client is not "
            "installed. Install the local AXIOM client package."
        ) from exc

    sandbox_client = SandboxClient(settings.endpoint, token=settings.token)
    keep_sandbox = str(os.environ.get("AXIOM_SANDBOX_KEEP", "false")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return _AxiomSandboxProvider(
        sandbox_client,
        workspace_id=UUID(settings.workspace_id),
        cleanup=not keep_sandbox,
    )


class ExampleIntentAnalyzer:
    def analyze(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> Intent:
        del corpus_package, session_context, user_context
        text = query.text.lower()
        if any(
            term in text
            for term in (
                "report",
                "dashboard",
                "write up",
                "write-up",
                "briefing",
                "summarize",
                "summary",
                "overview",
            )
        ):
            return "report"
        if any(
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
        return "general"


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
        del query, corpus_package, session_context, user_context
        return ExecutionSpec(
            intent=intent,
            objective=f"{previous_spec.objective}\nRevision: {user_feedback}",
            data_requirements=list(previous_spec.data_requirements),
            capability_requirements=list(previous_spec.capability_requirements),
            constraints={
                **previous_spec.constraints,
                "user_revision_feedback": user_feedback,
            },
            confirmed=False,
            engine_hint=previous_spec.engine_hint,
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
    spec_builder: object | None = None,
    spec_llm_client: LLMClient | None = None,
    use_llm_spec_builder: bool = False,
    allow_method_generation: bool = True,
    method_hub: MethodHub | None = None,
    mcp_client: MCPMethodClient | None = None,
    interface_registry: object | None = None,
    interface_builder: object | None = None,
    sandbox_executor: object | None = None,
    sandbox_provider: SandboxSessionProvider | None = None,
    artifact_store: object | None = None,
    logger: RuntimeLogger | None = None,
    intent_service_base_url: str | None = None,
    default_organization_id: str | None = None,
    markdown_report_engine: object | None = None,
) -> DataIntelligencePipeline:
    resolved_config_manager = config_manager or ConfigManager(config_path)
    if artifact_store is None:
        artifact_settings = resolved_config_manager.artifact_settings()
        artifact_store = FilesystemArtifactStore(artifact_settings.root)
    if spec_builder is None:
        if use_llm_spec_builder:
            settings = resolved_config_manager.openrouter_settings()
            spec_llm_client = spec_llm_client or OpenAICompatibleLLMClient(
                base_url=settings.base_url,
                api_key=api_key or settings.api_key,
                model=model or settings.model,
            )
            spec_builder = LLMSpecBuilder(
                spec_llm_client,
                require_actionable_spec=True,
                default_missing_requirements=True,
                logger=logger,
            )
        else:
            spec_builder = ExampleSpecBuilder()
    if sandbox_provider is None:
        sandbox_settings = resolved_config_manager.sandbox_settings()
        if sandbox_settings.enabled:
            sandbox_provider = _configure_axiom_sandbox_provider(
                config_manager=resolved_config_manager,
            )
    if engine is None:
        if llm is not None:
            engine = GeneralPurposeEngine(llm=llm)
        else:
            engine = GeneralPurposeEngine(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=resolved_config_manager,
                allow_method_generation=allow_method_generation,
            )
    interface_registry = interface_registry or InMemoryInterfaceRegistry()
    registry = InMemoryEngineRegistry(fallback_engine=engine)
    registry.register(engine)
    intent_analyzer = (
        AxiomIntentServiceAnalyzer(base_url=intent_service_base_url)
        if intent_service_base_url
        else ExampleIntentAnalyzer()
    )
    resolved_markdown_report_engine = markdown_report_engine
    if resolved_markdown_report_engine is None:
        resolved_markdown_report_engine = (
            ReportEngine() if use_llm_spec_builder else engine
        )
    return DataIntelligencePipeline(
        intent_analyzer=intent_analyzer,
        spec_builder=spec_builder,
        spec_confirmation=ExampleSpecConfirmation(),
        engine_registry=registry,
        evidence_collector=ExampleEvidenceCollector(),
        synthesizer=ExampleSynthesizer(),
        method_hub=method_hub,
        mcp_client=mcp_client,
        interface_registry=interface_registry,
        interface_builder=interface_builder,
        sandbox_executor=sandbox_executor,
        sandbox_provider=sandbox_provider,
        artifact_store=artifact_store,
        include_evidence=False,
        logger=logger,
        markdown_spec_builder=(
            LLMMarkdownSpecBuilder(spec_llm_client)
            if use_llm_spec_builder and spec_llm_client is not None
            else None
        ),
        markdown_report_engine=resolved_markdown_report_engine,
        default_organization_id=(
            default_organization_id or os.getenv("DEFAULT_ORGANIZATION_ID", "test-org")
        ),
    )


def create_report_pipeline(
    *,
    method_hub: MethodHub | None = None,
    mcp_client: MCPMethodClient | None = None,
    interface_registry: object | None = None,
    logger: RuntimeLogger | None = None,
) -> DataIntelligencePipeline:
    """Create an offline report-generation pipeline for examples."""

    return create_example_pipeline(
        engine=ReportEngine(),
        method_hub=method_hub,
        mcp_client=mcp_client,
        interface_registry=interface_registry,
        logger=logger,
    )
