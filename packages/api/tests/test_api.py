import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.main import create_app
from data_intelligence_api.infrastructure.persistence.memory.run_repository import (
    InMemoryRunRepository,
)
from data_intelligence_sdk.core.types import (
    FinalResponse,
    IntentAnalysis,
    PreparedMarkdownExecution,
)


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def json(self):
        return json.loads(self.content)

    def iter_lines(self):
        return self.content.decode("utf-8").splitlines()


async def asgi_request(app, method, path, *, json_body=None, content=None, headers=None):
    body = json.dumps(json_body).encode() if json_body is not None else (content or b"")
    request_headers = {key.lower(): value for key, value in (headers or {}).items()}
    if json_body is not None:
        request_headers.setdefault("content-type", "application/json")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.encode(), value.encode()) for key, value in request_headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return AsgiResponse(
        start["status"],
        {key.decode(): value.decode() for key, value in start["headers"]},
        response_body,
    )


def parse_sse(response):
    events = []
    event_name = None
    data = None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif not line and event_name and data is not None:
            events.append((event_name, data))
            event_name = data = None
    return events


def multipart_body(fields, files):
    boundary = "data-intelligence-test-boundary"
    chunks = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    for field_name, filename, content, content_type in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class FakePipeline:
    def __init__(self, logger, factory):
        self.logger = logger
        self.factory = factory

    def prepare_markdown(self, query, session_context, user_context):
        self.factory.prepare_calls += 1
        self.logger.log("pipeline.start", {})
        self.logger.log("pipeline.intent_analyzed", {"intent": "reason"})
        markdown = (
            "# Interactive Execution Spec\n\n"
            f"## User Request\n\n{query.text}\n\n"
            "## Intent\n\nReport.\n\n"
            "## Preparation Guidance\n\nRetrieve context.\n\n"
            "## Execution Instructions\n\nRetrieve and cite sources.\n\n"
            "## Expected Output\n\nMarkdown report.\n"
        )
        return PreparedMarkdownExecution(
            query=query,
            intent_analysis=IntentAnalysis(intent="report"),
            spec_markdown=markdown,
            session_context=session_context,
            user_context=user_context,
        )

    def execute_confirmed_markdown(self, prepared, spec_markdown):
        self.factory.execute_calls += 1
        self.logger.log("pipeline.spec_confirmed", {})
        return FinalResponse(
            answer=f"Answer for: {spec_markdown}",
            metadata={"engine_name": "report"},
        )


class RecordingFactory:
    def __init__(self):
        self.prepare_calls = 0
        self.revise_calls = 0
        self.execute_calls = 0

    def __call__(self, *, logger):
        return FakePipeline(logger, self)


