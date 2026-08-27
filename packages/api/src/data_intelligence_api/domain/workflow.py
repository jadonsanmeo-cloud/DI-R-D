"""Application-level workflow input types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from data_intelligence_sdk.core.types import (
    SessionContext,
    UploadedFile,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext


WorkflowName = Literal["report", "dashboard_extraction"]


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeOptions:
    method_hub_enabled: bool
    engine: str | None = None
    workflow: WorkflowName = "report"


@dataclass(frozen=True, slots=True)
class WorkflowInvocation:
    query: UserQuery
    uploaded_files: list[UploadedFile]
    session_context: SessionContext
    user_context: UserContext
    runtime_options: WorkflowRuntimeOptions
    memory_context: MemoryContext = field(default_factory=MemoryContext)
