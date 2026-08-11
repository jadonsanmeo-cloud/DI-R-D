"""Client for delegating report runs to GenReport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class GenReportClient:
    def __init__(
        self,
        base_url: str,
        *,
        public_base_url: str | None = None,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.public_base_url = (public_base_url or base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def create_conversation(self, title: str) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/conversations",
                json={"title": title or "AXIOM report", "model": None},
            )
            response.raise_for_status()
            payload = response.json()
        conversation_id = payload.get("hash_id") or payload.get("id")
        if conversation_id is None:
            raise RuntimeError("GenReport did not return a conversation ID.")
        return str(conversation_id)

    async def upload_file(
        self,
        *,
        conversation_id: str,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> int:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/files/upload",
                data={"conversation_id": conversation_id},
                files={
                    "file": (
                        filename,
                        content,
                        content_type or "application/octet-stream",
                    )
                },
            )
            response.raise_for_status()
            payload = response.json()
        file_id = payload.get("id")
        if not isinstance(file_id, int):
            raise RuntimeError("GenReport upload did not return a file ID.")
        return file_id

    async def stream_chat(
        self,
        *,
        conversation_id: str,
        message: str,
        file_ids: list[int],
        runtime_gateway: dict[str, Any] | None = None,
        language: str = "en",
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "message": message,
            "conversation_id": conversation_id,
            "files": file_ids,
            "analysis_mode": "auto",
            "language": language,
        }
        if runtime_gateway is not None:
            payload["runtime_gateway"] = runtime_gateway
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/v1/chat/stream",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    yield chunk
