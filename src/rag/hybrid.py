"""Hybrid retrieval for the RAG pipeline.

Dense vector search alone misses queries whose meaning lives in exact terms
(French/Arabic keywords like "autoconsommation", "ترشيد"), because the
embedding is trained to capture semantics, not spelling. This module adds a
sparse BM25 leg over the same corpus and fuses the two ranked lists with
Reciprocal Rank Fusion (RRF), then optionally reranks the fused top-k with a
cross-encoder (multilingual, covers French + Arabic).

Design notes:
- Everything Chroma-coupled is lazy: the corpus is fetched and the BM25 index
  built once on first use (cached for the process lifetime), so importing the
  module costs nothing.
- The pure pieces (``tokenize``, ``build_bm25``, ``bm25_search``, ``rrf_fuse``,
  ``rerank_candidates``) take their inputs explicitly so they are unit-testable
  on synthetic corpora without ChromaDB.
"""

import logging
import os
import re
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# How many candidates each leg contributes before fusion, and the final count.
VECTOR_CANDIDATES = 25
BM25_CANDIDATES = 25
DEFAULT_FINAL_K = 5
# RRF constant (standard value from the original paper).
RRF_CONSTANT = 60

# Cross-encoder used for reranking (multilingual MARCO: ~13 languages incl.
# French and Arabic). Lazy-loaded on first rerank; override via RERANK_MODEL.
DEFAULT_RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
# Defaults to OFF. The deployment image deliberately does not bake the ~470 MB
# cross-encoder (Dockerfile ARG RERANK_ENABLED=false), so defaulting to "true"
# meant a live query tried to download it from the Hugging Face Hub inside a
# request thread. Observed in production: the stream emitted "searching", hung
# ~48s, then died with no sources, no tokens and no error frame. Enabling this
# requires baking the model into the image, not just setting the flag.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() in ("1", "true", "yes")
# Even when reranking is enabled, only load the model from the local cache.
# Downloading ~470 MB while serving a request is what produced the silent hang;
# opt in explicitly with RERANK_ALLOW_DOWNLOAD=true once the image ships it.
RERANK_ALLOW_DOWNLOAD = os.getenv("RERANK_ALLOW_DOWNLOAD", "false").lower() in (
    "1", "true", "yes",
)
RERANK_MODEL = os.getenv("RERANK_MODEL", DEFAULT_RERANK_MODEL)
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "10"))

# Word-ish tokens (unicode-aware: \\w covers Arabic + accented Latin).
_TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens; keeps Arabic letters and accented Latin intact."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


# ---------------------------------------------------------------------------
# BM25 (pure, testable)
# ---------------------------------------------------------------------------

def build_bm25(corpus_texts: Sequence[str]):
    """Build a BM25Okapi index over the given corpus texts (rank_bm25)."""
    from rank_bm25 import BM25Okapi

    tokenized = [tokenize(t) for t in corpus_texts]
    return BM25Okapi(tokenized)


def bm25_search(bm25, query: str, k: int = BM25_CANDIDATES) -> List[int]:
    """Return the top-k corpus indices for a query under BM25."""
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return ranked[:k]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (pure, testable)
# ---------------------------------------------------------------------------

def rrf_fuse(
    ranked_id_lists: Sequence[Sequence[Any]],
    k: int = DEFAULT_FINAL_K,
    constant: int = RRF_CONSTANT,
) -> List[Any]:
    """Fuse several ranked id lists with Reciprocal Rank Fusion.

    Each item's score is the sum of ``1 / (constant + rank)`` across the legs
    it appears in (rank is 1-based). Returns the top-k fused ids in order.
    """
    scores: Dict[Any, float] = {}
    for ranked in ranked_id_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (constant + rank)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [item for item, _score in ordered[:k]]


# ---------------------------------------------------------------------------
# Chroma-coupled corpus access (lazy, cached)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_corpus():
    """Fetch all Chroma documents + metadata once, cached for the process.

    Returns ``(ids, texts, meta_by_id)`` where meta_by_id maps each doc id to
    its metadata dict ({} when missing). Deliberately imported inside the
    function so importing this module never touches ChromaDB.

    The cache is essential: this pulls the *entire* corpus (~15k documents plus
    metadata) out of SQLite. Called without caching, every chat request re-read
    the whole index from disk before the query could even be embedded.
    """
    from src.rag.retrieve import collection

    data = collection.get(include=["documents", "metadatas"])
    ids: List[str] = data.get("ids") or []
    texts: List[str] = data.get("documents") or []
    metas: List[Dict[str, Any]] = data.get("metadatas") or []
    meta_by_id = {
        doc_id: (meta or {})
        for doc_id, meta in zip(ids, metas)
    }
    return ids, texts, meta_by_id


def corpus_size() -> Optional[int]:
    """Number of documents in the Chroma corpus, or None if not loaded yet.

    Only meaningful once ``_get_corpus()`` is cached: calling it cold would
    pull the whole index out of SQLite, which is exactly what diagnostics
    must not do.
    """
    if _get_corpus.cache_info().currsize == 0:
        return None
    ids, _texts, _metas = _get_corpus()
    return len(ids)


