from fastapi import APIRouter

from data_intelligence_api.application.runtime_capabilities import (
    method_hub_available,
)
from data_intelligence_api.http.schemas.responses import (
    MethodHubCapabilityResponse,
    RuntimeCapabilitiesResponse,
)
from data_intelligence_api.infrastructure.config.settings import ApiSettings


def create_runtime_capabilities_router(settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/v1/runtime-capabilities",
        response_model=RuntimeCapabilitiesResponse,
    )
    def get_runtime_capabilities() -> RuntimeCapabilitiesResponse:
        return RuntimeCapabilitiesResponse(
            method_hub=MethodHubCapabilityResponse(
                default_enabled=settings.method_hub_default_enabled,
                available=method_hub_available(settings.method_hub_endpoint),
            )
        )

    return router

