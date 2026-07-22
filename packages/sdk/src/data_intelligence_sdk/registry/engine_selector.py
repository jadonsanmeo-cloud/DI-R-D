"""LLM-backed engine selection contracts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol

from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.runtime.llm_client import LLMClient


@dataclass(frozen=True, slots=True)
class EngineDescriptor:
    """Public metadata the selector may use to choose an engine."""

    name: str
    description: str


class EngineSelector(Protocol):
    """Choose one engine name from the supplied immutable catalog."""

    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        """Return the exact name of one catalog engine."""


class LLMEngineSelector:
    """Ask a JSON-capable LLM to route a confirmed execution spec."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        spec_payload = asdict(spec)
        spec_payload.pop("engine_hint", None)
        payload = self.llm_client.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the engine routing agent. Choose exactly one engine "
                        "from the supplied catalog for the confirmed execution spec. "
                        "Use engine capabilities described by name and description. "
                        'Return JSON only in the form {"engine_name": "<name>"}. '
                        "Never invent an engine name."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "spec": spec_payload,
                            "engines": [asdict(engine) for engine in engines],
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                },
            ],
            stage="engine-selector",
        )
        engine_name = payload.get("engine_name")
        if not isinstance(engine_name, str) or not engine_name.strip():
            raise ValueError("Engine selector response requires engine_name.")
        return engine_name.strip()
