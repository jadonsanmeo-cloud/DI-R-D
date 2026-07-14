"""Environment-backed settings for the FastAPI application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def from_env(cls) -> "ApiSettings":
        root = Path(os.getenv("DATA_CORPUS_ROOT", Path.cwd())).resolve()
        origins_value = os.getenv("API_CORS_ORIGINS", "")
        origins = tuple(
            origin.strip()
            for origin in origins_value.split(",")
            if origin.strip()
        ) or DEFAULT_CORS_ORIGINS
        timeout = float(os.getenv("PIPELINE_TIMEOUT_SECONDS", "300"))
        if timeout <= 0:
            raise ValueError("PIPELINE_TIMEOUT_SECONDS must be greater than zero.")
        ttl = int(os.getenv("SPEC_CONFIRMATION_TTL_SECONDS", "86400"))
        if ttl <= 0:
            raise ValueError(
                "SPEC_CONFIRMATION_TTL_SECONDS must be greater than zero."
            )
        max_revisions = int(os.getenv("MAX_SPEC_REVISION_ROUNDS", "5"))
        if max_revisions <= 0:
            raise ValueError("MAX_SPEC_REVISION_ROUNDS must be greater than zero.")
        max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
        if max_upload_bytes <= 0:
            raise ValueError("MAX_UPLOAD_BYTES must be greater than zero.")
        return cls(
            data_corpus_root=root,
            cors_origins=origins,
            pipeline_timeout_seconds=timeout,
            database_url=os.getenv("DATABASE_URL") or None,
            spec_confirmation_ttl_seconds=ttl,
            max_spec_revision_rounds=max_revisions,
            max_upload_bytes=max_upload_bytes,
            model_config_path=(
                Path(model_config_path)
                if (model_config_path := os.getenv("MODEL_CONFIG_PATH"))
                else None
            ),
        )
