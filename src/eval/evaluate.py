"""Run retrieval evaluation against the golden Q/A set.

Compares the hybrid retriever (vector + BM25 + optional rerank) against the
pure-vector baseline on the same golden queries, reporting recall@k and MRR.

Usage:
    python -m src.eval.evaluate                     # hybrid retriever, k=5
    python -m src.eval.evaluate --retriever vector  # pure-vector baseline
    python -m src.eval.evaluate --k 10 --json       # machine-readable output
    RERANK_ENABLED=false python -m src.eval.evaluate   # skip cross-encoder

Note: the first run downloads the embedding weights (cached after) and, with
reranking enabled, the cross-encoder model on first use.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.eval.metrics import evaluate_retrieval  # noqa: E402

GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "eval", "golden_qa.json"
)


def _load_golden(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _vector_retriever(query: str, n_results: int = 5):
    from src.rag.retrieve import retrieve_context_structured

    return retrieve_context_structured(query, n_results=n_results)


def _hybrid_retriever(query: str, n_results: int = 5):
    from src.rag.hybrid import retrieve_context_hybrid

    return retrieve_context_hybrid(query, n_results=n_results)


RETRIEVERS = {"vector": _vector_retriever, "hybrid": _hybrid_retriever}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retriever",
        choices=sorted(RETRIEVERS),
        default="hybrid",
        help="Which retriever to evaluate (default: hybrid).",
    )
    parser.add_argument("--k", type=int, default=5, help="Top-k for recall@k (default: 5).")
    parser.add_argument("--golden", default=None, help="Override the golden set path.")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of a table.")
    args = parser.parse_args()

    golden_path = args.golden or GOLDEN_PATH
    if not os.path.exists(golden_path):
        print(f"[eval] golden set not found: {golden_path}")
        sys.exit(1)

    golden = _load_golden(golden_path)
    results = evaluate_retrieval(golden, RETRIEVERS[args.retriever], k=args.k)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f"\n=== Retrieval evaluation [{args.retriever}, k={args.k}] ===")
    for row in results["rows"]:
        mark = "✅" if row["recall_at_k"] else "❌"
        expected = " | ".join(row.get("expected_sources") or [])
        print(
            f"{mark} [{row['id']}] {row['query'][:60]}\n"
            f"    expected: {expected}\n"
            f"    recall@{args.k}: {row['recall_at_k']:.2f}  mrr: {row['mrr']:.3f}"
        )
        if row.get("error"):
            print(f"    retriever error: {row['error']}")
    print(
        f"\nSummary: mean recall@{args.k} = {results['mean_recall_at_k']:.2f} "
        f"· mean MRR = {results['mean_mrr']:.3f} ({results['n']} golden queries)"
    )


if __name__ == "__main__":
    main()
