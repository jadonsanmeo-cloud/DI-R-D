"""Request-scoped sandbox services shared by all engines."""

from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession

RESULT_MARKER = "__AXIOM_RESULT__"
MAX_COMMAND_LENGTH = 262_144
DEFAULT_NATURAL_RECORD_SHAPES = (
    "table_rows",
    "spreadsheet_rows",
    "document_pages",
    "text_chunks",
    "metadata_records",
)


@dataclass(frozen=True, slots=True)
class SandboxEnvironment:
    """Capabilities and access policy of one request-scoped sandbox."""

    contract_version: str = "1.0"
    runtime: str = "python"
    runtime_version: str | None = None
    dependency_management: str = "axiom_sandbox_service"
    available_packages: tuple[str, ...] = ()
    network_access: bool = False
    source_access: str = "read_only"
    materialization_format: str = "json_serializable"
    natural_record_shapes: tuple[str, ...] = DEFAULT_NATURAL_RECORD_SHAPES

    def to_prompt_payload(self) -> dict[str, Any]:
        """Return the serializable environment contract exposed to agents."""

        return {
            "contract_version": self.contract_version,
            "runtime": self.runtime,
            "runtime_version": self.runtime_version,
            "dependency_management": self.dependency_management,
            "available_packages": list(self.available_packages),
            "network_access": self.network_access,
            "source_access": self.source_access,
            "materialization_contract": {
                "format": self.materialization_format,
                "natural_record_shapes": list(self.natural_record_shapes),
            },
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "SandboxEnvironment":
        """Normalize a service capability payload into the versioned contract."""

        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="json")
            elif hasattr(payload, "__dict__"):
                payload = vars(payload)
            else:
                return cls()
        materialization = payload.get("materialization_contract", {})
        if not isinstance(materialization, dict):
            materialization = {}
        packages = payload.get("available_packages", ())
        if isinstance(packages, str):
            packages = [packages]
        shapes = materialization.get(
            "natural_record_shapes",
            payload.get("natural_record_shapes", DEFAULT_NATURAL_RECORD_SHAPES),
        )
        if isinstance(shapes, str):
            shapes = [shapes]
        runtime_version = payload.get("runtime_version") or payload.get(
            "python_version"
        )
        return cls(
            contract_version=str(payload.get("contract_version", "1.0")),
            runtime=str(payload.get("runtime", "python")),
            runtime_version=(
                str(runtime_version) if runtime_version is not None else None
            ),
            dependency_management=str(
                payload.get("dependency_management", "axiom_sandbox_service")
            ),
            available_packages=tuple(str(item) for item in packages or ()),
            network_access=bool(payload.get("network_access", False)),
            source_access=str(payload.get("source_access", "read_only")),
            materialization_format=str(
                materialization.get(
                    "format",
                    payload.get("materialization_format", "json_serializable"),
                )
            ),
            natural_record_shapes=tuple(str(item) for item in shapes or ()),
        )


class SandboxSessionProvider(Protocol):
    """Creates one shared sandbox session for one pipeline request."""

    def open(
        self,
        corpus_package: DataCorpusPackage,
    ) -> AbstractContextManager["EngineSandboxSession"]:
        """Open a staged request sandbox and clean it up on exit."""


@dataclass(slots=True)
class EngineSandboxSession:
    """One staged AXIOM sandbox shared by all engines in a request."""

    sandbox: object
    source_paths: dict[str, str] = field(default_factory=dict)
    environment: SandboxEnvironment = field(default_factory=SandboxEnvironment)
    sandbox_factory: Callable[[], object] | None = None
    staged_files: dict[str, bytes] = field(default_factory=dict)
    _reprovision_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
    )

    def write(self, path: str, content: bytes | str) -> None:
        """Stage a file and retain it for transparent sandbox reprovisioning."""

        payload = content.encode() if isinstance(content, str) else content
        self.sandbox.write(path, payload)
        self.staged_files[path] = payload

    def reprovision(self) -> bool:
        """Replace a failed request sandbox and restore all staged files."""

        if self.sandbox_factory is None:
            return False
        with self._reprovision_lock:
            try:
                record = self.sandbox.refresh()
                status = getattr(record, "status", None)
                status_text = str(getattr(status, "value", status) or "").lower()
                if status_text == "running":
                    return True
            except Exception:
                pass
            previous = self.sandbox
            replacement = self.sandbox_factory()
            replacement.wait_until_ready()
            for path, payload in self.staged_files.items():
                replacement.write(path, payload)
            self.sandbox = replacement
            try:
                previous.delete()
            except Exception:
                pass
            return True

    def execute_python(
        self,
        code: str,
        run_artifact: RunArtifactSession,
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Persist and execute one generated Python source attempt."""

        if not code.strip():
            raise ValueError("Generated Python source cannot be empty.")
        runner = self._runner_source(code)
        if len(runner) > MAX_COMMAND_LENGTH:
            raise ValueError(
                f"Generated Python command exceeds {MAX_COMMAND_LENGTH} characters."
            )
        attempt = run_artifact.record_code_attempt(code)
        command = self.sandbox.run(
            runner,
            timeout_seconds=timeout_seconds,
            wait=True,
        )
        stdout = str(getattr(command, "stdout", "") or "")
        stderr = str(getattr(command, "stderr", "") or "")
        success = bool(getattr(command, "success", False))
        observation = {
            "success": success,
            "status": self._status_text(getattr(command, "status", None)),
            "result": self._parse_result(stdout) if success else None,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": getattr(command, "exit_code", None),
            "sandbox_id": str(getattr(self.sandbox, "id", "")),
            "command_id": str(getattr(command, "id", "")),
            "attempt": attempt.attempt,
            "code_artifact_ref": attempt.artifact_ref,
            "execution_artifact_ref": run_artifact.execution_ref(attempt),
            "method_calls": [
                (
                    call.model_dump(mode="json")
                    if hasattr(call, "model_dump")
                    else dict(call)
                )
                for call in getattr(command, "method_calls", [])
            ],
        }
        run_artifact.record_execution(attempt, observation)
        return observation

    @staticmethod
    def _runner_source(code: str) -> str:
        source_literal = json.dumps(code, ensure_ascii=True)
        return (
            "import json as __axiom_json\n"
            f"__axiom_source = {source_literal}\n"
            "__axiom_namespace = {'__name__': '__axiom_generated__'}\n"
            "exec(compile(__axiom_source, '<generated-analysis>', 'exec'), "
            "__axiom_namespace)\n"
            "__axiom_result = __axiom_namespace.get('result')\n"
            f"print({RESULT_MARKER!r} + __axiom_json.dumps(__axiom_result, default=str))\n"
        )

    @staticmethod
    def _parse_result(stdout: str) -> Any:
        for line in reversed(stdout.splitlines()):
            if line.startswith(RESULT_MARKER):
                return json.loads(line.removeprefix(RESULT_MARKER))
        return None

    @staticmethod
    def _status_text(status: object) -> str:
        value = getattr(status, "value", status)
        return str(value or "unknown")
