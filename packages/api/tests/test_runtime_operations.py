import importlib.util
import unittest
import httpx
import yaml
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from data_intelligence_api.application.runtime_operations import (
    execute_direct_report,
    execute_spec,
    prepare_spec,
    revise_spec,
)
from data_intelligence_api.application.workflow import default_pipeline_factory
from data_intelligence_api.application.workflow import (
    build_workflow_invocation,
    execute_direct_report_workflow,
)
from data_intelligence_api.app.factory import create_app
from data_intelligence_api.domain.workflow import WorkflowRuntimeOptions
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.http.schemas.runtime_inputs import WorkflowRequest
from data_intelligence_api.http.schemas.runtime_operations import (
    DirectExecuteRequest,
    ExecuteRequest,
    PrepareSpecRequest,
    ReviseSpecRequest,
)
from data_intelligence_sdk.core.types import (
    FinalResponse,
    IntentAnalysis,
    PreparedMarkdownExecution,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline


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
        self.direct_calls = []

    def prepare_markdown(self, query, session_context, user_context):
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

    def execute_report_direct(self, query, session_context, user_context):
        self.direct_calls.append((query, session_context, user_context))
        return FinalResponse(answer=f"Direct report for {query.text}")


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
    def test_pipeline_direct_report_uses_raw_query_as_instruction(self):
        captured: dict = {}

        class RecordingReportEngine:
            def run_markdown(self, **kwargs):
                captured.update(kwargs)
                return FinalResponse(answer="direct report")

        pipeline = DataIntelligencePipeline(
            intent_analyzer=object(),
            spec_builder=object(),
            spec_confirmation=object(),
            engine_registry=object(),
            markdown_report_engine=RecordingReportEngine(),
            default_organization_id="org-1",
        )
        query = UserQuery(text="Create a report about NAPH", session_id="session-1")
        result = pipeline.execute_report_direct(
            query,
            SessionContext(session_id="session-1"),
            UserContext(user_id="user-1"),
        )

        self.assertEqual(result.answer, "direct report")
        self.assertEqual(captured["spec_markdown"], query.text)
        self.assertIs(captured["user_query"], query)
        self.assertEqual(captured["organization_id"], "org-1")

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
            )

        self.assertIs(result, pipeline)
        engine = captured.get("markdown_report_engine")
        self.assertIsNotNone(engine)
        self.assertEqual(captured["default_organization_id"], "org-1")
        self.assertFalse(captured.get("configure_default_sandbox", True))
        self.assertEqual(engine.workspace_id, "workspace-1")
        self.assertTrue(engine.discover_workspace_files)

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

    def test_operation_attempt_must_be_positive(self):
        with self.assertRaises(ValidationError):
            PrepareSpecRequest.model_validate(
                {
                    **operation_payload(),
                    "attempt": 0,
                    "runtime_input": runtime_input_payload(),
                }
            )

    def test_direct_execute_accepts_report_without_prepared_spec(self):
        request = DirectExecuteRequest.model_validate(
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

    def test_direct_execute_rejects_non_report_engine(self):
        runtime_input = runtime_input_payload()
        runtime_input["runtime_options"] = {"engine": "general"}

        with self.assertRaises(ValidationError):
            DirectExecuteRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_direct_general",
                    "runtime_input": runtime_input,
                }
            )


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


class RuntimeOperationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            data_corpus_root=Path("."),
            method_hub_default_enabled=False,
        )

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
        revised_markdown = SPEC_MARKDOWN.replace("A concise report.", "A detailed report.")

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

        result = execute_spec(
            ExecuteRequest.model_validate(
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
        ):
            del logger, runtime_options
            captured["execution_context"] = execution_context
            captured["execution_files"] = execution_files
            captured["organization_id"] = organization_id
            captured["workspace_id"] = workspace_id
            captured["discover_workspace_files"] = discover_workspace_files
            return FakePipeline()

        runtime_input = runtime_input_payload()
        runtime_input["organization_id"] = "org-1"
        runtime_input["workspace_id"] = "workspace-1"
        runtime_input["execution_context"] = execution_context_payload()
        runtime_input["execution_files"] = []
        execute_spec(
            ExecuteRequest.model_validate(
                {
                    **operation_payload(),
                    "operation_id": "op_execute_context",
                    "runtime_input": runtime_input,
                    "prepared_execution": prepared.prepared_execution,
                    "spec_markdown": prepared.spec_markdown,
                }
            ),
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

    def test_execution_requires_confirmed_spec_payload(self):
        with self.assertRaises(ValidationError):
            ExecuteRequest.model_validate(
                {
                    **operation_payload(),
                    "runtime_input": runtime_input_payload(),
                    "prepared_execution": {},
                    "spec_markdown": "",
                }
            )

    def test_direct_report_workflow_passes_raw_query_and_discovery_context(self):
        captured: dict = {}
        pipeline = FakePipeline()

        def capture_pipeline(**kwargs):
            captured.update(kwargs)
            return pipeline

        invocation = build_workflow_invocation(
            WorkflowRequest.model_validate(
                {
                    "input": "Create a report",
                    "session_id": "session_1",
                    "organization_id": "org-1",
                    "workspace_id": "workspace-1",
                    "runtime_options": {"engine": "report"},
                }
            ),
            Path("."),
        )
        result = execute_direct_report_workflow(
            invocation,
            logger=SimpleNamespace(log=lambda *args, **kwargs: None),
            pipeline_factory=capture_pipeline,
            execution_context=execution_context_payload(),
            execution_files=[],
            organization_id="org-1",
            workspace_id="workspace-1",
            discover_workspace_files=True,
        )

        self.assertEqual(result.answer, "Direct report for Create a report")
        self.assertEqual(captured["runtime_options"].engine, "report")
        self.assertEqual(captured["workspace_id"], "workspace-1")
        self.assertEqual(captured["execution_files"], [])
        self.assertTrue(captured["discover_workspace_files"])
        self.assertEqual(pipeline.direct_calls[0][0].text, "Create a report")

    def test_direct_report_workflow_disables_discovery_for_explicit_files(self):
        captured: dict = {}

        def capture_pipeline(**kwargs):
            captured.update(kwargs)
            return FakePipeline()

        invocation = build_workflow_invocation(
            WorkflowRequest.model_validate(
                {
                    "input": "Create a report",
                    "session_id": "session_1",
                    "runtime_options": {"engine": "report"},
                }
            ),
            Path("."),
        )
        execute_direct_report_workflow(
            invocation,
            logger=SimpleNamespace(log=lambda *args, **kwargs: None),
            pipeline_factory=capture_pipeline,
            execution_files=[
                {
                    "artifact_id": "asset-1",
                    "filename": "input.csv",
                    "sandbox_path": "/workspace/runs/resp_1/inputs/input.csv",
                    "size": 10,
                }
            ],
            discover_workspace_files=False,
        )

        self.assertFalse(captured["discover_workspace_files"])

    def test_direct_operation_uses_raw_runtime_input(self):
        captured: dict = {}
        pipeline = FakePipeline()

        def capture_pipeline(**kwargs):
            captured.update(kwargs)
            return pipeline

        request = DirectExecuteRequest.model_validate(
            {
                **operation_payload(),
                "operation_id": "op_direct_adapter",
                "runtime_input": {
                    **runtime_input_payload(),
                    "organization_id": "org-1",
                    "workspace_id": "workspace-1",
                    "execution_context": execution_context_payload(),
                    "execution_files": [],
                },
            }
        )
        result = execute_direct_report(
            request,
            settings=self.settings,
            pipeline_factory=capture_pipeline,
        )

        self.assertEqual(result.answer, "Direct report for Create a report")
        self.assertEqual(pipeline.direct_calls[0][0].text, "Create a report")
        self.assertEqual(captured["workspace_id"], "workspace-1")
        self.assertTrue(captured["discover_workspace_files"])


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
            "/v1/executions:stream",
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
        self.assertTrue(execute_response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: runtime.completed", execute_response.text)

    async def test_direct_report_execution_streams_without_confirmation(self):
        response = await self.client.post(
            "/v1/executions:run-stream",
            json={
                **operation_payload(),
                "operation_id": "op_direct_1",
                "runtime_input": runtime_input_payload(),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: runtime.completed", response.text)
        self.assertNotIn("response.requires_confirmation", response.text)

    async def test_direct_report_execution_requires_service_authentication(self):
        response = await self.client.post(
            "/v1/executions:run-stream",
            json={
                **operation_payload(),
                "operation_id": "op_direct_unauthorized",
                "runtime_input": runtime_input_payload(),
            },
        )

        self.assertEqual(response.status_code, 401)

    async def test_direct_report_execution_emits_correlated_failure(self):
        class FailingPipeline(FakePipeline):
            def execute_report_direct(self, query, session_context, user_context):
                del query, session_context, user_context
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
                "/v1/executions:run-stream",
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
        legacy_history = await self.client.get(
            "/api/v1/responses/history/session_1"
        )

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
