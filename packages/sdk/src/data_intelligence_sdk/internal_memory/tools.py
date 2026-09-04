from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


def create_internal_memory_tools(
    runtime: EngineRuntimeContext,
    *,
    include_session_history: bool = True,
) -> list[BaseTool]:
    """Create the internal-memory tools available during one agent run."""

    if runtime.internal_memory_client is None:
        return []
    client = runtime.internal_memory_client

    @tool
    def session_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search the current user's prior conversations for relevant messages."""

        return client.session_search(query, limit=limit)

    @tool
    def session_scroll(
        conversation_id: str,
        around_message_id: str,
        direction: Literal["forward", "backward"],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Read prior-conversation messages before or after a known message."""

        return client.session_scroll(
            conversation_id,
            around_message_id,
            direction=direction,
            limit=limit,
        )

    @tool
    def memory(
        target: Literal["user", "memory"],
        operation: Literal["add", "replace", "remove"],
        content: str | None = None,
        match: str | None = None,
    ) -> dict[str, Any]:
        """Persist one durable fact; replace and remove use an exact `match`."""

        return client.write(
            target=target,
            operation=operation,
            content=content,
            match=match,
        )

    if include_session_history:
        return [session_search, session_scroll, memory]
    return [memory]
