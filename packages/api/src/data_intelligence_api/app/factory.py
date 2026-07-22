"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.persistence.postgres.migrations import (
    run_migrations,
)
from data_intelligence_api.http.routers.responses import create_responses_router
from data_intelligence_api.http.routers.runtime_capabilities import (
    create_runtime_capabilities_router,
)
from data_intelligence_api.http.routers.health import create_health_router
from data_intelligence_api.http.routers.uploads import create_uploads_router
from data_intelligence_api.application.ports.run_repository import RunRepository
from data_intelligence_api.infrastructure.persistence.memory.run_repository import (
    InMemoryRunRepository,
)
from data_intelligence_api.infrastructure.persistence.postgres.run_repository import (
    PostgresRunRepository,
)
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    default_pipeline_factory,
)


def create_app(
    settings: ApiSettings | None = None,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    run_repository: RunRepository | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings.from_env()
    resolved_repository = run_repository or (
        PostgresRunRepository(resolved_settings.database_url)
        if resolved_settings.database_url
        else InMemoryRunRepository()
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if resolved_settings.database_url:
            run_migrations(resolved_settings.database_url)
        yield

    app = FastAPI(
        title="Data Intelligence Responses API",
        version="0.1.0",
        lifespan=lifespan,
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

    app.include_router(create_health_router(resolved_repository))
    app.include_router(create_uploads_router(resolved_settings))
    app.include_router(create_runtime_capabilities_router(resolved_settings))
    app.include_router(
        create_responses_router(
            resolved_settings,
            pipeline_factory,
            resolved_repository,
        )
    )
    return app


app = create_app()
