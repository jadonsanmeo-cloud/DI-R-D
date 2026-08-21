"""Environment-backed settings for the FastAPI application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from data_intelligence_sdk.runtime.config import ConfigManager

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


@dataclass(frozen=True, slots=True)
class ApiSettings:
    data_corpus_root: Path
    cors_origins: tuple[str, ...]
    method_hub_default_enabled: bool = False
    method_hub_endpoint: str = "http://localhost:8000/mcp"
    runtime_service_token: str | None = None
    runtime_consumer_service: str = "intelligence-service"
    gen_report_api_url: str = "http://host.docker.internal:8011"
    gen_report_public_url: str | None = None
    memory_enabled: bool = False
    memory_service_url: str = "http://localhost:8005"
    memory_tenant_id: str = "test-org"
    memory_default_user_id: str = "local-dev-user"
    memory_search_limit: int = 20
    memory_timeout_seconds: float = 2.0
    memory_service_token: str = ""

    @classmethod
    def from_env(cls) -> "ApiSettings":
        model_config_value = os.getenv("MODEL_CONFIG_PATH")
        model_config_path = Path(model_config_value) if model_config_value else None
        config_manager = ConfigManager(model_config_path)
        payload = config_manager.load()
        method_hub = config_manager.method_hub_settings()
        api_payload = payload.get("api", {})
        if not isinstance(api_payload, dict):
            raise ValueError("The [api] configuration must be a TOML table.")

        root = Path(os.getenv("DATA_CORPUS_ROOT", Path.cwd())).resolve()
        origins_env = os.getenv("API_CORS_ORIGINS")
        origins_value = (
            origins_env.split(",")
            if origins_env is not None
            else api_payload.get("cors_origins", DEFAULT_CORS_ORIGINS)
        )
        if not isinstance(origins_value, (list, tuple)):
            raise ValueError("api.cors_origins must be an array of origins.")
        origins = tuple(
            str(origin).strip() for origin in origins_value if str(origin).strip()
        )
        if not origins:
            origins = DEFAULT_CORS_ORIGINS

        memory_enabled = _parse_boolean_env("MEMORY_ENABLED", default=False)
        memory_search_limit = int(os.getenv("MEMORY_SEARCH_LIMIT", "20"))
        if memory_search_limit <= 0:
            raise ValueError("MEMORY_SEARCH_LIMIT must be greater than zero.")
        memory_timeout_seconds = float(os.getenv("MEMORY_TIMEOUT_SECONDS", "2"))
        if memory_timeout_seconds <= 0:
            raise ValueError("MEMORY_TIMEOUT_SECONDS must be greater than zero.")
        return cls(
            data_corpus_root=root,
            cors_origins=origins,
            method_hub_default_enabled=method_hub.enabled,
            method_hub_endpoint=method_hub.endpoint,
            runtime_service_token=os.getenv("RUNTIME_SERVICE_TOKEN") or None,
            runtime_consumer_service=os.getenv(
                "RUNTIME_CONSUMER_SERVICE",
                "intelligence-service",
            ),
            gen_report_api_url=os.getenv(
                "GEN_REPORT_API_URL",
                "http://host.docker.internal:8011",
            ),
            gen_report_public_url=os.getenv("GEN_REPORT_PUBLIC_URL") or None,
            memory_enabled=memory_enabled,
            memory_service_url=os.getenv(
                "MEMORY_SERVICE_URL", "http://localhost:8005"
            ),
            memory_tenant_id=os.getenv("MEMORY_TENANT_ID", "test-org"),
            memory_default_user_id=os.getenv(
                "MEMORY_DEFAULT_USER_ID", "local-dev-user"
            ),
            memory_search_limit=memory_search_limit,
            memory_timeout_seconds=memory_timeout_seconds,
            memory_service_token=os.getenv("MEMORY_SERVICE_TOKEN", ""),
        )


def _parse_boolean_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off."
    )