@lru_cache(maxsize=1)
def _get_bm25():
    """BM25 index over the whole corpus, built once and cached."""
    ids, texts, _meta = _get_corpus()
    if not texts:
        return None
    logger.info("Building BM25 index over %d corpus documents...", len(texts))
    return build_bm25(texts)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _structured_sources(
    ordered_ids: Sequence[str],
    texts_by_id: Dict[str, str],
    meta_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map fused doc ids to the structured source shape used by the pipeline."""
    from src.rag.retrieve import extract_document_date

    sources = []
    for doc_id in ordered_ids:
        meta = meta_by_id.get(doc_id, {})
        source_file = meta.get("source") or meta.get("file_name") or "Unknown Document"
        sources.append(
            {
                "content": texts_by_id.get(doc_id, ""),
                "source_file": source_file,
                "page": meta.get("page") or meta.get("page_number") or "N/A",
                "date": extract_document_date(source_file),
            }
        )
    return sources


def retrieve_hybrid(query: str, n_results: int = DEFAULT_FINAL_K) -> List[Dict[str, Any]]:
    """Fuse vector + BM25 retrieval and return the top ``n_results`` sources.

    Falls back to pure vector retrieval when the corpus is empty or BM25 is
    unavailable, so the pipeline never hard-fails on this path.
    """
    from src.rag.retrieve import collection

    ids, texts, meta_by_id = _get_corpus()
    if not ids:
        return []

    texts_by_id = dict(zip(ids, texts))

    # Leg 1: dense vector search (Chroma).
    vector_results = collection.query(
        query_texts=[query],
        n_results=min(VECTOR_CANDIDATES, len(ids)),
        include=["documents", "metadatas"],
    )
    vector_ids: List[str] = (vector_results.get("ids") or [[]])[0]

    # Leg 2: sparse BM25 keyword search over the same corpus.
    bm25 = _get_bm25()
    if bm25 is not None and query.strip():
        bm25_idx = bm25_search(bm25, query, k=BM25_CANDIDATES)
        bm25_ids = [ids[i] for i in bm25_idx if i < len(ids)]
    else:
        bm25_ids = []

    fused_ids = rrf_fuse([vector_ids, bm25_ids], k=n_results)
    return _structured_sources(fused_ids, texts_by_id, meta_by_id)


# ---------------------------------------------------------------------------
# Cross-encoder reranking (lazy, env-gated, graceful fallback)
# ---------------------------------------------------------------------------

_cross_encoder = None
_cross_encoder_loaded = False
# Set once a load has been attempted, successfully or not. Without this the
# loader retried the ~470 MB download on *every* request whenever the attempt
# failed (offline container, HF rate limit, missing weight files), turning a
# one-off problem into a multi-minute stall on all subsequent queries.
_cross_encoder_attempted = False


def warm_cross_encoder() -> bool:
    """Load the cross-encoder ahead of the first request.

    Returns True if the reranker is available. Safe to call repeatedly: the
    download is attempted at most once per process, and a failure is cached so
    later calls degrade instantly to the fused ranking order.
    """
    if not RERANK_ENABLED:
        return False
    try:
        _load_cross_encoder()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Cross-encoder unavailable (%s) — reranking disabled.", e)
        return False


def _load_cross_encoder():
    global _cross_encoder, _cross_encoder_loaded, _cross_encoder_attempted
    if not _cross_encoder_loaded and not _cross_encoder_attempted:
        from sentence_transformers import CrossEncoder

        logger.info("Loading cross-encoder reranker %s (first use only)...", RERANK_MODEL)
        _cross_encoder_attempted = True
        _cross_encoder = CrossEncoder(
            RERANK_MODEL,
            local_files_only=not RERANK_ALLOW_DOWNLOAD,
        )
        _cross_encoder_loaded = True
    if not _cross_encoder_loaded:
        raise RuntimeError("cross-encoder previously failed to load")
    return _cross_encoder


def rerank_candidates(
    sources: List[Dict[str, Any]],
    query: str,
    top_k: int = RERANK_TOP_K,
    scorer: Optional[Callable[[str, str], float]] = None,
) -> List[Dict[str, Any]]:
    """Rerank sources by cross-encoder relevance to the query.

    ``scorer`` is injectable for tests; the default lazily loads the real
    multilingual cross-encoder. Any failure (model download, inference error)
    degrades gracefully to the input order.
    """
    if len(sources) <= 1:
        return sources

    if scorer is None:
        if not RERANK_ENABLED:
            return sources
        try:
            model = _load_cross_encoder()
            scorer = lambda q, doc: float(model.predict([(q, doc)])[0])  # noqa: E731
        except Exception as e:
            logger.warning("Reranker unavailable (%s) — using fused order.", e)
            return sources

    try:
        scored = [
            (src, scorer(query, src["content"]))
            for src in sources
        ]
    except Exception as e:
        logger.warning("Reranking failed (%s) — using fused order.", e)
        return sources

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [src for src, _score in scored[:top_k]]


def retrieve_context_hybrid(query: str, n_results: int = DEFAULT_FINAL_K) -> List[Dict[str, Any]]:
    """Pipeline-facing entry point: hybrid retrieval + optional rerank."""
    sources = retrieve_hybrid(query, n_results=n_results)
    return rerank_candidates(sources, query, top_k=n_results)
