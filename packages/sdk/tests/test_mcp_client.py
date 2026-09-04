from contextlib import asynccontextmanager

from data_intelligence_sdk.runtime.mcp_client import (
    MCPMethodClient,
    _authorization_fingerprint,
)


def test_authorization_fingerprint_redacts_the_bearer_value() -> None:
    assert _authorization_fingerprint("Bearer user-token") == "b8f2c25d6e8d"
    assert _authorization_fingerprint(None) == "missing"


def test_mcp_session_forwards_user_authorization_header(monkeypatch) -> None:
    observed: dict[str, object] = {}

    @asynccontextmanager
    async def fake_transport(endpoint, *, http_client=None, terminate_on_close=True):
        observed["endpoint"] = endpoint
        observed["headers"] = dict(http_client.headers)
        observed["terminate_on_close"] = terminate_on_close
        yield (object(), object(), lambda: None)

    class FakeClientSession:
        def __init__(self, read_stream, write_stream):
            del read_stream, write_stream

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        async def list_tools(self):
            return {"tools": []}

    import mcp
    import mcp.client.streamable_http

    monkeypatch.setattr(
        mcp.client.streamable_http,
        "streamable_http_client",
        fake_transport,
    )
    monkeypatch.setattr(mcp, "ClientSession", FakeClientSession)

    client = MCPMethodClient(
        "http://methods-hub/mcp",
        organization_id="org-1",
        user_authorization="Bearer user-token",
    )

    client.list_tools()

    assert observed["endpoint"] == "http://methods-hub/mcp"
    assert observed["headers"]["authorization"] == "Bearer user-token"
    assert observed["headers"]["x-org-id"] == "org-1"
    assert observed["terminate_on_close"] is True
