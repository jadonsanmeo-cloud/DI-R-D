"""Application workflow wiring around the Data Intelligence SDK."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import httpx

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    EngineInput,
    EngineOutput,
    EngineTrace,
    EvidenceBundle,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.engines.base import Engine
from data_intelligence_sdk.engines.general import GeneralPurposeEngine, LLMInvoker
from data_intelligence_sdk.intent import IntentAnalysis
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.registry.engine_selector import (
    EngineSelector,
    LLMEngineSelector,
)
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.sandbox import (
    EngineSandboxSession,
    SandboxEnvironment,
    SandboxSessionProvider,
)
from data_intelligence_sdk.runtime.interfaces import (
    InMemoryInterfaceRegistry,
    InterfaceBuilder,
    InterfaceRegistry,
)
from data_intelligence_sdk.runtime.llm_client import (
    LLMClient,
    OpenAICompatibleLLMClient,
)
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient, MCPToolDefinition
from data_intelligence_sdk.sandbox.artifacts import (
    ArtifactStore,
    FilesystemArtifactStore,
)
from data_intelligence_sdk.sandbox.executor import SandboxExecutor
from data_intelligence_sdk.spec import LLMSpecBuilder
from data_intelligence_sdk.spec.markdown_builder import LLMMarkdownSpecBuilder
from data_intelligence_api.infrastructure.intent import AxiomIntentServiceAnalyzer

DEFAULT_QUERYAI_REASON_URL = "http://localhost:7205/query"


class _SpecBuilderDelegate(Protocol):
    """Minimum spec-builder contract wrapped with report defaults."""

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        """Build an execution spec."""

    def revise(
        self,
        *,
        previous_spec: ExecutionSpec,
        user_feedback: str,
        query: UserQuery,
        intent: Intent,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        """Revise an execution spec."""


class _MarkdownReportEngine(Protocol):
    """Execute a Markdown report spec at the report-engine boundary."""

    def run_markdown(
        self,
        *,
        spec_markdown: str,
        organization_id: str,
        runtime: object,
        user_context: object,
        user_query: object,
    ) -> object:
        """Run the supplied report spec."""


class _ReportDefaultsSpecBuilder:
    """Apply API report-delivery defaults without overriding explicit choices."""

    def __init__(self, delegate: object) -> None:
        self.delegate = cast(_SpecBuilderDelegate, delegate)

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        spec = self.delegate.build(
            query,
            intent,
            session_context,
            user_context,
        )
        return self._apply(spec)

    def build_with_intent_analysis(
        self,
        query: UserQuery,
        intent_analysis: IntentAnalysis,
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
                session_context,
                user_context,
            )
        else:
            spec = self.delegate.build(
                query,
                intent_analysis.intent,
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
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        spec = self.delegate.revise(
            previous_spec=previous_spec,
            user_feedback=user_feedback,
            query=query,
            intent=intent,
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
                session_context=session_context,
                user_context=user_context,
            )
        else:
            spec = self.delegate.revise(
                previous_spec=previous_spec,
                user_feedback=user_feedback,
                query=query,
                intent=intent_analysis.intent,
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
    def open(self):
        def create_sandbox():
            return self.client.create_sandbox(
                self.workspace_id,
                capability_profiles=list(self.capability_profiles),
            )

        sandbox = create_sandbox()
        session = None
        try:
            sandbox.wait_until_ready()
            session = EngineSandboxSession(
                sandbox=sandbox,
                environment=SandboxEnvironment.from_payload(
                    getattr(sandbox, "capabilities", None)
                ),
                sandbox_factory=create_sandbox,
            )
            yield session
        finally:
            if self.cleanup:
                with suppress(Exception):
                    (session.sandbox if session is not None else sandbox).delete()


def _configure_axiom_sandbox_provider(
    *,
    config_manager: ConfigManager,
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

    sandbox_client = SandboxClient(settings.endpoint)
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
    config_manager: ConfigManager,
    method_hub_enabled: bool,
) -> SandboxSessionProvider:
    """Build the AXIOM-managed request sandbox used by QA workflows."""

    return _configure_axiom_sandbox_provider(
        config_manager=config_manager,
        method_hub_enabled=method_hub_enabled,
    )


class ExampleIntentAnalyzer:
    def analyze(
        self,
        query: UserQuery,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> Intent:
        del session_context, user_context
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
        return ExecutionSpec(
            intent=intent,
            objective=query.text,
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
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        del query, session_context, user_context
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


class QueryAIRemoteReasonEngine:
    """Reasoning engine backed by the QueryAI workflow HTTP API."""

    name = "reason"
    description = (
        "Remote QueryAI reason engine for question answering, retrieval, "
        "table checks, and code-draft workflows."
    )

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_QUERYAI_REASON_URL,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def run(self, input: EngineInput) -> EngineOutput:
        spec_text = json.dumps(asdict(input.spec), ensure_ascii=False, sort_keys=True)
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.endpoint,
                    json={"query": input.query.text, "spec": spec_text},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "QueryAI reason engine request failed. Ensure the QueryAI workflow "
                f"server is reachable at {self.endpoint}. When the API runs in "
                "Docker, set QUERYAI_REASON_ENDPOINT to a host-reachable URL such "
                "as http://host.docker.internal:7205/query."
            ) from exc

        final_answer = str(payload.get("final_answer") or "")
        raw_evidence = payload.get("evidence")
        evidence_sources = (
            [str(item) for item in raw_evidence]
            if isinstance(raw_evidence, list)
            else []
        )
        run_id = payload.get("run_id")
        return EngineOutput(
            engine_name=self.name,
            answer=final_answer,
            result=final_answer,
            evidence=EvidenceBundle(sources=evidence_sources),
            trace=EngineTrace(),
            metadata={
                "engine_name": self.name,
                "remote_engine": "queryai",
                "queryai_run_id": str(run_id) if run_id is not None else None,
                "queryai_endpoint": self.endpoint,
                "evidence": evidence_sources,
            },
        )


def create_example_pipeline(
    *,
    engine: Engine | None = None,
    llm: LLMInvoker | None = None,
    model: str | None = None,
    api_key: str | None = None,
    config_path: str | Path | None = None,
    config_manager: ConfigManager | None = None,
    spec_builder: object | None = None,
    spec_llm_client: LLMClient | None = None,
    engine_selector: EngineSelector | None = None,
    use_llm_spec_builder: bool = False,
    allow_method_generation: bool = True,
    mcp_client: MCPMethodClient | None = None,
    mcp_tools: tuple[MCPToolDefinition, ...] = (),
    method_hub_enabled: bool | None = None,
    interface_registry: InterfaceRegistry | None = None,
    interface_builder: InterfaceBuilder | None = None,
    sandbox_executor: SandboxExecutor | None = None,
    sandbox_provider: SandboxSessionProvider | None = None,
    configure_default_sandbox: bool = True,
    artifact_store: ArtifactStore | None = None,
    logger: RuntimeLogger | None = None,
    intent_service_base_url: str | None = None,
    queryai_reason_endpoint: str | None = None,
    default_organization_id: str | None = None,
    markdown_report_engine: _MarkdownReportEngine | None = None,
) -> DataIntelligencePipeline:
    resolved_config_manager = config_manager or ConfigManager(config_path)
    resolved_method_hub_enabled = (
        mcp_client is not None if method_hub_enabled is None else method_hub_enabled
    )
    resolved_mcp_tools = mcp_tools
    if method_hub_enabled is None and mcp_client is not None and not resolved_mcp_tools:
        resolved_mcp_tools = tuple(mcp_client.list_tools())
    shared_llm_client = spec_llm_client
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
                logger=logger,
            )
    else:
        spec_builder = ExampleSpecBuilder()
    spec_builder = _ReportDefaultsSpecBuilder(spec_builder)
    uses_default_engine = engine is None
    if configure_default_sandbox and sandbox_provider is None and uses_default_engine:
        sandbox_settings = resolved_config_manager.sandbox_settings()
        if sandbox_settings.enabled:
            sandbox_provider = _configure_request_sandbox_provider(
                config_manager=resolved_config_manager,
                method_hub_enabled=resolved_method_hub_enabled,
            )
    if uses_default_engine:
        reason_engine = QueryAIRemoteReasonEngine(
            endpoint=(
                queryai_reason_endpoint
                or os.environ.get("QUERYAI_REASON_ENDPOINT")
                or DEFAULT_QUERYAI_REASON_URL
            )
        )
        if llm is not None:
            general_engine = GeneralPurposeEngine(llm=llm)
        else:
            general_engine = GeneralPurposeEngine(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=resolved_config_manager,
                allow_method_generation=allow_method_generation,
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
        registry.register(reason_engine)
    else:
        assert engine is not None
        registry = InMemoryEngineRegistry(fallback_engine=engine)
        registry.register(engine)
    interface_registry = interface_registry or InMemoryInterfaceRegistry()
    intent_analyzer = (
        AxiomIntentServiceAnalyzer(
            base_url=intent_service_base_url,
        )
        if intent_service_base_url
        else ExampleIntentAnalyzer()
    )
    resolved_markdown_report_engine = markdown_report_engine
    resolved_default_organization_id = default_organization_id or os.getenv(
        "DEFAULT_ORGANIZATION_ID"
    )
    if resolved_default_organization_id is None:
        resolved_default_organization_id = "test-org"
    return DataIntelligencePipeline(
        intent_analyzer=intent_analyzer,
        spec_builder=spec_builder,
        spec_confirmation=ExampleSpecConfirmation(),
        engine_registry=registry,
        mcp_client=mcp_client,
        mcp_tools=resolved_mcp_tools,
        interface_registry=interface_registry,
        interface_builder=interface_builder,
        sandbox_executor=sandbox_executor,
        sandbox_provider=sandbox_provider,
        artifact_store=artifact_store,
        include_evidence=True,
        logger=logger,
        markdown_spec_builder=(
            LLMMarkdownSpecBuilder(shared_llm_client)
            if use_llm_spec_builder and shared_llm_client is not None
            else None
        ),
        markdown_report_engine=resolved_markdown_report_engine,
        default_organization_id=resolved_default_organization_id,
    )


def create_report_pipeline(
    *,
    mcp_client: MCPMethodClient | None = None,
    interface_registry: object | None = None,
    logger: RuntimeLogger | None = None,
) -> DataIntelligencePipeline:
    """Reject the removed standalone report-pipeline constructor."""

    del mcp_client, interface_registry, logger
    raise RuntimeError(
        "The standalone report pipeline has been removed. Use the authenticated "
        "stateless runtime operation flow with an available report engine."
    )
