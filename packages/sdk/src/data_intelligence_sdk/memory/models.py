from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

MemoryType = Literal[
    "profile",
    "preference",
    "constraint",
    "episodic",
    "semantic",
    "outcome",
    "procedure",
]

MemoryTarget = Literal["orchestrator", "spec_builder"]

_ORCHESTRATOR_TYPES = frozenset({"profile", "preference", "constraint"})
_SPEC_BUILDER_TYPES = frozenset(
    {"preference", "constraint", "semantic", "episodic", "outcome", "procedure"}
)
_TYPE_PRIORITY: dict[MemoryType, int] = {
    "constraint": 0,
    "preference": 1,
    "profile": 2,
    "procedure": 3,
    "semantic": 4,
    "episodic": 5,
    "outcome": 6,
}
_SECTION_LABELS: dict[MemoryType, str] = {
    "constraint": "Constraints",
    "preference": "Preferences",
    "profile": "Profile",
    "procedure": "Procedures",
    "semantic": "Relevant Knowledge",
    "episodic": "Relevant Experience",
    "outcome": "Prior Outcomes",
}
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class MemoryScope:
    tenant_id: str
    workspace_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryCard:
    memory_id: str
    memory_type: MemoryType
    content: str
    confidence: float
    importance: float
    scope: MemoryScope
    source_refs: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryContext:
    cards: tuple[MemoryCard, ...] = ()
    loaded: bool = False
    mode: str = "disabled"
    error: str | None = None

    def for_orchestrator(self) -> tuple[MemoryCard, ...]:
        return tuple(
            card for card in self.cards if card.memory_type in _ORCHESTRATOR_TYPES
        )

    def for_spec_builder(self) -> tuple[MemoryCard, ...]:
        return tuple(
            card for card in self.cards if card.memory_type in _SPEC_BUILDER_TYPES
        )

    def render(self, *, target: MemoryTarget, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""

        cards = (
            self.for_orchestrator()
            if target == "orchestrator"
            else self.for_spec_builder()
        )
        ranked = _deduplicate_and_rank(cards)
        max_characters = max_tokens * 4
        sections: list[str] = []

        for memory_type in sorted(
            {card.memory_type for card in ranked},
            key=_TYPE_PRIORITY.__getitem__,
        ):
            header = f"{_SECTION_LABELS[memory_type]}:"
            section_lines: list[str] = []
            for card in ranked:
                if card.memory_type != memory_type:
                    continue
                candidate_lines = [*section_lines, f"- {card.content}"]
                candidate_section = "\n".join([header, *candidate_lines])
                candidate = "\n\n".join([*sections, candidate_section])
                if len(candidate) > max_characters:
                    continue
                section_lines = candidate_lines
            if section_lines:
                sections.append("\n".join([header, *section_lines]))

        return "\n\n".join(sections)


def _deduplicate_and_rank(cards: tuple[MemoryCard, ...]) -> tuple[MemoryCard, ...]:
    selected: dict[tuple[MemoryType, str], MemoryCard] = {}
    for card in cards:
        content = _WHITESPACE.sub(" ", card.content).strip()
        if not content:
            continue
        normalized = (card.memory_type, content.casefold().rstrip(".!?"))
        candidate = MemoryCard(
            memory_id=card.memory_id,
            memory_type=card.memory_type,
            content=content,
            confidence=card.confidence,
            importance=card.importance,
            scope=card.scope,
            source_refs=card.source_refs,
        )
        existing = selected.get(normalized)
        if existing is None or _card_score(candidate) > _card_score(existing):
            selected[normalized] = candidate

    return tuple(
        sorted(
            selected.values(),
            key=lambda card: (
                _TYPE_PRIORITY[card.memory_type],
                -card.importance,
                -card.confidence,
                card.memory_id,
            ),
        )
    )


def _card_score(card: MemoryCard) -> tuple[float, float, str]:
    return (card.confidence, card.importance, card.memory_id)
