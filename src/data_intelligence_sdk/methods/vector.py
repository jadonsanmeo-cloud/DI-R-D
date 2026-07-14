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


def inspect_vector_chunks(vectordb: str, limit: int = 20) -> dict[str, Any]:
    """Read a bounded sample of persisted document chunks."""

    return inspect_vector_chunks_with_connection(
        vectordb,
        limit=limit,
        connection_factory=None,
    )


def inspect_vector_chunks_with_connection(
    vectordb: str,
    limit: int = 20,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    dsn, schema = _parse_vectordb_uri(vectordb)
    limit = _normalize_limit(limit)
    connect = connection_factory or _default_connection_factory
    with connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT chunk_id, document_id, content, metadata, 1.0 AS score
                FROM {schema}.document_chunks
                ORDER BY chunk_id
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    return {
        "vectordb": vectordb,
        "row_count": len(rows),
        "rows": [_row_to_match(row) for row in rows],
    }


def get_vector_stats(vectordb: str) -> dict[str, Any]:
    """Return exact chunk and document counts for a pgvector collection."""

    return get_vector_stats_with_connection(
        vectordb,
        connection_factory=None,
    )


def get_vector_stats_with_connection(
    vectordb: str,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    dsn, schema = _parse_vectordb_uri(vectordb)
    connect = connection_factory or _default_connection_factory
    with connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS chunk_count,
                       COUNT(DISTINCT document_id) AS document_count
                FROM {schema}.document_chunks
                """,
                (),
            )
            row = cursor.fetchone()
    chunk_count = int(row[0]) if row else 0
    document_count = int(row[1]) if row else 0
    return {
        "vectordb": vectordb,
        "collection": f"{schema}.document_chunks",
        "chunk_count": chunk_count,
        "document_count": document_count,
        "rows": [
            {
                "collection": f"{schema}.document_chunks",
                "chunk_count": chunk_count,
                "document_count": document_count,
            }
        ],
    }


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
        tags=["vector", "retrieval", "semantic_search"],
        status="stable",
        priority=95,
        source=__name__,
        metadata={
            "description": (
                "Search document chunks in a Postgres pgvector source. Use this for "
                "postgresql:// sources with schema=vectordb and questions about "
                "documents, text content, retrieval, summaries, or what the corpus is about."
            ),
            "category": "vector",
            "deterministic": True,
            "side_effects": False,
            "use_when": [
                "You need relevant document chunks from a vector store.",
                "The user asks what a document corpus is about or requests semantic retrieval.",
            ],
            "do_not_use_when": [
                "You are querying tabular CSV data.",
            ],
        },
    )
    method_hub.register(
        "inspect_vector_chunks",
        inspect_vector_chunks,
        capability_names=[
            "inspect_data",
            "inspect_vectordb",
            "retrieve_documents",
            "summarize_corpus",
        ],
        metadata={
            "description": (
                "Read a bounded sample of document chunks from a PostgreSQL/pgvector "
                "source for corpus summaries and inspection."
            )
        },
    )
    method_hub.register(
        "get_vector_stats",
        get_vector_stats,
        capability_names=["inspect_data", "aggregate_data", "summarize_corpus"],
        metadata={
            "description": (
                "Return exact chunk and distinct-document counts for a pgvector corpus."
            )
        },
    )
