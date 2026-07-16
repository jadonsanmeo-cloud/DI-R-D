"""Filesystem-backed runtime artifact storage."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from uuid import UUID, uuid4

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
)


class ArtifactPersistenceError(RuntimeError):
    """Raised when mandatory runtime trace data cannot be persisted."""


@dataclass(frozen=True, slots=True)
class CodeAttemptArtifact:
    """Immutable source artifact for one generated-code attempt."""

    attempt: int
    path: Path
    artifact_ref: str


@dataclass(slots=True)
class RunArtifactSession:
    """Filesystem artifact bundle associated with one pipeline invocation."""

    run_id: str
    root: Path
    _manifest: dict[str, Any] = field(default_factory=dict)
    _attempts: dict[int, CodeAttemptArtifact] = field(default_factory=dict)
    _attempt_count: int = 0
    _event_count: int = 0
    _terminal_status: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        root: Path,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
    ) -> "RunArtifactSession":
        try:
            root.mkdir(parents=True, exist_ok=False)
            (root / "code").mkdir()
            (root / "executions").mkdir()
        except Exception as exc:
            raise ArtifactPersistenceError(
                f"Could not create artifact bundle {run_id}: {exc}"
            ) from exc

        session = cls(run_id=run_id, root=root)
        session._manifest = {
            "run_id": run_id,
            "status": "running",
            "query": {
                "text": query.text,
                "metadata": _redact(query.metadata),
            },
            "sources": list(corpus_package.sources),
            "attempts": [],
            "event_count": 0,
            "events_artifact_ref": session.events_artifact_ref,
            "engine": None,
            "final_answer": None,
            "failure_phase": None,
            "error": None,
            "created_at": _utc_now(),
            "completed_at": None,
        }
        session._write_manifest()
        session.record_event(
            phase="run",
            event_type="run.created",
            payload={"created_at": session._manifest["created_at"]},
        )
        session.record_event(
            phase="corpus",
            event_type="corpus.registered",
            payload={
                "sources": list(corpus_package.sources),
                "schemas": corpus_package.schemas,
                "metadata": corpus_package.metadata,
            },
        )
        session.record_event(
            phase="query",
            event_type="query.received",
            payload={
                "text": query.text,
                "user_id": query.user_id,
                "session_id": query.session_id,
                "metadata": query.metadata,
            },
        )
        return session

    @property
    def artifact_ref(self) -> str:
        return f"artifact://{self.run_id}"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def events_artifact_ref(self) -> str:
        return f"artifact://{self.run_id}/events.jsonl"

    def record_event(
        self,
        *,
        phase: str,
        event_type: str,
        status: str = "completed",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one ordered, redacted event to this run."""

        self._ensure_running()
        sequence = self._event_count + 1
        event = {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "run_id": self.run_id,
            "sequence": sequence,
            "timestamp": _utc_now(),
            "phase": phase,
            "event_type": event_type,
            "status": status,
            "payload": _redact(payload or {}),
        }
        self._append_jsonl(self.events_path, event)
        self._event_count = sequence
        self._manifest["event_count"] = sequence
        self._write_manifest()
        return event

    def record_code_attempt(self, code: str) -> CodeAttemptArtifact:
        self._ensure_running()
        self._attempt_count += 1
        attempt = self._attempt_count
        path = self.root / "code" / f"attempt-{attempt:03d}.py"
        self._write_text_atomic(path, code)
        artifact = CodeAttemptArtifact(
            attempt=attempt,
            path=path,
            artifact_ref=f"artifact://{self.run_id}/code/{path.name}",
        )
        self._attempts[attempt] = artifact
        self._manifest["attempts"].append(
            {
                "attempt": attempt,
                "code_artifact_ref": artifact.artifact_ref,
                "execution_artifact_ref": None,
                "success": None,
            }
        )
        self._write_manifest()
        return artifact

    def execution_ref(self, attempt: CodeAttemptArtifact) -> str:
        self._owned_attempt(attempt)
        return (
            f"artifact://{self.run_id}/executions/"
            f"attempt-{attempt.attempt:03d}.json"
        )

    def record_execution(
        self,
        attempt: CodeAttemptArtifact,
        observation: dict[str, Any],
    ) -> str:
        self._ensure_running()
        self._owned_attempt(attempt)
        path = self.root / "executions" / f"attempt-{attempt.attempt:03d}.json"
        execution_ref = self.execution_ref(attempt)
        persisted_observation = {
            **_redact(observation),
            "execution_artifact_ref": execution_ref,
        }
        self._write_json_atomic(path, persisted_observation)
        manifest_attempt = next(
            item
            for item in self._manifest["attempts"]
            if item["attempt"] == attempt.attempt
        )
        manifest_attempt.update(
            {
                "execution_artifact_ref": execution_ref,
                "success": bool(observation.get("success")),
            }
        )
        self._write_manifest()
        return execution_ref

    def finalize(
        self,
        *,
        status: Literal["completed", "failed"],
        engine_name: str | None = None,
        final_answer: str | None = None,
        failure_phase: str | None = None,
        error: str | None = None,
    ) -> str:
        if self._terminal_status is not None and self._terminal_status != status:
            raise ArtifactPersistenceError(
                f"Run {self.run_id} is already finalized as "
                f"{self._terminal_status}."
            )
        if self._terminal_status == status:
            return self.artifact_ref

        terminal_event = "run.completed" if status == "completed" else "run.failed"
        self.record_event(
            phase="run",
            event_type=terminal_event,
            status=status,
            payload={
                "engine_name": engine_name,
                "failure_phase": failure_phase,
                "error": error,
            },
        )
        self._manifest.update(
            {
                "status": status,
                "engine": engine_name,
                "final_answer": final_answer,
                "failure_phase": failure_phase,
                "error": error,
                "completed_at": _utc_now(),
            }
        )
        self._write_manifest()
        self._terminal_status = status
        return self.artifact_ref

    def _owned_attempt(self, attempt: CodeAttemptArtifact) -> None:
        if self._attempts.get(attempt.attempt) != attempt:
            raise ArtifactPersistenceError(
                "Execution attempt is not owned by this run."
            )

    def _ensure_running(self) -> None:
        if self._terminal_status is not None:
            raise ArtifactPersistenceError(
                f"Run {self.run_id} is already finalized."
            )

    def _write_manifest(self) -> None:
        self._write_json_atomic(self.manifest_path, self._manifest)

    def _write_json_atomic(
        self,
        destination: Path,
        payload: dict[str, Any],
    ) -> None:
        self._write_text_atomic(
            destination,
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
        )

    def _append_jsonl(
        self,
        destination: Path,
        payload: dict[str, Any],
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        default=str,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            raise ArtifactPersistenceError(
                f"Could not append artifact event: {exc}"
            ) from exc

    def _write_text_atomic(self, destination: Path, content: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=destination.parent,
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, destination)
        except Exception as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise ArtifactPersistenceError(
                f"Could not persist artifact {destination.name}: {exc}"
            ) from exc


class ArtifactStore:
    """Compatibility boundary for legacy artifact references."""

    def add(self, artifact: str) -> None:
        raise NotImplementedError("Artifact storage is not configured.")


class FilesystemArtifactStore(ArtifactStore):
    """Creates one local filesystem artifact bundle per pipeline run."""

    def __init__(self, root: str | Path = "artifacts") -> None:
        self.root = Path(root).resolve()

    def create_run(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
    ) -> RunArtifactSession:
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid4())
        return RunArtifactSession.create(
            run_id=run_id,
            root=self.root / run_id,
            query=query,
            corpus_package=corpus_package,
        )

    def open_run(self, run_id: str) -> RunArtifactSession:
        """Reopen a non-terminal artifact bundle after workflow persistence."""

        try:
            normalized_run_id = str(UUID(run_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ArtifactPersistenceError("Invalid runtime artifact ID.") from exc
        root = (self.root / normalized_run_id).resolve()
        if root.parent != self.root:
            raise ArtifactPersistenceError("Runtime artifact path escaped its root.")
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ArtifactPersistenceError(
                f"Could not reopen artifact bundle {normalized_run_id}: {exc}"
            ) from exc
        event_count = _recover_event_sequence(
            root / "events.jsonl",
            normalized_run_id,
        )
        session = RunArtifactSession(
            run_id=normalized_run_id,
            root=root,
            _manifest=manifest,
            _event_count=event_count,
        )
        session._manifest.update(
            {
                "event_count": event_count,
                "events_artifact_ref": session.events_artifact_ref,
            }
        )
        session._write_manifest()
        for item in manifest.get("attempts", []):
            attempt_number = int(item["attempt"])
            path = root / "code" / f"attempt-{attempt_number:03d}.py"
            session._attempts[attempt_number] = CodeAttemptArtifact(
                attempt=attempt_number,
                path=path,
                artifact_ref=str(item["code_artifact_ref"]),
            )
            session._attempt_count = max(session._attempt_count, attempt_number)
        status = str(manifest.get("status", "running"))
        if status in {"completed", "failed"}:
            session._terminal_status = status
        return session

    def add(self, artifact: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        legacy_path = self.root / "legacy-artifacts.log"
        try:
            with legacy_path.open("a", encoding="utf-8") as handle:
                handle.write(artifact + "\n")
        except Exception as exc:
            raise ArtifactPersistenceError(
                f"Could not append legacy artifact reference: {exc}"
            ) from exc


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _recover_event_sequence(path: Path, run_id: str) -> int:
    if not path.exists():
        return 0
    try:
        content = path.read_bytes()
        if content and not content.endswith(b"\n"):
            final_newline = content.rfind(b"\n")
            valid_size = final_newline + 1 if final_newline >= 0 else 0
            with path.open("r+b") as handle:
                handle.truncate(valid_size)
            content = content[:valid_size]
        expected = 0
        for line in content.splitlines():
            event = json.loads(line.decode("utf-8"))
            sequence = int(event["sequence"])
            if event.get("run_id") != run_id or sequence != expected + 1:
                raise ValueError("Artifact event sequence is invalid.")
            expected = sequence
        return expected
    except Exception as exc:
        raise ArtifactPersistenceError(
            f"Could not recover artifact event sequence: {exc}"
        ) from exc


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value
