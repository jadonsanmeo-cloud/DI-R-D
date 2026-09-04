import json
import unittest
from types import SimpleNamespace

import httpx
from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportEngine,
)
from data_intelligence_sdk.core.types import (
    EngineInput,
    ExecutionSpec,
    FinalResponse,
    UserQuery,
)


def report_event(
    event_id: str,
    event_type: str,
    payload: dict,
    *,
    operation_id: str = "op_1",
    response_id: str = "resp_1",
    run_id: str = "resp_1",
) -> str:
    return (
        f"event: {event_type}\n"
        "data: "
        + json.dumps(
            {
                "schema_version": "1",
                "event_id": event_id,
                "type": event_type,
                "producer": "gen-report",
                "occurred_at": "2026-08-17T00:00:00Z",
                "operation_id": operation_id,
                "response_id": response_id,
                "run_id": run_id,
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "trace_id": "trace-1",
                "payload": payload,
            }
        )
        + "\n\n"
    )


class GenReportEngineTests(unittest.TestCase):
    def test_run_adapts_the_shared_engine_contract_to_markdown_execution(self):
        captured: dict = {}

        class AdapterEngine(GenReportEngine):
            def run_markdown(self, **kwargs):
                captured.update(kwargs)
                return FinalResponse(answer="Report ready.", metadata={"usage": {}})

        engine = AdapterEngine("http://gen-report", organization_id="org-1")
        output = engine.run(
            EngineInput(
                query=UserQuery(text="Create a report"),
                spec=ExecutionSpec(
                    intent="report",
                    objective="# Report spec",
                    confirmed=True,
                ),
                runtime=SimpleNamespace(),  # type: ignore[arg-type]
            )
        )

        self.assertEqual(output.engine_name, "report")
        self.assertEqual(output.answer, "Report ready.")
        self.assertEqual(captured["spec_markdown"], "# Report spec")
        self.assertEqual(captured["organization_id"], "org-1")

    def test_run_markdown_posts_one_unauthenticated_stateless_request(self):
        requests: list[dict] = []
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            self.assertEqual(request.url.path, "/api/v1/reports:stream")
            self.assertNotIn("Authorization", request.headers)
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                text=(
                    report_event(
                        "evt_1",
                        "report.output_text.delta",
                        {"delta": "Report ready."},
                    )
                    + report_event(
                        "evt_2",
                        "report.usage",
                        {
                            "model": "deepseek-v4-pro",
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "reasoning_tokens": 0,
                            "total_tokens": 14,
                            "estimated": False,
                        },
                    )
                    + report_event(
                        "evt_3",
                        "report.completed",
                        {
                            "output_text": "Report ready.",
                            "artifacts": [
                                {
                                    "artifact_ref": "artifact://report-1",
                                    "filename": "report.pdf",
                                }
                            ],
                        },
                    )
                ),
                headers={"content-type": "text/event-stream"},
            )

        execution_context = {
            "run_id": "resp_1",
            "conversation_id": "session-1",
            "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
            "capability_token": "runtime-token",
            "expires_at": 1234567890,
            "input_path": "/workspace/runs/resp_1/inputs",
            "work_path": "/workspace/runs/resp_1/work",
            "output_path": "/workspace/runs/resp_1/outputs",
            "capabilities": ["sandbox.files", "sandbox.commands"],
        }
        execution_files = [
            {
                "artifact_id": "artifact-1",
                "filename": "input.csv",
                "sandbox_path": "/workspace/runs/resp_1/inputs/input.csv",
                "content_type": "text/csv",
                "size": 10,
            }
        ]
        history = [
            {
                "role": "user",
                "content": "Earlier question",
                "artifact_refs": [],
            },
            {
                "role": "assistant",
                "content": "Earlier answer",
                "artifact_refs": ["artifact://report-0"],
            },
        ]
        engine = GenReportEngine(
            "http://gen-report",
            operation_id="op_1",
            response_id="resp_1",
            trace_id="trace-1",
            model="deepseek-v4-pro",
            language="en",
            history=history,
            execution_context=execution_context,
            execution_files=execution_files,
            workspace_id="workspace-1",
            discover_workspace_files=False,
            all_inputs_primary=True,
            transport=httpx.MockTransport(handler),
        )

        result = engine.run_markdown(
            spec_markdown="# Report spec",
            organization_id="org-1",
            runtime=SimpleNamespace(
                selected_files={
                    "mode": "selected",
                    "resource_ids": ["document-1"],
                    "resource_names": ["report.pdf"],
                }
            ),
            user_context=SimpleNamespace(),
            user_query=SimpleNamespace(text="Create a report"),
        )

        self.assertEqual(paths, ["/api/v1/reports:stream"])
        self.assertEqual(result.answer, "Report ready.")
        self.assertEqual(result.metadata["engine_name"], "report")
        self.assertEqual(
            result.metadata["artifacts"],
            ["artifact://report-1"],
        )
        self.assertNotIn("gen_report_conversation_id", result.metadata)
        self.assertEqual(len(requests), 1)
        payload = requests[0]
        self.assertEqual(payload["instruction"], "# Report spec")
        self.assertEqual(payload["operation_id"], "op_1")
        self.assertEqual(payload["response_id"], "resp_1")
        self.assertEqual(payload["run_id"], "resp_1")
        self.assertEqual(payload["history"], history)
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["execution_context"], execution_context)
        self.assertEqual(payload["execution_files"], execution_files)
        self.assertTrue(payload["all_inputs_primary"])
        self.assertEqual(
            payload["selected_files"],
            {
                "mode": "selected",
                "resource_ids": ["document-1"],
                "resource_names": ["report.pdf"],
            },
        )
        self.assertEqual(
            payload["runtime_gateway"],
            {
                "run_id": "resp_1",
                "endpoint": "http://axiom/api/v1/runtime/runs/resp_1",
                "token": "runtime-token",
                "token_type": "bearer",
                "expires_at": 1234567890,
                "workspace_id": "workspace-1",
                "capabilities": ["sandbox.files", "sandbox.commands"],
            },
        )

    def test_run_markdown_enables_workspace_discovery_without_explicit_files(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                text=report_event(
                    "evt_1",
                    "report.completed",
                    {"output_text": "Complete", "artifacts": []},
                ),
                headers={"content-type": "text/event-stream"},
            )

        engine = GenReportEngine(
            "http://gen-report",
            operation_id="op_1",
            response_id="resp_1",
            trace_id="trace-1",
            model=None,
            language="auto",
            history=[],
            execution_context={
                "run_id": "resp_1",
                "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
                "capability_token": "runtime-token",
                "expires_at": 1234567890,
                "capabilities": [],
            },
            execution_files=[],
            workspace_id="workspace-1",
            discover_workspace_files=True,
            transport=httpx.MockTransport(handler),
        )

        engine.run_markdown(
            spec_markdown="# Report spec",
            organization_id="org-1",
            runtime=SimpleNamespace(),
            user_context=SimpleNamespace(),
            user_query=SimpleNamespace(text="Create a report"),
        )

        self.assertEqual(requests[0]["organization_id"], "org-1")
        self.assertEqual(requests[0]["workspace_id"], "workspace-1")
        self.assertTrue(requests[0]["discover_workspace_files"])

    def test_dashboard_extraction_uses_distinct_endpoint_without_discovery(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/reports:extract-dashboard")
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                text=report_event(
                    "evt_dashboard_1",
                    "report.completed",
                    {
                        "output_text": "Dashboard extracted.",
                        "artifacts": [
                            {
                                "artifact_ref": "artifact://dashboard-1",
                                "filename": "report-dashboard.json",
                            }
                        ],
                    },
                    operation_id="op_dashboard",
                    response_id="resp_dashboard",
                    run_id="resp_dashboard",
                ),
                headers={"content-type": "text/event-stream"},
            )

        engine = GenReportEngine(
            "http://gen-report",
            operation_id="op_dashboard",
            response_id="resp_dashboard",
            trace_id="trace-dashboard",
            model="deepseek-v4-pro",
            execution_context={
                "run_id": "resp_dashboard",
                "gateway_url": "http://axiom/api/v1/runtime/runs/resp_dashboard",
                "capability_token": "runtime-token",
                "expires_at": 1234567890,
                "capabilities": ["sandbox.files", "sandbox.commands"],
            },
            execution_files=[
                {
                    "artifact_id": "report-pdf",
                    "filename": "report.pdf",
                    "sandbox_path": "/workspace/runs/resp_dashboard/inputs/report.pdf",
                    "content_type": "application/pdf",
                    "size": 10,
                }
            ],
            workspace_id="workspace-1",
            discover_workspace_files=False,
            workflow="dashboard_extraction",
            transport=httpx.MockTransport(handler),
        )

        result = engine.run_markdown(
            spec_markdown="Extract the dashboard.",
            organization_id="org-1",
            runtime=SimpleNamespace(),
            user_context=SimpleNamespace(),
            user_query=SimpleNamespace(text="Extract the dashboard"),
        )

        self.assertEqual(result.answer, "Dashboard extracted.")
        self.assertEqual(len(requests), 1)
        self.assertFalse(requests[0]["discover_workspace_files"])
        self.assertNotIn("workflow", requests[0])


class GenReportStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_events_forwards_user_context_without_replacing_service_auth(
        self,
    ):
        captured_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(request.headers)
            return httpx.Response(
                200,
                text=report_event(
                    "evt_1",
                    "report.completed",
                    {"output_text": "done", "artifacts": []},
                ),
                headers={"content-type": "text/event-stream"},
            )

        engine = GenReportEngine(
            "http://gen-report",
            operation_id="op_1",
            response_id="resp_1",
            trace_id="trace-1",
            user_authorization="Bearer user-token",
            execution_context={
                "run_id": "resp_1",
                "gateway_url": "http://axiom/runtime/resp_1",
                "capability_token": "runtime-token",
                "expires_at": 1234567890,
                "capabilities": [],
            },
            workspace_id="workspace-1",
            transport=httpx.MockTransport(handler),
        )

        events = [
            event
            async for event in engine.stream_events(
                instruction="Create a report",
                organization_id="org-1",
            )
        ]

        self.assertEqual(events[0]["type"], "report.completed")
        self.assertEqual(
            captured_headers["x-axiom-user-authorization"],
            "Bearer user-token",
        )
        self.assertEqual(captured_headers["x-trace-id"], "trace-1")
        self.assertNotIn("authorization", captured_headers)

    async def test_stream_events_yields_normalized_events_in_order(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=(
                    report_event(
                        "evt_1",
                        "report.output_text.delta",
                        {"delta": "first"},
                    )
                    + report_event(
                        "evt_2",
                        "report.completed",
                        {"output_text": "first", "artifacts": []},
                    )
                ),
                headers={"content-type": "text/event-stream"},
            )

        engine = GenReportEngine(
            "http://gen-report",
            operation_id="op_1",
            response_id="resp_1",
            trace_id="trace-1",
            history=[],
            execution_context={
                "run_id": "resp_1",
                "gateway_url": "http://axiom/runtime/resp_1",
                "capability_token": "runtime-token",
                "expires_at": 1234567890,
                "capabilities": [],
            },
            workspace_id="workspace-1",
            transport=httpx.MockTransport(handler),
        )

        events = [
            event
            async for event in engine.stream_events(
                instruction="Create a report",
                organization_id="org-1",
            )
        ]

        self.assertEqual(
            [event["type"] for event in events],
            ["report.output_text.delta", "report.completed"],
        )


if __name__ == "__main__":
    unittest.main()
