"""Workflow execution port used by API services and background workers."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    ExecutionSpec,
    FinalResponse,
    PreparedExecution,
)

from data_intelligence_api.domain.workflow import WorkflowInvocation


class WorkflowExecutor(Protocol):
    def prepare(self, invocation: WorkflowInvocation) -> PreparedExecution: ...

    def revise(
        self,
        prepared: PreparedExecution,
        previous_spec: ExecutionSpec,
        feedback: str,
    ) -> ExecutionSpec: ...

    def execute(
        self,
        prepared: PreparedExecution,
        confirmed_spec: ExecutionSpec,
    ) -> FinalResponse: ...
