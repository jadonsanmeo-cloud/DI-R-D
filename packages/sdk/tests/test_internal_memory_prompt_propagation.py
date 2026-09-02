from data_intelligence_sdk.internal_memory.context import InternalMemoryContext
from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import UserQuery
from data_intelligence_sdk.runtime.config import ConfigManager


def test_internal_memory_context_renders_frozen_snapshot_for_agent_prompt() -> None:
    context = InternalMemoryContext(
        user_markdown="Prefers Vietnamese.",
        memory_markdown="Uses rtk for shell commands.",
    )

    rendered = context.render()

    assert "Prefers Vietnamese." in rendered
    assert "Uses rtk for shell commands." in rendered


def test_internal_memory_context_normalizes_untrusted_runtime_payload() -> None:
    context = InternalMemoryContext.from_payload(
        {
            "user_markdown": "Prefers Vietnamese.",
            "memory_markdown": 42,
            "external_memories": ["Relevant fact", 7],
        }
    )

    assert context.user_markdown == "Prefers Vietnamese."
    assert context.memory_markdown == ""
    assert context.external_memories == ("Relevant fact",)


def test_internal_memory_context_ignores_non_mapping_payload() -> None:
    assert (
        InternalMemoryContext.from_payload("not a mapping") == InternalMemoryContext()
    )


def test_pipeline_builds_request_scoped_memory_client_when_enabled() -> None:
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=object(),
        internal_memory_service_url="http://intelligence/api/v1",
    )

    client = pipeline._internal_memory_client(
        UserQuery(
            text="Revenue",
            user_id="user-a",
            metadata={"organization_id": "tenant-a"},
        )
    )

    assert client is not None


def test_internal_memory_config_defaults_to_intelligence_service_api() -> None:
    settings = ConfigManager("missing-config.toml").internal_memory_service_settings()

    assert settings.endpoint == "http://intelligence-service:8006/api/v1"


def test_pipeline_builds_workspace_skill_client_when_scope_is_complete() -> None:
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=object(),
        workspace_id="workspace-a",
        skill_registry_service_url="http://skills",
    )

    client = pipeline._skill_registry_client(
        UserQuery(
            text="Revenue",
            user_id="user-a",
            metadata={"organization_id": "tenant-a"},
        )
    )

    assert client is not None


def test_skill_registry_config_defaults_to_registry_service() -> None:
    settings = ConfigManager("missing-config.toml").skill_registry_service_settings()

    assert settings.endpoint == "http://skill-registry:9000"
