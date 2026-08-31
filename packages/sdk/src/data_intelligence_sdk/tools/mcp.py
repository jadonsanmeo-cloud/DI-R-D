"""Request-scoped LangChain tools backed by the Method Hub MCP server."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition
from data_intelligence_sdk.runtime.selected_files import (
    SelectedFilesScopeError,
)

_CORPUS_SCOPE_TOOL_NAMES = frozenset(
    {
        "corpus_vector_search",
        "corpus_bm25_search",
        "corpus_retrieve_context",
    }
)
_SINGLE_DOCUMENT_SCOPE_TOOL_NAMES = frozenset(
    {
        "corpus_get_file_ingested_data",
        "get_neighbor_chunk",
    }
)
_SUPPORTED_SELECTED_SCOPE_TOOL_NAMES = (
    _CORPUS_SCOPE_TOOL_NAMES | _SINGLE_DOCUMENT_SCOPE_TOOL_NAMES
)
_FILE_SELECTOR_KEYS = frozenset({"file_name", "bucket", "object_key", "match_mode"})
_RUNTIME_SCOPE_FIELDS = frozenset({"workspace_id"})


def _scoped_tool_arguments(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scoped_arguments = dict(arguments)
    if runtime.workspace_id:
        scoped_arguments["workspace_id"] = runtime.workspace_id

    if runtime.selected_files_scope is None:
        return scoped_arguments

    selected_ids = _selected_document_ids(runtime)
    if definition.name in _CORPUS_SCOPE_TOOL_NAMES:
        scoped_arguments.pop("document_id", None)
        scoped_arguments["document_ids"] = selected_ids
        return scoped_arguments

    if definition.name == "corpus_get_file_ingested_data":
        document_id = _normalized_selector(scoped_arguments.get("document_id"))
        if document_id is not None and document_id not in selected_ids:
            raise SelectedFilesScopeError(
                f"document_id {document_id!r} is outside the selected file scope."
            )
        if document_id is None:
            if len(selected_ids) != 1:
                raise SelectedFilesScopeError(
                    "corpus_get_file_ingested_data requires an in-scope "
                    "document_id when multiple files are selected."
                )
            document_id = selected_ids[0]
        scoped_arguments = {
            key: value
            for key, value in scoped_arguments.items()
            if key not in _FILE_SELECTOR_KEYS and key != "document_id"
        }
        scoped_arguments["document_id"] = document_id
        return scoped_arguments

    if definition.name == "get_neighbor_chunk":
        file_id = _normalized_selector(scoped_arguments.get("file_id"))
        if file_id is None:
            if len(selected_ids) != 1:
                raise SelectedFilesScopeError(
                    "get_neighbor_chunk requires an in-scope file_id when "
                    "multiple files are selected."
                )
            file_id = selected_ids[0]
        elif file_id not in selected_ids:
            raise SelectedFilesScopeError(
                f"file_id {file_id!r} is outside the selected file scope."
            )
        scoped_arguments["file_id"] = file_id
        return scoped_arguments

    if definition.name.startswith("corpus_"):
        raise SelectedFilesScopeError(
            f"Method Hub tool {definition.name!r} is not available for a "
            "selected file scope."
        )

    return scoped_arguments


def _selected_document_ids(runtime: EngineRuntimeContext) -> list[str]:
    scope = runtime.selected_files_scope
    if scope is None:  # pragma: no cover - guarded by the caller.
        return []
    selected_ids = [
        document_id for item in scope.document_ids if (document_id := str(item).strip())
    ]
    if not selected_ids:
        raise SelectedFilesScopeError(
            "The selected file scope contains no document IDs."
        )
    return selected_ids


def _normalized_selector(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _scope_adjusted_definition(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
) -> MCPToolDefinition:
    if (
        runtime.selected_files_scope is None
        or definition.name != "get_neighbor_chunk"
        or not runtime.selected_files_scope.document_ids
        or len(_selected_document_ids(runtime)) != 1
    ):
        return definition
    if not definition.input_schema:
        return definition
    input_schema = dict(definition.input_schema)
    required = input_schema.get("required")
    if not isinstance(required, list) or "file_id" not in required:
        return definition
    input_schema["required"] = [item for item in required if item != "file_id"]
    return MCPToolDefinition(
        name=definition.name,
        description=definition.description,
        input_schema=input_schema,
        capability_names=definition.capability_names,
        metadata=definition.metadata,
    )


def _agent_input_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Remove scope that the runtime supplies from the model-visible schema."""

    schema = deepcopy(input_schema)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field in _RUNTIME_SCOPE_FIELDS:
            properties.pop(field, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [
            field for field in required if field not in _RUNTIME_SCOPE_FIELDS
        ]
    return schema


def _call_remote_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> Any:
    if runtime.mcp_client is None:
        raise RuntimeError("Method Hub is enabled but its MCP client is unavailable.")
    try:
        scoped_arguments = _scoped_tool_arguments(runtime, definition, arguments)
    except SelectedFilesScopeError as exc:
        runtime.run_context.record_method_call(
            definition.name,
            status="failed",
            inputs=dict(arguments),
            outputs={"error": str(exc), "provider": "mcp"},
        )
        raise
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
    definition = _scope_adjusted_definition(runtime, definition)

    def invoke(**arguments: Any) -> Any:
        return _call_remote_tool(runtime, definition, arguments)

    return StructuredTool.from_function(
        func=invoke,
        name=definition.name,
        description=definition.description or f"Method Hub tool {definition.name}",
        args_schema=_agent_input_schema(definition.input_schema),
        infer_schema=False,
    )


def create_mcp_tools(runtime: EngineRuntimeContext) -> list[BaseTool]:
    return [
        _create_mcp_tool(runtime, definition)
        for definition in runtime.mcp_tools
        if not _is_unscopable_tool(runtime, definition)
    ]


def _is_unscopable_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
) -> bool:
    scope = runtime.selected_files_scope
    if scope is None:
        return False
    if not scope.document_ids:
        return definition.name.startswith("corpus_") or definition.name == (
            "get_neighbor_chunk"
        )
    return (
        definition.name.startswith("corpus_")
        and definition.name not in _SUPPORTED_SELECTED_SCOPE_TOOL_NAMES
    )
