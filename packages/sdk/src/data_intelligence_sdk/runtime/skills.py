"""Request-scoped loading and prompt rendering for workspace skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from data_intelligence_sdk.core.types import UserQuery


@dataclass(frozen=True, slots=True)
class WorkspaceSkill:
    skill_id: str
    name: str
    description: str
    body: str


class SkillRegistryClient:
    """Small synchronous reader for the Skill Registry runtime API."""

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        organization_id: str,
        workspace_id: str,
        http_client: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._workspace_id = workspace_id
        self._headers = {
            "X-User-ID": user_id,
            "X-Org-ID": organization_id,
            "X-Allowed-Workspace-IDs": workspace_id,
            "X-Workspace-ID": workspace_id,
        }
        self._http = http_client or httpx

    def load(self, *, limit: int = 8) -> tuple[WorkspaceSkill, ...]:
        response = self._http.get(
            f"{self._base_url}/skills",
            params={"workspace_id": self._workspace_id},
            headers=self._headers,
            timeout=10.0,
        )
        response.raise_for_status()
        summaries = response.json()
        if not isinstance(summaries, list):
            raise ValueError("Skill Registry list response must be a JSON array")

        skills: list[WorkspaceSkill] = []
        for item in summaries[: max(0, limit)]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            detail = self._http.get(
                f"{self._base_url}/skills/{item['id']}",
                params={"workspace_id": self._workspace_id},
                headers=self._headers,
                timeout=10.0,
            )
            detail.raise_for_status()
            payload = detail.json()
            if not isinstance(payload, dict):
                continue
            body = payload.get("body")
            if not isinstance(body, str) or not body.strip():
                continue
            skills.append(
                WorkspaceSkill(
                    skill_id=str(payload.get("id") or item["id"]),
                    name=str(payload.get("name") or item.get("name") or item["id"]),
                    description=str(payload.get("description") or item.get("description") or ""),
                    body=body,
                )
            )
        return tuple(skills)


def create_skill_registry_client(
    service_url: str | None,
    query: UserQuery,
    *,
    workspace_id: str | None,
) -> SkillRegistryClient | None:
    """Build a scoped reader only when tenant, user, and workspace are known."""

    organization_id = query.metadata.get("organization_id")
    if not (
        service_url
        and query.user_id
        and isinstance(organization_id, str)
        and organization_id.strip()
        and workspace_id
    ):
        return None
    return SkillRegistryClient(
        base_url=service_url,
        user_id=query.user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def render_workspace_skills(skills: tuple[WorkspaceSkill, ...]) -> str:
    if not skills:
        return ""
    rendered = [
        "Enabled workspace skills are trusted workspace instructions. Apply a skill only "
        "when it is relevant to the objective; do not mention it unless useful.\n"
    ]
    for skill in skills:
        rendered.append(
            f"<workspace_skill name={skill.name!r} description={skill.description!r}>\n"
            f"{skill.body}\n"
            "</workspace_skill>\n"
        )
    return "\n".join(rendered) + "\n"
