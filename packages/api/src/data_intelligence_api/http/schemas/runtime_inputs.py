"""Request models shared by stateless runtime operations and workflow adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class RuntimeOptionsRequest(BaseModel):
    method_hub_enabled: bool | None = None
    engine: Literal["auto", "general", "reason", "report"] | None = None
    workflow: Literal["report", "dashboard_extraction"] = "report"


class UploadedFileRequest(BaseModel):
    filename: str
    size: int = 0
    content_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionFileRequest(BaseModel):
    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sandbox_path: str = Field(pattern=r"^/workspace/runs/[^/]+/inputs/")
    content_type: str = "application/octet-stream"
    size: int = Field(ge=0)
    checksum: str | None = None
    source_id: str | None = Field(default=None, max_length=2048)
    document_id: str | None = Field(default=None, max_length=255)
    source_object_key: str | None = Field(default=None, max_length=2048)
    source_last_modified: datetime | None = None


class ExecutionContextRequest(BaseModel):
    version: Literal["v1"] = "v1"
    run_id: str
    conversation_id: str
    sandbox_id: UUID
    execution_workspace_id: UUID
    gateway_url: AnyHttpUrl
    capability_token: str = Field(min_length=1)
    expires_at: int
    input_path: str
    work_path: str
    output_path: str
    capabilities: list[str]

    @model_validator(mode="after")
    def validate_run_scoped_paths(self):
        run_root = f"/workspace/runs/{self.run_id}"
        expected_paths = {
            "input_path": f"{run_root}/inputs",
            "work_path": f"{run_root}/work",
            "output_path": f"{run_root}/outputs",
        }
        for field_name, expected_path in expected_paths.items():
            if getattr(self, field_name) != expected_path:
                raise ValueError(
                    f"{field_name} must be exactly scoped to run_id {self.run_id!r}"
                )
        return self


class ReportHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)


class WorkflowRequest(BaseModel):
    input: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    language: str = Field(default="auto", min_length=1, max_length=32)
    history: list[ReportHistoryMessage] = Field(default_factory=list, max_length=200)
    uploaded_files: list[UploadedFileRequest] = Field(default_factory=list)
    user_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    workspace_ids: list[str] | None = Field(default=None, min_length=1)
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )
    execution_context: ExecutionContextRequest | None = None
    execution_files: list[ExecutionFileRequest] = Field(default_factory=list)
    primary_source_id: str | None = Field(default=None, max_length=2048)
    internal_memory_context: dict[str, Any] | None = None
