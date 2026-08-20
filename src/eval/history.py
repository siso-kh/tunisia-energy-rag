"""Persistent eval history — append runs to ``data/eval/results.json``.

Each run is a timestamped snapshot with aggregate metrics + per-query detail,
so recall/MRR trends can be tracked over time and regressions detected.

The file layout::

    {
      "runs": [
        {
          "timestamp": "2026-08-20T14:30:00Z",
          "retriever": "hybrid",
          "k": 5,
          "n": 34,
          "mean_recall_at_k": 0.88,
          "mean_mrr": 0.773,
          "rows": [ ... per-query detail ... ]
        },
        ...
      ]
    }
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

RESULTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "eval", "results.json"
)

# Default regression threshold: fail if recall drops more than this fraction
# compared to the best historical run.
DEFAULT_REGRESSION_THRESHOLD = 0.05  # 5%


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_history(path: str = RESULTS_PATH) -> Dict[str, Any]:
    """Load existing results file, or return empty structure."""
    if not os.path.exists(path):
        return {"runs": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        # Corrupt or empty file — start fresh
        return {"runs": []}
    if "runs" not in data:
        data["runs"] = []
    return data


def save_history(history: Dict[str, Any], path: str = RESULTS_PATH) -> None:
    """Write the full history back to disk."""
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_run(
    eval_result: Dict[str, Any],
    retriever: str = "hybrid",
    path: str = RESULTS_PATH,
) -> Dict[str, Any]:
    """Append a single eval run to the history file.

    ``eval_result`` is the dict returned by ``evaluate_retrieval()``.
    Returns the newly appended run entry.
    """
    run = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retriever": retriever,
        "k": eval_result.get("k", 5),
        "n": eval_result.get("n", 0),
        "mean_recall_at_k": round(eval_result.get("mean_recall_at_k", 0.0), 4),
        "mean_mrr": round(eval_result.get("mean_mrr", 0.0), 4),
        "rows": eval_result.get("rows", []),
    }

    history = load_history(path)
    history["runs"].append(run)
    save_history(history, path)
    return run


def get_baseline(history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the best historical run by mean_recall_at_k (tiebreak: MRR).

    Returns ``None`` when no runs exist yet.
    """
    runs = history.get("runs", [])
    if not runs:
        return None
    return max(runs, key=lambda r: (r.get("mean_recall_at_k", 0), r.get("mean_mrr", 0)))


def get_latest(history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the most recent run, or ``None``."""
    runs = history.get("runs", [])
    return runs[-1] if runs else None


def check_regression(
    current: Dict[str, Any],
    baseline: Dict[str, Any],
    threshold: float = DEFAULT_REGRESSION_THRESHOLD,
) -> Dict[str, Any]:
    """Compare current run against a baseline.

    Returns a dict with:
      - ``regressed``: bool — True if recall dropped beyond threshold
      - ``recall_drop``: float — absolute drop in mean_recall_at_k
      - ``mrr_drop``: float — absolute drop in mean_mrr
      - ``details``: per-query regressions (queries that went from 1→0)
    """
    recall_drop = baseline.get("mean_recall_at_k", 0) - current.get("mean_recall_at_k", 0)
    mrr_drop = baseline.get("mean_mrr", 0) - current.get("mean_mrr", 0)

    # Per-query comparison: find queries that passed in baseline but fail now
    baseline_rows = {r["id"]: r for r in baseline.get("rows", []) if r.get("id")}
    current_rows = {r["id"]: r for r in current.get("rows", []) if r.get("id")}

    regressions = []
    for qid, b_row in baseline_rows.items():
        c_row = current_rows.get(qid)
        if c_row is None:
            continue
        if b_row.get("recall_at_k", 0) > 0 and c_row.get("recall_at_k", 0) == 0:
            regressions.append(
                {
                    "id": qid,
                    "query": c_row.get("query", "")[:80],
                    "baseline_recall": b_row["recall_at_k"],
                    "current_recall": c_row["recall_at_k"],
                    "baseline_top": b_row.get("top_sources", [])[:3],
                    "current_top": c_row.get("top_sources", [])[:3],
                }
            )

    return {
        "regressed": recall_drop > threshold,
        "recall_drop": round(recall_drop, 4),
        "mrr_drop": round(mrr_drop, 4),
        "threshold": threshold,
        "baseline_timestamp": baseline.get("timestamp"),
        "regressions": regressions,
    }


def format_history_summary(history: Dict[str, Any], last_n: int = 10) -> str:
    """Human-readable trend table of the last ``last_n`` runs."""
    runs = history.get("runs", [])
    if not runs:
        return "No eval history yet."

    recent = runs[-last_n:]
    lines = [
        f"{'Timestamp':<28} {'Retriever':<10} {'Recall@k':<10} {'MRR':<8} {'Queries':<8}",
        "-" * 68,
    ]
    for run in recent:
        ts = run.get("timestamp", "?")[:19]
        lines.append(
            f"{ts:<28} {run.get('retriever', '?'):<10} "
            f"{run.get('mean_recall_at_k', 0):<10.4f} "
            f"{run.get('mean_mrr', 0):<8.4f} "
            f"{run.get('n', 0):<8}"
        )

    if len(runs) > 1:
        best = get_baseline(history)
        latest = get_latest(history)
        if best and latest:
            lines.append("")
            lines.append(
                f"Best recall: {best['mean_recall_at_k']:.4f} "
                f"(at {best.get('timestamp', '?')[:19]})"
            )
            lines.append(
                f"Latest recall: {latest['mean_recall_at_k']:.4f} "
                f"(at {latest.get('timestamp', '?')[:19]})"
            )
            drop = best["mean_recall_at_k"] - latest["mean_recall_at_k"]
            if drop > 0:
                lines.append(f"Drop from best: {drop:.4f}")
            elif drop < 0:
                lines.append(f"Improvement from best: {-drop:.4f}")
            else:
                lines.append("No change from best.")

    return "\n".join(lines)
