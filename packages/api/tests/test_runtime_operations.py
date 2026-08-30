import importlib.util
import unittest
import httpx
import yaml
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from data_intelligence_api.application.runtime_operations import (
    _to_workflow_request,
    execute_instant,
    execute_thinking,
    prepare_spec,
    revise_spec,
    stream_report_events,
)
from data_intelligence_api.application.workflow import (
    build_workflow_invocation,
    default_pipeline_factory,
    execute_instant_workflow,
    select_instant_workflow,
    select_prepared_markdown_engine,
)
from data_intelligence_api.app.factory import create_app
from data_intelligence_api.domain.workflow import (
    WorkflowInvocation,
    WorkflowRuntimeOptions,
)
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    _AxiomSandboxProvider,
)
from data_intelligence_api.http.schemas.runtime_operations import (
    InstantExecutionRequest,
    PrepareSpecRequest,
    ReviseSpecRequest,
    RuntimeInput,
    ThinkingExecutionRequest,
)
from data_intelligence_api.http.schemas.runtime_inputs import WorkflowRequest
from data_intelligence_sdk.core.types import (
    FinalResponse,
    IntentAnalysis,
    PreparedMarkdownExecution,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext

SPEC_MARKDOWN = """# Interactive Execution Spec
## User Request
Create a report.
## Intent
report
## Preparation Guidance
Use the supplied data.
## Execution Instructions
Generate the report.
## Expected Output
A concise report.
"""


class FakePipeline:
    def __init__(self):
        self.memory_context = None

    def prepare_markdown(
        self,
        query,
        session_context,
        user_context,
        *,
        memory_context=None,
    ):
        self.memory_context = memory_context
        return PreparedMarkdownExecution(
            query=query,
            intent_analysis=IntentAnalysis(intent="report"),
            spec_markdown=SPEC_MARKDOWN,
            session_context=session_context,
            user_context=user_context,
        )

    def execute_confirmed_markdown(self, prepared, spec_markdown):
        self.executed_spec = spec_markdown
        return FinalResponse(answer=f"Executed for {prepared.query.text}")

    def execute_confirmed_spec(self, prepared, spec, *, memory_context=None):
        self.executed_spec = spec.objective
        self.memory_context = memory_context
        return FinalResponse(answer=f"Executed for {prepared.query.text}")


def fake_pipeline_factory(*, logger, runtime_options=None):
    del logger, runtime_options
    return FakePipeline()


def operation_payload() -> dict:
    return {
        "schema_version": "1",
        "operation_id": "op_prepare_1",
        "attempt": 1,
        "response_id": "resp_1",
        "trace_id": "trace_1",
    }


def runtime_input_payload() -> dict:
    return {
        "input": "Create a report",
        "session_id": "session_1",
        "runtime_options": {"engine": "report"},
    }


def report_history_payload() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "Compare this quarter with the previous quarter.",
            "artifact_refs": ["artifact://input-1"],
        },
        {
            "role": "assistant",
            "content": "The prior report found a 12% increase.",
            "artifact_refs": ["artifact://report-1"],
        },
    ]


def execution_context_payload() -> dict:
    return {
        "version": "v1",
        "run_id": "resp_1",
        "conversation_id": "session_1",
        "sandbox_id": "40acc1d4-2dae-45a0-b137-bb6f8a8d9bee",
        "execution_workspace_id": "dea1b114-40c7-50da-b196-f511fcde5185",
        "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
        "capability_token": "runtime-token",
        "expires_at": 1234567890,
        "input_path": "/workspace/runs/resp_1/inputs",
        "work_path": "/workspace/runs/resp_1/work",
        "output_path": "/workspace/runs/resp_1/outputs",
        "capabilities": ["sandbox.files", "sandbox.commands"],
    }


