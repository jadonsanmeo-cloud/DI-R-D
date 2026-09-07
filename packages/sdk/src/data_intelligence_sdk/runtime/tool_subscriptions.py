"""Read organization binding preferences through the authenticated gateway."""

import os

import httpx

DEFAULT_URL = (
    "http://host.docker.internal:8007/authz-service/api/v1/authz/me/tool-subscriptions"
)


async def registered_tool_names(
    organization_id: str, authorization: str | None
) -> set[str]:
    if not organization_id or not authorization:
        return set()
    url = os.getenv("TOOL_SUBSCRIPTIONS_API_URL", DEFAULT_URL)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers={"Authorization": authorization})
        response.raise_for_status()
        payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("organization_id") != organization_id
    ):
        raise ValueError("Tool subscriptions returned a different organization")
    names = payload.get("tool_names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("Tool subscriptions returned invalid tool names")
    return set(names)
