"""Run retrieval evaluation against the golden Q/A set.

Compares the hybrid retriever (vector + BM25 + optional rerank) against the
pure-vector baseline on the same golden queries, reporting recall@k and MRR.

Usage:
    python -m src.eval.evaluate                       # hybrid retriever, k=5
    python -m src.eval.evaluate --retriever vector    # pure-vector baseline
    python -m src.eval.evaluate --k 10 --json         # machine-readable output
    python -m src.eval.evaluate --save                # persist to results.json
    python -m src.eval.evaluate --history             # show trend table
    python -m src.eval.evaluate --baseline-check      # fail if recall regresses
    RERANK_ENABLED=false python -m src.eval.evaluate  # skip cross-encoder

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
    parser.add_argument(
        "--save",
        action="store_true",
        help="Append this run to data/eval/results.json for history tracking.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show the trend table from results.json and exit (no eval run).",
    )
    parser.add_argument(
        "--history-json",
        action="store_true",
        help="Dump full results.json as raw JSON and exit.",
    )
    parser.add_argument(
        "--baseline-check",
        action="store_true",
        help="After eval, compare against the best historical run. "
        "Exit with code 1 if recall regresses beyond the threshold.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Regression threshold for --baseline-check (default: 0.05 = 5%%).",
    )
    args = parser.parse_args()

    # ── History-only modes ──────────────────────────────────────────────
    from src.eval.history import (
        RESULTS_PATH,
        append_run,
        check_regression,
        format_history_summary,
        get_baseline,
        load_history,
    )

    if args.history:
        history = load_history()
        print(format_history_summary(history))
        return

    if args.history_json:
        history = load_history()
        print(json.dumps(history, ensure_ascii=False, indent=2))
        return

    # ── Run evaluation ──────────────────────────────────────────────────
    golden_path = args.golden or GOLDEN_PATH
    if not os.path.exists(golden_path):
        print(f"[eval] golden set not found: {golden_path}")
        sys.exit(1)

    golden = _load_golden(golden_path)
    results = evaluate_retrieval(golden, RETRIEVERS[args.retriever], k=args.k)

    # ── Print results ───────────────────────────────────────────────────
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
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

    # ── Save to history ─────────────────────────────────────────────────
    if args.save:
        run = append_run(results, retriever=args.retriever)
        print(f"\n[eval] Saved run to {RESULTS_PATH} (timestamp: {run['timestamp']})")

    # ── Baseline comparison ─────────────────────────────────────────────
    if args.baseline_check:
        history = load_history()
        baseline = get_baseline(history)
        if baseline is None:
            print("[eval] No baseline found — this is the first run. Use --save first.")
            if args.save:
                print("[eval] Run saved as the new baseline.")
            sys.exit(0)

        report = check_regression(results, baseline, threshold=args.threshold)

        if report["regressed"]:
            print(
                f"\n❌ REGRESSION DETECTED: recall dropped by {report['recall_drop']:.4f} "
                f"(threshold: {report['threshold']})"
            )
            if report["regressions"]:
                print(f"\n{len(report['regressions'])} queries regressed (1→0):")
                for reg in report["regressions"]:
                    print(f"  - [{reg['id']}] {reg['query']}")
                    print(f"    baseline top: {reg['baseline_top']}")
                    print(f"    current top:  {reg['current_top']}")
            sys.exit(1)
        else:
            print(
                f"\n✅ No regression (recall drop: {report['recall_drop']:.4f}, "
                f"threshold: {report['threshold']})"
            )
            if report["mrr_drop"] > 0:
                print(f"   MRR drop: {report['mrr_drop']:.4f}")


if __name__ == "__main__":
    main()