class RuntimeOperationModelTests(unittest.TestCase):
    def test_runtime_input_exposes_explicit_workspace_discovery_policy(self):
        self.assertIn("discover_workspace_files", RuntimeInput.model_fields)

    def test_selected_files_survive_runtime_to_workflow_mapping(self):
        request = InstantExecutionRequest.model_validate(
            {
                **operation_payload(),
                "runtime_input": {
                    **runtime_input_payload(),
                    "selected_files": {
                        "mode": "selected",
                        "resource_ids": ["document-1"],
                        "resource_names": ["report.pdf"],
                    },
                },
            }
        )

        workflow_request = _to_workflow_request(request.runtime_input)
        invocation = build_workflow_invocation(
            workflow_request,
            Path("/tmp/data-corpus"),
        )

        self.assertEqual(
            invocation.query.metadata["selected_files"],
            {
                "mode": "selected",
                "resource_ids": ["document-1"],
                "resource_names": ["report.pdf"],
            },
        )
        self.assertEqual(
            invocation.session_context.state["selected_files"]["resource_ids"],
            ["document-1"],
        )

    def test_workflow_invocation_keeps_conversation_history_on_query(self):
        invocation = build_workflow_invocation(
            WorkflowRequest(
                input="Who am I?",
                session_id="session_1",
                history=[
                    {
                        "role": "user",
                        "content": "My name is Anh.",
                    }
                ],
            ),
            Path("/tmp/data-corpus"),
        )

        self.assertEqual(
            invocation.query.metadata["history"],
            [
                {
                    "role": "user",
                    "content": "My name is Anh.",
                    "artifact_refs": [],
                }
            ],
        )

    def test_instant_execution_accepts_every_public_engine_choice(self):
        for engine in ("auto", "general", "reason", "report"):
            request = InstantExecutionRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": f"op_instant_{engine}",
                    "runtime_input": {
                        **runtime_input_payload(),
                        "runtime_options": {"engine": engine},
                    },
                }
            )

            self.assertEqual(request.runtime_input.runtime_options.engine, engine)

    def test_runtime_input_accepts_normalized_report_history(self):
        payload = runtime_input_payload()
        payload.update(
            {
                "model": "deepseek-v4-pro",
                "language": "en",
                "history": report_history_payload(),
            }
        )

        request = InstantExecutionRequest.model_validate(
            {
                **operation_payload(),
                "operation_id": "op_direct_history",
                "runtime_input": payload,
            }
        )

        self.assertEqual(request.runtime_input.model, "deepseek-v4-pro")
        self.assertEqual(request.runtime_input.language, "en")
        self.assertEqual(request.runtime_input.history[1].role, "assistant")
        self.assertEqual(
            request.runtime_input.history[1].artifact_refs,
            ["artifact://report-1"],
        )

    def test_runtime_input_rejects_internal_history_roles(self):
        payload = runtime_input_payload()
        payload["history"] = [{"role": "tool", "content": "secret tool output"}]

        with self.assertRaises(ValidationError):
            InstantExecutionRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_direct_invalid_history",
                    "runtime_input": payload,
                }
            )

    def test_default_pipeline_factory_configures_report_engine(self):
        captured: dict = {}
        pipeline = object()

        def capture_pipeline(**kwargs):
            captured.update(kwargs)
            return pipeline

        with patch(
            "data_intelligence_api.application.workflow.create_example_pipeline",
            side_effect=capture_pipeline,
        ):
            result = default_pipeline_factory(
                logger=SimpleNamespace(),
                runtime_options=WorkflowRuntimeOptions(
                    method_hub_enabled=False,
                    engine="report",
                ),
                execution_context=execution_context_payload(),
                organization_id="org-1",
                workspace_id="workspace-1",
                discover_workspace_files=True,
                operation_id="op_1",
                response_id="resp_1",
                trace_id="trace_1",
                model="deepseek-v4-pro",
                language="en",
                history=report_history_payload(),
            )

        self.assertIs(result, pipeline)
        engine = captured.get("markdown_report_engine")
        self.assertIsNotNone(engine)
        self.assertEqual(captured["default_organization_id"], "org-1")
        self.assertTrue(captured.get("configure_default_sandbox", False))
        self.assertEqual(engine.workspace_id, "workspace-1")
        self.assertTrue(engine.discover_workspace_files)
        self.assertEqual(engine.operation_id, "op_1")
        self.assertEqual(engine.response_id, "resp_1")
        self.assertEqual(engine.trace_id, "trace_1")
        self.assertEqual(engine.model, "deepseek-v4-pro")
        self.assertEqual(engine.language, "en")
        self.assertEqual(engine.organization_id, "org-1")
        self.assertEqual(engine.history, report_history_payload())
        self.assertFalse(hasattr(engine, "service_token"))

    def test_default_pipeline_factory_skips_method_hub_for_engine_selection(self):
        captured: dict = {}
        pipeline = object()

        def capture_pipeline(**kwargs):
            captured.update(kwargs)
            return pipeline

        with (
            patch(
                "data_intelligence_api.application.workflow.create_example_pipeline",
                side_effect=capture_pipeline,
            ),
            patch(
                "data_intelligence_api.application.workflow.resolve_method_hub"
            ) as resolve_method_hub,
        ):
            result = default_pipeline_factory(
                logger=SimpleNamespace(),
                runtime_options=WorkflowRuntimeOptions(
                    method_hub_enabled=True,
                    engine=None,
                ),
                include_method_hub=False,
            )

        self.assertIs(result, pipeline)
        resolve_method_hub.assert_not_called()
        self.assertIsNone(captured["mcp_client"])
        self.assertEqual(captured["mcp_tools"], ())
        self.assertFalse(captured["method_hub_enabled"])
        self.assertFalse(captured["configure_default_sandbox"])

    def test_instant_engine_selection_skips_method_hub_setup(self):
        captured: dict = {}
        selected = object()

        class SelectionPipeline:
            def select_engine(self, prepared, spec, memory_context):
                del prepared, spec, memory_context
                return selected

        def selection_pipeline_factory(
            *,
            logger,
            runtime_options=None,
            include_method_hub=True,
        ):
            del logger, runtime_options
            captured["include_method_hub"] = include_method_hub
            return SelectionPipeline()

        invocation = WorkflowInvocation(
            query=UserQuery(text="hello"),
            uploaded_files=[],
            session_context=SessionContext(),
            user_context=UserContext(),
            runtime_options=WorkflowRuntimeOptions(
                method_hub_enabled=True,
                engine=None,
            ),
            memory_context=MemoryContext(),
        )

        result = select_instant_workflow(
            invocation,
            logger=SimpleNamespace(),
            pipeline_factory=selection_pipeline_factory,
        )

        self.assertIs(result, selected)
        self.assertFalse(captured["include_method_hub"])

    def test_thinking_engine_selection_skips_method_hub_setup(self):
        captured: dict = {}
        selected = object()

        class SelectionPipeline:
            def select_engine(self, prepared, spec, memory_context):
                del prepared, spec, memory_context
                return selected

        def selection_pipeline_factory(
            *,
            logger,
            runtime_options=None,
            include_method_hub=True,
        ):
            del logger, runtime_options
            captured["include_method_hub"] = include_method_hub
            return SelectionPipeline()

        prepared = PreparedMarkdownExecution(
            query=UserQuery(text="hello"),
            intent_analysis=IntentAnalysis(intent="general"),
            spec_markdown=SPEC_MARKDOWN,
            session_context=SessionContext(),
            user_context=UserContext(),
        )

        result = select_prepared_markdown_engine(
            prepared,
            SPEC_MARKDOWN,
            logger=SimpleNamespace(),
            runtime_options=WorkflowRuntimeOptions(
                method_hub_enabled=True,
                engine=None,
            ),
            pipeline_factory=selection_pipeline_factory,
        )

        self.assertIs(result, selected)
        self.assertFalse(captured["include_method_hub"])

    def test_instant_execution_keeps_method_hub_setup(self):
        captured: dict = {}

        class ExecutionPipeline:
            def execute_confirmed_spec(self, prepared, spec, *, memory_context):
                del prepared, spec, memory_context
                return FinalResponse(answer="ok")

        def execution_pipeline_factory(
            *,
            logger,
            runtime_options=None,
            include_method_hub=True,
        ):
            del logger, runtime_options
            captured["include_method_hub"] = include_method_hub
            return ExecutionPipeline()

        invocation = WorkflowInvocation(
            query=UserQuery(text="retrieve data"),
            uploaded_files=[],
            session_context=SessionContext(),
            user_context=UserContext(),
            runtime_options=WorkflowRuntimeOptions(
                method_hub_enabled=True,
                engine=None,
            ),
            memory_context=MemoryContext(),
        )

        result = execute_instant_workflow(
            invocation,
            logger=SimpleNamespace(),
            pipeline_factory=execution_pipeline_factory,
        )

        self.assertEqual(result.answer, "ok")
        self.assertTrue(captured["include_method_hub"])

    def test_sandbox_provider_ignores_method_hub_capability(self):
        create_arguments: list[dict] = []

        class Sandbox:
            capabilities = None

            def wait_until_ready(self, *, timeout):
                del timeout

            def delete(self):
                return None

        class SandboxClient:
            def create_sandbox(self, workspace_id, **kwargs):
                del workspace_id
                create_arguments.append(kwargs)
                return Sandbox()

        provider = _AxiomSandboxProvider(
            SandboxClient(),
            workspace_id=uuid4(),
            cleanup=True,
            ready_timeout_seconds=90,
            pool_enabled=False,
        )

        with provider.open():
            pass

        self.assertEqual(create_arguments, [{}])

    def test_legacy_response_schema_module_is_absent(self):
        self.assertIsNone(
            importlib.util.find_spec("data_intelligence_api.http.schemas.responses")
        )

    def test_legacy_runtime_state_modules_are_absent(self):
        modules = (
            "data_intelligence_api.http.routers.responses",
            "data_intelligence_api.domain.runs",
            "data_intelligence_api.application.ports.run_repository",
            "data_intelligence_api.infrastructure.persistence.run_store",
            "data_intelligence_api.application.gen_report_bridge",
            "data_intelligence_api.application.query_orchestrator",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(module))

    def test_runtime_settings_exclude_response_lifecycle_state(self):
        settings = ApiSettings(
            data_corpus_root=Path("."),
            cors_origins=("http://localhost",),
            runtime_service_token="runtime-token",
        )

        self.assertFalse(hasattr(settings, "database_url"))
        self.assertFalse(hasattr(settings, "spec_confirmation_ttl_seconds"))
        self.assertFalse(hasattr(settings, "gen_report_api_token"))
        self.assertFalse(hasattr(settings, "max_spec_revision_rounds"))
        self.assertEqual(settings.runtime_consumer_service, "intelligence-service")

    def test_prepare_request_accepts_self_contained_payload(self):
        request = PrepareSpecRequest.model_validate(
            {
                **operation_payload(),
                "runtime_input": runtime_input_payload(),
                "memory_scope": {
                    "level": "workspace",
                    "tenant_id": "tenant_1",
                    "workspace_id": "workspace_1",
                },
                "memory_context": {"cards": []},
            }
        )

        self.assertEqual(request.operation_id, "op_prepare_1")
        self.assertEqual(request.runtime_input.session_id, "session_1")
        self.assertEqual(request.runtime_input.runtime_options.engine, "report")

    def test_prepare_spec_passes_upstream_memory_context_to_pipeline(self):
        pipeline = FakePipeline()
        request = PrepareSpecRequest.model_validate(
            {
                **operation_payload(),
                "runtime_input": runtime_input_payload(),
                "memory_context": {
                    "source": "intelligence-service",
                    "cards": [
                        {
                            "memory_id": "memory-1",
                            "memory_type": "preference",
                            "content": "Prefer concise reports.",
                            "confidence": 0.9,
                            "importance": 0.8,
                            "scope": {
                                "tenant_id": "tenant-1",
                                "user_id": "user-1",
                            },
                        }
                    ],
                },
            }
        )

        prepare_spec(
            request,
            settings=SimpleNamespace(
                data_corpus_root=Path("."),
                method_hub_default_enabled=False,
            ),
            pipeline_factory=lambda **kwargs: pipeline,
        )

        self.assertEqual(pipeline.memory_context.mode, "upstream")
        self.assertEqual(pipeline.memory_context.cards[0].memory_id, "memory-1")

    def test_operation_attempt_must_be_positive(self):
        with self.assertRaises(ValidationError):
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "attempt": 0,
                    "runtime_input": runtime_input_payload(),
                }
            )

    def test_instant_execution_does_not_need_a_prepared_spec(self):
        request = InstantExecutionRequest.model_validate(
            {
                **operation_payload(),
                "operation_id": "op_direct_1",
                "runtime_input": runtime_input_payload(),
                "memory_scope": {
                    "level": "workspace",
                    "tenant_id": "tenant_1",
                    "workspace_id": "workspace_1",
                },
                "memory_context": {"cards": []},
            }
        )

        self.assertEqual(request.runtime_input.runtime_options.engine, "report")
        self.assertFalse(hasattr(request, "prepared_execution"))
        self.assertFalse(hasattr(request, "spec_markdown"))


