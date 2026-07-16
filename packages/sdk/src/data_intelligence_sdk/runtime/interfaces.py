"""Interface discovery and creation boundaries."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    InterfaceDefinition,
)


class InterfaceRegistry(Protocol):
    """Discovers interfaces that can satisfy capability requirements."""

    def register(self, interface: InterfaceDefinition) -> None:
        """Register an interface definition."""

    def find(
        self,
        requirement: CapabilityRequirement,
        corpus_package: DataCorpusPackage,
    ) -> InterfaceDefinition | None:
        """Return an interface matching the requirement, if one exists."""

    def list_available(self) -> list[InterfaceDefinition]:
        """Return all known interface definitions."""


class InterfaceBuilder(Protocol):
    """Creates proposed interfaces for missing capabilities."""

    def propose(
        self,
        requirement: CapabilityRequirement,
        corpus_package: DataCorpusPackage,
    ) -> InterfaceDefinition:
        """Return a proposed interface definition for a missing capability."""


class InMemoryInterfaceRegistry:
    """Small in-memory registry for contract tests and examples."""

    def __init__(self) -> None:
        self._interfaces: dict[str, InterfaceDefinition] = {}

    def register(self, interface: InterfaceDefinition) -> None:
        self._interfaces[interface.name] = interface

    def find(
        self,
        requirement: CapabilityRequirement,
        corpus_package: DataCorpusPackage,
    ) -> InterfaceDefinition | None:
        del corpus_package
        for interface in self._interfaces.values():
            capability_names = interface.metadata.get("capability_names", [])
            if requirement.name in capability_names:
                return interface
        return None

    def list_available(self) -> list[InterfaceDefinition]:
        return list(self._interfaces.values())
