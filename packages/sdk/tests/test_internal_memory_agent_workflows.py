from collections.abc import Callable
from typing import Any

from data_intelligence_sdk.core.types import EngineInput, ExecutionSpec, UserQuery
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class _ScriptedAgent:
    """Deterministic Deep-Agent substitute that invokes the supplied tools."""

    def __init__(
        self,
        tools: list[Any],
        script: Callable[[dict[str, Any], dict[str, Any]], str],
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._script = script

    def invoke(self, payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        return {"messages": [{"content": self._script(self._tools, payload)}]}


def _run_scripted_agent(
    *,
    question: str,
    client: object,
    script: Callable[[dict[str, Any], dict[str, Any]], str],
) -> tuple[object, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def agent_factory(**kwargs: Any) -> _ScriptedAgent:
        captured["system_prompt"] = kwargs["system_prompt"]
        captured["tool_names"] = [tool.name for tool in kwargs["tools"]]
        return _ScriptedAgent(kwargs["tools"], script)

    engine = GeneralPurposeEngine(llm=object(), agent_factory=agent_factory)
    engine._register_minimal_profile = lambda: None
    output = engine.run(
        EngineInput(
            query=UserQuery(text=question),
            spec=ExecutionSpec(intent="general", objective=question),
            runtime=EngineRuntimeContext(
                internal_memory_client=client,
                sandbox=object(),
            ),
        )
    )
    return output, captured


def test_agent_can_write_a_durable_user_preference_with_memory_tool() -> None:
    writes: list[dict[str, str | None]] = []

    class Client:
        def write(self, *, target, operation, content, match):
            writes.append(
                {
                    "target": target,
                    "operation": operation,
                    "content": content,
                    "match": match,
                }
            )
            return {"user_markdown": "Prefers Vietnamese responses."}

    question = "Hãy nhớ rằng tôi luôn muốn câu trả lời bằng tiếng Việt."

    def script(tools: dict[str, Any], payload: dict[str, Any]) -> str:
        assert payload["messages"][-1]["content"] == question
        tools["memory"].invoke(
            {
                "target": "user",
                "operation": "add",
                "content": "Prefers Vietnamese responses.",
            }
        )
        return "Đã lưu tuỳ chọn ngôn ngữ."

    output, captured = _run_scripted_agent(
        question=question,
        client=Client(),
        script=script,
    )

    assert output.result == "Đã lưu tuỳ chọn ngôn ngữ."
    assert writes == [
        {
            "target": "user",
            "operation": "add",
            "content": "Prefers Vietnamese responses.",
            "match": None,
        }
    ]
    assert "memory" in captured["tool_names"]
    assert "durable, high-value facts" in captured["system_prompt"]


def test_agent_can_search_then_scroll_prior_conversation_messages() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def session_search(self, query: str, *, limit: int):
            calls.append(("session_search", {"query": query, "limit": limit}))
            return [
                {
                    "conversation_id": "conversation-prior",
                    "match_message_id": "msg-revenue",
                    "snippet": "Revenue was 18% above plan.",
                    "messages": [],
                }
            ]

        def session_scroll(
            self,
            conversation_id: str,
            around_message_id: str,
            *,
            direction: str,
            limit: int,
        ):
            calls.append(
                (
                    "session_scroll",
                    {
                        "conversation_id": conversation_id,
                        "around_message_id": around_message_id,
                        "direction": direction,
                        "limit": limit,
                    },
                )
            )
            return [
                {
                    "message_id": "msg-explanation",
                    "role": "assistant",
                    "content": "Growth came from the enterprise segment.",
                }
            ]

    question = "Trong báo cáo trước, vì sao doanh thu tăng?"

    def script(tools: dict[str, Any], payload: dict[str, Any]) -> str:
        assert payload["messages"][-1]["content"] == question
        matches = tools["session_search"].invoke(
            {"query": "Revenue above plan", "limit": 3}
        )
        tools["session_scroll"].invoke(
            {
                "conversation_id": matches[0]["conversation_id"],
                "around_message_id": matches[0]["match_message_id"],
                "direction": "forward",
                "limit": 3,
            }
        )
        return "Doanh thu tăng nhờ phân khúc enterprise."

    output, captured = _run_scripted_agent(
        question=question,
        client=Client(),
        script=script,
    )

    assert output.result == "Doanh thu tăng nhờ phân khúc enterprise."
    assert calls == [
        ("session_search", {"query": "Revenue above plan", "limit": 3}),
        (
            "session_scroll",
            {
                "conversation_id": "conversation-prior",
                "around_message_id": "msg-revenue",
                "direction": "forward",
                "limit": 3,
            },
        ),
    ]
    assert "session_search" in captured["tool_names"]
    assert "session_scroll" in captured["tool_names"]
    assert "then `session_scroll`" in captured["system_prompt"]
