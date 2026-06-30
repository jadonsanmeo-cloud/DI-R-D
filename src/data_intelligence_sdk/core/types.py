"""Shared data contracts for the base architecture.

These types are intentionally small. They describe the information that moves
between modules without locking the project into detailed SDK behavior too
early.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Intent = Literal["sql", "python", "rag", "workflow", "custom", "unknown"]
SUPPORTED_INTENTS: tuple[Intent, ...] = ("sql", "python", "rag", "workflow", "custom", "unknown")
TraceStatus = Literal["pending", "running", "completed", "failed", "skipped"]


@dataclass(slots=True)
class UserQuery:
    """Raw request from a user."""

    text: str
    user_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DataHubContext:
    """Data, metadata, and semantic context available to the system."""

    sources: list[str] = field(default_factory=list)
    schemas: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UserContext:
    """Long-lived user memory and preferences across tasks."""

    user_id: str | None = None
    preferences: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SessionContext:
    """Short-lived context for the current conversation or task session."""

    session_id: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionSpec:
    """Draft or confirmed execution request for engine selection."""

    intent: Intent
    objective: str
    data_requirements: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False
    engine_hint: str | None = None


@dataclass(slots=True)
class EngineStep:
    """A structured step recorded by an engine during execution."""

    name: str
    status: TraceStatus = "completed"
    description: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MethodCall:
    """A structured runtime or Method Hub call made by an engine."""

    method_name: str
    status: TraceStatus = "completed"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EngineTrace:
    """Structured execution trace produced while an engine runs."""

    steps: list[EngineStep] = field(default_factory=list)
    method_calls: list[MethodCall] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EngineOutput:
    """Raw engine result plus the structured trace recorded during execution."""

    engine_name: str
    result: Any = None
    trace: EngineTrace = field(default_factory=EngineTrace)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceBundle:
    """Evidence and trace material synthesized around an engine run."""

    sources: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    steps: list[EngineStep] = field(default_factory=list)
    method_calls: list[MethodCall] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FinalResponse:
    """User-facing answer plus supporting trace references."""

    answer: str
    evidence: EvidenceBundle | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
