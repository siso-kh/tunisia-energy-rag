"""Retrieval evaluation metrics (pure functions, unit-testable).

The golden set maps queries to the source file that genuinely contains the
answer. These metrics measure whether the retriever surfaces that source in
its top-k — the prerequisite for a grounded LLM answer.

No LLM calls: retrieval-only metrics run offline in seconds and cost nothing.
"""

from typing import Any, Callable, Dict, List


def _normalize_expected(expected_source: Any) -> List[str]:
    """Accept a single source name or a list; always return a list of names."""
    if isinstance(expected_source, str):
        return [expected_source]
    return list(expected_source or [])


def recall_at_k(
    retrieved_sources: List[Dict[str, Any]],
    expected_source: Any,
    k: int = 5,
) -> float:
    """1.0 if any expected source appears in the top-k retrieved, else 0.0.

    ``expected_source`` may be a single file name or a list of acceptable ones
    (a query can genuinely be answered from several corpus documents).
    """
    expected = _normalize_expected(expected_source)
    for src in retrieved_sources[:k]:
        if src.get("source_file") in expected:
            return 1.0
    return 0.0


def mrr(
    retrieved_sources: List[Dict[str, Any]],
    expected_source: Any,
) -> float:
    """Best reciprocal rank among the expected sources (1/rank, 0.0 if absent)."""
    expected = _normalize_expected(expected_source)
    for rank, src in enumerate(retrieved_sources, start=1):
        if src.get("source_file") in expected:
            return 1.0 / rank
    return 0.0


def evaluate_retrieval(
    golden_entries: List[Dict[str, Any]],
    retriever: Callable[[str, int], List[Dict[str, Any]]],
    k: int = 5,
) -> Dict[str, Any]:
    """Run every golden query through ``retriever(query, n_results=k)``.

    Returns a summary dict with per-row detail plus aggregate
    ``mean_recall_at_k`` / ``mean_mrr``.
    """
    rows = []
    for entry in golden_entries:
        try:
            sources = retriever(entry["query"], n_results=k) or []
        except Exception as exc:  # a broken retriever must not kill the whole run
            sources = []
            error = f"{type(exc).__name__}: {exc}"
        else:
            error = None
        expected = entry.get("expected_source") or entry.get("expected_sources")
        rows.append(
            {
                "id": entry.get("id"),
                "query": entry.get("query"),
                "expected_sources": _normalize_expected(expected),
                "recall_at_k": recall_at_k(sources, expected, k),
                "mrr": mrr(sources, expected),
                "top_sources": [s.get("source_file") for s in sources[:k]],
                "error": error,
            }
        )

    n = len(rows)
    return {
        "k": k,
        "n": n,
        "mean_recall_at_k": sum(r["recall_at_k"] for r in rows) / n if n else 0.0,
        "mean_mrr": sum(r["mrr"] for r in rows) / n if n else 0.0,
        "rows": rows,
    }
