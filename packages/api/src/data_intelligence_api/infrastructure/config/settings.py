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
    pipeline_timeout_seconds: float
    database_url: str | None = None
    spec_confirmation_ttl_seconds: int = 86400
    max_spec_revision_rounds: int = 5
    max_upload_bytes: int = 50 * 1024 * 1024
    model_config_path: Path | None = None
    artifact_root: Path = Path("artifacts")
    method_hub_default_enabled: bool = False
    method_hub_endpoint: str = "http://localhost:8000/mcp"
    openai_compatible_base_url: str = "http://localhost:20128/v1"
    openai_compatible_api_key: str = ""
    openai_compatible_model: str = ""
    default_organization_id: str = "test-org"
    gen_report_api_url: str = "http://host.docker.internal:8011"
    gen_report_public_url: str = "http://localhost:8011"

    @classmethod
    def from_env(cls) -> "ApiSettings":
        model_config_value = os.getenv("MODEL_CONFIG_PATH")
        model_config_path = Path(model_config_value) if model_config_value else None
        config_manager = ConfigManager(model_config_path)
        payload = config_manager.load()
        method_hub = config_manager.method_hub_settings()
        openrouter = config_manager.openrouter_settings()
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

        timeout = float(
            os.getenv(
                "PIPELINE_TIMEOUT_SECONDS",
                str(api_payload.get("pipeline_timeout_seconds", 300)),
            )
        )
        if timeout <= 0:
            raise ValueError(
                "PIPELINE_TIMEOUT_SECONDS/api.pipeline_timeout_seconds must be greater than zero."
            )
        ttl = int(
            os.getenv(
                "SPEC_CONFIRMATION_TTL_SECONDS",
                str(api_payload.get("spec_confirmation_ttl_seconds", 86400)),
            )
        )
        if ttl <= 0:
            raise ValueError(
                "SPEC_CONFIRMATION_TTL_SECONDS/api.spec_confirmation_ttl_seconds must be greater than zero."
            )
        max_revisions = int(
            os.getenv(
                "MAX_SPEC_REVISION_ROUNDS",
                str(api_payload.get("max_spec_revision_rounds", 5)),
            )
        )
        if max_revisions <= 0:
            raise ValueError(
                "MAX_SPEC_REVISION_ROUNDS/api.max_spec_revision_rounds must be greater than zero."
            )
        max_upload_bytes = int(api_payload.get("max_upload_bytes", 50 * 1024 * 1024))
        if max_upload_bytes <= 0:
            raise ValueError("api.max_upload_bytes must be greater than zero.")

        return cls(
            data_corpus_root=root,
            cors_origins=origins,
            pipeline_timeout_seconds=timeout,
            database_url=os.getenv("DATABASE_URL") or None,
            spec_confirmation_ttl_seconds=ttl,
            max_spec_revision_rounds=max_revisions,
            max_upload_bytes=max_upload_bytes,
            model_config_path=model_config_path,
            artifact_root=Path(config_manager.artifact_settings().root).resolve(),
            method_hub_default_enabled=method_hub.enabled,
            method_hub_endpoint=method_hub.endpoint,
            openai_compatible_base_url=openrouter.base_url,
            openai_compatible_api_key=openrouter.api_key or "",
            openai_compatible_model=openrouter.model or "",
            default_organization_id=os.getenv(
                "DEFAULT_ORGANIZATION_ID",
                str(api_payload.get("default_organization_id", "test-org")),
            ),
            gen_report_api_url=os.getenv(
                "GEN_REPORT_API_URL",
                str(
                    api_payload.get(
                        "gen_report_api_url",
                        "http://host.docker.internal:8011",
                    )
                ),
            ),
            gen_report_public_url=os.getenv(
                "GEN_REPORT_PUBLIC_URL",
                str(api_payload.get("gen_report_public_url", "http://localhost:8011")),
            ),
        )
