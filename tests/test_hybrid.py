"""Unit tests for hybrid retrieval (src/rag/hybrid.py).

The pure pieces (tokenize / BM25 / RRF / rerank-with-injected-scorer) run on
synthetic corpora with no ChromaDB or model download. One smoke test exercises
the real corpus and skips cleanly when it is unavailable.
"""

import pytest

from src.rag import hybrid


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_tokenize_keeps_french_and_arabic():
    toks = hybrid.tokenize("Efficacité énergétique ترشيد استهلاك ÉLECTRICITÉ")
    assert "efficacité" in toks
    assert "électricité" in toks
    assert "ترشيد" in toks
    assert "استهلاك" in toks
    assert all(t == t.lower() for t in toks)


def test_tokenize_empty_and_punct():
    assert hybrid.tokenize("") == []
    assert hybrid.tokenize("  !? — ... ") == []


# ---------------------------------------------------------------------------
# BM25 (synthetic corpus)
# ---------------------------------------------------------------------------

def test_bm25_surfaces_keyword_doc():
    corpus = [
        "Rapport sur la production industrielle en Tunisie.",
        "Guide du régime d'autoconsommation pour les panneaux solaires photovoltaïques.",
        "Étude hydraulique des stations de pompage.",
    ]
    bm25 = hybrid.build_bm25(corpus)
    top = hybrid.bm25_search(bm25, "régime autoconsommation panneaux solaires", k=3)
    assert top[0] == 1


def test_bm25_surfaces_arabic_keyword():
    corpus = [
        "تقرير عن الصناعة في تونس.",
        "دليل ترشيد استهلاك الكهرباء في المباني.",
        "دراسة هيدروليكية لمحطات الضخ.",
    ]
    bm25 = hybrid.build_bm25(corpus)
    top = hybrid.bm25_search(bm25, "ترشيد استهلاك الكهرباء", k=3)
    assert top[0] == 1


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def test_rrf_fuse_ranks_shared_items_higher():
    vector = ["a", "b", "c", "d"]
    bm25 = ["b", "a", "e", "f"]
    fused = hybrid.rrf_fuse([vector, bm25], k=4)
    # Items present in both legs outrank singletons.
    assert set(fused[:2]) == {"a", "b"}


def test_rrf_fuse_truncates_to_k():
    fused = hybrid.rrf_fuse([["a", "b", "c", "d", "e"]], k=2)
    assert fused == ["a", "b"]


def test_rrf_fuse_returns_top_by_sum():
    # 'a' ranks 1 in both legs; 'b' appears in only one -> 'a' accumulates
    # the higher RRF score and wins.
    fused = hybrid.rrf_fuse([["a", "b"], ["a"]], k=1)
    assert fused == ["a"]

    # A doc ranked 1st by both legs beats one ranked 1st by a single leg.
    fused2 = hybrid.rrf_fuse([["a", "b"], ["a"]], k=2)
    assert fused2 == ["a", "b"]


# ---------------------------------------------------------------------------
# Reranking (injected scorer — no model download)
# ---------------------------------------------------------------------------

def test_rerank_reorders_with_stub_scorer():
    sources = [
        {"content": "à propos du pompage de l'eau", "source_file": "a.pdf"},
        {"content": "le régime d'autoconsommation solaire", "source_file": "b.pdf"},
    ]
    scorer = lambda q, doc: 1.0 if "autoconsommation" in doc else 0.0
    out = hybrid.rerank_candidates(sources, "autoconsommation", top_k=2, scorer=scorer)
    assert out[0]["source_file"] == "b.pdf"


def test_rerank_truncates_to_top_k():
    sources = [{"content": f"doc numéro {i}", "source_file": f"{i}.pdf"} for i in range(5)]
    scorer = lambda q, doc: float(len(doc))
    out = hybrid.rerank_candidates(sources, "q", top_k=2, scorer=scorer)
    assert len(out) == 2


def test_rerank_falls_back_when_scorer_raises():
    sources = [
        {"content": "x", "source_file": "a.pdf"},
        {"content": "y", "source_file": "b.pdf"},
    ]

    def boom(_q, _doc):
        raise RuntimeError("model unavailable")

    out = hybrid.rerank_candidates(sources, "q", scorer=boom)
    assert [s["source_file"] for s in out] == ["a.pdf", "b.pdf"]


def test_rerank_disabled_keeps_fused_order(monkeypatch):
    monkeypatch.setattr(hybrid, "RERANK_ENABLED", False)
    sources = [
        {"content": "x", "source_file": "a.pdf"},
        {"content": "y", "source_file": "b.pdf"},
    ]
    out = hybrid.rerank_candidates(sources, "q", scorer=lambda q, d: 9.0)
    assert [s["source_file"] for s in out] == ["a.pdf", "b.pdf"]


def test_rerank_single_source_noop():
    sources = [{"content": "x", "source_file": "a.pdf"}]
    assert hybrid.rerank_candidates(sources, "q") == sources


# ---------------------------------------------------------------------------
# Real-corpus smoke test (skips when the vector DB is absent)
# ---------------------------------------------------------------------------

def test_hybrid_real_corpus_smoke(monkeypatch):
    try:
        from src.rag.retrieve import collection

        if collection.count() == 0:
            pytest.skip("ChromaDB corpus is empty")
    except Exception:
        pytest.skip("ChromaDB corpus unavailable")

    # Avoid downloading the cross-encoder during tests.
    monkeypatch.setattr(hybrid, "RERANK_ENABLED", False)

    sources = hybrid.retrieve_context_hybrid(
        "Comment fonctionne le régime d'autoconsommation en Tunisie ?", n_results=5
    )
    assert 0 < len(sources) <= 5
    assert all(s.get("source_file") for s in sources)
    # Dense-only top-3 missed it; the BM25 leg should surface at least one
    # source whose text literally contains the keyword.
    assert any("autoconsommation" in s.get("content", "").lower() for s in sources)
