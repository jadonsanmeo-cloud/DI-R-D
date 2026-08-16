"""Resolve request-scoped Method Hub availability without exposing credentials."""

from __future__ import annotations

from dataclasses import dataclass

from data_intelligence_sdk.runtime.mcp_client import (
    MCPMethodClient,
    MCPToolDefinition,
)

from data_intelligence_api.domain.workflow import WorkflowRuntimeOptions
from data_intelligence_api.http.schemas.runtime_inputs import RuntimeOptionsRequest


class MethodHubUnavailableError(RuntimeError):
    """Raised when a request explicitly requires an unavailable Method Hub."""


@dataclass(frozen=True, slots=True)
class ResolvedMethodHub:
    client: MCPMethodClient | None
    tools: tuple[MCPToolDefinition, ...]


def resolve_runtime_options(
    requested: RuntimeOptionsRequest,
    *,
    default_enabled: bool,
) -> WorkflowRuntimeOptions:
    enabled = (
        default_enabled
        if requested.method_hub_enabled is None
        else requested.method_hub_enabled
    )
    engine = None if requested.engine in (None, "auto") else requested.engine
    return WorkflowRuntimeOptions(method_hub_enabled=enabled, engine=engine)


def resolve_method_hub(
    options: WorkflowRuntimeOptions,
    *,
    endpoint: str,
) -> ResolvedMethodHub:
    if not options.method_hub_enabled:
        return ResolvedMethodHub(client=None, tools=())
    client = MCPMethodClient(endpoint)
    try:
        tools = tuple(client.list_tools())
        names: set[str] = set()
        for tool in tools:
            if tool.name == "execute_python" or tool.name in names:
                raise ValueError(f"Invalid or duplicate Method Hub tool: {tool.name}")
            names.add(tool.name)
    except Exception as exc:
        raise MethodHubUnavailableError(
            "Method Hub is enabled for this request but is unavailable."
        ) from exc
    return ResolvedMethodHub(client=client, tools=tools)


def method_hub_available(endpoint: str) -> bool:
    try:
        client = MCPMethodClient(endpoint)
        client.connect()
        tuple(client.list_tools())
    except Exception:
        return False
    return True
