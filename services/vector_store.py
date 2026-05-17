"""
Abstracción VectorStore para Qdrant (local / prod). Qdrant es síncrono → asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, vectors: dict[str | UUID, tuple[list[float], dict[str, Any]]]) -> None:
        ...

    @abstractmethod
    async def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        ...


class QdrantVectorStore(VectorStore):
    def __init__(self, collection: str = "documents", *, url_env_default: str = "http://localhost:6333") -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams

            url = os.getenv("QDRANT_URL", url_env_default)
            api_key = os.getenv("QDRANT_API_KEY")  # None para local, requerido en Cloud
            self._client = QdrantClient(url=url, api_key=api_key, timeout=60)
            self._collection = collection
            self._Distance = Distance
            self._PointStruct = PointStruct
            self._VectorParams = VectorParams
            self._import_exc = None
        except ImportError as exc:
            self._import_exc = exc
            self._client = None

    def _ensure(self) -> None:
        if self._client is None:
            raise RuntimeError(
                "qdrant_client no instalado o error de importación; pip install qdrant-client",
            ) from self._import_exc

    async def upsert(self, vectors: dict[str | UUID, tuple[list[float], dict[str, Any]]]) -> None:
        self._ensure()
        pts = [self._PointStruct(id=str(sid), vector=list(vec), payload=payload) for sid, (vec, payload) in vectors.items()]  # type: ignore[union-attr]

        def _run() -> None:
            cols = self._client.get_collections().collections  # type: ignore[union-attr]
            names = {c.name for c in cols}
            if self._collection not in names and vectors:
                dim = len(next(iter(vectors.values()))[0])
                self._client.recreate_collection(  # type: ignore[union-attr]
                    collection_name=self._collection,
                    vectors_config=self._VectorParams(size=dim, distance=self._Distance.COSINE),  # type: ignore[union-attr]
                )
            self._client.upsert(collection_name=self._collection, points=pts)  # type: ignore[union-attr]

        await asyncio.to_thread(_run)

    async def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        self._ensure()

        def _run():
            resp = self._client.query_points(  # type: ignore[union-attr]
                collection_name=self._collection,
                query=list(query_vector),
                limit=limit,
                with_payload=True,
            )
            rows = []
            for p in resp.points:
                rows.append({"id": p.id, "score": getattr(p, "score", None), "payload": p.payload or {}})
            return rows

        return await asyncio.to_thread(_run)


class InMemoryVectorStore(VectorStore):
    """VectorStore trivial para pruebas (similitud coseno ingenua)."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[list[float], dict[str, Any]]] = {}

    async def upsert(self, vectors: dict[str | UUID, tuple[list[float], dict[str, Any]]]) -> None:
        for sid, tup in vectors.items():
            self._items[str(sid)] = tup

    async def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        def dot(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        def norm(v: list[float]) -> float:
            return dot(v, v) ** 0.5

        nv = norm(query_vector) or 1.0
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for sid, (vec, payload) in self._items.items():
            mu = norm(vec) or 1.0
            scored.append((dot(query_vector, vec) / (nv * mu), sid, payload))
        scored.sort(key=lambda x: -x[0])
        return [{"id": sid, "score": s, "payload": p} for s, sid, p in scored[:limit]]
