import importlib.util
import unittest
import httpx
import yaml
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from data_intelligence_api.application.runtime_operations import (
    execute_spec,
    prepare_spec,
    revise_spec,
)
from data_intelligence_api.app.factory import create_app
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.http.schemas.runtime_operations import (
    ExecuteRequest,
    PrepareSpecRequest,
    ReviseSpecRequest,
)
from data_intelligence_sdk.core.types import (
    FinalResponse,
    IntentAnalysis,
    PreparedMarkdownExecution,
)


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


class RuntimeOperationModelTests(unittest.TestCase):
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
