from data_intelligence_sdk.memory import MemoryCard, MemoryContext, MemoryScope


def _card(
    memory_type: str,
    content: str,
    *,
    memory_id: str = "memory-id",
    confidence: float = 0.9,
    importance: float = 0.8,
) -> MemoryCard:
    return MemoryCard(
        memory_id=memory_id,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        importance=importance,
        scope=MemoryScope(tenant_id="test-org", user_id="local-dev-user"),
        source_refs=({"document_id": "private-source"},),
    )


def test_orchestrator_view_selects_identity_memory_only() -> None:
    context = MemoryContext(
        cards=(
            _card("preference", "Use concise Markdown."),
            _card("constraint", "Do not expose PII."),
            _card("procedure", "Retrieve related reports."),
        )
    )

    assert [item.memory_type for item in context.for_orchestrator()] == [
        "preference",
        "constraint",
    ]


def test_spec_builder_view_selects_deep_memory_without_second_load() -> None:
    context = MemoryContext(
        cards=(
            _card("profile", "The user is an analyst."),
            _card("semantic", "Reporting year differs from publication year."),
            _card("procedure", "Normalize reporting periods."),
        )
    )

    assert [item.memory_type for item in context.for_spec_builder()] == [
        "semantic",
        "procedure",
    ]


def test_rendered_memory_is_bounded_and_does_not_include_source_metadata() -> None:
    context = MemoryContext(cards=(_card("preference", "Use Markdown."),))

    rendered = context.render(target="orchestrator", max_tokens=200)

    assert rendered == "Preferences:\n- Use Markdown."
    assert "memory-id" not in rendered
    assert "private-source" not in rendered


def test_render_skips_blank_and_oversized_cards_without_losing_later_cards() -> None:
    context = MemoryContext(
        cards=(
            _card("preference", "   "),
            _card("profile", "x" * 500),
            _card("constraint", "Keep answers factual."),
        )
    )

    assert context.render(target="orchestrator", max_tokens=20) == (
        "Constraints:\n- Keep answers factual."
    )


def test_render_deduplicates_and_prioritizes_protected_memory_types() -> None:
    context = MemoryContext(
        cards=(
            _card("profile", "The user is an analyst.", memory_id="profile"),
            _card(
                "preference",
                " Prefer concise Markdown. ",
                memory_id="preference-low",
                confidence=0.6,
            ),
            _card(
                "constraint",
                "Do not expose PII.",
                memory_id="constraint",
                importance=0.95,
            ),
            _card(
                "preference",
                "prefer   concise markdown.",
                memory_id="preference-high",
                confidence=0.95,
            ),
        )
    )

    rendered = context.render(target="orchestrator", max_tokens=100)

    assert rendered == (
        "Constraints:\n"
        "- Do not expose PII.\n\n"
        "Preferences:\n"
        "- prefer concise markdown.\n\n"
        "Profile:\n"
        "- The user is an analyst."
    )
    assert rendered.count("concise") == 1


def test_render_respects_estimated_token_budget() -> None:
    context = MemoryContext(
        cards=(
            _card("constraint", "Keep answers factual."),
            _card("preference", "Use concise Markdown."),
        )
    )

    rendered = context.render(target="orchestrator", max_tokens=10)

    assert rendered == "Constraints:\n- Keep answers factual."
    assert len(rendered) <= 40
