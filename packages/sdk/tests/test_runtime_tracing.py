import langsmith

from data_intelligence_sdk.runtime import tracing
from data_intelligence_sdk.runtime.tracing import langsmith_tracing_enabled


def test_langsmith_tracing_uses_current_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    assert langsmith_tracing_enabled() is True


def test_langsmith_tracing_keeps_legacy_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert langsmith_tracing_enabled() is True


def test_current_tracing_setting_overrides_legacy_setting(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert langsmith_tracing_enabled() is False


def test_traceable_call_uses_current_project_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_traceable(**kwargs):
        captured.update(kwargs)
        return lambda function: function

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "internal-memory-live")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy-project")
    monkeypatch.setattr(langsmith, "traceable", fake_traceable)

    wrapped = tracing.traceable_llm_call(lambda: "ok", name="test-call")

    assert wrapped() == "ok"
    assert captured["project_name"] == "internal-memory-live"
