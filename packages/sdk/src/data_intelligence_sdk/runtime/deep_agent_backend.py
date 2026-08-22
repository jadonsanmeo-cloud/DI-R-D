"""Deep Agents adapter for the shared engine sandbox session."""

from __future__ import annotations

from pathlib import PurePosixPath

from deepagents.backends.protocol import (
    BackendProtocol,
    FileData,
    ReadResult,
    WriteResult,
)

from data_intelligence_sdk.runtime.sandbox import EngineSandboxSession

WORKSPACE_ROOT = PurePosixPath("/workspace")
AGENT_ROOT = WORKSPACE_ROOT / "agent"


class DeepAgentSandboxBackend(BackendProtocol):
    """Expose a shared sandbox through the Deep Agents backend contract."""

    def __init__(self, session: EngineSandboxSession) -> None:
        self.session = session

    def workspace_path(self, file_path: str) -> str:
        """Convert a virtual workspace path to an AXIOM-relative path."""

        path = self._absolute_path(file_path)
        try:
            relative = path.relative_to(WORKSPACE_ROOT)
        except ValueError as exc:
            raise ValueError("Paths must be located under /workspace.") from exc
        if not relative.parts:
            raise ValueError("A file path below /workspace is required.")
        return relative.as_posix()

    def generated_path(self, file_path: str) -> str:
        """Return an AXIOM-relative path restricted to generated agent files."""

        path = self._absolute_path(file_path)
        try:
            path.relative_to(AGENT_ROOT)
        except ValueError as exc:
            raise ValueError(
                "Generated files must be located under /workspace/agent."
            ) from exc
        return self.workspace_path(file_path)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            relative_path = self.generated_path(file_path)
            self.session.write(relative_path, content)
        except Exception as exc:  # noqa: BLE001 - translate sandbox failures for agents
            return WriteResult(error=f"Error writing file '{file_path}': {exc}")
        return WriteResult(path=file_path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        try:
            content = self.session.sandbox.read(self.workspace_path(file_path)).decode(
                "utf-8"
            )
            lines = content.splitlines(keepends=True)
            if offset >= len(lines) and lines:
                return ReadResult(
                    error=(
                        f"Line offset {offset} exceeds file length ({len(lines)} lines)"
                    )
                )
            window = "".join(lines[offset : offset + limit])
        except Exception as exc:  # noqa: BLE001 - translate sandbox failures for agents
            return ReadResult(error=f"Error reading file '{file_path}': {exc}")
        return ReadResult(file_data=FileData(content=window, encoding="utf-8"))

    @staticmethod
    def _absolute_path(file_path: str) -> PurePosixPath:
        path = PurePosixPath(file_path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Sandbox paths must be absolute and cannot traverse parents."
            )
        return path
