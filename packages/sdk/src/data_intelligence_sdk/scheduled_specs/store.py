"""Filesystem persistence for scheduled Markdown specs."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

_SAFE_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class FilesystemScheduledSpecStore:
    """Persist one immutable Markdown spec per document ID."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def exists(self, document_id: str) -> bool:
        return self._destination(document_id).is_file()

    def create(self, document_id: str, markdown: str) -> Path:
        destination = self._destination(document_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.output_dir / f".{document_id}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(markdown, encoding="utf-8")
            os.link(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def _destination(self, document_id: str) -> Path:
        normalized = document_id.strip()
        if (
            not normalized
            or ".." in normalized
            or _SAFE_DOCUMENT_ID.fullmatch(normalized) is None
        ):
            raise ValueError("Document ID is unsafe for filesystem persistence.")
        return self.output_dir / f"{normalized}.md"
