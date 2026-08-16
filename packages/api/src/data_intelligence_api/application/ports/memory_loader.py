from typing import Protocol

from data_intelligence_sdk.memory import MemoryContext


class MemoryLoader(Protocol):
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
    ) -> MemoryContext: ...
