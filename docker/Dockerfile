FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

WORKDIR /workspace/Data-Intelligence-SDK

COPY Data-Intelligence-SDK/pyproject.toml \
     Data-Intelligence-SDK/uv.lock \
     Data-Intelligence-SDK/README.md ./
COPY Data-Intelligence-SDK/packages/sdk/pyproject.toml packages/sdk/pyproject.toml
COPY Data-Intelligence-SDK/packages/api/pyproject.toml packages/api/pyproject.toml
COPY Data-Intelligence-SDK/packages/sdk/src packages/sdk/src
COPY Data-Intelligence-SDK/packages/api/src packages/api/src
COPY AXIOM/packages/axiom-sandbox-client \
     /workspace/AXIOM/packages/axiom-sandbox-client

RUN uv sync --frozen --no-dev --package data-intelligence-api --no-editable

ENV PATH="/workspace/Data-Intelligence-SDK/.venv/bin:$PATH"

WORKDIR /app

COPY --chown=app:app Data-Intelligence-SDK/configs ./configs

RUN mkdir -p /app/data /app/.data/chat /app/artifacts \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["uvicorn", "data_intelligence_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
