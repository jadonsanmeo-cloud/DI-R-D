"""Request-scoped selected workspace file authorization."""

from __future__ import annotations

from dataclasses import dataclass


class SelectedFilesScopeError(ValueError):
    """Raised when a Method Hub call cannot be limited to selected files."""


@dataclass(frozen=True, slots=True)
class SelectedFilesScope:
    """The document IDs an execution is allowed to retrieve."""

    document_ids: tuple[str, ...]
