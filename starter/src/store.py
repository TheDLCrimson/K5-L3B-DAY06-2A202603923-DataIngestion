"""PostgreSQL + pgvector store: persistent vectors, cosine search, metadata filter in SQL.

One table holds every strategy, keyed by (strategy, chunk_id). There is no ANN
index, so search is an exact sequential scan: results do not depend on index
parameters, and a filter never shrinks the candidate list the way it can with HNSW.
Configuration C searches strategy B's rows with a filter.
"""
import json
from collections.abc import Callable
from typing import Any

import psycopg

from .models import Chunk

FILTERABLE = {"year": "year", "document_id": "document_id", "law_number": "law_number"}


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        self.database_url = database_url
        self.dimensions = dimensions

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, connect_timeout=5)

    def create_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    strategy text NOT NULL,
                    chunk_id text NOT NULL,
                    document_id text NOT NULL,
                    source text NOT NULL,
                    page int NOT NULL,
                    page_end int NOT NULL,
                    year int NOT NULL,
                    law_number text NOT NULL,
                    article int,
                    chapter text,
                    text text NOT NULL,
                    content_hash text NOT NULL,
                    embedding_model text NOT NULL,
                    metadata jsonb NOT NULL,
                    embedding vector({self.dimensions}) NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (strategy, chunk_id)
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_strategy_year ON chunks (strategy, year)")
            stored = conn.execute("""
                SELECT atttypmod FROM pg_attribute
                WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'""").fetchone()[0]
            if stored != self.dimensions:
                raise ValueError(f"table stores {stored}-d vectors, model gives {self.dimensions}-d")

    def existing(self, strategy: str) -> dict[str, tuple[str, str]]:
        """chunk_id -> (content_hash, embedding_model) already stored for a strategy."""
        with self.connect() as conn:
            rows = conn.execute("SELECT chunk_id, content_hash, embedding_model FROM chunks "
                                "WHERE strategy = %s", (strategy,)).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def count(self, strategy: str) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM chunks WHERE strategy = %s", (strategy,)).fetchone()[0]

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]],
               embedding_model: str) -> None:
        """Insert or replace chunks by (strategy, chunk_id); validates counts and dimensions."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError(f"a vector is not {self.dimensions}-d")
        rows = [(strategy, c.chunk_id, c.document_id, c.source, c.page, c.metadata["page_end"],
                 c.metadata["year"], c.metadata["law_number"], c.metadata["article"],
                 c.metadata["chapter"], c.text, c.metadata["content_hash"], embedding_model,
                 json.dumps(c.metadata, ensure_ascii=False), _vector_literal(v))
                for c, v in zip(chunks, vectors)]
        with self.connect() as conn, conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO chunks (strategy, chunk_id, document_id, source, page, page_end, year,
                                    law_number, article, chapter, text, content_hash,
                                    embedding_model, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id, source = EXCLUDED.source,
                    page = EXCLUDED.page, page_end = EXCLUDED.page_end, year = EXCLUDED.year,
                    law_number = EXCLUDED.law_number, article = EXCLUDED.article,
                    chapter = EXCLUDED.chapter, text = EXCLUDED.text,
                    content_hash = EXCLUDED.content_hash, embedding_model = EXCLUDED.embedding_model,
                    metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding, updated_at = now()
                """, rows)

    def delete(self, strategy: str, chunk_ids: list[str]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM chunks WHERE strategy = %s AND chunk_id = ANY(%s)",
                         (strategy, chunk_ids))

    def sync(self, strategy: str, chunks: list[Chunk], embed: Callable[[list[str]], list[list[float]]],
             embedding_model: str) -> dict[str, int]:
        """Make the stored rows of `strategy` equal `chunks`, embedding only what changed.

        New or changed chunks (different content_hash or model) are embedded and upserted;
        stored chunks that are no longer produced (a document shrank or was removed) are
        deleted. Reloading the same corpus therefore writes nothing and adds no duplicates.
        """
        stored = self.existing(strategy)
        current = {c.chunk_id for c in chunks}
        changed = [c for c in chunks
                   if stored.get(c.chunk_id) != (c.metadata["content_hash"], embedding_model)]
        stale = sorted(set(stored) - current)
        if changed:
            self.upsert(strategy, changed, embed([c.text for c in changed]), embedding_model)
        if stale:
            self.delete(strategy, stale)
        return {"inserted": sum(c.chunk_id not in stored for c in changed),
                "updated": sum(c.chunk_id in stored for c in changed),
                "unchanged": len(chunks) - len(changed), "deleted": len(stale),
                "rows_after": self.count(strategy)}

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Exact cosine search, filtered in SQL on whitelisted columns; score = cosine similarity."""
        where, params = ["strategy = %s"], [strategy]
        for key, value in (filters or {}).items():
            if key not in FILTERABLE:
                raise ValueError(f"filter on {key!r} is not allowed")
            where.append(f"{FILTERABLE[key]} = %s")
            params.append(value)
        vector = _vector_literal(query_vector)
        with self.connect() as conn:
            rows = conn.execute(f"""
                SELECT chunk_id, document_id, source, page, page_end, year, law_number, article,
                       chapter, text, metadata, 1 - (embedding <=> %s::vector) AS score
                FROM chunks WHERE {' AND '.join(where)}
                ORDER BY embedding <=> %s::vector, chunk_id
                LIMIT %s""", [vector, *params, vector, top_k]).fetchall()
        keys = ["chunk_id", "document_id", "source", "page", "page_end", "year", "law_number",
                "article", "chapter", "text", "metadata", "score"]
        return [dict(zip(keys, r)) for r in rows]
