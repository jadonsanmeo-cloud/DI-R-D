from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class InternalMemoryContext:
    """Frozen curated memory supplied once for a runtime operation."""

    user_markdown: str = ""
    memory_markdown: str = ""
    external_memories: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: object) -> "InternalMemoryContext":
        """Normalize the JSON snapshot received from Intelligence Service.

        Runtime clients can instantiate the SDK directly, outside the Data API's
        Pydantic validation boundary. Treat an invalid optional memory payload as
        absent so it cannot break the user's primary request.
        """

        if not isinstance(payload, Mapping):
            return cls()
        raw_external = payload.get("external_memories", ())
        external_memories = (
            tuple(item for item in raw_external if isinstance(item, str))
            if isinstance(raw_external, (list, tuple))
            else ()
        )
        return cls(
            user_markdown=_string_or_empty(payload.get("user_markdown")),
            memory_markdown=_string_or_empty(payload.get("memory_markdown")),
            external_memories=external_memories,
        )

    def render(self) -> str:
        sections: list[str] = []
        if self.user_markdown.strip():
            sections.append(f"USER.md:\n{self.user_markdown.strip()}")
        if self.memory_markdown.strip():
            sections.append(f"MEMORY.md:\n{self.memory_markdown.strip()}")
        if self.external_memories:
            sections.append("External memory:\n" + "\n".join(self.external_memories))
        return "\n\n".join(sections)


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""
