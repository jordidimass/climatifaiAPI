"""
Embedder async — usa ClimateBERT via HuggingFace Inference API.

Sin PyTorch local. Requiere HF_TOKEN en env (plan gratuito de HF es suficiente).
Fallback: si HF_TOKEN no está, devuelve vector de ceros (RAG desactivado).
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache

MODEL_NAME = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
EMBED_DIM = 768
HF_TOKEN = os.getenv("HF_TOKEN", "")


@lru_cache(maxsize=1)
def _get_client():
    from huggingface_hub import InferenceClient
    return InferenceClient(token=HF_TOKEN)


def _embed_sync(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    result = client.feature_extraction(texts, model=MODEL_NAME)
    # result puede ser ndarray o lista; lo normalizamos a list[list[float]]
    import numpy as np
    arr = np.array(result)
    # Si viene con dimensión de tokens (batch, tokens, dim) → mean pool
    if arr.ndim == 3:
        arr = arr.mean(axis=1)
    # Normalizar cada vector
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    arr = arr / norms
    return arr.tolist()


async def embed(texts: list[str]) -> list[list[float]]:
    """Embebe una lista de textos via HF Inference API. Devuelve vectores normalizados."""
    if not HF_TOKEN:
        return [[0.0] * EMBED_DIM for _ in texts]
    return await asyncio.to_thread(_embed_sync, texts)


async def embed_one(text: str) -> list[float]:
    """Shorthand para embeber un solo texto."""
    results = await embed([text])
    return results[0]
