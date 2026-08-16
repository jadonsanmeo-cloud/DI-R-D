from __future__ import annotations

from data_intelligence_api.http.schemas.responses import MemoryContextRequest
from data_intelligence_sdk.memory import MemoryCard, MemoryContext, MemoryScope


def parse_upstream_memory_context(value: MemoryContextRequest) -> MemoryContext:
    cards = tuple(
        MemoryCard(
            memory_id=item.memory_id,
            memory_type=item.memory_type,
            content=item.content,
            confidence=item.confidence,
            importance=item.importance,
            scope=MemoryScope(
                tenant_id=str(item.scope.get("tenant_id") or "upstream"),
                workspace_id=item.scope.get("workspace_id"),
                user_id=item.scope.get("user_id"),
                agent_id=item.scope.get("agent_id"),
                session_id=item.scope.get("session_id"),
            ),
            source_refs=tuple(item.source_refs),
        )
        for item in value.cards
    )
    return MemoryContext(cards=cards, loaded=True, mode="upstream")
