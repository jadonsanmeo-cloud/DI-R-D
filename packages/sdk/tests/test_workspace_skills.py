from __future__ import annotations

from data_intelligence_sdk.runtime.skills import SkillRegistryClient


class _Response:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/skills"):
            return _Response(
                [
                    {
                        "id": "report-validation",
                        "name": "Report validation",
                        "description": "Validate reports.",
                    }
                ]
            )
        return _Response(
            {
                "id": "report-validation",
                "name": "Report validation",
                "description": "Validate reports.",
                "body": "# Report validation\nCheck totals.",
            }
        )


def test_skill_registry_client_loads_visible_skill_bodies_with_workspace_scope() -> None:
    http = _Http()
    client = SkillRegistryClient(
        base_url="http://skills",
        user_id="user-a",
        organization_id="tenant-a",
        workspace_id="workspace-a",
        http_client=http,
    )

    skills = client.load()

    assert skills[0].name == "Report validation"
    assert skills[0].body == "# Report validation\nCheck totals."
    assert http.calls[0][1]["headers"]["X-Workspace-ID"] == "workspace-a"
    assert http.calls[1][1]["params"] == {"workspace_id": "workspace-a"}
