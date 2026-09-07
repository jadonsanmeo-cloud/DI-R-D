from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient


def test_agent_binding_filters_org_snapshot_but_raw_catalog_remains_full(monkeypatch):
    session = AsyncMock()
    session.list_tools.return_value = {
        "tools": [{"name": "allowed"}, {"name": "other"}]
    }

    @asynccontextmanager
    async def factory():
        yield session

    loader = AsyncMock(return_value={"allowed", "unknown"})
    monkeypatch.setattr(
        "data_intelligence_sdk.runtime.mcp_client.registered_tool_names", loader
    )
    client = MCPMethodClient(
        "http://hub/mcp",
        organization_id="org",
        user_authorization="Bearer token",
        session_factory=factory,
    )
    assert [tool.name for tool in client.list_tools()] == ["allowed", "other"]
    assert [tool.name for tool in client.list_agent_tools()] == ["allowed"]
    loader.assert_awaited_with("org", "Bearer token")
    loader.return_value = set()
    assert client.list_agent_tools() == []
    loader.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        client.list_agent_tools()


def test_runtime_construction_uses_filtered_bindings(monkeypatch):
    from types import SimpleNamespace
    from data_intelligence_api.application.runtime_capabilities import (
        resolve_method_hub,
    )
    from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition

    class Client:
        def __init__(self, endpoint, **kwargs):
            assert kwargs["organization_id"] == "org"
            assert kwargs["user_authorization"] == "Bearer token"

        def list_agent_tools(self):
            return [MCPToolDefinition(name="registered")]

        def list_tools(self):
            raise AssertionError("Runtime must not bind the raw catalog")

    monkeypatch.setattr(
        "data_intelligence_api.application.runtime_capabilities.MCPMethodClient", Client
    )
    result = resolve_method_hub(
        SimpleNamespace(method_hub_enabled=True),
        endpoint="http://hub/mcp",
        organization_id="org",
        user_authorization="Bearer token",
    )
    assert [tool.name for tool in result.tools] == ["registered"]
