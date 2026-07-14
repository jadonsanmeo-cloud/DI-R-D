"""Health endpoint."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from data_intelligence_api.application.ports.run_repository import RunRepository


def create_health_router(run_repository: RunRepository) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        if not run_repository.check_ready():
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    return router
