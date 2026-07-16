"""Sandbox execution boundary for untrusted or generated interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from data_intelligence_sdk.core.types import InterfaceDefinition, TraceStatus


@dataclass(slots=True)
class SandboxRunResult:
    """Result and evidence references from sandbox execution."""

    status: TraceStatus
    result: Any = None
    error: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)
    resource_usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SandboxExecutor(Protocol):
    """Runs or validates interface definitions in a controlled sandbox."""

    def run(
        self,
        interface: InterfaceDefinition,
        inputs: dict[str, Any],
        resource_policy: dict[str, Any] | None = None,
    ) -> SandboxRunResult:
        """Run an interface in the sandbox."""

    def validate(
        self,
        interface: InterfaceDefinition,
        validation_inputs: dict[str, Any],
        resource_policy: dict[str, Any] | None = None,
    ) -> SandboxRunResult:
        """Validate an interface in the sandbox."""
