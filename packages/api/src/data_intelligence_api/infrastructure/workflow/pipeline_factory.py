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
from data_intelligence_sdk.intent import IntentAnalysis
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.registry.engine_selector import (
    EngineSelector,
    LLMEngineSelector,
)
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.sandbox import (
    EngineSandboxSession,
    SandboxSessionProvider,
)
from data_intelligence_sdk.runtime.interfaces import InMemoryInterfaceRegistry
from data_intelligence_sdk.runtime.llm_client import (
    LLMClient,
    OpenAICompatibleLLMClient,
)
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient, MCPToolDefinition
from data_intelligence_sdk.sandbox.artifacts import FilesystemArtifactStore
from data_intelligence_sdk.spec import LLMSpecBuilder
from data_intelligence_api.infrastructure.intent import AxiomIntentServiceAnalyzer

from data_intelligence_api.infrastructure.workflow.docker_sandbox import (
    docker_provider_from_env,
)


def _env_flag(name: str, *, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _as_result_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"result": value}


class _ReportDefaultsSpecBuilder:
    """Apply API report-delivery defaults without overriding explicit choices."""

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        spec = self.delegate.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        return self._apply(spec)

    def build_with_intent_analysis(
        self,
        query: UserQuery,
        intent_analysis: IntentAnalysis,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        build_with_intent_analysis = getattr(
            self.delegate,
            "build_with_intent_analysis",
            None,
        )
        if callable(build_with_intent_analysis):
            spec = build_with_intent_analysis(
                query,
                intent_analysis,
                corpus_package,
                session_context,
                user_context,
            )
        else:
            spec = self.delegate.build(
                query,
                intent_analysis.intent,
                corpus_package,
                session_context,
                user_context,
            )
        return self._apply(spec)

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
        spec = self.delegate.revise(
            previous_spec=previous_spec,
            user_feedback=user_feedback,
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
        )
        return self._apply(spec)

    def revise_with_intent_analysis(
        self,
        *,
        previous_spec: ExecutionSpec,
        user_feedback: str,
        query: UserQuery,
        intent_analysis: IntentAnalysis,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        revise_with_intent_analysis = getattr(
            self.delegate,
            "revise_with_intent_analysis",
            None,
        )
        if callable(revise_with_intent_analysis):
            spec = revise_with_intent_analysis(
                previous_spec=previous_spec,
                user_feedback=user_feedback,
                query=query,
                intent_analysis=intent_analysis,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
            )
        else:
            spec = self.delegate.revise(
                previous_spec=previous_spec,
                user_feedback=user_feedback,
                query=query,
                intent=intent_analysis.intent,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
            )
        return self._apply(spec)

    @staticmethod
    def _apply(spec: ExecutionSpec) -> ExecutionSpec:
        if spec.intent != "report":
            return spec
        spec.engine_hint = spec.engine_hint or "report"
        spec.constraints = dict(spec.constraints)
        spec.constraints.setdefault("output_format", "html")
        return spec


class _AxiomSandboxProvider:
    """Provision and stage one AXIOM sandbox per pipeline request."""

    def __init__(
        self,
        client: object,
        *,
        workspace_id: UUID,
        cleanup: bool,
        capability_profiles: tuple[str, ...] = (),
    ) -> None:
        self.client = client
        self.workspace_id = workspace_id
        self.cleanup = cleanup
        self.capability_profiles = capability_profiles

    @contextmanager
    def open(self, corpus_package: DataCorpusPackage):
        sandbox = self.client.create_sandbox(
            self.workspace_id,
            capability_profiles=list(self.capability_profiles),
        )
        try:
            sandbox.wait_until_ready()
            source_paths = self._stage_sources(sandbox, corpus_package)
            yield EngineSandboxSession(
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
    method_hub_enabled: bool,
) -> SandboxSessionProvider:
    """Build the request-scoped AXIOM sandbox provider."""

    settings = config_manager.sandbox_settings()
    if not settings.enabled:
        raise RuntimeError("AXIOM sandbox configuration is disabled.")
    if not settings.workspace_id:
        raise ValueError("SANDBOX_WORKSPACE_ID is required when SANDBOX_ENABLED=true.")

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
        capability_profiles=("method_hub",) if method_hub_enabled else (),
    )


def _configure_request_sandbox_provider(
    *,
    config_manager: object,
    method_hub_enabled: bool,
) -> SandboxSessionProvider:
    """Select the configured request-scoped sandbox implementation."""

    backend = os.environ.get("SANDBOX_BACKEND", "axiom").strip().lower()
    if backend == "axiom":
        return _configure_axiom_sandbox_provider(
            config_manager=config_manager,
            method_hub_enabled=method_hub_enabled,
        )
    if backend == "docker":
        return docker_provider_from_env()
    raise ValueError(
        "SANDBOX_BACKEND must be either 'axiom' or 'docker', " f"not {backend!r}."
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
    engine_selector: EngineSelector | None = None,
    use_llm_spec_builder: bool = False,
    allow_method_generation: bool = True,
    force_report_code_agent: bool | None = None,
    mcp_client: MCPMethodClient | None = None,
    mcp_tools: tuple[MCPToolDefinition, ...] = (),
    method_hub_enabled: bool | None = None,
    interface_registry: object | None = None,
    interface_builder: object | None = None,
    sandbox_executor: object | None = None,
    sandbox_provider: SandboxSessionProvider | None = None,
    artifact_store: object | None = None,
    logger: RuntimeLogger | None = None,
    intent_service_base_url: str | None = None,
) -> DataIntelligencePipeline:
    resolved_config_manager = config_manager or ConfigManager(config_path)
    resolved_method_hub_enabled = (
        mcp_client is not None if method_hub_enabled is None else method_hub_enabled
    )
    resolved_mcp_tools = mcp_tools
    if method_hub_enabled is None and mcp_client is not None and not resolved_mcp_tools:
        resolved_mcp_tools = tuple(mcp_client.list_tools())
    shared_llm_client = spec_llm_client
    if force_report_code_agent is None:
        force_report_code_agent = _env_flag(
            "REPORT_FORCE_CODE_AGENT",
            default=False,
        )
    if artifact_store is None:
        artifact_settings = resolved_config_manager.artifact_settings()
        artifact_store = FilesystemArtifactStore(artifact_settings.root)
    if spec_builder is None:
        if use_llm_spec_builder:
            settings = resolved_config_manager.openrouter_settings()
            shared_llm_client = shared_llm_client or OpenAICompatibleLLMClient(
                base_url=settings.base_url,
                api_key=api_key or settings.api_key,
                model=model or settings.model,
            )
            spec_builder = LLMSpecBuilder(
                shared_llm_client,
                require_actionable_spec=True,
                default_missing_requirements=True,
            )
        else:
            spec_builder = ExampleSpecBuilder()
    spec_builder = _ReportDefaultsSpecBuilder(spec_builder)
    if sandbox_provider is None:
        sandbox_settings = resolved_config_manager.sandbox_settings()
        if sandbox_settings.enabled:
            sandbox_provider = _configure_request_sandbox_provider(
                config_manager=resolved_config_manager,
                method_hub_enabled=resolved_method_hub_enabled,
            )
    uses_default_engine = engine is None
    if uses_default_engine:
        if llm is not None:
            general_engine = GeneralPurposeEngine(llm=llm)
            report_engine = ReportEngine(
                llm=llm,
                force_code_agent=force_report_code_agent,
            )
        else:
            general_engine = GeneralPurposeEngine(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=resolved_config_manager,
                allow_method_generation=allow_method_generation,
            )
            report_engine = ReportEngine(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=resolved_config_manager,
                force_code_agent=force_report_code_agent,
            )
        if engine_selector is None:
            if shared_llm_client is None:
                settings = resolved_config_manager.openrouter_settings()
                shared_llm_client = OpenAICompatibleLLMClient(
                    base_url=settings.base_url,
                    api_key=api_key or settings.api_key,
                    model=model or settings.model,
                )
            engine_selector = LLMEngineSelector(shared_llm_client)
        registry = InMemoryEngineRegistry(
            selector=engine_selector,
            fallback_engine_name=general_engine.name,
        )
        registry.register(general_engine)
        registry.register(report_engine)
    else:
        registry = InMemoryEngineRegistry(fallback_engine=engine)
        registry.register(engine)
    interface_registry = interface_registry or InMemoryInterfaceRegistry()
    intent_analyzer = (
        AxiomIntentServiceAnalyzer(base_url=intent_service_base_url)
        if intent_service_base_url
        else ExampleIntentAnalyzer()
    )
    return DataIntelligencePipeline(
        intent_analyzer=intent_analyzer,
        spec_builder=spec_builder,
        spec_confirmation=ExampleSpecConfirmation(),
        engine_registry=registry,
        evidence_collector=ExampleEvidenceCollector(),
        synthesizer=ExampleSynthesizer(),
        mcp_client=mcp_client,
        mcp_tools=resolved_mcp_tools,
        interface_registry=interface_registry,
        interface_builder=interface_builder,
        sandbox_executor=sandbox_executor,
        sandbox_provider=sandbox_provider,
        artifact_store=artifact_store,
        include_evidence=False,
        logger=logger,
    )


def create_report_pipeline(
    *,
    mcp_client: MCPMethodClient | None = None,
    interface_registry: object | None = None,
    logger: RuntimeLogger | None = None,
) -> DataIntelligencePipeline:
    """Create an offline report-generation pipeline for examples."""

    return create_example_pipeline(
        engine=ReportEngine(
            force_code_agent=_env_flag(
                "REPORT_FORCE_CODE_AGENT",
                default=False,
            )
        ),
        mcp_client=mcp_client,
        interface_registry=interface_registry,
        logger=logger,
    )
