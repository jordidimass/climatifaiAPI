"""
Embedder async — usa ClimateBERT (distilroberta-base-climate-f) via sentence-transformers.

El modelo se descarga la primera vez (~300MB) y queda cacheado en ~/.cache/huggingface.
Todas las llamadas pesadas corren en asyncio.to_thread para no bloquear el event loop.
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache

MODEL_NAME = os.getenv(
    "EMBED_MODEL",
    "climatebert/distilroberta-base-climate-f",
)

EMBED_DIM = 768  # distilroberta hidden size


@lru_cache(maxsize=1)
def _get_model():
    """Carga el modelo una sola vez (singleton)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def _embed_sync(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


async def embed(texts: list[str]) -> list[list[float]]:
    """Embebe una lista de textos. Devuelve vectores normalizados [dim=768]."""
    return await asyncio.to_thread(_embed_sync, texts)


async def embed_one(text: str) -> list[float]:
    """Shorthand para embeber un solo texto."""
    results = await embed([text])
    return results[0]
