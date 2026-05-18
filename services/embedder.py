"""
Embedder async — usa ClimateBERT via HuggingFace Inference API.

Sin PyTorch local. Requiere HF_TOKEN en env (plan gratuito de HF es suficiente).
Fallback: si HF_TOKEN no está, devuelve vector de ceros (RAG desactivado).
"""

from __future__ import annotations

import os
import statistics

import httpx

MODEL_NAME = os.getenv("EMBED_MODEL", "climatebert/distilroberta-base-climate-f")
EMBED_DIM = 768
HF_TOKEN = os.getenv("HF_TOKEN", "")

_HF_URL = f"https://api-inference.huggingface.co/models/{MODEL_NAME}"
_HEADERS = {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}


def _mean_pool(token_vecs: list[list[float]]) -> list[float]:
    """Promedia los vectores de tokens para obtener un embedding de oración."""
    if not token_vecs:
        return [0.0] * EMBED_DIM
    dim = len(token_vecs[0])
    return [statistics.mean(v[i] for v in token_vecs) for i in range(dim)]


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


async def embed(texts: list[str]) -> list[list[float]]:
    """Embebe una lista de textos via HF Inference API. Devuelve vectores normalizados."""
    if not HF_TOKEN:
        return [[0.0] * EMBED_DIM for _ in texts]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _HF_URL,
            headers=_HEADERS,
            json={"inputs": texts, "options": {"wait_for_model": True}},
        )
        resp.raise_for_status()
        raw = resp.json()

    results: list[list[float]] = []
    for item in raw:
        if isinstance(item[0], list):
            # token-level embeddings → mean pool
            vec = _mean_pool(item)
        else:
            vec = item
        results.append(_normalize(vec))
    return results


async def embed_one(text: str) -> list[float]:
    """Shorthand para embeber un solo texto."""
    results = await embed([text])
    return results[0]