class RuntimeDeploymentTests(unittest.TestCase):
    def test_compose_has_no_runtime_database(self):
        compose_path = Path(__file__).parents[3] / "docker/docker-compose.yaml"
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = document["services"]
        api = services["api"]

        self.assertNotIn("db", services)
        self.assertNotIn("DATABASE_URL", api.get("environment", {}))
        self.assertNotIn("depends_on", api)
        self.assertNotIn("api_state", document.get("volumes", {}))
        self.assertNotIn("postgres_data", document.get("volumes", {}))
        self.assertIn("GEN_REPORT_API_URL", api.get("environment", {}))
        self.assertNotIn("GEN_REPORT_API_TOKEN", api.get("environment", {}))


class RuntimeReportStreamingAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_report_events_maps_genreport_events_live(self):
        captured: dict = {}

        class FakeStreamingEngine:
            def __init__(self, base_url, **kwargs):
                captured["base_url"] = base_url
                captured.update(kwargs)

            async def stream_events(self, *, instruction, organization_id):
                captured["instruction"] = instruction
                captured["organization_id"] = organization_id
                for event_type, payload in (
                    (
                        "report.tool.started",
                        {
                            "tool_call_id": "call_read_1",
                            "tool_name": "read_file",
                            "inputs": {"path": "input.csv"},
                            "status": "started",
                        },
                    ),
                    (
                        "report.tool.completed",
                        {
                            "tool_call_id": "call_read_1",
                            "tool_name": "read_file",
                            "status": "completed",
                            "success": True,
                            "inputs": {"path": "input.csv"},
                            "outputs": {"success": True, "output": "ok"},
                        },
                    ),
                    ("report.output_text.delta", {"delta": "Report"}),
                    (
                        "report.usage",
                        {
                            "model": "deepseek-v4-pro",
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "reasoning_tokens": 0,
                            "total_tokens": 14,
                            "estimated": False,
                        },
                    ),
                    (
                        "report.completed",
                        {
                            "output_text": "Report",
                            "artifacts": [
                                {
                                    "artifact_ref": "artifact://report-1",
                                    "filename": "report.pdf",
                                }
                            ],
                        },
                    ),
                ):
                    yield {"type": event_type, "payload": payload}

        runtime_input = runtime_input_payload()
        runtime_input.update(
            {
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "model": "deepseek-v4-pro",
                "language": "en",
                "history": report_history_payload(),
                "execution_context": execution_context_payload(),
                "selected_files": {
                    "mode": "selected",
                    "resource_ids": ["document-1"],
                    "resource_names": ["report.pdf"],
                },
            }
        )
        request = InstantExecutionRequest.model_validate(
            {
                **operation_payload(),
                "operation_id": "op_stream_report",
                "runtime_input": runtime_input,
            }
        )
        settings = SimpleNamespace(
            gen_report_api_url="http://gen-report",
            gen_report_public_url=None,
        )

        events = [
            event
            async for event in stream_report_events(
                request,
                instruction="Create a report",
                settings=settings,
                engine_factory=FakeStreamingEngine,
            )
        ]

        self.assertEqual(
            [event["type"] for event in events],
            [
                "runtime.progress",
                "runtime.progress",
                "runtime.output_text.delta",
                "runtime.usage",
                "runtime.completed",
            ],
        )
        self.assertEqual(
            events[0]["payload"],
            {
                "event_type": "report.tool.started",
                "phase": "tool",
                "status": "started",
                "label": "read_file",
                "tool_call_id": "call_read_1",
                "tool_name": "read_file",
                "inputs": {"path": "input.csv"},
            },
        )
        self.assertEqual(events[2]["payload"], {"delta": "Report"})
        self.assertEqual(events[1]["payload"]["inputs"], {"path": "input.csv"})
        self.assertEqual(events[1]["payload"]["outputs"]["output"], "ok")
        self.assertEqual(
            events[-1]["payload"]["metadata"]["artifacts"][0]["artifact_ref"],
            "artifact://report-1",
        )
        self.assertEqual(captured["operation_id"], "op_stream_report")
        self.assertEqual(captured["response_id"], "resp_1")
        self.assertEqual(captured["history"], report_history_payload())
        self.assertEqual(captured["instruction"], "Create a report")
        self.assertEqual(captured["workflow"], "report")
        self.assertEqual(captured["selected_files"]["resource_ids"], ["document-1"])
        self.assertNotIn("service_token", captured)


class RuntimeOperationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            data_corpus_root=Path("."),
            method_hub_default_enabled=False,
            gen_report_api_url="http://gen-report",
            gen_report_public_url=None,
        )

    def test_instant_execution_uses_raw_query_without_preparing_a_spec(self):
        captured: dict = {}

        class InstantPipeline:
            def execute_confirmed_spec(self, prepared, spec, *, memory_context):
                captured["prepared"] = prepared
                captured["spec"] = spec
                captured["memory_context"] = memory_context
                return FinalResponse(answer="Instant result")

        request = InstantExecutionRequest.model_validate(
            {
                **operation_payload(),
                "operation_id": "op_instant_general",
                "runtime_input": {
                    **runtime_input_payload(),
                    "runtime_options": {"engine": "general"},
                },
            }
        )

        result = execute_instant(
            request,
            settings=self.settings,
            pipeline_factory=lambda **kwargs: InstantPipeline(),
        )

        self.assertEqual(result.answer, "Instant result")
        self.assertEqual(captured["prepared"].query.text, "Create a report")
        self.assertTrue(captured["spec"].confirmed)
        self.assertEqual(captured["spec"].engine_hint, "general")

    def test_prepare_is_self_contained_and_preserves_operation_envelope(self):
        first = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=self.settings,
            pipeline_factory=fake_pipeline_factory,
        )
        second = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_prepare_2",
                    "response_id": "resp_2",
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=self.settings,
            pipeline_factory=fake_pipeline_factory,
        )

        self.assertEqual(first.operation_id, "op_prepare_1")
        self.assertEqual(first.response_id, "resp_1")
        self.assertEqual(first.intent["value"], "report")
        self.assertIn("intent_analysis", first.prepared_execution)
        self.assertEqual(second.operation_id, "op_prepare_2")
        self.assertEqual(second.response_id, "resp_2")

    def test_revise_validates_requested_markdown(self):
        prepared = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=self.settings,
            pipeline_factory=fake_pipeline_factory,
        )
        revised_markdown = SPEC_MARKDOWN.replace(
            "A concise report.", "A detailed report."
        )

        revised = revise_spec(
            ReviseSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_revise_1",
                    "runtime_input": runtime_input_payload(),
                    "prepared_execution": prepared.prepared_execution,
                    "current_spec_markdown": prepared.spec_markdown,
                    "revised_spec_markdown": revised_markdown,
                }
            ),
            pipeline_factory=fake_pipeline_factory,
        )

        self.assertEqual(revised.operation_id, "op_revise_1")
        self.assertIn("A detailed report.", revised.spec_markdown)

    def test_execute_reconstructs_prepared_input_from_request(self):
        prepared = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=self.settings,
            pipeline_factory=fake_pipeline_factory,
        )

        result = execute_thinking(
            ThinkingExecutionRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_execute_1",
                    "runtime_input": runtime_input_payload(),
                    "prepared_execution": prepared.prepared_execution,
                    "spec_markdown": prepared.spec_markdown,
                }
            ),
            pipeline_factory=fake_pipeline_factory,
        )

        self.assertEqual(result.answer, "Executed for Create a report")

    def test_execute_passes_request_execution_context_to_pipeline_factory(self):
        prepared = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=self.settings,
            pipeline_factory=fake_pipeline_factory,
        )
        captured: dict = {}

        def context_pipeline_factory(
            *,
            logger,
            runtime_options=None,
            execution_context=None,
            execution_files=None,
            organization_id=None,
            workspace_id=None,
            discover_workspace_files=False,
            operation_id=None,
            response_id=None,
            trace_id=None,
            model=None,
            language="auto",
            history=None,
            gen_report_base_url=None,
            gen_report_public_url=None,
        ):
            del logger, runtime_options
            captured["execution_context"] = execution_context
            captured["execution_files"] = execution_files
            captured["organization_id"] = organization_id
            captured["workspace_id"] = workspace_id
            captured["discover_workspace_files"] = discover_workspace_files
            captured["operation_id"] = operation_id
            captured["response_id"] = response_id
            captured["trace_id"] = trace_id
            captured["model"] = model
            captured["language"] = language
            captured["history"] = history
            captured["gen_report_base_url"] = gen_report_base_url
            captured["gen_report_public_url"] = gen_report_public_url
            return FakePipeline()

        runtime_input = runtime_input_payload()
        runtime_input["organization_id"] = "org-1"
        runtime_input["workspace_id"] = "workspace-1"
        runtime_input["execution_context"] = execution_context_payload()
        runtime_input["execution_files"] = []
        runtime_input["model"] = "deepseek-v4-pro"
        runtime_input["language"] = "en"
        runtime_input["history"] = report_history_payload()
        execute_thinking(
            ThinkingExecutionRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_execute_context",
                    "runtime_input": runtime_input,
                    "prepared_execution": prepared.prepared_execution,
                    "spec_markdown": prepared.spec_markdown,
                }
            ),
            settings=self.settings,
            pipeline_factory=context_pipeline_factory,
        )

        self.assertEqual(captured["execution_context"]["run_id"], "resp_1")
        self.assertEqual(
            captured["execution_context"]["capability_token"],
            "runtime-token",
        )
        self.assertEqual(captured["execution_files"], [])
        self.assertEqual(captured["organization_id"], "org-1")
        self.assertEqual(captured["workspace_id"], "workspace-1")
        self.assertTrue(captured["discover_workspace_files"])
        self.assertEqual(captured["operation_id"], "op_execute_context")
        self.assertEqual(captured["response_id"], "resp_1")
        self.assertEqual(captured["trace_id"], "trace_1")
        self.assertEqual(captured["model"], "deepseek-v4-pro")
        self.assertEqual(captured["language"], "en")
        self.assertEqual(captured["history"], report_history_payload())
        self.assertEqual(captured["gen_report_base_url"], "http://gen-report")

    def test_execution_requires_confirmed_spec_payload(self):
        with self.assertRaises(ValidationError):
            ThinkingExecutionRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                    "prepared_execution": {},
                    "spec_markdown": "",
                }
            )


class RuntimeOperationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settings = ApiSettings(
            data_corpus_root=Path("."),
            cors_origins=("http://localhost",),
            runtime_service_token="runtime-token",
        )
        app = create_app(
            settings=settings,
            pipeline_factory=fake_pipeline_factory,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        self.headers = {
            "Authorization": "Bearer runtime-token",
            "X-Consumer-Service": "intelligence-service",
        }

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_stateless_prepare_revise_and_execute_flow(self):
        prepare_payload = {
            **operation_payload(),
            "runtime_input": runtime_input_payload(),
        }
        prepare_response = await self.client.post(
            "/v1/specs:prepare",
            json=prepare_payload,
            headers=self.headers,
        )

        self.assertEqual(prepare_response.status_code, 200)
        prepared = prepare_response.json()
        self.assertEqual(prepared["schema_version"], "1")

        revised_markdown = prepared["spec_markdown"].replace(
            "A concise report.",
            "A detailed report.",
        )
        revise_response = await self.client.post(
            "/v1/specs:revise",
            json={
                **operation_payload(),
                "operation_id": "op_revise_1",
                "runtime_input": runtime_input_payload(),
                "prepared_execution": prepared["prepared_execution"],
                "current_spec_markdown": prepared["spec_markdown"],
                "revised_spec_markdown": revised_markdown,
            },
            headers=self.headers,
        )

        self.assertEqual(revise_response.status_code, 200)
        self.assertIn("A detailed report.", revise_response.json()["spec_markdown"])

        execute_response = await self.client.post(
            "/v1/execution:thinking",
            json={
                **operation_payload(),
                "operation_id": "op_execute_1",
                "runtime_input": runtime_input_payload(),
                "prepared_execution": prepared["prepared_execution"],
                "spec_markdown": revise_response.json()["spec_markdown"],
            },
            headers=self.headers,
        )

        self.assertEqual(execute_response.status_code, 200)
        self.assertTrue(
            execute_response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertIn("event: runtime.completed", execute_response.text)

    async def test_instant_endpoint_executes_without_a_spec_payload(self):
        response = await self.client.post(
            "/v1/execution:instant",
            json={
                **operation_payload(),
                "operation_id": "op_instant_1",
                "runtime_input": {
                    **runtime_input_payload(),
                    "runtime_options": {"engine": "general"},
                },
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: runtime.completed", response.text)
        self.assertNotIn("response.requires_confirmation", response.text)

    async def test_direct_report_execution_streams_without_confirmation(self):
        response = await self.client.post(
            "/v1/execution:instant",
            json={
                **operation_payload(),
                "operation_id": "op_direct_1",
                "runtime_input": runtime_input_payload(),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertIn("event: runtime.completed", response.text)
        self.assertNotIn("response.requires_confirmation", response.text)

    async def test_auto_report_endpoint_forwards_live_genreport_events(self):
        settings = ApiSettings(
            data_corpus_root=Path("."),
            cors_origins=("http://localhost",),
            runtime_service_token="runtime-token",
            gen_report_api_url="http://gen-report",
        )
        app = create_app(settings=settings)
        captured_user_authorizations: list[str | None] = []

        async def fake_report_stream(
            request,
            *,
            instruction,
            settings,
            user_authorization=None,
        ):
            del settings
            captured_user_authorizations.append(user_authorization)
            yield {
                "type": "runtime.output_text.delta",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {"delta": instruction},
            }
            yield {
                "type": "runtime.usage",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {
                    "model": "deepseek-v4-pro",
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "reasoning_tokens": 0,
                    "total_tokens": 14,
                    "estimated": False,
                },
            }
            yield {
                "type": "runtime.completed",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {
                    "output_text": instruction,
                    "evidence": None,
                    "metadata": {"engine_name": "report"},
                },
            }

        runtime_input = runtime_input_payload()
        runtime_input.update(
            {
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "execution_context": execution_context_payload(),
                "runtime_options": {"engine": "auto"},
            }
        )
        with (
            patch(
                "data_intelligence_api.http.routers.runtime_operations.stream_report_events",
                side_effect=fake_report_stream,
            ),
            patch(
                "data_intelligence_api.http.routers.runtime_operations.select_instant_engine",
                return_value=SimpleNamespace(
                    engine=SimpleNamespace(name="report"),
                    selection_source="auto",
                ),
            ) as select_engine,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/execution:instant",
                    json={
                        **operation_payload(),
                        "operation_id": "op_live_report",
                        "runtime_input": runtime_input,
                    },
                    headers={
                        **self.headers,
                        "X-Axiom-User-Authorization": "Bearer user-token",
                    },
                )

        self.assertEqual(response.status_code, 200)
        select_engine.assert_called_once()
        self.assertEqual(captured_user_authorizations, ["Bearer user-token"])
        self.assertEqual(response.text.count("event: runtime.engine.selected"), 1)
        self.assertEqual(response.text.count("event: runtime.output_text.delta"), 1)
        self.assertEqual(response.text.count("event: runtime.usage"), 1)
        self.assertEqual(response.text.count("event: runtime.completed"), 1)

    async def test_thinking_report_endpoint_forwards_live_genreport_events_after_selection(
        self,
    ):
        settings = ApiSettings(
            data_corpus_root=Path("."),
            cors_origins=("http://localhost",),
            runtime_service_token="runtime-token",
            gen_report_api_url="http://gen-report",
        )
        app = create_app(settings=settings)
        captured_user_authorizations: list[str | None] = []
        prepared = prepare_spec(
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                }
            ),
            settings=settings,
            pipeline_factory=fake_pipeline_factory,
        )

        async def fake_report_stream(
            request,
            *,
            instruction,
            settings,
            user_authorization=None,
        ):
            del settings
            captured_user_authorizations.append(user_authorization)
            yield {
                "type": "runtime.output_text.delta",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {"delta": instruction},
            }
            yield {
                "type": "runtime.usage",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {"total_tokens": 14},
            }
            yield {
                "type": "runtime.completed",
                "operation_id": request.operation_id,
                "response_id": request.response_id,
                "payload": {
                    "output_text": instruction,
                    "evidence": None,
                    "metadata": {"engine_name": "report"},
                },
            }

        runtime_input = runtime_input_payload()
        runtime_input.update(
            {
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "execution_context": execution_context_payload(),
                "runtime_options": {"engine": "auto"},
            }
        )
        with (
            patch(
                "data_intelligence_api.http.routers.runtime_operations.stream_report_events",
                side_effect=fake_report_stream,
            ),
            patch(
                "data_intelligence_api.http.routers.runtime_operations.select_thinking_engine",
                return_value=SimpleNamespace(
                    engine=SimpleNamespace(name="report"),
                    selection_source="auto",
                ),
            ) as select_engine,
            patch(
                "data_intelligence_api.http.routers.runtime_operations.execute_thinking",
                return_value=FinalResponse(answer="buffered result"),
            ) as execute,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/execution:thinking",
                    json={
                        **operation_payload(),
                        "operation_id": "op_thinking_live_report",
                        "runtime_input": runtime_input,
                        "prepared_execution": prepared.prepared_execution,
                        "spec_markdown": prepared.spec_markdown,
                    },
                    headers={
                        **self.headers,
                        "X-Axiom-User-Authorization": "Bearer user-token",
                    },
                )

        self.assertEqual(response.status_code, 200)
        select_engine.assert_called_once()
        execute.assert_not_called()
        self.assertEqual(captured_user_authorizations, ["Bearer user-token"])
        self.assertEqual(response.text.count("event: runtime.engine.selected"), 1)
        self.assertEqual(response.text.count("event: runtime.output_text.delta"), 1)
        self.assertEqual(response.text.count("event: runtime.usage"), 1)
        self.assertEqual(response.text.count("event: runtime.completed"), 1)

    async def test_direct_report_execution_requires_service_authentication(self):
        response = await self.client.post(
            "/v1/execution:instant",
            json={
                **operation_payload(),
                "operation_id": "op_direct_unauthorized",
                "runtime_input": runtime_input_payload(),
            },
        )

        self.assertEqual(response.status_code, 401)

    async def test_direct_report_execution_emits_correlated_failure(self):
        class FailingPipeline(FakePipeline):
            def execute_confirmed_spec(self, prepared, spec, *, memory_context=None):
                del prepared, spec, memory_context
                raise RuntimeError("provider failed")

        app = create_app(
            settings=ApiSettings(
                data_corpus_root=Path("."),
                cors_origins=("http://localhost",),
                runtime_service_token="runtime-token",
            ),
            pipeline_factory=lambda **kwargs: FailingPipeline(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/execution:instant",
                json={
                    **operation_payload(),
                    "operation_id": "op_direct_failed",
                    "response_id": "resp_failed",
                    "runtime_input": runtime_input_payload(),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("event: runtime.failed"), 1)
        self.assertIn('"operation_id":"op_direct_failed"', response.text)
        self.assertIn('"response_id":"resp_failed"', response.text)

    async def test_app_is_stateless_and_legacy_response_routes_are_absent(self):
        health = await self.client.get("/health")
        legacy_create = await self.client.post(
            "/api/v1/responses",
            json={"input": "legacy", "session_id": "session_1"},
        )
        legacy_decision = await self.client.post(
            "/api/v1/responses/resp_1/decision",
            json={"action": "confirm", "revision": 1},
        )
        legacy_history = await self.client.get("/api/v1/responses/history/session_1")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(legacy_create.status_code, 404)
        self.assertEqual(legacy_decision.status_code, 404)
        self.assertEqual(legacy_history.status_code, 404)

    async def test_runtime_operations_require_service_authentication(self):
        response = await self.client.post(
            "/v1/specs:prepare",
            json={
                **operation_payload(),
                "runtime_input": runtime_input_payload(),
            },
        )

        self.assertEqual(response.status_code, 401)

    async def test_runtime_operations_fail_closed_when_token_is_not_configured(self):
        settings = ApiSettings(
            data_corpus_root=Path("."),
            cors_origins=("http://localhost",),
        )
        app = create_app(
            settings=settings,
            pipeline_factory=fake_pipeline_factory,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/specs:prepare",
                json={
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                },
                headers={"X-Consumer-Service": "intelligence-service"},
            )

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
