"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.http.routers.runtime_capabilities import (
    create_runtime_capabilities_router,
)
from data_intelligence_api.http.routers.runtime_operations import (
    create_runtime_operations_router,
)
from data_intelligence_api.http.routers.health import create_health_router
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    default_pipeline_factory,
)


def create_app(
    settings: ApiSettings | None = None,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FastAPI:
    resolved_settings = settings or ApiSettings.from_env()

    app = FastAPI(
        title="Data Intelligence Stateless Runtime API",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Confirmation-Token",
            "user-id",
        ],
    )

    app.include_router(create_health_router())
    app.include_router(create_runtime_capabilities_router(resolved_settings))
    app.include_router(
        create_runtime_operations_router(
            settings=resolved_settings,
            pipeline_factory=pipeline_factory,
        )
    )
    return app


app = create_app()
