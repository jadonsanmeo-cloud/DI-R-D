"""Internal report engine implementation module."""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import re
import sys
import threading
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    FinalResponse,
    InterfaceDefinition,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.sandbox import SandboxEnvironment
from data_intelligence_sdk.sandbox.executor import SandboxRunResult
from data_intelligence_sdk.tools import create_mcp_tools

from data_intelligence_sdk.engines.reporting.composition import (
    ChartAgent,
    ReportAgent,
)
from data_intelligence_sdk.engines.reporting.corpus import (
    ReportCorpusResolutionError,
    ReportCorpusResolver,
    ingested_document_route,
    is_ingested_data_tool,
)
from data_intelligence_sdk.engines.reporting.execution import (
    CodeAgent,
    DataScienceAgent,
    RouterAgent,
    ToolExecutor,
    ValidatorAgent,
)
from data_intelligence_sdk.engines.reporting.planning import (
    PlanAgent,
    TemplateAgent,
    TemplatePool,
)
from data_intelligence_sdk.engines.reporting.policies import (
    ChartPolicy,
    DEFAULT_SOURCE_MATERIALIZATION_REGISTRY,
    LocalePolicy,
    ReportAssetPolicy,
    ReportFormat,
    ReportFormatRegistry,
    SourceMaterializationRegistry,
)
from data_intelligence_sdk.engines.reporting.processing import (
    ChartInputAssembler,
    DataScienceProcessor,
)
from data_intelligence_sdk.engines.reporting.prompts import (
    GENERATED_TOOL_CAPABILITY,
)
from data_intelligence_sdk.engines.reporting.rendering import ReportRenderer
from data_intelligence_sdk.engines.reporting.utils import (
    _STEP_OUTPUT_REF,
    _StepInputResolver,
    _StepOutputRegistry,
    _execution_spec_payload,
    _json_dumps,
    _list_value,
    _method_hub_payload,
    _negotiation_hash,
    _normalize_plan_inputs,
    _safe_id,
    _scope_from_spec,
    _scoped_corpus_payload,
    _step_id_from_input_ref,
)

class _DataStepState(TypedDict, total=False):
    step: dict[str, Any]
    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    runtime: EngineRuntimeContext
    locale_policy: LocalePolicy
    output_registry: _StepOutputRegistry
    template_requirements: list[dict[str, Any]]
    upstream_step_results: list[dict[str, Any]]
    resolved_inputs: list[dict[str, Any]]
    input_resolution_errors: list[str]
    route: dict[str, Any]
    attempt: int
    error_logs: str | None
    validation_feedback: str | None
    code_spec: dict[str, Any]
    interface: InterfaceDefinition
    sandbox_result: SandboxRunResult
    contract_errors: list[str]
    validation: dict[str, Any]
    execution_result: dict[str, Any]
    data_step_result: dict[str, Any]


class _ReportGraphState(TypedDict, total=False):
    query_text: str
    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    ingested_evidence_package: dict[str, Any]
    runtime: EngineRuntimeContext
    output_registry: _StepOutputRegistry
    user_context: UserContext | None
    report_format: ReportFormat
    locale_policy: LocalePolicy
    plan: dict[str, Any]
    template_proposal: dict[str, Any]
    template_instance: dict[str, Any]
    previous_template_instance: dict[str, Any] | None
    template_feedback: list[dict[str, Any]]
    negotiation_iteration: int
    negotiation_status: str
    negotiation_revision_hash: str
    ready_steps: list[dict[str, Any]]
    completed_step_ids: Annotated[list[str], operator.add]
    data_step_results: Annotated[list[dict[str, Any]], operator.add]
    scheduler_warnings: Annotated[list[str], operator.add]
    step: dict[str, Any]
    upstream_step_results: list[dict[str, Any]]
    template_requirements: list[dict[str, Any]]
    chart_requests: list[dict[str, Any]]
    chart_request: dict[str, Any]
    chart_results: Annotated[list[dict[str, Any]], operator.add]
    structured_report: dict[str, Any]
    legacy_markdown: str | None
    rendered_reports: list[dict[str, Any]]
    final_result: Any


