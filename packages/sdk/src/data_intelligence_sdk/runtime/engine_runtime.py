"""Runtime context passed to selected engines."""

from __future__ import annotations

from dataclasses import dataclass, field

from data_intelligence_sdk.runtime.interfaces import InterfaceBuilder, InterfaceRegistry
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient, MCPToolDefinition
from data_intelligence_sdk.runtime.sandbox import (
    EngineSandboxSession,
    SandboxEnvironment,
)
from data_intelligence_sdk.runtime.resource_manager import ResourceManager
from data_intelligence_sdk.runtime.run_context import EngineRunContext
from data_intelligence_sdk.sandbox.artifacts import ArtifactStore, RunArtifactSession
from data_intelligence_sdk.sandbox.executor import SandboxExecutor
from data_intelligence_sdk.sandbox.logs import LogStore


@dataclass(slots=True)
class EngineRuntimeContext:
    """Runtime services available to an engine during execution."""

    run_context: EngineRunContext = field(default_factory=EngineRunContext)
    mcp_client: MCPMethodClient | None = None
    mcp_tools: tuple[MCPToolDefinition, ...] = ()
    interface_registry: InterfaceRegistry | None = None
    interface_builder: InterfaceBuilder | None = None
    sandbox_executor: SandboxExecutor | None = None
    artifact_store: ArtifactStore | None = None
    log_store: LogStore | None = None
    resource_manager: ResourceManager | None = None
    sandbox: EngineSandboxSession | None = None
    run_artifact: RunArtifactSession | None = None

    @property
    def sandbox_environment(self) -> SandboxEnvironment | None:
        """Return capabilities for the active request sandbox, when provisioned."""

        return self.sandbox.environment if self.sandbox is not None else None
