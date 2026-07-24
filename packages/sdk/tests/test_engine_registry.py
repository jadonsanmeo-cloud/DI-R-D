import json
import unittest
from dataclasses import dataclass

from data_intelligence_sdk.core.errors import EngineNotFoundError
from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.registry.engine_selector import (
    EngineDescriptor,
    LLMEngineSelector,
)


@dataclass
class FakeEngine:
    name: str
    description: str


class StaticSelector:
    def __init__(self, selected_name: str) -> None:
        self.selected_name = selected_name

    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        del spec, engines
        return self.selected_name


class RaisingSelector:
    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        del spec, engines
        raise ConnectionError("selector unavailable")


class RecordingLLMClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.messages: list[dict[str, str]] = []
        self.stage: str | None = None

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        stage: str,
    ) -> dict[str, object]:
        self.messages = messages
        self.stage = stage
        return self.response

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        stage: str,
    ) -> str:
        del messages, stage
        raise AssertionError("Engine selection must use JSON completion.")


def make_spec(
    *, intent: str = "report", engine_hint: str | None = None
) -> ExecutionSpec:
    return ExecutionSpec(
        intent=intent,
        objective="Create a revenue report",
        data_requirements=["sales.csv"],
        constraints={"output_format": "markdown"},
        confirmed=True,
        engine_hint=engine_hint,
    )


class EngineRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.general = FakeEngine(
            "general_purpose",
            "General exploratory analysis and code execution.",
        )
        self.report = FakeEngine(
            "report",
            "Structured reports, charts, and report templates.",
        )

    def make_registry(self, selector: object) -> InMemoryEngineRegistry:
        registry = InMemoryEngineRegistry(
            selector=selector,
            fallback_engine_name="general_purpose",
        )
        registry.register(self.general)
        registry.register(self.report)
        return registry

    def test_agent_selection_ignores_engine_hint(self) -> None:
        registry = self.make_registry(StaticSelector("report"))

        selected = registry.select(
            make_spec(intent="report", engine_hint="general_purpose")
        )

        self.assertIs(selected, self.report)

    def test_agent_selection_uses_registered_engine_name(self) -> None:
        selected_engine = FakeEngine("custom", "Selected by name.")
        registry = InMemoryEngineRegistry(selector=StaticSelector("custom"))
        registry.register(selected_engine)

        selected = registry.select(make_spec())

        self.assertIs(selected, selected_engine)

    def test_unknown_agent_selection_falls_back_to_general_purpose(self) -> None:
        registry = self.make_registry(StaticSelector("missing"))

        selected = registry.select(make_spec())

        self.assertIs(selected, self.general)

    def test_selector_exception_falls_back_to_general_purpose(self) -> None:
        registry = self.make_registry(RaisingSelector())

        selected = registry.select(make_spec())

        self.assertIs(selected, self.general)

    def test_missing_configured_fallback_is_an_error(self) -> None:
        registry = InMemoryEngineRegistry(
            selector=StaticSelector("missing"),
            fallback_engine_name="general_purpose",
        )
        registry.register(self.report)

        with self.assertRaisesRegex(EngineNotFoundError, "general_purpose"):
            registry.select(make_spec())

    def test_legacy_fallback_engine_does_not_need_registration(self) -> None:
        registry = InMemoryEngineRegistry()
        registry.set_fallback(self.general)

        selected = registry.select(make_spec(intent="unknown"))

        self.assertIs(selected, self.general)

    def test_no_selector_uses_fallback_without_engine_self_routing(self) -> None:
        registry = InMemoryEngineRegistry(fallback_engine=self.general)
        registry.register(self.report)

        selected = registry.select(make_spec(intent="report"))

        self.assertIs(selected, self.general)

    def test_selector_prompt_contains_catalog_but_not_engine_hint(self) -> None:
        client = RecordingLLMClient({"engine_name": "report"})
        selector = LLMEngineSelector(client)

        selected = selector.select(
            make_spec(engine_hint="general_purpose"),
            (
                EngineDescriptor(self.general.name, self.general.description),
                EngineDescriptor(self.report.name, self.report.description),
            ),
        )

        self.assertEqual(selected, "report")
        self.assertEqual(client.stage, "engine_selector")
        request_payload = json.loads(client.messages[-1]["content"])
        self.assertNotIn("engine_hint", request_payload["spec"])
        self.assertEqual(
            request_payload["engines"],
            [
                {
                    "name": "general_purpose",
                    "description": self.general.description,
                },
                {"name": "report", "description": self.report.description},
            ],
        )

    def test_selector_rejects_missing_engine_name(self) -> None:
        selector = LLMEngineSelector(RecordingLLMClient({"reason": "no choice"}))

        with self.assertRaisesRegex(ValueError, "engine_name"):
            selector.select(
                make_spec(),
                (EngineDescriptor(self.report.name, self.report.description),),
            )


if __name__ == "__main__":
    unittest.main()
