"""
CLI: ingest_docs — embebe documentos agroclimaticos y los carga en Qdrant Cloud.

Uso:
    uv run python -m cli.ingest_docs
    uv run python -m cli.ingest_docs --docs-dir data/docs --chunk-size 400

El script:
1. Lee todos los .txt de data/docs/
2. Extrae el campo 'source:' de la primera linea
3. Parte el texto en chunks solapados
4. Embebe con ClimateBERT (sentence-transformers)
5. Upserta en Qdrant (coleccion climatifai_docs)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """Parte el texto en chunks de palabras con solapamiento."""
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def _parse_source(first_line: str) -> str:
    """Extrae el valor de 'source: ...' de la primera linea del doc."""
    if first_line.startswith("source:"):
        return first_line.replace("source:", "").strip()
    return "unknown"


async def ingest(docs_dir: Path, chunk_size: int, collection: str) -> None:
    from services.embedder import embed
    from services.vector_store import QdrantVectorStore

    store = QdrantVectorStore(collection=collection)
    files = sorted(docs_dir.glob("*.txt"))

    if not files:
        print(f"No se encontraron archivos .txt en {docs_dir}")
        return

    print(f"Procesando {len(files)} documentos...")

    all_vectors: dict[str, tuple[list[float], dict]] = {}

    for fpath in files:
        raw = fpath.read_text(encoding="utf-8")
        lines = raw.strip().splitlines()
        source = _parse_source(lines[0]) if lines else fpath.stem
        body = "\n".join(lines[1:]).strip()

        chunks = _chunk_text(body, chunk_size=chunk_size)
        print(f"  {fpath.name}: {len(chunks)} chunks")

        vectors = await embed(chunks)

        for chunk_text, vec in zip(chunks, vectors):
            uid = str(uuid.uuid4())
            all_vectors[uid] = (vec, {"text": chunk_text, "source": source, "file": fpath.name})

    print(f"\nUpsertando {len(all_vectors)} vectores en Qdrant coleccion '{collection}'...")
    await store.upsert(all_vectors)
    print("Ingesta completada.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestar docs agroclimaticos en Qdrant")
    parser.add_argument("--docs-dir", default="data/docs", help="Directorio con archivos .txt")
    parser.add_argument("--chunk-size", type=int, default=400, help="Palabras por chunk")
    parser.add_argument(
        "--collection",
        default=os.getenv("QDRANT_COLLECTION", "climatifai_docs"),
        help="Nombre de la coleccion Qdrant",
    )
    args = parser.parse_args()

    asyncio.run(ingest(Path(args.docs_dir), args.chunk_size, args.collection))


if __name__ == "__main__":
    main()
