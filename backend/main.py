"""FastAPI application entry point."""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import ApiSettings
from backend.database import run_migrations
from backend.responses import create_responses_router
from backend.run_store import InMemoryRunStore, PostgresRunStore, RunStore
from backend.workflow import PipelineFactory, default_pipeline_factory


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "upload"


def create_app(
    settings: ApiSettings | None = None,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    run_store: RunStore | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings.from_env()
    resolved_store = run_store or (
        PostgresRunStore(resolved_settings.database_url)
        if resolved_settings.database_url
        else InMemoryRunStore()
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if resolved_settings.database_url:
            run_migrations(resolved_settings.database_url)
        yield

    app = FastAPI(
        title="Data Intelligence Responses API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Confirmation-Token"],
    )

    @app.get("/health")
    async def health():
        if not resolved_store.check_ready():
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    @app.post("/api/v1/backend_qa_flow/upload")
    async def upload_corpus_files(
        conv_uid: str = Form(...),
        files: list[UploadFile] = File(...),
    ) -> dict:
        if not SAFE_ID.fullmatch(conv_uid):
            raise HTTPException(status_code=400, detail="Invalid conversation ID.")
        target_dir = resolved_settings.data_corpus_root / ".uploads" / conv_uid
        target_dir.mkdir(parents=True, exist_ok=True)
        stored = []
        for upload in files:
            content = await upload.read(resolved_settings.max_upload_bytes + 1)
            if len(content) > resolved_settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Uploaded file is too large.")
            filename = f"{uuid.uuid4().hex[:12]}-{_safe_filename(upload.filename)}"
            destination = target_dir / filename
            destination.write_bytes(content)
            relative_path = destination.relative_to(
                resolved_settings.data_corpus_root
            ).as_posix()
            stored.append(
                {
                    "filename": upload.filename,
                    "relative_path": relative_path,
                    "size": len(content),
                }
            )
        return {"success": True, "data": {"files": stored}}

    app.include_router(
        create_responses_router(
            resolved_settings,
            pipeline_factory,
            resolved_store,
        )
    )
    return app


app = create_app()
