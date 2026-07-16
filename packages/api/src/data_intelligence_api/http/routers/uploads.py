"""Corpus upload endpoints."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from data_intelligence_api.infrastructure.config.settings import ApiSettings


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "upload"


def create_uploads_router(settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/backend_qa_flow/upload")
    async def upload_corpus_files(
        conv_uid: str = Form(...),
        files: list[UploadFile] = File(...),
    ) -> dict:
        if not SAFE_ID.fullmatch(conv_uid):
            raise HTTPException(status_code=400, detail="Invalid conversation ID.")
        target_dir = settings.data_corpus_root / ".uploads" / conv_uid
        target_dir.mkdir(parents=True, exist_ok=True)
        stored = []
        for upload in files:
            content = await upload.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Uploaded file is too large.")
            filename = f"{uuid.uuid4().hex[:12]}-{_safe_filename(upload.filename)}"
            destination = target_dir / filename
            destination.write_bytes(content)
            stored.append(
                {
                    "filename": upload.filename,
                    "relative_path": destination.relative_to(
                        settings.data_corpus_root
                    ).as_posix(),
                    "size": len(content),
                }
            )
        return {"success": True, "data": {"files": stored}}

    return router
