"""VectorStore on PostgreSQL + pgvector (ADR 0018): the same contract as Qdrant.

One table holds the vectors of every logical collection (`persons_semantic`, ...); a
collection records its vector size. The embedding column has no fixed dimension, so each
collection gets its own partial HNSW index on `(embedding::vector(N))` for its rows of
that size, and the nearest-neighbour query repeats that exact cast and predicate so the
planner uses it. Another embedding model (another N) needs a full rebuild, not a
migration.

Collections named in `exact_collections` (the person collection, from the factory) get no
HNSW index and are always searched exactly: the research workflow's candidates are then
the true nearest neighbours, as Qdrant's exact search of the same collection returns.

Like the Qdrant points, rows hold no facts: entity id/type, representation version,
content hash and embedding model. Score is cosine similarity, higher is closer.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.models import (
    RetrievalEntityType,
    RetrievalUnavailableError,
    VectorSizeMismatchError,
)
from semantic_retrieval.vector_store import VectorMatch, VectorPoint, require_model

logger = logging.getLogger("semantic_retrieval")

# Collection names go into index names and index predicates, so they are identifiers.
_COLLECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# pgvector indexes `vector` of at most 2000 dimensions; larger collections are searched
# exactly (a sequential scan), which is still correct.
MAX_HNSW_DIMENSIONS = 2000
# An HNSW scan returns at most ef_search rows and finds more of the true neighbours as it
# grows. On the working corpus (reports/vector_store_benchmark.md) ef_search = 4 × limit
# finds 99% of the exact top-100 in ~7 ms; pgvector's default 40 would cap the result.
EF_SEARCH_PER_RESULT = 4
MIN_EF_SEARCH = 40
MAX_EF_SEARCH = 1000  # pgvector's upper bound
_UNAVAILABLE = (OperationalError, InterfaceError)


def _collection(name: str) -> str:
    if not _COLLECTION_NAME.match(name):
        raise ValueError(f"Invalid vector collection name {name!r}: use [a-z][a-z0-9_]{{0,39}}")
    return name


def _index_name(name: str, vector_size: int) -> str:
    return f"ix_semvec_hnsw_{name}_{vector_size}"


def _index_predicate(name: str, vector_size: int) -> str:
    # The dimension check keeps rows of another size out of the cast: rows deleted by a
    # recreate in the same transaction are still seen by the index build.
    return f"collection_name = '{name}' AND vector_dims(embedding) = {vector_size}"


def ef_search(limit: int) -> int:
    return min(MAX_EF_SEARCH, max(MIN_EF_SEARCH, EF_SEARCH_PER_RESULT * limit))


def dense_search_sql(name: str, vector_size: int) -> str:
    """Nearest neighbours of `:query` in a whole collection, through its HNSW index: the
    cast and the literal predicate repeat the partial index definition."""
    distance = f"embedding::vector({vector_size}) <=> CAST(:query AS vector({vector_size}))"
    return (
        f"SELECT entity_id, embedding_model_id, 1 - ({distance}) AS score "
        f"FROM semantic_vectors WHERE {_index_predicate(name, vector_size)} "
        f"ORDER BY {distance} LIMIT :limit"
    )


def prepare_dense_search(session: Session, vector_size: int, limit: int) -> None:
    """Settings of the dense search's transaction (SET LOCAL: they end with it)."""
    if vector_size > MAX_HNSW_DIMENSIONS:
        return  # no index: an exact scan is the only plan
    session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search(limit)}"))
    # Right after a bulk load, before autoanalyze, the table statistics still describe
    # the old contents, and the planner can prefer reading every row and sorting: exact,
    # but 20x slower (reports/vector_store_benchmark.md). Only the HNSW index returns the
    # rows already ordered, so without sorts it is the plan.
    session.execute(text("SET LOCAL enable_sort = off"))


def _vector_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def exact_search_sql(name: str) -> str:
    """Exact nearest neighbours of `:query` in a whole collection: every row's distance,
    then a top-N sort. The distance is computed once, sorted by its alias (a score
    expression next to it would be a second distance per row). No `vector_dims` filter:
    the collection's metadata fixes the size (upserts are checked against it, a recreate
    deletes the old rows), and the condition would read each vector a second time."""
    return (
        "SELECT entity_id, embedding_model_id, embedding <=> CAST(:query AS vector) AS distance "
        f"FROM semantic_vectors WHERE collection_name = '{name}' "
        "ORDER BY distance, entity_id LIMIT :limit"
    )


