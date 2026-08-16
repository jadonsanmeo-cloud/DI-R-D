"""Request models shared by stateless runtime operations and workflow adapters."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


class RuntimeOptionsRequest(BaseModel):
    method_hub_enabled: bool | None = None
    engine: Literal["auto", "general", "reason", "report"] | None = None


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


class WorkflowRequest(BaseModel):
    input: str | None = None
    uploaded_files: list[UploadedFileRequest] = Field(default_factory=list)
    user_id: str | None = None
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(default_factory=RuntimeOptionsRequest)
    execution_context: ExecutionContextRequest | None = None
    execution_files: list[ExecutionFileRequest] = Field(default_factory=list)
