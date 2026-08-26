from __future__ import annotations

from typing import Any

import httpx

from data_intelligence_sdk.core.types import UserQuery


class InternalMemoryClient:
    """Synchronous request-scoped bridge to Intelligence internal memory APIs."""

    def __init__(
        self,
        base_url: str,
        user_id: str,
        organization_id: str,
        *,
        authorization: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-User-ID": user_id, "X-Org-ID": organization_id}
        if authorization:
            self._headers["Authorization"] = authorization
        self._http = http_client or httpx

    def session_search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return self._get(
            "/internal-memory/session-search", {"query": query, "limit": limit}
        )

    def session_scroll(
        self,
        conversation_id: str,
        around_message_id: str,
        *,
        direction: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._get(
            "/internal-memory/session-scroll",
            {
                "conversation_id": conversation_id,
                "around_message_id": around_message_id,
                "direction": direction,
                "limit": limit,
            },
        )

    def write(
        self, *, target: str, operation: str, content: str | None, match: str | None
    ) -> dict[str, Any]:
        response = self._http.post(
            f"{self._base_url}/internal-memory/write",
            json={
                "target": target,
                "operation": operation,
                "content": content,
                "match": match,
            },
            headers=self._headers,
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._http.get(
            f"{self._base_url}{path}",
            params=params,
            headers=self._headers,
            timeout=10.0,
        )
        response.raise_for_status()
        return list(response.json())


def create_internal_memory_client(
    service_url: str | None,
    query: UserQuery,
) -> InternalMemoryClient | None:
    """Create a memory client only when the trusted request scope is complete."""

    organization_id = query.metadata.get("organization_id")
    if not service_url or not query.user_id or not isinstance(organization_id, str):
        return None
    if not organization_id.strip():
        return None
    return InternalMemoryClient(
        service_url,
        query.user_id,
        organization_id,
    )
