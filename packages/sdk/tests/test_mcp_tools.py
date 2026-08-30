from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition
from data_intelligence_sdk.tools.mcp import _call_remote_tool


class RecordingMcpClient:
    def __init__(self) -> None:
        self.arguments = None

    def call_tool(self, name, arguments):
        self.arguments = (name, arguments)
        return {"results": []}


def test_selected_files_override_corpus_search_document_filter() -> None:
    client = RecordingMcpClient()
    runtime = EngineRuntimeContext(
        mcp_client=client,
        selected_files={
            "mode": "selected",
            "resource_ids": ["document-1", "document-2"],
        },
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