class BackendApiTests(unittest.TestCase):
    def make_app(self, root, *, store=None, factory=None):
        return create_app(
            ApiSettings(Path(root), ("http://localhost:3000",), 5),
            factory or RecordingFactory(),
            store or InMemoryRunRepository(),
        )

    def create_pending(self, app, source="sales.csv"):
        response = asyncio.run(
            asgi_request(
                app,
                "POST",
                "/api/v1/responses",
                json_body={
                    "input": "What is total revenue?",
                    "session_id": "session-1",
                },
            )
        )
        events = parse_sse(response)
        confirmation = events[-1][1]
        return response, events, confirmation

    def test_initial_request_pauses_before_engine_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            factory = RecordingFactory()
            app = self.make_app(temp_dir, factory=factory)
            response, events, confirmation = self.create_pending(app)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(events[-1][0], "response.requires_confirmation")
        self.assertEqual(confirmation["revision"], 1)
        self.assertIn("What is total revenue?", confirmation["spec_markdown"])
        self.assertNotIn("capability_requirements", confirmation)
        self.assertEqual(factory.execute_calls, 0)

    def test_revise_then_confirm_resumes_same_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            factory = RecordingFactory()
            app = self.make_app(temp_dir, factory=factory)
            _, _, pending = self.create_pending(app)
            response_id = pending["response_id"]
            token = pending["confirmation_token"]
            revised = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    f"/api/v1/responses/{response_id}/decision",
                    headers={"X-Confirmation-Token": token},
                    json_body={
                        "action": "revise",
                        "revision": 1,
                        "spec_markdown": pending["spec_markdown"].replace(
                            "What is total revenue?", "Use monthly totals"
                        ),
                    },
                )
            )
            revision = parse_sse(revised)[-1][1]
            confirmed = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    f"/api/v1/responses/{response_id}/decision",
                    headers={"X-Confirmation-Token": token},
                    json_body={"action": "confirm", "revision": 2},
                )
            )
            events = parse_sse(confirmed)

        self.assertEqual(revision["revision"], 2)
        self.assertIn("Use monthly totals", revision["spec_markdown"])
        self.assertEqual(events[-1][0], "response.completed")
        self.assertEqual(events[-1][1]["response_id"], response_id)
        self.assertEqual(factory.revise_calls, 0)
        self.assertEqual(factory.execute_calls, 1)

    def test_complete_markdown_can_be_revised(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            app = self.make_app(temp_dir)
            _, _, pending = self.create_pending(app)
            response = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    f"/api/v1/responses/{pending['response_id']}/decision",
                    headers={"X-Confirmation-Token": pending["confirmation_token"]},
                    json_body={
                        "action": "revise",
                        "revision": 1,
                        "spec_markdown": pending["spec_markdown"].replace(
                            "What is total revenue?", "Compare monthly revenue"
                        ),
                    },
                )
            )
        spec_markdown = parse_sse(response)[-1][1]["spec_markdown"]
        self.assertIn("Compare monthly revenue", spec_markdown)

    def test_recovery_requires_token_and_returns_pending_spec(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            app = self.make_app(temp_dir)
            _, _, pending = self.create_pending(app)
            wrong = asyncio.run(asgi_request(app, "GET", f"/api/v1/responses/{pending['response_id']}", headers={"X-Confirmation-Token": "wrong"}))
            recovered = asyncio.run(asgi_request(app, "GET", f"/api/v1/responses/{pending['response_id']}", headers={"X-Confirmation-Token": pending["confirmation_token"]}))
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["status"], "awaiting_confirmation")

    def test_stale_revision_returns_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            app = self.make_app(temp_dir)
            _, _, pending = self.create_pending(app)
            response = asyncio.run(asgi_request(app, "POST", f"/api/v1/responses/{pending['response_id']}/decision", headers={"X-Confirmation-Token": pending["confirmation_token"]}, json_body={"action": "confirm", "revision": 2}))
        self.assertEqual(response.status_code, 409)

    def test_expired_confirmation_returns_gone(self):
        clock_now = [datetime.now(timezone.utc)]
        store = InMemoryRunRepository(clock=lambda: clock_now[0])
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sales.csv").write_text("revenue\n42\n")
            app = self.make_app(temp_dir, store=store)
            _, _, pending = self.create_pending(app)
            clock_now[0] = (
                datetime.fromisoformat(pending["expires_at"]) + timedelta(seconds=1)
            )
            response = asyncio.run(asgi_request(app, "GET", f"/api/v1/responses/{pending['response_id']}", headers={"X-Confirmation-Token": pending["confirmation_token"]}))
        self.assertEqual(response.status_code, 410)

    def test_upload_keeps_files_under_corpus_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self.make_app(temp_dir)
            body, content_type = multipart_body(
                {"conv_uid": "session-1"},
                [("files", "../../sales report.csv", b"42", "text/csv")],
            )
            response = asyncio.run(asgi_request(app, "POST", "/api/v1/backend_qa_flow/upload", content=body, headers={"content-type": content_type}))
            stored = Path(temp_dir, response.json()["data"]["files"][0]["relative_path"]).resolve()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stored.parent.name, "session-1")
        self.assertTrue(stored.name.endswith("-sales_report.csv"))

    def test_health_and_cors_include_confirmation_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self.make_app(temp_dir)
            health = asyncio.run(asgi_request(app, "GET", "/health"))
            cors = asyncio.run(asgi_request(app, "OPTIONS", "/api/v1/responses/x/decision", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Confirmation-Token"}))
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(cors.status_code, 200)
        self.assertIn("X-Confirmation-Token", cors.headers["access-control-allow-headers"])


if __name__ == "__main__":
    unittest.main()
