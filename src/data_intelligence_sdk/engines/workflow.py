"""Workflow engine placeholder."""

from __future__ import annotations

from data_intelligence_sdk.core.types import DataHubContext, EngineOutput, ExecutionSpec, UserContext
from data_intelligence_sdk.runtime.run_context import EngineRunContext


class WorkflowEngine:
    """Placeholder for multi-step or multi-engine workflows."""

    name = "workflow"

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent == self.name

    def run(
        self,
        spec: ExecutionSpec,
        datahub: DataHubContext,
        context: EngineRunContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        raise NotImplementedError("Workflow execution is not part of the base scaffold.")
