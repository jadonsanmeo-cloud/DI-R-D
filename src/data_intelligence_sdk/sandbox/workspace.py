"""Sandbox workspace boundary."""


class Workspace:
    """Placeholder for isolated execution workspaces."""

    def path_for(self, name: str) -> str:
        raise NotImplementedError("Workspace storage is not part of the base scaffold.")
