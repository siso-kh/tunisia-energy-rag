"""Tests for the retrieval evaluation harness (src/eval/metrics.py)."""

import json
import os

from src.eval.metrics import evaluate_retrieval, mrr, recall_at_k


def _src(name):
    return {"source_file": name, "content": "..."}


# ---------------------------------------------------------------------------
# recall@k
# ---------------------------------------------------------------------------

def test_recall_at_k_hit():
    srcs = [_src("a.pdf"), _src("b.pdf"), _src("c.pdf")]
    assert recall_at_k(srcs, "b.pdf", k=3) == 1.0


def test_recall_at_k_miss():
    assert recall_at_k([_src("a.pdf")], "z.pdf", k=5) == 0.0


def test_recall_at_k_truncation():
    srcs = [_src("a.pdf"), _src("b.pdf")]
    assert recall_at_k(srcs, "b.pdf", k=1) == 0.0


def test_recall_at_k_accepts_multiple_expected():
    srcs = [_src("a.pdf"), _src("b.pdf")]
    # any of the acceptable sources in top-k counts as a hit
    assert recall_at_k(srcs, ["z.pdf", "b.pdf"], k=5) == 1.0
    assert recall_at_k(srcs, ["z.pdf", "w.pdf"], k=5) == 0.0
    # list matching is also rank-aware
    assert recall_at_k(srcs, ["z.pdf", "b.pdf"], k=1) == 0.0


# ---------------------------------------------------------------------------
# MRR
# ---------------------------------------------------------------------------

def test_mrr_ranks():
    srcs = [_src("a.pdf"), _src("b.pdf"), _src("c.pdf")]
    assert mrr(srcs, "a.pdf") == 1.0
    assert abs(mrr(srcs, "c.pdf") - 1 / 3) < 1e-9
    assert mrr(srcs, "z.pdf") == 0.0


def test_mrr_best_rank_among_expected():
    srcs = [_src("a.pdf"), _src("b.pdf"), _src("c.pdf")]
    assert abs(mrr(srcs, ["z.pdf", "c.pdf"]) - 1 / 3) < 1e-9
    assert mrr(srcs, ["z.pdf", "a.pdf"]) == 1.0


# ---------------------------------------------------------------------------
# evaluate_retrieval
# ---------------------------------------------------------------------------

def test_evaluate_retrieval_aggregates():
    golden = [
        {"id": "q1", "query": "a ?", "expected_source": "a.pdf"},
        {"id": "q2", "query": "b ?", "expected_source": "z.pdf"},
    ]

    def retriever(query, n_results=5):
        return [_src("a.pdf"), _src("b.pdf")]

    out = evaluate_retrieval(golden, retriever, k=5)
    assert out["n"] == 2
    assert out["mean_recall_at_k"] == 0.5
    assert abs(out["mean_mrr"] - 0.5) < 1e-9
    assert out["rows"][0]["recall_at_k"] == 1.0
    assert out["rows"][1]["recall_at_k"] == 0.0


def test_evaluate_retrieval_survives_retriever_error():
    golden = [{"id": "q1", "query": "a ?", "expected_source": "a.pdf"}]

    def retriever(query, n_results=5):
        raise RuntimeError("down")

    out = evaluate_retrieval(golden, retriever)
    assert out["mean_recall_at_k"] == 0.0
    assert out["rows"][0]["error"]


# ---------------------------------------------------------------------------
# Golden set schema (the file lives in the repo)
# ---------------------------------------------------------------------------

def test_golden_set_schema():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "eval", "golden_qa.json")
    with open(path, encoding="utf-8") as f:
        golden = json.load(f)

    assert len(golden) >= 4
    queries = set()
    for entry in golden:
        expected = entry.get("expected_source") or entry.get("expected_sources")
        assert entry["id"] and entry["query"]
        assert isinstance(expected, list), "expected_sources must be a list"
        # Empty list is valid for out-of-scope / edge-case queries
        queries.add(entry["query"])
    assert len(queries) == len(golden), "golden queries must be unique"
