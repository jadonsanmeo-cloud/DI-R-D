from data_intelligence_sdk.core.types import ExecutionSpec, UserQuery
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition
from data_intelligence_sdk.runtime.selected_files import SelectedFilesScope


def test_general_engine_prompt_keeps_method_hub_outside_sandbox() -> None:
    engine = GeneralPurposeEngine(llm=object())
    prompt = engine._system_prompt(
        ExecutionSpec(intent="general", objective="Find the latest revenue."),
        EngineRuntimeContext(
            mcp_client=object(),
            mcp_tools=(MCPToolDefinition(name="document_retrieve_context"),),
        ),
        UserQuery(text="Find the latest revenue."),
    )

    assert "call the matching method hub tool directly" in prompt.lower()
    assert "axiom_method_hub" not in prompt


def test_general_engine_prompt_explains_current_workspace_scope() -> None:
    engine = GeneralPurposeEngine(llm=object())
    prompt = engine._system_prompt(
        ExecutionSpec(intent="general", objective="Find the latest revenue."),
        EngineRuntimeContext(
            mcp_client=object(),
            mcp_tools=(MCPToolDefinition(name="corpus_bm25_search"),),
            workspace_id="workspace-1",
        ),
        UserQuery(text="Find the latest revenue."),
    )

    assert "workspace-1" in prompt
    assert "do not ask the user for a workspace_id" in prompt.lower()


def test_general_engine_builds_llm_messages_from_conversation_history() -> None:
    engine = GeneralPurposeEngine(llm=object())
    messages = engine._conversation_messages(
        UserQuery(
            text="Who am I?",
            metadata={
                "history": [
                    {"role": "user", "content": "My name is Anh."},
                    {"role": "assistant", "content": "Nice to meet you, Anh."},
                ]
            },
        )
    )

    assert messages == [
        {"role": "user", "content": "My name is Anh."},
        {"role": "assistant", "content": "Nice to meet you, Anh."},
        {"role": "user", "content": "Who am I?"},
    ]


def test_general_engine_prompt_explains_selected_workspace_files() -> None:
    engine = GeneralPurposeEngine(llm=object())
    prompt = engine._system_prompt(
        ExecutionSpec(intent="general", objective="What is this file about?"),
        EngineRuntimeContext(
            mcp_client=object(),
            mcp_tools=(MCPToolDefinition(name="corpus_retrieve_context"),),
            selected_files_scope=SelectedFilesScope(document_ids=("document-1",)),
        ),
        UserQuery(text="What is this file about?"),
    )

    assert "document-1" in prompt
    assert "use the retrieval tools" in prompt.lower()
    assert "do not search the local filesystem" in prompt.lower()
    assert "do not ask the user for a local path" in prompt.lower()


def test_general_engine_prompt_does_not_expose_a_staged_selected_file_path() -> None:
    engine = GeneralPurposeEngine(llm=object())
    prompt = engine._system_prompt(
        ExecutionSpec(intent="general", objective="What is this file about?"),
        EngineRuntimeContext(
            selected_files_scope=SelectedFilesScope(document_ids=("document-1",)),
            execution_files=(
                {
                    "filename": "report.pdf",
                    "sandbox_path": "/workspace/runs/resp-1/inputs/report.pdf",
                },
            ),
        ),
        UserQuery(text="What is this file about?"),
    )

    assert "/workspace/runs/resp-1/inputs/report.pdf" not in prompt
