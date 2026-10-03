"""Re-embed the existing corpus with the ONNX embedder into a cosine collection.

Why this exists
---------------
The index in ``data/chroma_db`` was written by chromadb's
``SentenceTransformerEmbeddingFunction`` with ``normalize_embeddings=False``,
so the stored vectors are *un-normalised*: measured L2 norms span 1.26-6.61,
mean 2.90. Chroma then ranks by squared L2::

    ||q - d||^2 = ||q||^2 - 2 q.d + ||d||^2

Switching the query path to the ONNX embedder silently broke this. The ONNX
embedder returns L2-*normalised* vectors (``onnx_embedder.embed_texts`` divides
by the norm), so ``||q||`` became 1.0 while every ``||d||`` stayed between 1.26
and 6.61. ``||d||^2`` then spans 1.6-43.7 while ``q.d`` can never exceed 1.0,
so the magnitude term dominated the ranking and retrieval effectively sorted by
vector norm. Measured overlap with the true-cosine top-5 fell to 0-1 out of 5.

The build-time check missed it because it only compared vector *direction*
(cosine 1.0000 against fp32) and never compared *magnitude* -- and L2 is
magnitude-sensitive.

The fix
-------
Rebuild the index so both sides of the comparison agree: every stored vector
L2-normalised, in a collection whose metric is cosine. The ONNX build is
cosine 1.0000 against the fp32 weights it replaces, so this is not a quality
trade -- it needs no torch, which is the entire point of the ONNX switch.

The source collection is left untouched so a bad rebuild can simply be
discarded by pointing ``CHROMA_COLLECTION`` back at it.

Usage
-----
    ONNX_EMBEDDER_DIR=models/onnx python scripts/rebuild_index_onnx.py
    CHROMA_COLLECTION=tunisia_energy_rag_cosine python -m src.api.main

Run it from the project root with the project importable (``PYTHONPATH=.``).
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_COLLECTION = "tunisia_energy_rag"
TARGET_COLLECTION = "tunisia_energy_rag_cosine"
CHROMA_PATH = str(PROJECT_ROOT / "data" / "chroma_db")

BATCH = 64
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("rebuild")


def iter_records(collection, page=2000):
    """Yield (ids, documents, metadatas) pages without holding the corpus in RAM."""
    offset = 0
    total = collection.count()
    while offset < total:
        batch = collection.get(
            limit=page,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = batch["ids"]
        if not ids:
            break
        yield ids, batch["documents"], batch["metadatas"]
        offset += len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=TARGET_COLLECTION)
    parser.add_argument("--reset", action="store_true",
                        help="delete the target collection first if it exists")
    args = parser.parse_args()

    from src.rag import onnx_embedder

    if not onnx_embedder.embedder_available():
        log.error("ONNX embedder not available (set ONNX_EMBEDDER_DIR). Aborting; "
                  "the source collection is unchanged.")
        return 1
    if not onnx_embedder.warm():
        log.error("ONNX embedder failed to load. Aborting; source collection is unchanged.")
        return 1

    import chromadb

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    source = client.get_collection(name=SOURCE_COLLECTION)
    expected = source.count()
    log.info("source collection %r holds %d chunks", SOURCE_COLLECTION, expected)

    if args.reset:
        try:
            client.delete_collection(name=args.target)
            log.info("deleted existing target %r", args.target)
        except Exception:
            pass

    # hnsw:space is fixed at creation time and cannot be altered afterwards,
    # which is exactly why this writes a new collection instead of patching
    # the old one in place.
    target = client.create_collection(
        name=args.target,
        metadata={"hnsw:space": "cosine"},
    )

    started = time.monotonic()
    written = 0
    norm_min, norm_max = float("inf"), 0.0

    for ids, documents, metadatas in iter_records(source):
        for start in range(0, len(documents), BATCH):
            sl = slice(start, start + BATCH)
            vectors = onnx_embedder.embed_texts(documents[sl])

            arr = np.asarray(vectors, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1)
            norm_min = min(norm_min, float(norms.min()))
            norm_max = max(norm_max, float(norms.max()))

            target.upsert(
                ids=ids[sl],
                documents=documents[sl],
                metadatas=metadatas[sl],
                embeddings=arr.tolist(),
            )
            written += arr.shape[0]

        pct = written / expected * 100 if expected else 100.0
        log.info("%d/%d chunks (%.1f%%) %.0fs elapsed",
                 written, expected, pct, time.monotonic() - started)

    actual = target.count()
    log.info("wrote %d chunks in %.0fs", actual, time.monotonic() - started)
    log.info("stored vector norms: %.6f .. %.6f", norm_min, norm_max)

    if actual != expected:
        log.error("count mismatch: wrote %d, expected %d -- do not switch over",
                  actual, expected)
        return 1

    if not (abs(norm_min - 1.0) < 1e-3 and abs(norm_max - 1.0) < 1e-3):
        log.error("vectors are not unit length (%.6f..%.6f) -- do not switch over",
                  norm_min, norm_max)
        return 1

    log.info("OK. Switch with: CHROMA_COLLECTION=%s", args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())