def candidate_search_sql() -> str:
    """Exact nearest neighbours among `:ids` (a structured candidate set), found by the
    primary key. The uncast expression cannot use an HNSW index, whose approximate scan
    could stop before it reaches the filtered rows; the distance is computed once."""
    return (
        "SELECT entity_id, embedding_model_id, embedding <=> CAST(:query AS vector) AS distance "
        "FROM semantic_vectors WHERE collection_name = :collection AND entity_id = ANY(:ids) "
        "ORDER BY distance, entity_id LIMIT :limit"
    )


class PgVectorStore:
    backend_name = "pgvector"

    def __init__(
        self, session_factory: sessionmaker[Session], *, exact_collections: Iterable[str] = ()
    ) -> None:
        self._session_factory = session_factory
        self._exact = frozenset(_collection(name) for name in exact_collections)

    def exact_search_sql(self, name: str) -> str:
        return exact_search_sql(_collection(name))

    def candidate_search_sql(self, name: str) -> str:
        _collection(name)
        return candidate_search_sql()

    def _call[T](self, operation: str, action: Callable[[Session], T]) -> T:
        """One transaction per operation, like one Qdrant request."""
        try:
            with self._session_factory.begin() as session:
                return action(session)
        except _UNAVAILABLE as exc:
            logger.warning(
                "event=vector_store_unavailable backend=pgvector operation=%s error=%s",
                operation,
                type(exc).__name__,
            )
            raise RetrievalUnavailableError(f"PostgreSQL unavailable during {operation}") from exc

    @staticmethod
    def _vector_size(session: Session, name: str) -> int | None:
        size = session.execute(
            text("SELECT vector_size FROM semantic_vector_collections WHERE name = :name"),
            {"name": name},
        ).scalar_one_or_none()
        return None if size is None else int(size)

    def _create(self, session: Session, name: str, vector_size: int) -> None:
        session.execute(
            text(
                "INSERT INTO semantic_vector_collections (name, vector_size) "
                "VALUES (:name, :size) ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "size": vector_size},
        )
        if name in self._exact:
            return  # searched exactly: an HNSW index would only cost writes
        if vector_size > MAX_HNSW_DIMENSIONS:
            logger.warning(
                "event=pgvector_exact_search collection=%s vector_size=%d: no HNSW index "
                "above %d dimensions",
                name,
                vector_size,
                MAX_HNSW_DIMENSIONS,
            )
            return
        # Name and size are validated identifiers/integers; DDL takes no bind parameters.
        session.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_index_name(name, vector_size)} "
                "ON semantic_vectors USING hnsw "
                f"((embedding::vector({vector_size})) vector_cosine_ops) "
                f"WHERE {_index_predicate(name, vector_size)}"
            )
        )

    @staticmethod
    def _drop_indexes(session: Session, name: str) -> None:
        pattern = re.compile(rf"^ix_semvec_hnsw_{name}_\d+$")
        indexes = session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'semantic_vectors'")
        ).scalars()
        for index in indexes:
            if pattern.match(index):
                session.execute(text(f"DROP INDEX IF EXISTS {index}"))

    def ensure_collection(self, name: str, vector_size: int) -> None:
        name = _collection(name)

        def ensure(session: Session) -> None:
            existing = self._vector_size(session, name)
            if existing is None:
                self._create(session, name, vector_size)
            elif existing != vector_size:
                raise VectorSizeMismatchError(
                    f"Collection {name} has vector size {existing}, embedder produces "
                    f"{vector_size}; rebuild the semantic index"
                )
            elif name in self._exact:
                # A collection indexed before it became exact would keep a dead HNSW
                # index that every write still maintains.
                self._drop_indexes(session, name)

        self._call("ensure_collection", ensure)

    def recreate_collection(self, name: str, vector_size: int) -> None:
        name = _collection(name)

        def recreate(session: Session) -> None:
            # The vectors go with the collection row (ON DELETE CASCADE).
            session.execute(
                text("DELETE FROM semantic_vector_collections WHERE name = :name"), {"name": name}
            )
            self._drop_indexes(session, name)
            self._create(session, name, vector_size)

        self._call("recreate_collection", recreate)

    def upsert(self, name: str, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        name = _collection(name)

        def upsert(session: Session) -> None:
            size = self._vector_size(session, name)
            if size is None:
                raise RetrievalUnavailableError(
                    f"Collection {name} does not exist; run rebuild-semantic-index"
                )
            for point in points:
                if len(point.vector) != size:
                    raise VectorSizeMismatchError(
                        f"Vector size {len(point.vector)} differs from collection {name} "
                        f"size {size}"
                    )
            session.execute(
                text(
                    "INSERT INTO semantic_vectors (collection_name, entity_type, entity_id, "
                    "embedding, embedding_model_id, representation_version, content_hash) "
                    "VALUES (:collection, :entity_type, :entity_id, CAST(:embedding AS vector), "
                    ":model, :version, :content_hash) "
                    "ON CONFLICT (collection_name, entity_type, entity_id) DO UPDATE SET "
                    "embedding = excluded.embedding, "
                    "embedding_model_id = excluded.embedding_model_id, "
                    "representation_version = excluded.representation_version, "
                    "content_hash = excluded.content_hash, updated_at = now()"
                ),
                [
                    {
                        "collection": name,
                        "entity_type": point.document.entity_type.value,
                        "entity_id": point.document.entity_id,
                        "embedding": _vector_literal(point.vector),
                        "model": point.embedding_model_id,
                        "version": point.document.representation_version,
                        "content_hash": point.document.content_hash,
                    }
                    for point in points
                ],
            )

        self._call("upsert", upsert)

    def delete(
        self, name: str, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> None:
        if not entity_ids:
            return
        name = _collection(name)
        self._call(
            "delete",
            lambda session: session.execute(
                text(
                    "DELETE FROM semantic_vectors WHERE collection_name = :collection "
                    "AND entity_type = :entity_type AND entity_id = ANY(:ids)"
                ),
                {"collection": name, "entity_type": entity_type.value, "ids": list(entity_ids)},
            ),
        )

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        name = _collection(name)
        query = _vector_literal(vector)

        def search(session: Session) -> list[tuple[int, str, float]]:
            size = self._vector_size(session, name)
            if size is None:
                raise RetrievalUnavailableError(
                    f"Collection {name} does not exist; run rebuild-semantic-index"
                )
            if size != len(vector):
                raise VectorSizeMismatchError(
                    f"Query vector size {len(vector)} differs from collection {name} size {size}"
                )
            if entity_ids is not None:
                rows = session.execute(
                    text(candidate_search_sql()),
                    {"query": query, "collection": name, "ids": list(entity_ids), "limit": limit},
                ).all()
                return [(int(row[0]), str(row[1]), 1 - float(row[2])) for row in rows]
            if name in self._exact:
                # A sort is the plan here: none of the HNSW settings below apply.
                rows = session.execute(
                    text(exact_search_sql(name)), {"query": query, "limit": limit}
                ).all()
                return [(int(row[0]), str(row[1]), 1 - float(row[2])) for row in rows]
            prepare_dense_search(session, size, limit)
            rows = session.execute(
                text(dense_search_sql(name, size)), {"query": query, "limit": limit}
            ).all()
            return [(int(row[0]), str(row[1]), float(row[2])) for row in rows]

        matches: list[VectorMatch] = []
        for entity_id, model, score in self._call("search", search):
            require_model(name, model, embedding_model_id)
            matches.append(VectorMatch(entity_id=entity_id, score=score))
        return matches

    def check_embedding_model(self, name: str, embedding_model_id: str) -> None:
        name = _collection(name)
        # Any row of another model, not a sample.
        foreign = self._call(
            "check_embedding_model",
            lambda session: session.execute(
                text(
                    "SELECT embedding_model_id FROM semantic_vectors "
                    "WHERE collection_name = :collection AND embedding_model_id <> :model LIMIT 1"
                ),
                {"collection": name, "model": embedding_model_id},
            ).scalar_one_or_none(),
        )
        if foreign is not None:
            require_model(name, foreign, embedding_model_id)

    def count(self, name: str) -> int:
        name = _collection(name)
        return self._call(
            "count",
            lambda session: int(
                session.execute(
                    text("SELECT count(*) FROM semantic_vectors WHERE collection_name = :name"),
                    {"name": name},
                ).scalar_one()
            ),
        )
