import json
import unittest
from types import SimpleNamespace

import httpx

from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportMarkdownEngine,
)


class GenReportMarkdownEngineTests(unittest.TestCase):
    def test_run_markdown_forwards_execution_context_and_collects_result(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/conversations":
                return httpx.Response(201, json={"hash_id": "conversation-1"})
            if request.url.path == "/api/v1/chat/stream":
                requests.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    text=(
                        'data: {"type":"delta","content":"Report ready."}\n\n'
                        'data: {"type":"done","generated_files":['
                        '{"name":"report.pdf","url":"/files/report.pdf"}]}\n\n'
                    ),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(404)

        execution_context = {
            "run_id": "resp_1",
            "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
            "capability_token": "runtime-token",
            "expires_at": 1234567890,
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
        engine = GenReportMarkdownEngine(
            "http://gen-report",
            public_base_url="http://public-gen-report",
            execution_context=execution_context,
            execution_files=execution_files,
            workspace_id="workspace-1",
            discover_workspace_files=False,
            transport=httpx.MockTransport(handler),
        )

        try:
            result = engine.run_markdown(
                spec_markdown="# Report spec",
                organization_id="test-org",
                runtime=SimpleNamespace(),
                user_context=SimpleNamespace(),
                user_query=SimpleNamespace(text="Create a report"),
            )
        except NotImplementedError:
            self.fail("GenReport Markdown execution is not implemented")

        self.assertEqual(result.answer, "Report ready.")
        self.assertEqual(result.metadata["engine_name"], "report")
        self.assertEqual(
            result.metadata["generated_files"][0]["url"],
            "http://public-gen-report/files/report.pdf",
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["message"], "# Report spec")
        self.assertEqual(requests[0]["execution_context"], execution_context)
        self.assertEqual(requests[0]["execution_files"], execution_files)
        self.assertEqual(requests[0]["workspace_id"], "workspace-1")
        self.assertFalse(requests[0]["discover_workspace_files"])
        self.assertEqual(
            requests[0]["runtime_gateway"],
            {
                "run_id": "resp_1",
                "endpoint": "http://axiom/api/v1/runtime/runs/resp_1",
                "token": "runtime-token",
                "token_type": "bearer",
                "expires_at": 1234567890,
            },
        )

    def test_run_markdown_enables_workspace_discovery_without_explicit_files(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/conversations":
                return httpx.Response(201, json={"hash_id": "conversation-1"})
            if request.url.path == "/api/v1/chat/stream":
                requests.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    text='data: {"type":"done","generated_files":[]}\n\n',
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(404)

        engine = GenReportMarkdownEngine(
            "http://gen-report",
            execution_context={
                "run_id": "resp_1",
                "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
                "capability_token": "runtime-token",
                "expires_at": 1234567890,
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


if __name__ == "__main__":
    unittest.main()
