"""Request-scoped LangChain tools backed by the Method Hub MCP server."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition


def _call_remote_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> Any:
    if runtime.mcp_client is None:
        raise RuntimeError("Method Hub is enabled but its MCP client is unavailable.")
    try:
        result = runtime.mcp_client.call_tool(definition.name, arguments)
    except Exception as exc:
        runtime.run_context.record_method_call(
            definition.name,
            status="failed",
            inputs=arguments,
            outputs={"error": str(exc), "provider": "mcp"},
        )
        raise
    runtime.run_context.record_method_call(
        definition.name,
        status="completed",
        inputs=arguments,
        outputs={"result": result, "provider": "mcp"},
    )
    return result


def _create_mcp_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
) -> BaseTool:
    def invoke(**arguments: Any) -> Any:
        return _call_remote_tool(runtime, definition, arguments)

    return StructuredTool.from_function(
        func=invoke,
        name=definition.name,
        description=definition.description or f"Method Hub tool {definition.name}",
        args_schema=definition.input_schema,
        infer_schema=False,
    )


def create_mcp_tools(runtime: EngineRuntimeContext) -> list[BaseTool]:
    return [_create_mcp_tool(runtime, definition) for definition in runtime.mcp_tools]
