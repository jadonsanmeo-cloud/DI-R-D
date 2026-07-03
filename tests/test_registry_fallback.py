import unittest

from data_intelligence_sdk.core.errors import EngineNotFoundError
from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry


class FakeEngine:
    def __init__(self, name: str, handles: bool) -> None:
        self.name = name
        self.handles = handles

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return self.handles


class RegistryFallbackTests(unittest.TestCase):
    def test_constructor_fallback_is_selected_when_no_engine_handles_spec(self) -> None:
        fallback = FakeEngine("fallback", False)
        registry = InMemoryEngineRegistry(fallback_engine=fallback)

        self.assertIs(
            registry.select(ExecutionSpec(intent="unknown", objective="x")), fallback
        )

    def test_specific_engine_wins_before_fallback(self) -> None:
        specific = FakeEngine("specific", True)
        fallback = FakeEngine("fallback", False)
        registry = InMemoryEngineRegistry(fallback_engine=fallback)
        registry.register(specific)

        self.assertIs(
            registry.select(ExecutionSpec(intent="reason", objective="x")), specific
        )

    def test_set_fallback_updates_fallback_engine(self) -> None:
        fallback = FakeEngine("fallback", False)
        registry = InMemoryEngineRegistry()
        registry.set_fallback(fallback)

        self.assertIs(
            registry.select(ExecutionSpec(intent="unknown", objective="x")), fallback
        )

    def test_missing_engine_without_fallback_raises(self) -> None:
        with self.assertRaises(EngineNotFoundError):
            InMemoryEngineRegistry().select(
                ExecutionSpec(intent="unknown", objective="x")
            )


if __name__ == "__main__":
    unittest.main()
