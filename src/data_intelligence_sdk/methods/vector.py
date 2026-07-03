"""Postgres/pgvector MethodHub methods."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from data_intelligence_sdk.runtime.method_hub import MethodHub

ConnectionFactory = Callable[[str], Any]

def _normalize_limit(limit: int) -> int:
    return max(1, min(int(limit), 50))

def _parse_vectordb_uri(vectordb: str) -> tuple[str, str]:
    parsed = urlparse(vectordb)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("Vector search currently requires a Postgres/pgvector URI.")
    query = parse_qs(parsed.query)
    schema = query.get("schema", ["vectordb"])[0] or "vectordb"
    dsn = urlunparse(parsed._replace(query=""))
    return dsn, schema

def _default_connection_factory(dsn: str) -> Any:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional install.
        raise RuntimeError(
            "psycopg is required to search Postgres vector DBs. "
            "Install psycopg or pass a connection_factory."
        ) from exc
    return psycopg.connect(dsn)

def _embedding_literal(query_embedding: Sequence[float]) -> str:
    if not query_embedding:
        raise ValueError("query_embedding must contain at least one value.")
    return "[" + ",".join(str(float(value)) for value in query_embedding) + "]"

def _row_to_match(row: Sequence[Any]) -> dict[str, Any]:
    chunk_id, document_id, content, metadata, score = row
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "content": content,
        "metadata": metadata,
        "score": float(score),
    }

def search_vector_chunks(
    vectordb: str,
    query: str,
    query_embedding: list[float] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search persisted document chunks in a Postgres pgvector database."""

    return search_vector_chunks_with_connection(
        vectordb=vectordb,
        query=query,
        query_embedding=query_embedding,
        limit=limit,
        connection_factory=None,
    )

def search_vector_chunks_with_connection(
    vectordb: str,
    query: str,
    query_embedding: list[float] | None = None,
    limit: int = 5,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    """Search document chunks with an injectable DB connection for tests."""

    dsn, schema = _parse_vectordb_uri(vectordb)
    limit = _normalize_limit(limit)
    connect = connection_factory or _default_connection_factory

    with connect(dsn) as connection:
        with connection.cursor() as cursor:
            if query_embedding is not None:
                embedding = _embedding_literal(query_embedding)
                cursor.execute(
                    f"""
                    SELECT chunk_id, document_id, content, metadata,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {schema}.document_chunks
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (embedding, embedding, limit),
                )
                search_mode = "vector"
            else:
                cursor.execute(
                    f"""
                    SELECT chunk_id, document_id, content, metadata,
                           1.0 AS score
                    FROM {schema}.document_chunks
                    WHERE content ILIKE %s
                    ORDER BY chunk_id
                    LIMIT %s
                    """,
                    (f"%{query}%", limit),
                )
                search_mode = "lexical"
            rows = cursor.fetchall()

    return {
        "vectordb": vectordb,
        "query": query,
        "search_mode": search_mode,
        "matches": [_row_to_match(row) for row in rows],
    }

def register_vector_methods(method_hub: MethodHub) -> None:
    """Register vector search methods with capability metadata."""

    method_hub.register(
        "search_vector_chunks",
        search_vector_chunks,
        capability_names=[
            "search_vector_chunks",
            "search_vectordb",
            "semantic_search",
            "retrieve_documents",
            "answer_question",
        ],
        metadata={
            "description": (
                "Search document chunks in a Postgres pgvector source. Use this for "
                "postgresql:// sources with schema=vectordb and questions about "
                "documents, text content, retrieval, summaries, or what the corpus is about."
            )
        },
    )
