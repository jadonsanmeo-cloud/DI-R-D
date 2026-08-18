import json
import unittest
from types import SimpleNamespace

import httpx

from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportMarkdownEngine,
)


def report_event(event_id: str, event_type: str, payload: dict) -> str:
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
                "operation_id": "op_1",
                "response_id": "resp_1",
                "run_id": "resp_1",
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "trace_id": "trace-1",
                "payload": payload,
            }
        )
        + "\n\n"
    )


class GenReportMarkdownEngineTests(unittest.TestCase):
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
        engine = GenReportMarkdownEngine(
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
            transport=httpx.MockTransport(handler),
        )

        result = engine.run_markdown(
            spec_markdown="# Report spec",
            organization_id="org-1",
            runtime=SimpleNamespace(),
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

        engine = GenReportMarkdownEngine(
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


class GenReportStreamingTests(unittest.IsolatedAsyncioTestCase):
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

        engine = GenReportMarkdownEngine(
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
