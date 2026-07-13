"""Runtime logging boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


class RuntimeLogger:
    """Collects structured runtime events."""

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        raise NotImplementedError("Logging backend is not part of the base scaffold.")


class InMemoryRuntimeLogger(RuntimeLogger):
    """Stores structured runtime events in memory for tests and inspection."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append((event, payload or {}))


class ConsoleRuntimeLogger(RuntimeLogger):
    """Writes structured runtime events as JSON lines."""

    def __init__(self, stream: object | None = None) -> None:
        self.stream = stream or sys.stderr

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        print(
            json.dumps(
                {"event": event, "payload": payload or {}},
                ensure_ascii=True,
                default=str,
            ),
            file=self.stream,  # type: ignore[arg-type]
        )


class FileRuntimeLogger(RuntimeLogger):
    """Appends structured runtime events as JSON lines to a file."""

    def __init__(self, path: str | Path = "logs/pipeline.log") -> None:
        self.path = Path(path)

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(
                    {"event": event, "payload": payload or {}},
                    ensure_ascii=True,
                    default=str,
                )
                + "\n"
            )
