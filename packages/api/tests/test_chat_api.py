import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from data_intelligence_api.app.factory import create_app
from data_intelligence_api.application.chat_service import ChatService
from data_intelligence_api.domain.chat import ChatConversation
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.persistence.file_chat_store import (
    FileChatStore,
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


async def asgi_request(app, method, path, *, json_body=None, headers=None):
    body = json.dumps(json_body).encode() if json_body is not None else b""
    request_headers = {key.lower(): value for key, value in (headers or {}).items()}
    if json_body is not None:
        request_headers.setdefault("content-type", "application/json")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path.split("?")[0],
        "raw_path": path.split("?")[0].encode("ascii"),
        "query_string": path.split("?", 1)[1].encode("ascii") if "?" in path else b"",
        "root_path": "",
        "headers": [
            (key.encode(), value.encode()) for key, value in request_headers.items()
        ],
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


class FakeChatWorkflow:
    async def stream_response(
        self,
        *,
        conversation: ChatConversation,
        user_input: str,
        model_name: str,
    ) -> AsyncIterator[str]:
        del conversation, user_input, model_name
        yield "Hel"
        yield "lo"


class ChatApiTests(unittest.TestCase):
    def make_app(self, root: Path):
        chat_service = ChatService(
            store=FileChatStore(root / "chat"),
            workflow=FakeChatWorkflow(),
        )
        return create_app(
            ApiSettings(
                data_corpus_root=root,
                cors_origins=("http://localhost:3000",),
                pipeline_timeout_seconds=5,
                chat_store_dir=root / "chat",
            ),
            chat_service=chat_service,
        )

    def test_dialogue_crud_uses_frontend_envelope_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = self.make_app(root)

            created = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    "/api/v1/chat/dialogue/new?chat_mode=chat_normal&model_name=cx/gpt-5.5",
                    headers={"user-id": "user-1"},
                )
            )
            listed = asyncio.run(
                asgi_request(app, "GET", "/api/v1/chat/dialogue/list")
            )

        self.assertEqual(created.status_code, 200)
        created_payload = created.json()
        self.assertTrue(created_payload["success"])
        self.assertIn("conv_uid", created_payload["data"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            listed.json()["data"][0]["conv_uid"],
            created_payload["data"]["conv_uid"],
        )

    def test_chat_completion_streams_sse_and_persists_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = self.make_app(root)
            created = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    "/api/v1/chat/dialogue/new?chat_mode=chat_normal&model_name=cx/gpt-5.5",
                )
            )
            conv_uid = created.json()["data"]["conv_uid"]

            streamed = asyncio.run(
                asgi_request(
                    app,
                    "POST",
                    "/api/v1/chat/completions",
                    json_body={
                        "conv_uid": conv_uid,
                        "chat_mode": "chat_normal",
                        "model_name": "cx/gpt-5.5",
                        "user_input": "Hi",
                    },
                )
            )
            history = asyncio.run(
                asgi_request(
                    app,
                    "GET",
                    f"/api/v1/chat/dialogue/messages/history?con_uid={conv_uid}",
                )
            )

        self.assertEqual(streamed.status_code, 200)
        stream_text = streamed.content.decode("utf-8")
        self.assertIn('"content": "Hel"', stream_text)
        self.assertIn('"content": "Hello"', stream_text)
        self.assertIn("data: [DONE]", stream_text)
        history_messages = history.json()["data"]
        self.assertEqual([item["role"] for item in history_messages], ["human", "view"])
        self.assertEqual(history_messages[0]["context"], "Hi")
        self.assertEqual(history_messages[1]["context"], "Hello")


if __name__ == "__main__":
    unittest.main()
