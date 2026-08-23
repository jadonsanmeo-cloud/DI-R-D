from data_intelligence_sdk.core.types import ExecutionSpec, UserQuery
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition


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
