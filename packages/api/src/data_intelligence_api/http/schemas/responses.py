"""Pydantic request models for the responses API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DataCorpusPackageRequest(BaseModel):
    sources: list[str]
    schemas: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateResponseRequest(BaseModel):
    input: str | None = None
    data_corpus_package: DataCorpusPackageRequest | None = None
    user_id: str | None = None
    session_id: str | None = None


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
