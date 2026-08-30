import pytest

from data_intelligence_sdk.runtime.selected_files import SelectedFilesScope
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition
from data_intelligence_sdk.tools.mcp import (
    SelectedFilesScopeError,
    _call_remote_tool,
    create_mcp_tools,
)


class RecordingMcpClient:
    def __init__(self) -> None:
        self.arguments = None

    def call_tool(self, name, arguments):
        self.arguments = (name, arguments)
        return {"results": []}


def test_selected_files_scope_contains_authorized_document_ids() -> None:
    scope = SelectedFilesScope(document_ids=("document-1",))
    runtime = EngineRuntimeContext(selected_files_scope=scope)

    assert runtime.selected_files_scope is scope
    assert scope.document_ids == ("document-1",)


def test_workspace_id_is_injected_for_selected_file_lookup() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        workspace_id="workspace-1",
        selected_files_scope=SelectedFilesScope(document_ids=("document-1",)),
    )

    _call_remote_tool(
        runtime,
        MCPToolDefinition(name="corpus_get_file_ingested_data"),
        {"document_id": "document-1"},
    )

    assert client.arguments == (
        "corpus_get_file_ingested_data",
        {"document_id": "document-1", "workspace_id": "workspace-1"},
    )


def test_workspace_scope_is_hidden_from_model_and_injected_for_bm25_search() -> None:
    client = RecordingMcpClient()
    definition = MCPToolDefinition(
        name="corpus_bm25_search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace_id": {"type": "string"},
            },
            "required": ["query", "workspace_id"],
        },
    )
    runtime = EngineRuntimeContext(
        mcp_client=client,
        mcp_tools=(definition,),
        workspace_id="workspace-1",
    )

    tool = create_mcp_tools(runtime)[0]
    tool.invoke({"query": "revenue"})

    assert tool.args_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    assert client.arguments == (
        "corpus_bm25_search",
        {"query": "revenue", "workspace_id": "workspace-1"},
    )


def test_selected_files_override_corpus_search_document_filter() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(
            document_ids=("document-1", "document-2")
        ),
    )

    _call_remote_tool(
        runtime,
        MCPToolDefinition(name="corpus_vector_search"),
        {
            "query": "climate impact",
            "workspace_id": "workspace-1",
            "document_ids": ["document-outside-scope"],
        },
    )

    assert client.arguments == (
        "corpus_vector_search",
        {
            "query": "climate impact",
            "workspace_id": "workspace-1",
            "document_ids": ["document-1", "document-2"],
        },
    )


def test_selected_files_scope_forces_every_corpus_search_filter() -> None:
    for tool_name in (
        "corpus_vector_search",
        "corpus_bm25_search",
        "corpus_retrieve_context",
    ):
        client = RecordingMcpClient()
        runtime = EngineRuntimeContext(
            mcp_client=client,
            selected_files_scope=SelectedFilesScope(
                document_ids=("document-1", "document-2")
            ),
        )

        _call_remote_tool(
            runtime,
            MCPToolDefinition(name=tool_name),
            {
                "query": "climate impact",
                "workspace_id": "workspace-1",
                "document_id": "document-outside-scope",
                "document_ids": ["document-outside-scope"],
            },
        )

        assert client.arguments == (
            tool_name,
            {
                "query": "climate impact",
                "workspace_id": "workspace-1",
                "document_ids": ["document-1", "document-2"],
            },
        )


def test_selected_files_scope_infers_single_document_lookup() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(document_ids=("document-1",)),
    )

    _call_remote_tool(
        runtime,
        MCPToolDefinition(name="corpus_get_file_ingested_data"),
        {
            "workspace_id": "workspace-1",
            "file_name": "outside-scope.pdf",
            "bucket": "outside-bucket",
            "object_key": "outside-key",
        },
    )

    assert client.arguments == (
        "corpus_get_file_ingested_data",
        {
            "workspace_id": "workspace-1",
            "document_id": "document-1",
        },
    )


def test_selected_files_scope_rejects_ambiguous_document_lookup() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(
            document_ids=("document-1", "document-2")
        ),
    )

    with pytest.raises(SelectedFilesScopeError, match="document_id"):
        _call_remote_tool(
            runtime,
            MCPToolDefinition(name="corpus_get_file_ingested_data"),
            {"workspace_id": "workspace-1"},
        )

    assert client.arguments is None


def test_selected_files_scope_rejects_out_of_scope_document_lookup() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(document_ids=("document-1",)),
    )

    with pytest.raises(SelectedFilesScopeError, match="outside"):
        _call_remote_tool(
            runtime,
            MCPToolDefinition(name="corpus_get_file_ingested_data"),
            {
                "workspace_id": "workspace-1",
                "document_id": "document-outside-scope",
            },
        )

    assert client.arguments is None
    assert runtime.run_context.trace.method_calls[-1].status == "failed"


def test_selected_files_scope_validates_neighbor_document() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(
            document_ids=("document-1", "document-2")
        ),
    )

    _call_remote_tool(
        runtime,
        MCPToolDefinition(name="get_neighbor_chunk"),
        {
            "workspace_id": "workspace-1",
            "file_id": "document-1",
            "inc": 1,
            "des": 1,
            "chunk_id": 2,
        },
    )

    assert client.arguments == (
        "get_neighbor_chunk",
        {
            "workspace_id": "workspace-1",
            "file_id": "document-1",
            "inc": 1,
            "des": 1,
            "chunk_id": 2,
        },
    )


def test_selected_files_scope_rejects_neighbor_without_document() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files_scope=SelectedFilesScope(
            document_ids=("document-1", "document-2")
        ),
    )

    with pytest.raises(SelectedFilesScopeError, match="file_id"):
        _call_remote_tool(
            runtime,
            MCPToolDefinition(name="get_neighbor_chunk"),
            {
                "workspace_id": "workspace-1",
                "inc": 1,
                "des": 1,
                "chunk_id": 2,
            },
        )

    assert client.arguments is None
