"""Response models for runtime capability discovery."""

from pydantic import BaseModel


class MethodHubCapabilityResponse(BaseModel):
    default_enabled: bool
    available: bool


class RuntimeCapabilitiesResponse(BaseModel):
    method_hub: MethodHubCapabilityResponse
