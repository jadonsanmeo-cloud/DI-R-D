"""Pydantic request models for the responses API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator


class RuntimeOptionsRequest(BaseModel):
    method_hub_enabled: bool | None = None
    engine: Literal["auto", "general", "reason", "report"] | None = None

class UploadedFileRequest(BaseModel):
    filename: str
    relative_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("relative_path", "relativePath"),
    )
    size: int = 0
    content_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MethodHubCapabilityResponse(BaseModel):
    default_enabled: bool
    available: bool


class RuntimeCapabilitiesResponse(BaseModel):
    method_hub: MethodHubCapabilityResponse


class CreateResponseRequest(BaseModel):
    input: str | None = None
    uploaded_files: list[UploadedFileRequest] = Field(default_factory=list)
    user_id: str | None = None
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )


class ResponseDecisionRequest(BaseModel):
    action: Literal["confirm", "revise"]
    revision: int = Field(gt=0)
    spec_markdown: str | None = None

    @model_validator(mode="after")
    def validate_revision_input(self):
        if self.action == "confirm" and self.spec_markdown is not None:
            raise ValueError("Confirm decisions cannot include spec_markdown.")
        if self.action == "revise" and not (
            self.spec_markdown and self.spec_markdown.strip()
        ):
            raise ValueError("Revise decisions require spec_markdown.")
        return self


class ResponseHistorySummary(BaseModel):
    response_id: str
    title: str
    status: str
    output_preview: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


class ResponseHistoryDetail(BaseModel):
    response_id: str
    status: str
    input: str
    spec: dict[str, Any]
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )
    output_text: str | None = None
    evidence: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, str] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
