"""SQL engine placeholder."""

from __future__ import annotations

from data_intelligence_sdk.core.types import DataHubContext, EngineOutput, ExecutionSpec, UserContext
from data_intelligence_sdk.runtime.run_context import EngineRunContext


class SqlEngine:
    """Placeholder for structured data query execution."""

    name = "sql"

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent == self.name

    def run(
        self,
        spec: ExecutionSpec,
        datahub: DataHubContext,
        context: EngineRunContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        raise NotImplementedError("SQL execution is not part of the base scaffold.")
