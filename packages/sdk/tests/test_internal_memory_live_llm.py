"""Opt-in checks that a real tool-calling model chooses internal-memory tools.

Run manually with ``RUN_LIVE_LLM_TESTS=1`` and an ``OPENROUTER_API_KEY``. These
tests are excluded from the ordinary suite because they make billable network
calls and model behaviour may evolve.
"""

import os
from typing import Any

import pytest

from data_intelligence_sdk.core.types import EngineInput, ExecutionSpec, UserQuery
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM_TESTS") != "1",
    reason="Set RUN_LIVE_LLM_TESTS=1 to make live OpenRouter calls.",
)


class RecordingMemoryClient:
    """In-memory stand-in for the HTTP boundary that records model tool calls."""

    def __init__(self, *, history: list[dict[str, Any]] | None = None) -> None:
        self.writes: list[dict[str, str | None]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.history = history or []

    def write(
        self,
        *,
        target: str,
        operation: str,
        content: str | None,
        match: str | None,
    ) -> dict[str, str]:
        self.writes.append(
            {
                "target": target,
                "operation": operation,
                "content": content,
                "match": match,
            }
        )
        return {"user_markdown": content or "", "memory_markdown": ""}

    def session_search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("session_search", {"query": query, "limit": limit}))
        return self.history

    def session_scroll(
        self,
        conversation_id: str,
        around_message_id: str,
        *,
        direction: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.calls.append(
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
                "content": "Revenue grew because enterprise renewals increased.",
            }
        ]


def _run_live_agent(question: str, client: RecordingMemoryClient):
    engine = GeneralPurposeEngine(config_path="configs/proxy-openrouter.toml")
    return engine.run(
        EngineInput(
            query=UserQuery(text=question),
            spec=ExecutionSpec(intent="general", objective=question),
            runtime=EngineRuntimeContext(
                internal_memory_client=client,
                sandbox=object(),
            ),
        )
    )


def test_live_llm_decides_to_persist_a_durable_preference() -> None:
    client = RecordingMemoryClient()

    output = _run_live_agent(
        "I always prefer answers in Vietnamese in every future conversation. "
        "Please handle this as a durable preference, not transient task state.",
        client,
    )

    assert output.result
    assert len(client.writes) == 1
    assert client.writes[0]["target"] == "user"
    assert client.writes[0]["operation"] == "add"
    assert client.writes[0]["content"]
    print(
        "live_memory_write=",
        {"write": client.writes[0], "result": str(output.result)[:240]},
    )


def test_live_llm_searches_then_scrolls_before_answering_from_history() -> None:
    client = RecordingMemoryClient(
        history=[
            {
                "conversation_id": "conversation-prior",
                "match_message_id": "msg-revenue",
                "snippet": "Revenue was above plan.",
                "messages": [],
            }
        ]
    )

    output = _run_live_agent(
        "A prior conversation explains why revenue increased, but the reason is "
        "not in this prompt. Find the prior conversation and answer from it; do "
        "not guess.",
        client,
    )

    assert output.result
    assert [name for name, _ in client.calls[:2]] == [
        "session_search",
        "session_scroll",
    ]
    assert client.calls[1][1]["conversation_id"] == "conversation-prior"
    assert client.calls[1][1]["around_message_id"] == "msg-revenue"
    print(
        "live_session_retrieval=",
        {"calls": client.calls, "result": str(output.result)[:240]},
    )
