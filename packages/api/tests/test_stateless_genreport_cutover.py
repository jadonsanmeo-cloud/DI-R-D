import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import httpx

from data_intelligence_api.application.runtime_operations import stream_report_events
from data_intelligence_api.http.schemas.runtime_operations import DirectExecuteRequest
from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportMarkdownEngine,
)

WORKSPACE = Path(__file__).resolve().parents[4]
GENREPORT_BACKEND = WORKSPACE / "GenReport" / "backend"
if str(GENREPORT_BACKEND) not in sys.path:
    sys.path.insert(0, str(GENREPORT_BACKEND))

spec = importlib.util.spec_from_file_location(
    "genreport_cutover_main",
    GENREPORT_BACKEND / "main.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("Unable to load GenReport application")
genreport_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(genreport_main)

from app.api.v1 import reports as genreport_reports  # noqa: E402
from app.contracts.report_execution import ReportCompletion, ReportUsage  # noqa: E402
from app.services.report_events import ReportEventFactory  # noqa: E402


def direct_request(
    history: list[dict],
    *,
    primary_source: bool = False,
    method_hub_enabled: bool = False,
) -> DirectExecuteRequest:
    execution_files: list[dict] = []
    primary_source_id: str | None = None
    if primary_source:
        primary_source_id = "source-primary"
        execution_files.append(
            {
                "artifact_id": "asset-primary",
                "filename": "latest.csv",
                "sandbox_path": "/workspace/runs/resp_1/inputs/latest.csv",
                "content_type": "text/csv",
                "size": 42,
                "source_id": primary_source_id,
                "document_id": "document-primary",
                "source_object_key": "organizations/org-1/sources/latest.csv",
                "source_last_modified": "2026-08-20T09:00:00Z",
            }
        )
    return DirectExecuteRequest.model_validate(
        {
            "schema_version": "1",
            "operation_id": "op_1",
            "attempt": 1,
            "response_id": "resp_1",
            "trace_id": "trace_1",
            "runtime_input": {
                "input": "Create the follow-up report",
                "session_id": "conv_1",
                "model": "deepseek-v4-pro",
                "language": "en",
                "history": history,
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "runtime_options": {
                    "engine": "report",
                    "method_hub_enabled": method_hub_enabled,
                },
                "execution_context": {
                    "version": "v1",
                    "run_id": "resp_1",
                    "conversation_id": "conv_1",
                    "sandbox_id": "40acc1d4-2dae-45a0-b137-bb6f8a8d9bee",
                    "execution_workspace_id": "dea1b114-40c7-50da-b196-f511fcde5185",
                    "gateway_url": "http://axiom/api/v1/runtime/runs/resp_1",
                    "capability_token": "runtime-token",
                    "expires_at": 2_000_000_000,
                    "input_path": "/workspace/runs/resp_1/inputs",
                    "work_path": "/workspace/runs/resp_1/work",
                    "output_path": "/workspace/runs/resp_1/outputs",
                    "capabilities": ["sandbox.files", "sandbox.commands"],
                },
                "execution_files": execution_files,
                "primary_source_id": primary_source_id,
            },
        }
    )


class RecordingExecutionService:
    def __init__(self, requests: list) -> None:
        self.requests = requests

    async def stream(self, request):
        self.requests.append(request)
        factory = ReportEventFactory(request)
        usage = ReportUsage(
            model="deepseek-v4-pro",
            input_tokens=10,
            output_tokens=4,
            reasoning_tokens=0,
            total_tokens=14,
            estimated=False,
        )
        yield factory.create("report.status", {"phase": "model"})
        yield factory.create(
            "report.output_text.delta",
            {"delta": "Report ready."},
        )
        yield factory.create("report.usage", usage.model_dump(mode="json"))
        yield factory.create(
            "report.completed",
            ReportCompletion(
                output_text="Report ready.",
                artifacts=[
                    {
                        "artifact_ref": "artifact://report-1",
                        "filename": "report.pdf",
                    }
                ],
                usage=usage,
            ).model_dump(mode="json"),
        )


class RecordingAsgiApp:
    def __init__(self, app) -> None:
        self.app = app
        self.paths: list[str] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.paths.append(scope["path"])
        await self.app(scope, receive, send)


class StatelessGenReportCutoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_selected_inputs_as_runtime_progress(self):
        captured_engine_kwargs: dict = {}

        class FakeStreamingEngine:
            def __init__(self, _base_url, **kwargs) -> None:
                captured_engine_kwargs.update(kwargs)

            async def stream_events(self, *, instruction, organization_id):
                del instruction, organization_id
                yield {
                    "type": "report.inputs.selected",
                    "payload": {
                        "inputs": [
                            {
                                "source_id": "source-primary",
                                "role": "primary",
                            }
                        ]
                    },
                }
                yield {
                    "type": "report.completed",
                    "payload": {"output_text": "Report ready.", "artifacts": []},
                }

        events = [
            event
            async for event in stream_report_events(
                direct_request([], primary_source=True, method_hub_enabled=True),
                instruction="Create a PDF report",
                settings=SimpleNamespace(
                    gen_report_api_url="http://genreport.test",
                    gen_report_public_url=None,
                ),
                engine_factory=FakeStreamingEngine,
            )
        ]

        selected = next(
            event
            for event in events
            if event["payload"].get("event_type") == "report.inputs.selected"
        )
        self.assertEqual(selected["type"], "runtime.progress")
        self.assertEqual(
            selected["payload"]["inputs"],
            [{"source_id": "source-primary", "role": "primary"}],
        )
        self.assertEqual(captured_engine_kwargs["primary_source_id"], "source-primary")
        self.assertTrue(captured_engine_kwargs["discover_workspace_files"])

    async def test_restart_uses_supplied_axiom_history_and_one_internal_path(self):
        captured_requests: list = []
        recording_app = RecordingAsgiApp(genreport_main.app)
        settings = SimpleNamespace(
            gen_report_api_url="http://genreport.test",
            gen_report_public_url=None,
        )

        def engine_factory(base_url, **kwargs):
            return GenReportMarkdownEngine(
                base_url,
                transport=httpx.ASGITransport(app=recording_app),
                **kwargs,
            )

        async def first_service():
            return RecordingExecutionService(captured_requests)

        genreport_main.app.dependency_overrides[
            genreport_reports.get_report_execution_service
        ] = first_service
        first_events = [
            event
            async for event in stream_report_events(
                direct_request([]),
                instruction="Create the baseline report",
                settings=settings,
                engine_factory=engine_factory,
            )
        ]

        history = [
            {
                "role": "user",
                "content": "Create the baseline report",
                "artifact_refs": [],
            },
            {
                "role": "assistant",
                "content": first_events[-1]["payload"]["output_text"],
                "artifact_refs": ["artifact://report-1"],
            },
        ]

        # A new request-local service simulates restarting GenReport between turns.
        async def restarted_service():
            return RecordingExecutionService(captured_requests)

        genreport_main.app.dependency_overrides[
            genreport_reports.get_report_execution_service
        ] = restarted_service
        second_events = [
            event
            async for event in stream_report_events(
                direct_request(history),
                instruction="Create the follow-up report",
                settings=settings,
                engine_factory=engine_factory,
            )
        ]

        self.assertEqual(
            [event["type"] for event in second_events],
            [
                "runtime.progress",
                "runtime.output_text.delta",
                "runtime.usage",
                "runtime.completed",
            ],
        )
        self.assertEqual(
            recording_app.paths,
            [
                "/api/v1/reports:stream",
                "/api/v1/reports:stream",
            ],
        )
        self.assertEqual(
            [item.model_dump(mode="json") for item in captured_requests[-1].history],
            history,
        )
        self.assertEqual(
            second_events[-1]["payload"]["metadata"]["artifacts"][0]["artifact_ref"],
            "artifact://report-1",
        )
        genreport_main.app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
