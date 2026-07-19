"""Application-level workflow input types."""

from __future__ import annotations

from dataclasses import dataclass

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    SessionContext,
    UserContext,
    UserQuery,
)


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeOptions:
    method_hub_enabled: bool


@dataclass(frozen=True, slots=True)
class WorkflowInvocation:
    query: UserQuery
    corpus_package: DataCorpusPackage
    session_context: SessionContext
    user_context: UserContext
    runtime_options: WorkflowRuntimeOptions
