FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

COPY --chown=app:app backend ./backend
COPY --chown=app:app examples ./examples
COPY --chown=app:app configs ./configs
COPY --chown=app:app data ./data

RUN mkdir -p /app/.uploads && chown -R app:app /app/.uploads

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
