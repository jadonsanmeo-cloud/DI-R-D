from __future__ import annotations

from time import perf_counter
from typing import cast

import httpx

from data_intelligence_sdk.memory import (
    MemoryCard,
    MemoryContext,
    MemoryScope,
    MemoryType,
)
from data_intelligence_sdk.runtime.logger import RuntimeLogger


MEMORY_TYPES: tuple[MemoryType, ...] = (
    "profile",
    "preference",
    "constraint",
    "episodic",
    "semantic",
    "outcome",
    "procedure",
)
_MEMORY_TYPE_SET = frozenset(MEMORY_TYPES)


class AxiomExperienceMemoryLoader:
    def __init__(
        self,
        *,
        base_url: str,
        limit: int = 20,
        timeout_seconds: float = 2.0,
        service_token: str = "",
        client: httpx.AsyncClient | None = None,
        logger: RuntimeLogger | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.limit = limit
        self.timeout_seconds = timeout_seconds
        self.service_token = service_token
        self.client = client
        self.logger = logger

    async def load(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        agent_id: str | None,
        session_id: str | None,
        trace_id: str | None,
    ) -> MemoryContext:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": query,
            "memory_types": list(MEMORY_TYPES),
            "search_type": "hybrid",
            "limit": self.limit,
        }
        optional_scope = {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "trace_id": trace_id,
        }
        payload.update(
            {key: value for key, value in optional_scope.items() if value is not None}
        )

        started_at = perf_counter()
        self._log(
            "memory.load.started",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "memory_type_count": len(MEMORY_TYPES),
                "limit": self.limit,
            },
        )

        try:
            response = await self._post(payload)
            response.raise_for_status()
            body = response.json()
            raw_memories = body.get("memories") if isinstance(body, dict) else None
            if not isinstance(raw_memories, list):
                raise ValueError("Memory search response must contain a memories list.")

            parsed_cards = tuple(
                card
                for raw_card in raw_memories
                if (card := _parse_memory_card(raw_card)) is not None
            )
            cards = tuple(
                card
                for card in parsed_cards
                if _scope_is_compatible(
                    card.scope,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    session_id=session_id,
                )
            )
            self._log(
                "memory.load.completed",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "count": len(cards),
                    "rejected_scope_count": len(parsed_cards) - len(cards),
                    "duration_ms": round((perf_counter() - started_at) * 1000),
                },
            )
            return MemoryContext(cards=cards, loaded=True, mode="active")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            error_type = type(exc).__name__
            self._log(
                "memory.load.failed",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "error_type": error_type,
                    "duration_ms": round((perf_counter() - started_at) * 1000),
                },
            )
            return MemoryContext(loaded=False, mode="failed", error=error_type)

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        headers = (
            {"Authorization": f"Bearer {self.service_token}"}
            if self.service_token
            else None
        )
        url = f"{self.base_url}/api/v1/memories/search"
        if self.client is not None:
            return await self.client.post(url, json=payload, headers=headers)

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.post(url, json=payload, headers=headers)

    def _log(self, event: str, payload: dict[str, object]) -> None:
        if self.logger is not None:
            self.logger.log(event, payload)


def _parse_memory_card(value: object) -> MemoryCard | None:
    if not isinstance(value, dict):
        return None

    try:
        memory_type = value["memory_type"]
        scope_value = value["scope"]
        source_refs_value = value.get("source_refs", [])
        if memory_type not in _MEMORY_TYPE_SET or not isinstance(scope_value, dict):
            return None
        if not isinstance(source_refs_value, list):
            return None

        source_refs = tuple(
            dict(item) for item in source_refs_value if isinstance(item, dict)
        )
        return MemoryCard(
            memory_id=str(value["memory_id"]),
            memory_type=cast(MemoryType, memory_type),
            content=str(value["content"]),
            confidence=float(value["confidence"]),
            importance=float(value["importance"]),
            scope=MemoryScope(
                tenant_id=str(scope_value["tenant_id"]),
                workspace_id=_optional_string(scope_value.get("workspace_id")),
                user_id=_optional_string(scope_value.get("user_id")),
                agent_id=_optional_string(scope_value.get("agent_id")),
                session_id=_optional_string(scope_value.get("session_id")),
            ),
            source_refs=source_refs,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _scope_is_compatible(
    scope: MemoryScope,
    *,
    tenant_id: str,
    user_id: str,
    workspace_id: str | None,
    agent_id: str | None,
    session_id: str | None,
) -> bool:
    if scope.tenant_id != tenant_id:
        return False
    return all(
        actual is None or actual == expected
        for actual, expected in (
            (scope.user_id, user_id),
            (scope.workspace_id, workspace_id),
            (scope.agent_id, agent_id),
            (scope.session_id, session_id),
        )
    )
