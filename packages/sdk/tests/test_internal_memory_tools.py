from data_intelligence_sdk.internal_memory.tools import create_internal_memory_tools
from data_intelligence_sdk.internal_memory.client import (
    InternalMemoryClient,
    create_internal_memory_client,
)
from data_intelligence_sdk.core.types import UserQuery
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


def test_internal_memory_tools_delegate_session_search_to_request_client() -> None:
    class Client:
        def session_search(self, query: str, *, limit: int):
            assert (query, limit) == ("revenue", 3)
            return [{"match_message_id": "msg-2"}]

    tools = create_internal_memory_tools(
        EngineRuntimeContext(internal_memory_client=Client())
    )

    result = next(tool for tool in tools if tool.name == "session_search").invoke(
        {"query": "revenue", "limit": 3}
    )

    assert result == [{"match_message_id": "msg-2"}]


def test_internal_memory_tools_delegate_scroll_and_write() -> None:
    class Client:
        def session_scroll(self, conversation_id, around_message_id, *, direction, limit):
            return [{"message_id": "msg-3", "direction": direction}]

        def write(self, *, target, operation, content, match):
            return {"memory_markdown": content}

    tools = create_internal_memory_tools(
        EngineRuntimeContext(internal_memory_client=Client())
    )

    scroll = next(tool for tool in tools if tool.name == "session_scroll").invoke(
        {"conversation_id": "conversation-a", "around_message_id": "msg-2", "direction": "forward", "limit": 5}
    )
    write = next(tool for tool in tools if tool.name == "memory").invoke(
        {"target": "memory", "operation": "add", "content": "Uses rtk"}
    )

    assert scroll == [{"message_id": "msg-3", "direction": "forward"}]
    assert write == {"memory_markdown": "Uses rtk"}


def test_internal_memory_tools_expose_constrained_arguments_to_the_agent() -> None:
    tools = create_internal_memory_tools(
        EngineRuntimeContext(internal_memory_client=object())
    )

    schema_by_name = {tool.name: tool.args_schema.model_json_schema() for tool in tools}

    assert schema_by_name["session_scroll"]["properties"]["direction"]["enum"] == [
        "forward",
        "backward",
    ]
    assert schema_by_name["memory"]["properties"]["target"]["enum"] == [
        "user",
        "memory",
    ]
    assert schema_by_name["memory"]["properties"]["operation"]["enum"] == [
        "add",
        "replace",
        "remove",
    ]


def test_http_client_sends_gateway_identity_headers() -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    class Http:
        def get(self, url, *, params, headers, timeout):
            assert url == "http://intelligence/api/v1/internal-memory/session-search"
            assert params == {"query": "revenue", "limit": 5}
            assert headers["X-User-ID"] == "user-a"
            assert headers["X-Org-ID"] == "tenant-a"
            return Response()

    client = InternalMemoryClient(
        "http://intelligence/api/v1", "user-a", "tenant-a", http_client=Http()
    )

    assert client.session_search("revenue", limit=5) == []


def test_request_scoped_client_uses_query_identity() -> None:
    client = create_internal_memory_client(
        "http://intelligence/api/v1",
        UserQuery(
            text="Revenue",
            user_id="user-a",
            metadata={"organization_id": "tenant-a"},
        ),
    )

    assert client is not None


def test_request_scoped_client_is_disabled_without_identity_or_endpoint() -> None:
    query = UserQuery(text="Revenue")

    assert create_internal_memory_client(None, query) is None
    assert create_internal_memory_client("http://intelligence/api/v1", query) is None
