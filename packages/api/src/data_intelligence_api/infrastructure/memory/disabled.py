from data_intelligence_sdk.memory import MemoryContext
from data_intelligence_sdk.runtime.logger import RuntimeLogger


class DisabledMemoryLoader:
    def __init__(self, *, logger: RuntimeLogger | None = None) -> None:
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
        del query, tenant_id, user_id, workspace_id, agent_id, session_id, trace_id
        if self.logger is not None:
            self.logger.log("memory.load.skipped", {"reason": "disabled"})
        return MemoryContext()
