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


class MemoryCardRequest(BaseModel):
    memory_id: str
    memory_type: Literal["profile", "preference", "constraint", "episodic", "semantic", "outcome", "procedure"]
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    memory_layer: Literal["short_term", "long_term"] | None = None
    provider: str | None = None
    score: float | None = None
    matched_by: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    scope: dict[str, str | None] = Field(default_factory=dict)


class MemoryContextRequest(BaseModel):
    source: str = "intelligence-service"
    cards: list[MemoryCardRequest] = Field(default_factory=list, max_length=100)


class MethodHubCapabilityResponse(BaseModel):
    default_enabled: bool
    available: bool


class RuntimeCapabilitiesResponse(BaseModel):
    method_hub: MethodHubCapabilityResponse


class CreateResponseRequest(BaseModel):
    input: str | None = None
    data_corpus_package: DataCorpusPackageRequest | None = None
    user_id: str | None = None
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )
    memory_context: MemoryContextRequest | None = None


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
