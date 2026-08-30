"""Request-scoped LangChain tools backed by the Method Hub MCP server."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition

_CORPUS_SCOPE_TOOL_NAMES = frozenset(
    {
        "corpus_vector_search",
        "corpus_bm25_search",
        "corpus_retrieve_context",
    }
)


def _scoped_tool_arguments(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scope = runtime.selected_files
    if (
        definition.name not in _CORPUS_SCOPE_TOOL_NAMES
        or not isinstance(scope, dict)
        or scope.get("mode") != "selected"
    ):
        return arguments

    resource_ids = scope.get("resource_ids")
    if not isinstance(resource_ids, list) or not resource_ids:
        return arguments

    selected_ids = [
        str(resource_id).strip()
        for resource_id in resource_ids
        if str(resource_id).strip()
    ]
    if not selected_ids:
        return arguments

    scoped_arguments = dict(arguments)
    scoped_arguments.pop("document_id", None)
    scoped_arguments["document_ids"] = selected_ids
    return scoped_arguments


def _call_remote_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> Any:
    if runtime.mcp_client is None:
        raise RuntimeError("Method Hub is enabled but its MCP client is unavailable.")
    scoped_arguments = _scoped_tool_arguments(runtime, definition, arguments)
    try:
        result = runtime.mcp_client.call_tool(definition.name, scoped_arguments)
    except Exception as exc:
        runtime.run_context.record_method_call(
            definition.name,
            status="failed",
            inputs=scoped_arguments,
            outputs={"error": str(exc), "provider": "mcp"},
        )
        raise
    runtime.run_context.record_method_call(
        definition.name,
        status="completed",
        inputs=scoped_arguments,
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
