"""Backward-compatible imports for the former Deep Agent sandbox module."""

from data_intelligence_sdk.runtime.deep_agent_backend import DeepAgentSandboxBackend
from data_intelligence_sdk.runtime.sandbox import (
    EngineSandboxSession,
    SandboxEnvironment,
    SandboxSessionProvider,
)

DeepAgentSandboxSession = EngineSandboxSession

__all__ = [
    "DeepAgentSandboxBackend",
    "DeepAgentSandboxSession",
    "EngineSandboxSession",
    "SandboxEnvironment",
    "SandboxSessionProvider",
]
