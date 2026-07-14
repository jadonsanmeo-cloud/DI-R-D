"""Thread-safe messages and SSE encoding for pipeline responses."""

from __future__ import annotations

import json
import queue
from dataclasses import dataclass
from typing import Any, Iterator

from data_intelligence_sdk.core.types import FinalResponse
from data_intelligence_sdk.runtime.logger import RuntimeLogger


@dataclass(frozen=True, slots=True)
class PipelineLogMessage:
    event: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowCompletedMessage:
    response: FinalResponse


@dataclass(frozen=True, slots=True)
class WorkflowResultMessage:
    result: object


@dataclass(frozen=True, slots=True)
class WorkflowFailedMessage:
    code: str
    message: str


class QueueRuntimeLogger(RuntimeLogger):
    def __init__(self, messages: queue.Queue[object]) -> None:
        self.messages = messages

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.messages.put(PipelineLogMessage(event=event, payload=payload or {}))


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
    )
    return f"event: {event}\ndata: {encoded}\n\n"


def chunk_text(text: str, chunk_size: int = 128) -> Iterator[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]
