"""Artifact storage boundary."""


class ArtifactStore:
    """Records files or objects produced during execution."""

    def add(self, artifact: str) -> None:
        raise NotImplementedError("Artifact storage is not part of the base scaffold.")
