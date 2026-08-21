"""Schemas for stateless Data Intelligence runtime operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from data_intelligence_api.http.schemas.runtime_inputs import (
    ExecutionContextRequest,
    ExecutionFileRequest,
    ReportHistoryMessage,
    RuntimeOptionsRequest,
    UploadedFileRequest,
)


class OperationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    operation_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(default=1, ge=1)
    response_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)


class RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1)
    session_id: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    language: str = Field(default="auto", min_length=1, max_length=32)
    history: list[ReportHistoryMessage] = Field(default_factory=list, max_length=200)
    organization_id: str | None = None
    workspace_id: str | None = None
    uploaded_files: list[UploadedFileRequest] = Field(default_factory=list)
    runtime_options: RuntimeOptionsRequest = Field(default_factory=RuntimeOptionsRequest)
    execution_context: ExecutionContextRequest | None = None
    execution_files: list[ExecutionFileRequest] = Field(default_factory=list)
    primary_source_id: str | None = Field(default=None, max_length=2048)


class DirectRuntimeOptions(RuntimeOptionsRequest):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["report"]


class DirectRuntimeInput(RuntimeInput):
    runtime_options: DirectRuntimeOptions


class PrepareSpecRequest(OperationEnvelope):
    runtime_input: RuntimeInput
    memory_scope: dict[str, Any] | None = None
    memory_context: dict[str, Any] | None = None


class PrepareSpecResponse(OperationEnvelope):
    prepared_execution: dict[str, Any]
    spec_markdown: str = Field(min_length=1)
    intent: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviseSpecRequest(OperationEnvelope):
    runtime_input: RuntimeInput
    prepared_execution: dict[str, Any]
    current_spec_markdown: str = Field(min_length=1)
    revised_spec_markdown: str = Field(min_length=1)
    memory_scope: dict[str, Any] | None = None


class ReviseSpecResponse(OperationEnvelope):
    spec_markdown: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecuteRequest(OperationEnvelope):
    runtime_input: RuntimeInput
    prepared_execution: dict[str, Any]
    spec_markdown: str = Field(min_length=1)
    memory_scope: dict[str, Any] | None = None
    memory_context: dict[str, Any] | None = None


class DirectExecuteRequest(OperationEnvelope):
    runtime_input: DirectRuntimeInput
    memory_scope: dict[str, Any] | None = None
    memory_context: dict[str, Any] | None = None


class RuntimeErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    operation_id: str
    trace_id: str | None = None
