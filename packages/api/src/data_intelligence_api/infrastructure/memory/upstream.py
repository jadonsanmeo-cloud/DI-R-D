from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from data_intelligence_sdk.memory import (
    MemoryCard,
    MemoryContext,
    MemoryScope,
    MemoryType,
)

_MEMORY_TYPES = frozenset(
    {
        "profile",
        "preference",
        "constraint",
        "episodic",
        "semantic",
        "outcome",
        "procedure",
    }
)


def parse_upstream_memory_context(
    value: Mapping[str, object] | None,
) -> MemoryContext:
    if value is None:
        return MemoryContext()

    cards_value = value.get("cards", [])
    if not isinstance(cards_value, list):
        return MemoryContext(loaded=True, mode="upstream")

    cards = tuple(
        card for item in cards_value if (card := _parse_card(item)) is not None
    )
    return MemoryContext(cards=cards, loaded=True, mode="upstream")


def _parse_card(value: object) -> MemoryCard | None:
    if not isinstance(value, Mapping):
        return None

    memory_type = value.get("memory_type")
    content = value.get("content")
    scope = value.get("scope")
    if (
        memory_type not in _MEMORY_TYPES
        or not isinstance(content, str)
        or not isinstance(scope, Mapping)
    ):
        return None

    try:
        source_refs = value.get("source_refs", [])
        if not isinstance(source_refs, list):
            return None
        return MemoryCard(
            memory_id=str(value["memory_id"]),
            memory_type=cast(MemoryType, memory_type),
            content=content,
            confidence=float(value["confidence"]),
            importance=float(value["importance"]),
            scope=MemoryScope(
                tenant_id=str(scope.get("tenant_id") or "upstream"),
                workspace_id=_optional_string(scope.get("workspace_id")),
                user_id=_optional_string(scope.get("user_id")),
                agent_id=_optional_string(scope.get("agent_id")),
                session_id=_optional_string(scope.get("session_id")),
            ),
            source_refs=tuple(
                dict(item) for item in source_refs if isinstance(item, Mapping)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
