"""Pydantic request models for the responses API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DataCorpusPackageRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)
    schemas: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeOptionsRequest(BaseModel):
    method_hub_enabled: bool | None = None
    engine: Literal["auto", "general", "reason", "report"] | None = None


class MethodHubCapabilityResponse(BaseModel):
    default_enabled: bool
    available: bool


class RuntimeCapabilitiesResponse(BaseModel):
    method_hub: MethodHubCapabilityResponse


class CreateResponseRequest(BaseModel):
    input: str | None = None
    data_corpus_package: DataCorpusPackageRequest
    user_id: str | None = None
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )


class CapabilityRequirementRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditableExecutionSpecRequest(BaseModel):
    objective: str = Field(min_length=1)
    data_requirements: list[str] = Field(default_factory=list)
    capability_requirements: list[CapabilityRequirementRequest] = Field(
        default_factory=list
    )
    constraints: dict[str, Any] = Field(default_factory=dict)
    engine_hint: str | None = None


class ResponseDecisionRequest(BaseModel):
    action: Literal["confirm", "revise"]
    revision: int = Field(gt=0)
    feedback: str | None = None
    edited_spec: EditableExecutionSpecRequest | None = None

    @model_validator(mode="after")
    def validate_revision_input(self):
        if self.action == "confirm" and (
            self.feedback is not None or self.edited_spec is not None
        ):
            raise ValueError("Confirm decisions cannot include revision input.")
        if self.action == "revise" and not (
            (self.feedback and self.feedback.strip()) or self.edited_spec is not None
        ):
            raise ValueError("Revise decisions require feedback or edited_spec.")
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