class ReportEngine:
    """LangGraph multi-agent report workflow with scoped planning and rendering."""

    name = "report"
    description = (
        "Structured multi-step report generation with planning, templates, "
        "data analysis, charts, validation, and rendered report artifacts."
    )

    def __init__(
        self,
        llm: object | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        max_generation_attempts: int = 4,
        max_negotiation_iterations: int = 3,
        max_data_concurrency: int = 4,
        max_chart_concurrency: int = 6,
        fallback_to_generation_on_tool_error: bool = True,
        force_code_agent: bool = False,
        template_pool: TemplatePool | None = None,
        source_registry: SourceMaterializationRegistry | None = None,
        format_registry: ReportFormatRegistry | None = None,
        chart_policy: ChartPolicy | None = None,
        asset_policy: ReportAssetPolicy | None = None,
        default_locale: str = "en",
    ) -> None:
        self.llm = llm
        if self.llm is None:
            self.llm = self._try_build_openrouter_llm(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=config_manager,
            )
        self.max_generation_attempts = max(1, max_generation_attempts)
        self.max_negotiation_iterations = max(1, max_negotiation_iterations)
        self.max_data_concurrency = max(1, max_data_concurrency)
        self.max_chart_concurrency = max(1, max_chart_concurrency)
        self._data_semaphore = threading.BoundedSemaphore(self.max_data_concurrency)
        self._chart_semaphore = threading.BoundedSemaphore(self.max_chart_concurrency)
        self.fallback_to_generation_on_tool_error = fallback_to_generation_on_tool_error
        self.force_code_agent = bool(force_code_agent)
        self.source_registry = (
            source_registry or DEFAULT_SOURCE_MATERIALIZATION_REGISTRY
        )
        self.format_registry = format_registry or ReportFormatRegistry()
        self.chart_policy = chart_policy or ChartPolicy()
        self.asset_policy = asset_policy or ReportAssetPolicy()
        self.default_locale = default_locale
        self.template_pool = template_pool or TemplatePool()
        self.plan_agent = PlanAgent(self.llm, self.source_registry)
        self.template_agent = TemplateAgent(self.llm, self.template_pool)
        self.router_agent = RouterAgent(self.llm, self.source_registry)
        self.code_agent = CodeAgent(self.llm)
        self.validator_agent = ValidatorAgent(self.llm)
        self.datascience_agent = DataScienceAgent(self.llm)
        self.chart_agent = ChartAgent(self.llm)
        self.report_agent = ReportAgent(self.llm)
        self.corpus_resolver = ReportCorpusResolver()
        self.tool_executor = ToolExecutor()
        self.input_resolver = _StepInputResolver()
        self.datascience_processor = DataScienceProcessor(
            self.datascience_agent,
            chart_policy=self.chart_policy,
        )
        self.chart_input_assembler = ChartInputAssembler()
        self.renderer = ReportRenderer(asset_policy=self.asset_policy)
        self._data_step_graph = self._build_data_step_graph()
        self._graph = self._build_graph()

    def _try_build_openrouter_llm(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
    ) -> object | None:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        resolved_api_key = (
            api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        )
        resolved_model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_api_key or not resolved_model:
            return None
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=resolved_api_key,
            base_url=settings.base_url,
            model=resolved_model,
        )


    def run(
        self,
        input: EngineInput,
    ) -> EngineOutput:
        spec = input.spec
        corpus_package = input.corpus_package
        runtime = input.runtime
        user_context = input.user_context
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        report_format = self.format_registry.resolve(
            constraints.get("output_format")
        )
        locale_policy = LocalePolicy.for_locale(
            constraints.get("locale")
            or (
                user_context.preferences.get("locale")
                if user_context is not None
                and isinstance(user_context.preferences, dict)
                else None
            )
            or self.default_locale
        )
        runtime.run_context.record_step(
            "report_workflow_start",
            inputs={
                "objective": spec.objective,
                "sources": _scope_from_spec(spec, corpus_package)["sources"],
                "orchestration": "langgraph",
            },
        )
        initial: _ReportGraphState = {
            "query_text": input.query.text,
            "spec": spec,
            "corpus_package": corpus_package,
            "runtime": runtime,
            "output_registry": _StepOutputRegistry(),
            "user_context": user_context,
            "report_format": report_format,
            "locale_policy": locale_policy,
            "template_feedback": [],
            "negotiation_iteration": 0,
            "completed_step_ids": [],
            "data_step_results": [],
            "scheduler_warnings": [],
            "chart_results": [],
        }
        state = self._graph.invoke(
            initial,
            config={
                "run_name": "report",
                "recursion_limit": 100,
                "max_concurrency": max(
                    self.max_data_concurrency, self.max_chart_concurrency
                ),
            },
        )
        generation_mode = "langchain" if self.llm is not None else "fallback"
        resolved_spec = state.get("spec", spec)
        resolved_corpus = state.get("corpus_package", corpus_package)
        scope = _scope_from_spec(resolved_spec, resolved_corpus)
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=state.get("final_result"),
            metadata={
                "sources": scope["sources"],
                "report_format": report_format.value,
                "generation_mode": generation_mode,
                "orchestration": "langgraph",
                "plan": state.get("plan", {}),
                "template_instance": state.get("template_instance", {}),
                "negotiation_status": state.get("negotiation_status"),
                "all_steps_data": state.get("data_step_results", []),
                "chart_results": state.get("chart_results", []),
                "structured_report": state.get("structured_report", {}),
                "rendered_reports": state.get("rendered_reports", []),
                "scheduler_warnings": state.get("scheduler_warnings", []),
                "ingested_evidence_package": state.get(
                    "ingested_evidence_package", {}
                ),
                "workflow": [
                    "Ingested Corpus Resolver",
                    "Plan Agent",
                    "Template Agent",
                    "DAG Scheduler",
                    "Router Agent",
                    "Code Agent",
                    "Sandbox",
                    "Validator Agent",
                    "Tool Executor",
                    "DataScience Processor",
                    "Chart Input Assembler",
                    "Chart Agent",
                    "Report Agent",
                    "Renderer",
                ],
            },
        )

    def run_markdown(
        self,
        *,
        spec_markdown: str,
        organization_id: str,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> FinalResponse:
        markdown = spec_markdown.strip()
        output = self.run(
            EngineInput(
                query=UserQuery(text=markdown),
                spec=ExecutionSpec(
                    intent="report",
                    objective=markdown,
                    confirmed=True,
                    engine_hint=self.name,
                    constraints={"output_format": "markdown"},
                ),
                corpus_package=DataCorpusPackage(
                    metadata={"organization_id": organization_id}
                ),
                runtime=runtime,
                user_context=user_context,
            )
        )
        answer = output.answer if output.answer is not None else output.result
        return FinalResponse(
            answer=str(answer),
            evidence=output.evidence,
            metadata={**dict(output.metadata), "engine_name": output.engine_name},
        )

    def _build_graph(self) -> Any:
        graph = StateGraph(_ReportGraphState)
        graph.add_node("resolve_ingested_data", self._graph_resolve_ingested_data)
        graph.add_node("plan", self._graph_plan)
        graph.add_node("template", self._graph_template)
        graph.add_node("negotiate", self._graph_negotiate)
        graph.add_node("negotiation_failed", self._graph_negotiation_failed)
        graph.add_node("schedule_data", self._graph_schedule_data)
        graph.add_node("run_data_step", self._graph_run_data_step)
        graph.add_node("prepare_charts", self._graph_prepare_charts)
        graph.add_node("run_chart", self._graph_run_chart)
        graph.add_node("compose_report", self._graph_compose_report)
        graph.add_node("render", self._graph_render)
        graph.add_edge(START, "resolve_ingested_data")
        graph.add_edge("resolve_ingested_data", "plan")
        graph.add_edge("plan", "template")
        graph.add_edge("template", "negotiate")
        graph.add_conditional_edges(
            "negotiate",
            self._negotiation_route,
            {
                "revise": "plan",
                "execute": "schedule_data",
                "failed": "negotiation_failed",
            },
        )
        graph.add_edge("negotiation_failed", "render")
        graph.add_conditional_edges("schedule_data", self._dispatch_ready_steps)
        graph.add_edge("run_data_step", "schedule_data")
        graph.add_conditional_edges("prepare_charts", self._dispatch_chart_tasks)
        graph.add_edge("run_chart", "compose_report")
        graph.add_edge("compose_report", "render")
        graph.add_edge("render", END)
        return graph.compile()

    def _graph_resolve_ingested_data(
        self,
        state: _ReportGraphState,
    ) -> dict[str, Any]:
        resolver = self.corpus_resolver
        if not resolver.should_resolve(
            state["spec"],
            state["corpus_package"],
            state["runtime"],
        ):
            state["runtime"].run_context.record_step(
                "resolve_ingested_data",
                status="skipped",
                description=(
                    "No report-local ingested corpus selection was requested "
                    "and the Method Hub ingested-data tool is unavailable."
                ),
            )
            return {"ingested_evidence_package": {}}
        try:
            resolution = resolver.resolve(
                state["spec"],
                state["corpus_package"],
                state["runtime"],
                query_text=state.get("query_text"),
            )
        except ReportCorpusResolutionError as exc:
            state["runtime"].run_context.record_step(
                "resolve_ingested_data",
                status="failed",
                inputs={
                    "objective": state["spec"].objective,
                    "organization_id": state["corpus_package"].metadata.get(
                        "organization_id"
                    ),
                },
                outputs={
                    "code": exc.code,
                    "error": str(exc),
                    "details": exc.details,
                },
            )
            raise
        documents = resolution.evidence_package.get("documents", [])
        state["runtime"].run_context.record_step(
            "resolve_ingested_data",
            inputs={
                "selection": resolution.evidence_package.get("selection", {}),
            },
            outputs={
                "document_count": len(documents),
                "document_ids": [
                    item.get("document_id")
                    for item in documents
                    if isinstance(item, dict)
                ],
                "artifact_refs": [
                    item.get("artifact_ref")
                    for item in documents
                    if isinstance(item, dict) and item.get("artifact_ref")
                ],
            },
            artifact_refs=[
                str(item["artifact_ref"])
                for item in documents
                if isinstance(item, dict) and item.get("artifact_ref")
            ],
        )
        return {
            "spec": resolution.spec,
            "corpus_package": resolution.corpus_package,
            "ingested_evidence_package": resolution.evidence_package,
        }

    def _build_data_step_graph(self) -> Any:
        graph = StateGraph(_DataStepState)
        graph.add_node("resolve_inputs", self._data_resolve_inputs)
        graph.add_node(
            "input_resolution_failed",
            self._data_input_resolution_failed,
        )
        graph.add_node("route", self._data_route)
        graph.add_node("execute_existing", self._data_execute_existing)
        graph.add_node("generate_code", self._data_generate_code)
        graph.add_node("validate_code", self._data_validate_code)
        graph.add_node("execute_generated", self._data_execute_generated)
        graph.add_node("generation_failed", self._data_generation_failed)
        graph.add_node("analyze", self._data_analyze)
        graph.add_edge(START, "resolve_inputs")
        graph.add_conditional_edges(
            "resolve_inputs",
            self._input_resolution_choice,
            {
                "ready": "route",
                "failed": "input_resolution_failed",
            },
        )
        graph.add_edge("input_resolution_failed", "analyze")
        graph.add_conditional_edges(
            "route",
            self._data_route_choice,
            {"existing": "execute_existing", "generate": "generate_code"},
        )
        graph.add_conditional_edges(
            "execute_existing",
            self._existing_execution_choice,
            {"analyze": "analyze", "generate": "generate_code"},
        )
        graph.add_edge("generate_code", "validate_code")
        graph.add_conditional_edges(
            "validate_code",
            self._validation_choice,
            {
                "execute": "execute_generated",
                "retry": "generate_code",
                "failed": "generation_failed",
            },
        )
        graph.add_edge("execute_generated", "analyze")
        graph.add_edge("generation_failed", "analyze")
        graph.add_edge("analyze", END)
        return graph.compile()

    def _graph_plan(self, state: _ReportGraphState) -> dict[str, Any]:
        plan = self.plan_agent.run(
            state["spec"],
            state["corpus_package"],
            state.get("plan"),
            state.get("template_feedback", []),
        )
        state["runtime"].run_context.record_step(
            "plan_agent",
            inputs={
                "execution_spec": _execution_spec_payload(state["spec"]),
                "template_feedback": state.get("template_feedback", []),
            },
            outputs={"plan": plan},
        )
        return {"plan": plan}

    def _graph_template(self, state: _ReportGraphState) -> dict[str, Any]:
        proposal = self.template_agent.run(
            state["spec"],
            state["plan"],
            state["corpus_package"],
            state.get("previous_template_instance"),
        )
        execution_plan = (
            self._plan_for_template(
                state["plan"],
                proposal["template_instance"],
            )
            if proposal.get("status") in {"accepted", "partial"}
            else state["plan"]
        )
        state["runtime"].run_context.record_step(
            "template_agent",
            inputs={"plan_revision": state["plan"].get("revision")},
            outputs={
                "status": proposal.get("status"),
                "selection": proposal.get("selection"),
                "missing_data_requests": proposal.get("missing_data_requests", []),
                "scheduled_step_ids": [
                    step.get("step_id") for step in execution_plan.get("steps", [])
                ],
            },
        )
        return {
            "plan": execution_plan,
            "template_proposal": proposal,
            "template_instance": proposal["template_instance"],
            "previous_template_instance": proposal["template_instance"],
        }

    @staticmethod
    def _plan_for_template(
        plan: dict[str, Any],
        template_instance: dict[str, Any],
    ) -> dict[str, Any]:
        steps = [
            step
            for step in plan.get("steps", [])
            if isinstance(step, dict) and step.get("step_id")
        ]
        steps_by_id = {str(step["step_id"]): step for step in steps}
        required_step_ids = {
            match.group(1)
            for binding in template_instance.get("bindings", [])
            if binding.get("status") == "resolved"
            for output_ref in (
                _list_value(binding.get("plan_output_refs"))
                or _list_value(binding.get("plan_output_ref"))
            )
            if (match := _STEP_OUTPUT_REF.match(str(output_ref)))
        }
        required_step_ids.update(
            str(step.get("step_id"))
            for step in steps
            if bool(step.get("required"))
            or any(
                "goal_evidence"
                in {
                    str(role)
                    for role in _list_value(output.get("semantic_roles"))
                }
                for output in step.get("outputs", [])
                if isinstance(output, dict)
            )
        )
        if not required_step_ids:
            return plan
        pending = list(required_step_ids)
        while pending:
            step_id = pending.pop()
            step = steps_by_id.get(step_id)
            if not step:
                continue
            dependencies = {
                str(dependency) for dependency in _list_value(step.get("depends_on"))
            }
            dependencies.update(
                dependency
                for item in _normalize_plan_inputs(step.get("inputs"))
                if (dependency := _step_id_from_input_ref(item.get("ref")))
                in steps_by_id
            )
            for dependency in dependencies:
                if dependency not in required_step_ids:
                    required_step_ids.add(dependency)
                    pending.append(dependency)
        execution_plan = deepcopy(plan)
        execution_plan["steps"] = [
            step for step in steps if str(step.get("step_id")) in required_step_ids
        ]
        omitted = [
            str(step.get("step_id"))
            for step in steps
            if str(step.get("step_id")) not in required_step_ids
        ]
        if omitted:
            execution_plan["warnings"] = list(
                dict.fromkeys(
                    [
                        *map(str, _list_value(execution_plan.get("warnings"))),
                        (
                            "Skipped plan steps with no resolved template "
                            "consumer: " + ", ".join(omitted)
                        ),
                    ]
                )
            )
        return execution_plan

    def _graph_negotiate(self, state: _ReportGraphState) -> dict[str, Any]:
        iteration = int(state.get("negotiation_iteration", 0)) + 1
        proposal_status = str(state["template_proposal"].get("status", "failed"))
        missing = state["template_proposal"].get("missing_data_requests", [])
        required_missing = [item for item in missing if item.get("required")]
        resolutions = {
            str(item.get("request_id")): item
            for item in state.get("plan", {}).get("request_resolutions", [])
            if isinstance(item, dict)
        }
        rejected_required = [
            item
            for item in required_missing
            if resolutions.get(str(item.get("request_id")), {}).get("decision")
            == "rejected"
        ]
        revision_hash = _negotiation_hash(
            state.get("plan", {}),
            state.get("template_proposal", {}),
        )
        stalled = (
            proposal_status == "needs_plan_revision"
            and state.get("negotiation_revision_hash") == revision_hash
        )
        if proposal_status == "needs_plan_revision" and rejected_required:
            status = "required_data_rejected"
        elif stalled:
            status = "no_negotiation_progress"
        elif (
            proposal_status == "needs_plan_revision"
            and iteration >= self.max_negotiation_iterations
        ):
            status = "iteration_limit_reached"
        else:
            status = proposal_status
        state["runtime"].run_context.record_step(
            "plan_template_negotiation",
            status=(
                "failed"
                if status
                in {
                    "failed",
                    "iteration_limit_reached",
                    "no_negotiation_progress",
                    "required_data_rejected",
                }
                else "completed"
            ),
            inputs={"iteration": iteration},
            outputs={
                "status": status,
                "missing_data_requests": missing,
                "request_resolutions": list(resolutions.values()),
                "revision_hash": revision_hash,
            },
        )
        return {
            "negotiation_iteration": iteration,
            "negotiation_status": status,
            "negotiation_revision_hash": revision_hash,
            "template_feedback": missing,
        }

    def _negotiation_route(self, state: _ReportGraphState) -> str:
        if (
            state.get("negotiation_status") == "needs_plan_revision"
            and int(state.get("negotiation_iteration", 0))
            < self.max_negotiation_iterations
        ):
            return "revise"
        if state.get("negotiation_status") in {"accepted", "partial"}:
            return "execute"
        return "failed"

    def _graph_negotiation_failed(
        self,
        state: _ReportGraphState,
    ) -> dict[str, Any]:
        missing = state.get("template_proposal", {}).get("missing_data_requests", [])
        warnings = [
            str(item.get("reason") or item.get("description"))
            for item in missing
            if item.get("required")
        ]
        status = str(state.get("negotiation_status", "failed"))
        structured = {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": "failed",
            "title": state["spec"].objective,
            "summary": (
                "The report template could not be bound to the required data "
                f"within the negotiation policy ({status})."
            ),
            "template": self.report_agent._template_ref(
                state.get("template_instance", {})
            ),
            "sections": [],
            "metrics": [],
            "charts": [],
            "sources": _scope_from_spec(state["spec"], state["corpus_package"])[
                "sources"
            ],
            "warnings": warnings,
        }
        return {"structured_report": structured, "legacy_markdown": None}

    def _graph_schedule_data(self, state: _ReportGraphState) -> dict[str, Any]:
        steps = state.get("plan", {}).get("steps", [])
        completed = set(state.get("completed_step_ids", []))
        results_by_step = {
            str(item.get("step_id")): item
            for item in state.get("data_step_results", [])
        }
        remaining = [
            step for step in steps if str(step.get("step_id")) not in completed
        ]
        ready = []
        warnings: list[str] = []
        skipped_results: list[dict[str, Any]] = []
        skipped_ids: list[str] = []
        failed_dependency_skips: list[str] = []
        for step in remaining:
            dependencies = set(map(str, step.get("depends_on", [])))
            if not dependencies.issubset(completed):
                continue
            failed_dependencies = [
                dependency
                for dependency in sorted(dependencies)
                if self._dependency_required(step, dependency)
                and self._dependency_failed(results_by_step.get(dependency))
            ]
            if failed_dependencies:
                step_id = str(step.get("step_id"))
                skipped_ids.append(step_id)
                failed_dependency_skips.append(step_id)
                skipped_results.append(
                    self._skipped_step_result(
                        step_id,
                        (
                            "Required dependencies failed or were skipped: "
                            + ", ".join(failed_dependencies)
                        ),
                    )
                )
                continue
            ready.append(step)

        unresolved = [
            step
            for step in remaining
            if str(step.get("step_id")) not in skipped_ids and step not in ready
        ]
        if unresolved and not ready and not skipped_ids:
            for step in unresolved:
                step_id = str(step.get("step_id"))
                skipped_ids.append(step_id)
                skipped_results.append(
                    self._skipped_step_result(
                        step_id,
                        "Unresolved or cyclic dependency.",
                    )
                )
            warnings.append(
                "The scheduler skipped steps with unresolved or cyclic dependencies."
            )
        if failed_dependency_skips:
            warnings.append(
                "The scheduler skipped steps whose required dependencies failed."
            )
        state["runtime"].run_context.record_step(
            "dag_scheduler",
            inputs={"completed_step_ids": sorted(completed)},
            outputs={
                "ready_step_ids": [step.get("step_id") for step in ready],
                "remaining_count": len(remaining),
                "skipped_step_ids": skipped_ids,
            },
        )
        return {
            "ready_steps": ready,
            "completed_step_ids": skipped_ids,
            "data_step_results": skipped_results,
            "scheduler_warnings": warnings,
        }

    def _dispatch_ready_steps(self, state: _ReportGraphState) -> str | list[Send]:
        ready = state.get("ready_steps", [])
        if not ready:
            planned_ids = {
                str(step.get("step_id"))
                for step in state.get("plan", {}).get("steps", [])
            }
            completed_ids = set(state.get("completed_step_ids", []))
            if planned_ids - completed_ids:
                return "schedule_data"
            return "prepare_charts"
        existing_results = state.get("data_step_results", [])
        template_instance = state.get("template_instance", {})
        sends = []
        for step in ready:
            dependencies = set(map(str, step.get("depends_on", [])))
            upstream = [
                item
                for item in existing_results
                if str(item.get("step_id")) in dependencies
            ]
            sends.append(
                Send(
                    "run_data_step",
                    {
                        "spec": state["spec"],
                        "corpus_package": state["corpus_package"],
                        "runtime": state["runtime"],
                        "locale_policy": state.get("locale_policy"),
                        "output_registry": state["output_registry"],
                        "step": step,
                        "upstream_step_results": upstream,
                        "template_requirements": self._template_requirements_for_step(
                            template_instance, step
                        ),
                    },
                )
            )
        return sends

    @staticmethod
    def _dependency_required(step: dict[str, Any], dependency: str) -> bool:
        matching_inputs = [
            item
            for item in _normalize_plan_inputs(step.get("inputs"))
            if _step_id_from_input_ref(item.get("ref")) == dependency
        ]
        if not matching_inputs:
            return True
        return any(bool(item.get("required", True)) for item in matching_inputs)

    @staticmethod
    def _dependency_failed(result: dict[str, Any] | None) -> bool:
        if result is None:
            return True
        return str(result.get("status", "")).lower() in {
            "blocked",
            "failed",
            "skipped",
        }

    @staticmethod
    def _skipped_step_result(
        step_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "status": "skipped",
            "step_id": step_id,
            "analysis_summary": (
                "The step was skipped because required upstream data was unavailable."
            ),
            "aggregated_data": {},
            "aggregated_metrics": [],
            "chart_datasets": [],
            "warnings": [reason],
            "lineage": {
                "source_refs": [],
                "upstream_step_refs": [],
                "tool_name": None,
            },
        }

    def _graph_run_data_step(self, state: _ReportGraphState) -> dict[str, Any]:
        with self._data_semaphore:
            result = self._data_step_graph.invoke(
                {
                    "step": state["step"],
                    "spec": state["spec"],
                    "corpus_package": state["corpus_package"],
                    "runtime": state["runtime"],
                    "output_registry": state["output_registry"],
                    "template_requirements": state.get("template_requirements", []),
                    "upstream_step_results": state.get("upstream_step_results", []),
                    "attempt": 0,
                },
                config={"recursion_limit": 30},
            )
        data_result = result["data_step_result"]
        return {
            "completed_step_ids": [str(state["step"].get("step_id"))],
            "data_step_results": [data_result],
        }

    def _graph_prepare_charts(self, state: _ReportGraphState) -> dict[str, Any]:
        ready, fallbacks = self.chart_input_assembler.prepare(
            state.get("template_instance", {}), state.get("data_step_results", [])
        )
        state["runtime"].run_context.record_step(
            "chart_input_assembler",
            inputs={"data_step_count": len(state.get("data_step_results", []))},
            outputs={
                "ready_chart_count": len(ready),
                "fallback_chart_count": len(fallbacks),
            },
        )
        return {"chart_requests": ready, "chart_results": fallbacks}

    def _dispatch_chart_tasks(self, state: _ReportGraphState) -> str | list[Send]:
        requests = state.get("chart_requests", [])
        if not requests:
            return "compose_report"
        return [
            Send(
                "run_chart",
                {"chart_request": request, "runtime": state["runtime"]},
            )
            for request in requests
        ]

    def _graph_run_chart(self, state: _ReportGraphState) -> dict[str, Any]:
        with self._chart_semaphore:
            result = self.chart_agent.run(state["chart_request"])
        state["runtime"].run_context.record_step(
            "chart_agent",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "chart_id": state["chart_request"].get("chart_id"),
                "dataset_ref": state["chart_request"]
                .get("dataset", {})
                .get("artifact_ref"),
            },
            outputs={
                "status": result.get("status"),
                "selected_type": result.get("selected_type"),
            },
        )
        return {"chart_results": [result]}

    def _graph_compose_report(self, state: _ReportGraphState) -> dict[str, Any]:
        scoped = _scoped_corpus_payload(state["spec"], state["corpus_package"])
        structured = self.report_agent.run_structured(
            state["spec"],
            state.get("template_instance", {}),
            state.get("data_step_results", []),
            state.get("chart_results", []),
            scoped,
        )
        state["runtime"].run_context.record_step(
            "report_agent",
            inputs={
                "user_goal": state["spec"].objective,
                "data_step_count": len(state.get("data_step_results", [])),
                "chart_count": len(state.get("chart_results", [])),
            },
            outputs={
                "status": structured.get("status"),
                "report_format": "structured_report",
            },
        )
        return {"structured_report": structured, "legacy_markdown": None}

    def _graph_render(self, state: _ReportGraphState) -> dict[str, Any]:
        rendered = self.renderer.render(
            state["structured_report"],
            state.get("legacy_markdown"),
            locale_policy=state.get("locale_policy"),
        )
        rendered_artifact_refs = []
        if state["runtime"].run_artifact is not None:
            for item in rendered:
                artifact = state["runtime"].run_artifact.record_rendered_report(
                    str(item.get("format", "report")),
                    str(item.get("media_type", "text/plain")),
                    str(item.get("content", "")),
                )
                item["artifact_ref"] = artifact.artifact_ref
                rendered_artifact_refs.append(artifact.artifact_ref)
                state["runtime"].run_context.add_artifact_ref(artifact.artifact_ref)
        report_format = self.format_registry.resolve(
            state.get("report_format", ReportFormat.MARKDOWN)
        )
        final_result = self.format_registry.select(
            report_format,
            state["structured_report"],
            rendered,
        )
        state["runtime"].run_context.record_step(
            "renderer",
            inputs={"requested_format": report_format.value},
            outputs={
                "rendered_formats": [item["format"] for item in rendered],
                "artifact_refs": rendered_artifact_refs,
            },
            artifact_refs=rendered_artifact_refs,
        )
        return {"rendered_reports": rendered, "final_result": final_result}

    def _template_requirements_for_step(
        self, template_instance: dict[str, Any], step: dict[str, Any]
    ) -> list[dict[str, Any]]:
        step_id = str(step.get("step_id"))
        matching_bindings = [
            binding
            for binding in template_instance.get("bindings", [])
            if any(
                str(output_ref).startswith(f"step-output://{step_id}/")
                for output_ref in (
                    _list_value(binding.get("plan_output_refs"))
                    or _list_value(binding.get("plan_output_ref"))
                )
            )
        ]
        requirement_ids = {
            str(binding.get("requirement_ref")) for binding in matching_bindings
        }
        binding_by_requirement = {
            str(binding.get("requirement_ref")): binding
            for binding in matching_bindings
        }
        chart_consumers: dict[str, list[str]] = {item: [] for item in requirement_ids}
        for section in template_instance.get("sections", []):
            for block in section.get("blocks", []):
                slot = block.get("chart_slot")
                if not isinstance(slot, dict):
                    continue
                for requirement_ref in slot.get("data_requirement_refs", []):
                    if str(requirement_ref) in chart_consumers:
                        chart_consumers[str(requirement_ref)].append(
                            str(slot.get("chart_id"))
                        )
        return [
            {
                "requirement_ref": requirement_id,
                "consumer_chart_ids": chart_consumers.get(requirement_id, []),
                "expected_output": deepcopy(
                    binding_by_requirement.get(requirement_id, {}).get(
                        "expected_output", {}
                    )
                ),
                "semantic_roles": deepcopy(
                    binding_by_requirement.get(requirement_id, {}).get(
                        "semantic_roles", {}
                    )
                ),
            }
            for requirement_id in sorted(requirement_ids)
        ]

    def _data_resolve_inputs(self, state: _DataStepState) -> dict[str, Any]:
        resolved, missing = self.input_resolver.resolve(
            state["step"],
            state["output_registry"],
        )
        state["runtime"].run_context.record_step(
            "input_resolver",
            status="failed" if missing else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "declared_inputs": state["step"].get("inputs", []),
            },
            outputs={
                "resolved_inputs": self.input_resolver.contract_payload(resolved),
                "missing_required_inputs": missing,
            },
            artifact_refs=[
                str(item["artifact_ref"])
                for item in resolved
                if item.get("artifact_ref")
            ],
        )
        return {
            "resolved_inputs": resolved,
            "input_resolution_errors": missing,
        }

    def _input_resolution_choice(self, state: _DataStepState) -> str:
        return "failed" if state.get("input_resolution_errors") else "ready"

    def _data_input_resolution_failed(
        self,
        state: _DataStepState,
    ) -> dict[str, Any]:
        missing = ", ".join(state.get("input_resolution_errors", []))
        return {
            "execution_result": {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": None,
                "arguments": {},
                "raw_result": None,
                "error": f"Required upstream inputs could not be resolved: {missing}",
            }
        }

    def _data_route(self, state: _DataStepState) -> dict[str, Any]:
        scope = _scope_from_spec(state["spec"], state["corpus_package"])
        inventory = _method_hub_payload(state["runtime"])
        corpus_route = ingested_document_route(state["step"], inventory)
        if corpus_route is not None:
            route = corpus_route
        elif self.force_code_agent:
            route = {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "ReportEngine force_code_agent mode bypasses existing methods."
                ),
            }
        else:
            route = self.router_agent.run(
                state["step"],
                (
                    state["runtime"]
                    if isinstance(self.router_agent, RouterAgent)
                    else inventory
                ),
                scope["sources"],
            )
        if route.get("route") == "existing_tool":
            tool = next(
                (
                    item
                    for item in inventory
                    if item.get("tool_name") == route.get("tool_name")
                ),
                {},
            )
            route["arguments"] = self.input_resolver.merge_arguments(
                route.get("arguments"),
                tool.get("parameters_schema", {}),
                state.get("resolved_inputs", []),
                sandbox=False,
            )
            if state.get("resolved_inputs") and not self._tool_accepts_resolved_inputs(
                tool.get("parameters_schema", {}),
                state["resolved_inputs"],
            ):
                route = {
                    "route": "generate_tool",
                    "tool_name": None,
                    "arguments": {},
                    "reason": (
                        "The selected MethodHub tool cannot consume the required "
                        "upstream step output."
                    ),
                }
            elif (
                isinstance(self.router_agent, RouterAgent)
                and not self.router_agent.arguments_match_schema(
                    route["arguments"],
                    tool.get("parameters_schema", {}),
                )
            ):
                route = {
                    "route": "generate_tool",
                    "tool_name": None,
                    "arguments": {},
                    "reason": (
                        "Resolved inputs do not satisfy the selected MethodHub "
                        "tool parameter schema."
                    ),
                }
        state["runtime"].run_context.record_step(
            "router_agent",
            inputs={
                "step_request": state["step"],
                "method_hub": inventory,
                "force_code_agent": self.force_code_agent,
            },
            outputs={"route": route},
        )
        return {"route": route}

    @staticmethod
    def _tool_accepts_resolved_inputs(
        parameters_schema: Any,
        resolved_inputs: list[dict[str, Any]],
    ) -> bool:
        properties = (
            parameters_schema.get("properties", {})
            if isinstance(parameters_schema, dict)
            else {}
        )
        if not isinstance(properties, dict) or not properties:
            return False
        property_names = {str(name) for name in properties}
        for binding in resolved_inputs:
            raw_names = {
                str(binding.get("argument_name") or ""),
                str(binding.get("output_name") or ""),
                str(binding.get("source_step_id") or ""),
            }
            candidates = {
                candidate
                for raw_name in raw_names
                if raw_name
                for candidate in (
                    raw_name,
                    re.sub(r"[^A-Za-z0-9_]+", "_", raw_name).strip("_"),
                    f"{raw_name}_path",
                )
            }
            if property_names.intersection(candidates):
                return True
        return len(resolved_inputs) == 1 and len(property_names) == 1

    def _data_route_choice(self, state: _DataStepState) -> str:
        return (
            "existing"
            if state.get("route", {}).get("route") == "existing_tool"
            else "generate"
        )

    def _data_execute_existing(self, state: _DataStepState) -> dict[str, Any]:
        result = self.tool_executor.execute_existing(state["route"], state["runtime"])
        state["runtime"].run_context.record_step(
            "tool_executor",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "tool_name": result.get("tool_name"),
            },
            outputs={"status": result.get("status"), "error": result.get("error")},
        )
        return {"execution_result": result}

    def _existing_execution_choice(self, state: _DataStepState) -> str:
        if is_ingested_data_tool(
            state.get("execution_result", {}).get("tool_name")
        ):
            return "analyze"
        if (
            state.get("execution_result", {}).get("status") == "failed"
            and self.fallback_to_generation_on_tool_error
        ):
            return "generate"
        return "analyze"

    def _data_generate_code(self, state: _DataStepState) -> dict[str, Any]:
        attempt = int(state.get("attempt", 0)) + 1
        scoped = _scoped_corpus_payload(state["spec"], state["corpus_package"])
        scoped["resolved_inputs"] = self.input_resolver.contract_payload(
            state.get("resolved_inputs", [])
        )
        sandbox_environment = (
            state["runtime"].sandbox_environment or SandboxEnvironment()
        )
        scoped["sandbox_environment"] = sandbox_environment.to_prompt_payload()
        code_spec = self.code_agent.run(
            state["step"],
            scoped,
            error_logs=state.get("error_logs"),
            validation_feedback=state.get("validation_feedback"),
        )
        code_spec = self._align_generated_parameter_schema(code_spec)
        code_spec["execution_arguments"] = CodeAgent._normalize_execution_arguments(
            code_spec.get("execution_arguments"),
            code_spec.get("parameters_schema", {}),
            scoped.get("sources", []),
        )
        code_spec["execution_arguments"] = self.input_resolver.merge_arguments(
            code_spec.get("execution_arguments"),
            code_spec.get("parameters_schema", {}),
            state.get("resolved_inputs", []),
            sandbox=True,
        )
        state["runtime"].run_context.record_step(
            "code_agent",
            inputs={
                "step_request": state["step"],
                "attempt": attempt,
                "error_logs": state.get("error_logs"),
                "validation_feedback": state.get("validation_feedback"),
                "sandbox_environment": scoped["sandbox_environment"],
            },
            outputs={
                "tool_name": code_spec.get("tool_name"),
                "language": "python",
                "source_code": code_spec.get("source_code", ""),
            },
        )
        return {"attempt": attempt, "code_spec": code_spec}

    @staticmethod
    def _align_generated_parameter_schema(
        code_spec: dict[str, Any],
    ) -> dict[str, Any]:
        aligned = deepcopy(code_spec)
        source = str(aligned.get("source_code") or "")
        tool_name = str(aligned.get("tool_name") or "")
        try:
            syntax_tree = ast.parse(source)
        except SyntaxError:
            return aligned
        function_node = next(
            (
                node
                for node in syntax_tree.body
                if isinstance(node, ast.FunctionDef) and node.name == tool_name
            ),
            None,
        )
        if function_node is None:
            return aligned

        positional = function_node.args.posonlyargs + function_node.args.args
        parameters = positional + function_node.args.kwonlyargs
        parameter_names = [argument.arg for argument in parameters]
        default_count = len(function_node.args.defaults)
        required_positional = positional[: len(positional) - default_count]
        required_keyword_only = [
            argument
            for argument, default in zip(
                function_node.args.kwonlyargs,
                function_node.args.kw_defaults,
            )
            if default is None
        ]
        required_names = [
            argument.arg for argument in required_positional + required_keyword_only
        ]

        raw_schema = aligned.get("parameters_schema")
        schema = deepcopy(raw_schema) if isinstance(raw_schema, dict) else {}
        raw_properties = schema.get("properties")
        properties = (
            deepcopy(raw_properties) if isinstance(raw_properties, dict) else {}
        )
        if function_node.args.kwarg is None:
            properties = {
                name: value
                for name, value in properties.items()
                if name in parameter_names
            }
        for argument in parameters:
            properties.setdefault(
                argument.arg,
                {
                    "type": ReportEngine._annotation_json_type(
                        argument.annotation,
                        argument.arg,
                    )
                },
            )
        schema.update(
            {
                "type": "object",
                "properties": properties,
                "required": required_names,
            }
        )
        aligned["parameters_schema"] = schema
        execution_arguments = aligned.get("execution_arguments")
        if isinstance(execution_arguments, dict) and function_node.args.kwarg is None:
            aligned["execution_arguments"] = {
                name: value
                for name, value in execution_arguments.items()
                if name in parameter_names
            }
        return aligned

    @staticmethod
    def _annotation_json_type(
        annotation: ast.expr | None,
        parameter_name: str,
    ) -> str:
        if parameter_name == "path" or parameter_name.endswith("_path"):
            return "string"
        rendered = ast.unparse(annotation).lower() if annotation is not None else ""
        if "list" in rendered or "sequence" in rendered or "tuple" in rendered:
            return "array"
        if "dict" in rendered or "mapping" in rendered:
            return "object"
        if "bool" in rendered:
            return "boolean"
        if "int" in rendered:
            return "integer"
        if "float" in rendered or "number" in rendered:
            return "number"
        return "string" if "str" in rendered else "object"

    def _data_validate_code(self, state: _DataStepState) -> dict[str, Any]:
        interface = self._build_generated_interface(state["step"], state["code_spec"])
        validation_inputs = state["code_spec"].get("execution_arguments", {})
        if not isinstance(validation_inputs, dict):
            validation_inputs = {}
        argument_errors = self._execution_argument_errors(
            state["code_spec"],
            validation_inputs,
            state["step"],
            (
                state["runtime"].sandbox_environment or SandboxEnvironment()
            ).to_prompt_payload(),
        )
        if argument_errors:
            sandbox_result = SandboxRunResult(
                status="failed",
                error="; ".join(argument_errors),
            )
        else:
            sandbox_result = self._validate_in_sandbox(
                interface,
                state["runtime"],
                validation_inputs,
            )
        contract_errors = list(argument_errors)
        if sandbox_result.status == "completed":
            contract_errors.extend(
                self._output_contract_errors(
                    sandbox_result.result,
                    interface.output_schema,
                )
            )
        state["runtime"].run_context.record_step(
            "sandbox_validate",
            status=(
                "failed"
                if sandbox_result.status != "completed" or contract_errors
                else "completed"
            ),
            inputs={"interface": interface.name},
            outputs={
                "result": sandbox_result.result,
                "error": sandbox_result.error,
                "contract_errors": contract_errors,
            },
            artifact_refs=sandbox_result.artifact_refs,
            log_refs=sandbox_result.log_refs,
        )
        sandbox_logs = self._sandbox_logs(sandbox_result)
        if contract_errors:
            sandbox_logs = f"{sandbox_logs}\nContract errors: " + "; ".join(
                contract_errors
            )
        validation = self.validator_agent.run(
            _json_dumps(state["step"]),
            str(state["code_spec"].get("source_code", "")),
            sandbox_logs,
            sandbox_result.result,
        )
        validator_passed = str(validation.get("status", "")).lower() == "pass"
        effective_pass = (
            sandbox_result.status == "completed"
            and not contract_errors
            and validator_passed
        )
        state["runtime"].run_context.record_step(
            "validator_agent",
            status="completed" if effective_pass else "failed",
            inputs={"step_description": state["step"].get("description", "")},
            outputs={
                "validation": validation,
                "sandbox_status": sandbox_result.status,
                "contract_errors": contract_errors,
                "effective_pass": effective_pass,
            },
        )
        return {
            "interface": interface,
            "sandbox_result": sandbox_result,
            "contract_errors": contract_errors,
            "validation": validation,
            "error_logs": sandbox_logs,
            "validation_feedback": str(validation.get("feedback", "")),
        }

    def _validation_choice(self, state: _DataStepState) -> str:
        sandbox_passed = (
            state.get("sandbox_result") is not None
            and state["sandbox_result"].status == "completed"
        )
        validator_passed = (
            str(state.get("validation", {}).get("status", "")).lower() == "pass"
        )
        if sandbox_passed and validator_passed and not state.get("contract_errors"):
            return "execute"
        if int(state.get("attempt", 0)) < self.max_generation_attempts:
            return "retry"
        return "failed"

    def _data_execute_generated(self, state: _DataStepState) -> dict[str, Any]:
        result = self.tool_executor.execute_generated(
            state["interface"],
            state["code_spec"],
            state["runtime"],
            state["sandbox_result"],
        )
        state["runtime"].run_context.record_step(
            "tool_executor",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "tool_name": result.get("tool_name"),
            },
            outputs={"status": result.get("status"), "error": result.get("error")},
        )
        return {"execution_result": result}

    def _data_generation_failed(self, state: _DataStepState) -> dict[str, Any]:
        return {
            "execution_result": {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": state.get("code_spec", {}).get("tool_name"),
                "raw_result": (
                    state.get("sandbox_result").result
                    if state.get("sandbox_result")
                    else None
                ),
                "error": state.get("error_logs")
                or state.get("validation_feedback")
                or "Generated tool validation failed.",
            }
        }

    def _data_analyze(self, state: _DataStepState) -> dict[str, Any]:
        result = self.datascience_processor.process(
            state["step"],
            state["execution_result"],
            state["runtime"],
            state["output_registry"],
            state.get("template_requirements", []),
            state.get("upstream_step_results", []),
            state["spec"].objective,
            state.get("locale_policy"),
        )
        return {"data_step_result": result}

    def _build_generated_interface(
        self, step: dict[str, Any], code_spec: dict[str, Any]
    ) -> InterfaceDefinition:
        return InterfaceDefinition(
            name=str(
                code_spec.get("tool_name")
                or f"generated_{_safe_id(step.get('step_id'))}"
            ),
            description=str(step.get("description", "")),
            input_schema=code_spec.get("parameters_schema", {}),
            output_schema=self._step_output_schema(step, code_spec),
            implementation_ref=str(code_spec.get("source_code", "")),
            source="generated",
            trust_level="generated_unvalidated",
            metadata={
                "capability_names": [GENERATED_TOOL_CAPABILITY],
                "source_code": code_spec.get("source_code", ""),
                "step_request": step,
            },
        )

    @staticmethod
    def _step_output_schema(
        step: dict[str, Any],
        code_spec: dict[str, Any],
    ) -> dict[str, Any]:
        outputs = [
            item for item in _list_value(step.get("outputs")) if isinstance(item, dict)
        ]
        declared = code_spec.get("output_schema")
        declared = deepcopy(declared) if isinstance(declared, dict) else {}
        if len(outputs) != 1:
            return declared or {"type": "object"}

        shape = str(outputs[0].get("shape", "table"))
        expected_type = (
            "array"
            if shape in {"table", "time_series", "category_series"}
            else "object" if shape == "record" else None
        )
        if expected_type is None:
            return declared or {}
        if declared.get("type") == expected_type:
            resolved = declared
        elif expected_type == "array":
            resolved = {"type": "array", "items": {"type": "object"}}
        else:
            resolved = {"type": "object"}
        semantic_roles = {
            str(item)
            for item in _list_value(outputs[0].get("semantic_roles"))
        }
        if (
            expected_type == "array"
            and bool(step.get("required", True))
            and "source_content" in semantic_roles
        ):
            resolved.setdefault("minItems", 1)
        return resolved

    def _validate_in_sandbox(
        self,
        interface: InterfaceDefinition,
        runtime: EngineRuntimeContext,
        validation_inputs: dict[str, Any],
    ) -> SandboxRunResult:
        if runtime.sandbox_executor is None:
            return SandboxRunResult(
                status="failed", error="Sandbox executor is not configured."
            )
        try:
            return runtime.sandbox_executor.validate(
                interface,
                validation_inputs,
                None,
            )
        except Exception as exc:
            return SandboxRunResult(status="failed", error=str(exc))

    def _sandbox_logs(self, sandbox_result: SandboxRunResult) -> str:
        if sandbox_result.status == "completed":
            return "Success"
        return f"Error: {sandbox_result.error or 'Sandbox validation failed.'}"

    @staticmethod
    def _execution_argument_errors(
        code_spec: dict[str, Any],
        arguments: dict[str, Any],
        step: dict[str, Any] | None = None,
        sandbox_environment: dict[str, Any] | None = None,
    ) -> list[str]:
        generation_error = str(code_spec.get("generation_error") or "").strip()
        if generation_error:
            return [generation_error]
        source_code = str(code_spec.get("source_code") or "").strip()
        if not source_code:
            return ["Generated source_code cannot be empty."]
        try:
            syntax_tree = ast.parse(source_code)
        except SyntaxError as exc:
            return [
                "Generated source_code is invalid Python: "
                f"{exc.msg} at line {exc.lineno}."
            ]
        if sandbox_environment is not None:
            available_packages = {
                str(item).strip().lower().replace("-", "_")
                for item in sandbox_environment.get("available_packages", [])
                if str(item).strip()
            }
            imported_roots = {
                (
                    node.module.split(".", 1)[0]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else alias.name.split(".", 1)[0]
                )
                for node in ast.walk(syntax_tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (
                    node.names if isinstance(node, ast.Import) else [node.names[0]]
                )
                if (
                    isinstance(node, ast.Import)
                    or isinstance(node, ast.ImportFrom)
                    and node.module
                )
            }
            unavailable_imports = sorted(
                module
                for module in imported_roots
                if module not in sys.stdlib_module_names
                and module.lower().replace("-", "_") not in available_packages
            )
            if unavailable_imports:
                return [
                    "Generated source imports packages absent from the sandbox "
                    "capability snapshot: " + ", ".join(unavailable_imports)
                ]
        operation = step.get("operation", {}) if isinstance(step, dict) else {}
        operation_kind = str(
            operation.get("kind") if isinstance(operation, dict) else operation or ""
        ).lower()
        if operation_kind in {
            "load_excel",
            "load_spreadsheet",
            "materialize_excel",
            "materialize_source",
            "materialize_spreadsheet",
            "read_excel",
            "read_spreadsheet",
        }:
            masks_read_failure = False
            for handler in (
                node
                for node in ast.walk(syntax_tree)
                if isinstance(node, ast.ExceptHandler)
            ):
                for node in handler.body:
                    for nested in ast.walk(node):
                        if not isinstance(nested, ast.Return):
                            continue
                        value = nested.value
                        if (
                            value is None
                            or isinstance(value, ast.Constant)
                            and value.value is None
                            or isinstance(value, ast.List)
                            and not value.elts
                            or isinstance(value, ast.Dict)
                            and not value.keys
                        ):
                            masks_read_failure = True
                            break
                    if masks_read_failure:
                        break
                if masks_read_failure:
                    break
            if masks_read_failure:
                return [
                    "Generated source materialization must not catch read or "
                    "parser errors and return an empty result. Let ingestion "
                    "errors propagate; return an empty collection only after a "
                    "successful read."
                ]
        tool_name = str(code_spec.get("tool_name") or "").strip()
        if not tool_name.isidentifier():
            return ["Generated tool_name must be a valid Python identifier."]
        function_node = next(
            (
                node
                for node in syntax_tree.body
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and node.name == tool_name
            ),
            None,
        )
        if function_node is None:
            return [
                f"Generated source_code must define a top-level function named "
                f"{tool_name}."
            ]
        if isinstance(function_node, ast.AsyncFunctionDef):
            return ["Generated report tools must be synchronous functions."]
        schema = code_spec.get("parameters_schema")
        if not isinstance(schema, dict):
            return ["Generated parameters_schema must be an object."]
        required = schema.get("required", [])
        if not isinstance(required, list):
            return ["parameters_schema.required must be an array."]
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return ["parameters_schema.properties must be an object."]
        errors = [
            f"Missing required execution argument: {name}"
            for name in map(str, required)
            if name not in arguments
        ]
        positional = function_node.args.posonlyargs + function_node.args.args
        default_count = len(function_node.args.defaults)
        required_positional = positional[: len(positional) - default_count]
        required_keyword_only = [
            argument
            for argument, default in zip(
                function_node.args.kwonlyargs,
                function_node.args.kw_defaults,
            )
            if default is None
        ]
        function_required = {
            argument.arg for argument in required_positional + required_keyword_only
        }
        function_parameters = {
            argument.arg for argument in positional + function_node.args.kwonlyargs
        }
        for name in sorted(function_required):
            if name not in properties:
                errors.append(
                    f"parameters_schema must declare required function argument: {name}"
                )
            if name not in arguments:
                errors.append(f"Missing required function execution argument: {name}")
        if function_node.args.kwarg is None:
            errors.extend(
                f"Unexpected execution argument for generated function: {name}"
                for name in arguments
                if name not in function_parameters
            )
        if function_node.args.posonlyargs:
            errors.append(
                "Generated report tools cannot declare positional-only arguments."
            )
        return list(dict.fromkeys(errors))

    @classmethod
    def _output_contract_errors(
        cls,
        result: Any,
        output_schema: Any,
    ) -> list[str]:
        if not isinstance(output_schema, dict) or not output_schema:
            return []
        expected_type = output_schema.get("type")
        if isinstance(expected_type, list):
            expected_types = [str(item) for item in expected_type]
        elif expected_type:
            expected_types = [str(expected_type)]
        else:
            expected_types = []
        if expected_types and not any(
            cls._matches_json_type(result, item) for item in expected_types
        ):
            rendered = ", ".join(expected_types)
            return [
                f"Generated output must have JSON type {rendered}; "
                f"received {type(result).__name__}."
            ]

        errors: list[str] = []
        if isinstance(result, list):
            minimum_items = output_schema.get("minItems")
            if (
                isinstance(minimum_items, int)
                and not isinstance(minimum_items, bool)
                and len(result) < minimum_items
            ):
                errors.append(
                    "Generated output must contain at least "
                    f"{minimum_items} item(s); received {len(result)}."
                )
        if isinstance(result, dict):
            errors.extend(cls._required_field_errors(result, output_schema))
        if isinstance(result, list):
            item_schema = output_schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(result[:100]):
                    if not cls._matches_declared_schema(item, item_schema):
                        errors.append(
                            f"Generated output item {index} does not match its "
                            "declared JSON type."
                        )
                        continue
                    if isinstance(item, dict):
                        errors.extend(
                            f"Generated output item {index}: {error}"
                            for error in cls._required_field_errors(item, item_schema)
                        )
        return errors

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str) -> bool:
        match expected_type:
            case "array":
                return isinstance(value, list)
            case "object":
                return isinstance(value, dict)
            case "string":
                return isinstance(value, str)
            case "number":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            case "integer":
                return isinstance(value, int) and not isinstance(value, bool)
            case "boolean":
                return isinstance(value, bool)
            case "null":
                return value is None
            case _:
                return True

    @classmethod
    def _matches_declared_schema(
        cls,
        value: Any,
        schema: dict[str, Any],
    ) -> bool:
        expected_type = schema.get("type")
        if not expected_type:
            return True
        if isinstance(expected_type, list):
            return any(
                cls._matches_json_type(value, str(item)) for item in expected_type
            )
        return cls._matches_json_type(value, str(expected_type))

    @staticmethod
    def _required_field_errors(
        value: dict[str, Any],
        schema: dict[str, Any],
    ) -> list[str]:
        required = schema.get("required", [])
        if not isinstance(required, list):
            return ["output_schema.required must be an array."]
        return [
            f"missing required field {name}"
            for name in map(str, required)
            if name not in value
        ]
