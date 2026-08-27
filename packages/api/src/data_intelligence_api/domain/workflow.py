"""Application-level workflow input types."""

from __future__ import annotations

from dataclasses import dataclass, field

from data_intelligence_sdk.core.types import (
    SessionContext,
    UploadedFile,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext
from data_intelligence_sdk.internal_memory.context import InternalMemoryContext


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeOptions:
    method_hub_enabled: bool
    engine: str | None = None
    workflow: str = "report"


@dataclass(frozen=True, slots=True)
class WorkflowInvocation:
    query: UserQuery
    uploaded_files: list[UploadedFile]
    session_context: SessionContext
    user_context: UserContext
    runtime_options: WorkflowRuntimeOptions
    memory_context: MemoryContext = field(default_factory=MemoryContext)
    internal_memory_context: InternalMemoryContext = field(
        default_factory=InternalMemoryContext
    )
