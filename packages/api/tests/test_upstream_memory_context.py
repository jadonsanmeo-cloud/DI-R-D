from data_intelligence_api.http.schemas.responses import CreateResponseRequest
from data_intelligence_api.infrastructure.memory import parse_upstream_memory_context


def test_upstream_memory_context_is_converted_to_sdk_cards() -> None:
    request = CreateResponseRequest(
        input="hello",
        memory_context={
            "source": "intelligence-service",
            "cards": [
                {
                    "memory_id": "m1",
                    "memory_type": "preference",
                    "content": "Prefer concise answers.",
                    "confidence": 0.9,
                    "importance": 0.8,
                    "memory_layer": "long_term",
                    "scope": {"tenant_id": "org-1", "user_id": "u-1"},
                }
            ],
        },
    )

    context = parse_upstream_memory_context(request.memory_context)

    assert context.loaded is True
    assert context.mode == "upstream"
    assert context.cards[0].memory_id == "m1"
    assert context.cards[0].scope.tenant_id == "org-1"
