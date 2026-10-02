"""Regression tests for retrieval caching and the reranker in src/rag/hybrid.py.

Two deployment-shaped bugs are pinned here:

* ``_get_corpus`` claimed to be cached but carried no decorator, so every chat
  request re-read the entire corpus (~15k documents plus metadata) out of
  SQLite. Measured on the real index: 24.5 s for the first request against
  0.04 s once cached.
* ``_load_cross_encoder`` left its "already loaded" flag unset when the load
  raised, so a container without the ~470 MB cross-encoder retried the whole
  download on *every* request instead of degrading once.
"""

import time

import pytest

from src.rag import hybrid


# ---------------------------------------------------------------------------
# Corpus cache
# ---------------------------------------------------------------------------

class _StubCollection:
    def __init__(self, n=5):
        self.reads = 0
        self._n = n
        self._ids = ["id-%d" % i for i in range(n)]
        self._docs = ["document number %d about energy" % i for i in range(n)]
        self._metas = [{"source": "doc-%d.pdf" % i, "page": i + 1} for i in range(n)]

    def get(self, include=None, **_kw):
        self.reads += 1
        return {
            "ids": list(self._ids),
            "documents": list(self._docs),
            "metadatas": list(self._metas),
        }

    def query(self, query_texts=None, n_results=5, include=None, **_kw):
        self.reads += 1
        return {"ids": [self._ids[:n_results]]}


@pytest.fixture
def stub_collection(monkeypatch):
    """Point hybrid at a stub collection and reset the memoized caches."""
    import src.rag.retrieve as retrieve

    stub = _StubCollection()
    monkeypatch.setattr(retrieve, "collection", stub, raising=False)

    hybrid._get_corpus.cache_clear()
    hybrid._get_bm25.cache_clear()
    yield stub
    hybrid._get_corpus.cache_clear()
    hybrid._get_bm25.cache_clear()


def test_corpus_is_read_once_not_per_request(stub_collection):
    """The whole corpus must be pulled a single time, not on every query."""
    for query in ("transition energetique", "photovoltaique", "gaz naturel"):
        sources = hybrid.retrieve_hybrid(query, n_results=3)
        assert sources, "expected sources for %r" % query

    # One full corpus read (cache fill) + one vector query per retrieval.
    reads = stub_collection.reads
    assert reads == 1 + 3, "corpus re-read per request: %d reads" % reads


def test_second_request_is_much_faster_than_the_first(stub_collection):
    """The cache exists precisely so request 2 does not redo the corpus load."""
    hybrid._get_bm25()  # warm the BM25 index first so we time the corpus only

    hybrid._get_corpus.cache_clear()
    hybrid._get_bm25.cache_clear()
    t0 = time.perf_counter()
    hybrid.retrieve_hybrid("premier", n_results=3)
    first = time.perf_counter() - t0

    t0 = time.perf_counter()
    hybrid.retrieve_hybrid("second", n_results=3)
    second = time.perf_counter() - t0

    # Not a strict bound (timing is noisy), just guards against the regression
    # where the second call repeats the full-corpus work.
    assert second < first + 0.5, (
        "second request did not benefit from the cache (%.3fs vs %.3fs)"
        % (second, first)
    )


def test_cache_info_reports_hits(stub_collection):
    hybrid.retrieve_hybrid("a", n_results=3)
    hybrid.retrieve_hybrid("b", n_results=3)
    info = hybrid._get_corpus.cache_info()
    assert info.hits >= 1, "expected cache hits, got %r" % (info,)


# ---------------------------------------------------------------------------
# Reranker: one attempt only
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_reranker():
    hybrid._cross_encoder = None
    hybrid._cross_encoder_loaded = False
    hybrid._cross_encoder_attempted = False
    yield
    hybrid._cross_encoder = None
    hybrid._cross_encoder_loaded = False
    hybrid._cross_encoder_attempted = False


def _make_failing_cross_encoder(monkeypatch, attempts):
    class Boom:
        def __init__(self, *args, **kwargs):
            attempts.append(1)
            raise RuntimeError("simulated download failure")

    import sys
    import types

    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = Boom
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_failed_reranker_load_is_attempted_only_once(monkeypatch, reset_reranker):
    """A failed load must be cached, not retried on every request."""
    attempts = []
    _make_failing_cross_encoder(monkeypatch, attempts)

    for _ in range(3):
        assert hybrid.warm_cross_encoder() is False

    assert len(attempts) == 1, "cross-encoder download retried %d times" % len(attempts)


def test_rerank_degrades_to_fused_order_when_unavailable(monkeypatch, reset_reranker):
    """Losing reranking must not lose results."""
    attempts = []
    _make_failing_cross_encoder(monkeypatch, attempts)

    sources = [
        {"content": "alpha", "source": "a.pdf", "page": 1},
        {"content": "beta", "source": "b.pdf", "page": 2},
        {"content": "gamma", "source": "c.pdf", "page": 3},
    ]
    out = hybrid.rerank_candidates(list(sources), "query")
    assert len(out) == 3
    assert len(attempts) == 1


def test_successful_reranker_load_is_cached(monkeypatch, reset_reranker):
    """A working model must also be loaded only once."""
    attempts = []

    class Working:
        def __init__(self, *args, **kwargs):
            attempts.append(1)

        def predict(self, pairs):
            return [0.5 for _ in pairs]

    import sys
    import types

    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = Working
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    assert hybrid.warm_cross_encoder() is True
    assert hybrid.warm_cross_encoder() is True
    assert len(attempts) == 1


def test_warm_cross_encoder_respects_the_kill_switch(monkeypatch, reset_reranker):
    """RERANK_ENABLED=false must skip the load entirely."""
    attempts = []
    _make_failing_cross_encoder(monkeypatch, attempts)

    monkeypatch.setattr(hybrid, "RERANK_ENABLED", False)
    assert hybrid.warm_cross_encoder() is False
    assert attempts == []