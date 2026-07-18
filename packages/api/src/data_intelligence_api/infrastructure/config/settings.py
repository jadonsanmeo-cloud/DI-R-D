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
    chat_store_dir: Path = Path(".data/chat")
    openai_compatible_base_url: str = "http://localhost:20128/v1"
    openai_compatible_api_key: str = ""
    openai_compatible_model: str = "cx/gpt-5.5"

    @classmethod
    def from_env(cls) -> "ApiSettings":
        model_config_value = os.getenv("MODEL_CONFIG_PATH")
        model_config_path = Path(model_config_value) if model_config_value else None
        payload = ConfigManager(model_config_path).load()
        api_payload = payload.get("api", {})
        if not isinstance(api_payload, dict):
            raise ValueError("The [api] configuration must be a TOML table.")
        chat_payload = api_payload.get("chat", {})
        if not isinstance(chat_payload, dict):
            raise ValueError("The [api.chat] configuration must be a TOML table.")

        root = Path(os.getenv("DATA_CORPUS_ROOT", Path.cwd())).resolve()
        origins_value = api_payload.get("cors_origins", DEFAULT_CORS_ORIGINS)
        if not isinstance(origins_value, (list, tuple)):
            raise ValueError("api.cors_origins must be an array of origins.")
        origins = tuple(
            str(origin).strip() for origin in origins_value if str(origin).strip()
        )
        if not origins:
            origins = DEFAULT_CORS_ORIGINS

        timeout = float(api_payload.get("pipeline_timeout_seconds", 300))
        if timeout <= 0:
            raise ValueError("api.pipeline_timeout_seconds must be greater than zero.")
        ttl = int(api_payload.get("spec_confirmation_ttl_seconds", 86400))
        if ttl <= 0:
            raise ValueError(
                "api.spec_confirmation_ttl_seconds must be greater than zero."
            )
        max_revisions = int(api_payload.get("max_spec_revision_rounds", 5))
        if max_revisions <= 0:
            raise ValueError("api.max_spec_revision_rounds must be greater than zero.")
        max_upload_bytes = int(api_payload.get("max_upload_bytes", 50 * 1024 * 1024))
        if max_upload_bytes <= 0:
            raise ValueError("api.max_upload_bytes must be greater than zero.")

        chat_api_key_env = str(
            chat_payload.get("api_key_env", "OPENAI_COMPATIBLE_API_KEY")
        ).strip()
        return cls(
            data_corpus_root=root,
            cors_origins=origins,
            pipeline_timeout_seconds=timeout,
            database_url=os.getenv("DATABASE_URL") or None,
            spec_confirmation_ttl_seconds=ttl,
            max_spec_revision_rounds=max_revisions,
            max_upload_bytes=max_upload_bytes,
            model_config_path=model_config_path,
            chat_store_dir=Path(os.getenv("CHAT_STORE_DIR", ".data/chat")),
            openai_compatible_base_url=str(
                chat_payload.get("base_url", "http://localhost:20128/v1")
            ),
            openai_compatible_api_key=(
                os.getenv(chat_api_key_env, "") if chat_api_key_env else ""
            ),
            openai_compatible_model=str(
                chat_payload.get("model", "cx/gpt-5.5")
            ),
        )
