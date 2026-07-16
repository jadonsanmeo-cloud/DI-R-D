"""Client boundary for Methods-Hub MCP tools.

The SDK deliberately keeps MCP transport details behind this adapter. Engines
receive tool definitions and invoke named remote tools; they never receive the
server's Python callables.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable


class MCPClientError(RuntimeError):
    """Base error raised by the Methods-Hub client."""


class MCPToolError(MCPClientError):
    """Raised when Methods-Hub reports a failed tool call."""


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    """Transport-neutral description of one remote MCP tool."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    capability_names: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


SessionFactory = Callable[[], AsyncContextManager[Any]]


class MCPMethodClient:
    """Synchronous facade over the asynchronous MCP streamable HTTP client."""

    def __init__(
        self,
        endpoint: str,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        endpoint = endpoint.strip()
        if not endpoint:
            raise ValueError("MCP endpoint must not be empty.")
        self.endpoint = endpoint
        self._session_factory = session_factory or _default_factory_for_endpoint(endpoint)

    def connect(self) -> None:
        """Verify that the MCP server can initialize and list its tools."""

        self.list_tools()

    def close(self) -> None:
        """Close hook kept for lifecycle symmetry with long-lived clients."""

    def check_ready(self) -> bool:
        """Return whether the Methods-Hub session is reachable and initialized."""

        try:
            self.connect()
        except Exception:
            return False
        return True

    def list_tools(self) -> list[MCPToolDefinition]:
        """Discover and normalize the server's current tool definitions."""

        return _run_sync(self._list_tools_async())

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke one remote MCP tool and return structured result data."""

        if not name.strip():
            raise ValueError("MCP tool name must not be empty.")
        return _run_sync(self._call_tool_async(name, arguments))

    async def _list_tools_async(self) -> list[MCPToolDefinition]:
        async with self._session_factory() as session:
            await _initialize_session(session)
            response = await session.list_tools()
            return [_normalize_tool(tool) for tool in _get_value(response, "tools", [])]

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._session_factory() as session:
            await _initialize_session(session)
            response = await session.call_tool(name, arguments)
            if bool(_get_value(response, "isError", False)):
                raise MCPToolError(_extract_text(response) or f"MCP tool failed: {name}")
            return _normalize_result(response)

def _default_factory_for_endpoint(endpoint: str) -> SessionFactory:
    @asynccontextmanager
    async def session_context() -> Any:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:  # pragma: no cover - runtime dependency guard.
            raise MCPClientError(
                "The 'mcp' package is required to connect to Methods-Hub."
            ) from exc
        async with streamable_http_client(endpoint) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                yield session

    return session_context


def _get_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


async def _initialize_session(session: Any) -> None:
    initialize = getattr(session, "initialize", None)
    if initialize is not None:
        await initialize()


def _normalize_tool(tool: Any) -> MCPToolDefinition:
    name = str(_get_value(tool, "name", "")).strip()
    if not name:
        raise MCPClientError("Methods-Hub returned a tool without a name.")
    input_schema = _get_value(tool, "inputSchema", None)
    if input_schema is None:
        input_schema = _get_value(tool, "input_schema", {})
    if not isinstance(input_schema, dict):
        input_schema = {}
    metadata = _get_value(tool, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    capabilities = metadata.get("capability_names", metadata.get("capabilities", []))
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    return MCPToolDefinition(
        name=name,
        description=str(_get_value(tool, "description", "") or ""),
        input_schema=input_schema,
        capability_names=tuple(str(item).strip() for item in capabilities if str(item).strip()),
        metadata=metadata,
    )


def _normalize_result(response: Any) -> Any:
    structured = _get_value(response, "structuredContent", None)
    if structured is None:
        structured = _get_value(response, "structured_content", None)
    if structured is not None:
        return structured
    content = _get_value(response, "content", [])
    texts = []
    for item in content or []:
        text = _get_value(item, "text", None)
        if text is not None:
            texts.append(str(text))
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return texts or content


def _extract_text(response: Any) -> str:
    normalized = _normalize_result(response)
    if isinstance(normalized, str):
        return normalized
    return json.dumps(normalized, default=str)


def _run_sync(awaitable: Any) -> Any:
    """Run an async MCP operation from the SDK's existing sync Engine API."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # pragma: no cover - exercised by async hosts.
            error.append(exc)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None
