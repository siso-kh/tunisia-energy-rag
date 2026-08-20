"""Tests for the eval history module (src/eval/history.py)."""

import json
import os
import tempfile

from src.eval.history import (
    append_run,
    check_regression,
    format_history_summary,
    get_baseline,
    get_latest,
    load_history,
    save_history,
)


def _tmp_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return path


def _sample_result(n=3, recall=1.0, mrr=1.0):
    """Minimal eval result dict matching evaluate_retrieval() output."""
    rows = [
        {
            "id": f"q{i}",
            "query": f"query {i}",
            "expected_sources": [f"doc{i}.pdf"],
            "recall_at_k": recall,
            "mrr": mrr,
            "top_sources": [f"doc{i}.pdf"],
            "error": None,
        }
        for i in range(n)
    ]
    return {
        "k": 5,
        "n": n,
        "mean_recall_at_k": recall,
        "mean_mrr": mrr,
        "rows": rows,
    }


# ── load / save ────────────────────────────────────────────────────────

def test_load_empty():
    path = _tmp_path()
    os.unlink(path)  # ensure it doesn't exist
    h = load_history(path)
    assert h == {"runs": []}


def test_save_and_load():
    path = _tmp_path()
    data = {"runs": [{"timestamp": "2026-01-01T00:00:00Z"}]}
    save_history(data, path)
    loaded = load_history(path)
    assert loaded == data
    os.unlink(path)


def test_load_existing_file():
    path = _tmp_path()
    with open(path, "w") as f:
        json.dump({"runs": [{"ts": 1}, {"ts": 2}]}, f)
    h = load_history(path)
    assert len(h["runs"]) == 2
    os.unlink(path)


# ── append_run ─────────────────────────────────────────────────────────

def test_append_run_creates_file():
    path = _tmp_path()
    os.unlink(path)  # ensure missing
    result = _sample_result()
    run = append_run(result, retriever="hybrid", path=path)
    assert run["retriever"] == "hybrid"
    assert run["k"] == 5
    assert run["n"] == 3
    assert run["mean_recall_at_k"] == 1.0
    assert "timestamp" in run
    assert len(run["rows"]) == 3
    os.unlink(path)


def test_append_multiple_runs():
    path = _tmp_path()
    append_run(_sample_result(recall=0.9), path=path)
    append_run(_sample_result(recall=1.0), path=path)
    h = load_history(path)
    assert len(h["runs"]) == 2
    assert h["runs"][0]["mean_recall_at_k"] == 0.9
    assert h["runs"][1]["mean_recall_at_k"] == 1.0
    os.unlink(path)


# ── get_baseline / get_latest ──────────────────────────────────────────

def test_get_baseline_picks_best():
    path = _tmp_path()
    append_run(_sample_result(recall=0.8), path=path)
    append_run(_sample_result(recall=1.0), path=path)
    append_run(_sample_result(recall=0.9), path=path)
    h = load_history(path)
    best = get_baseline(h)
    assert best["mean_recall_at_k"] == 1.0
    os.unlink(path)


def test_get_baseline_empty():
    assert get_baseline({"runs": []}) is None


def test_get_latest():
    path = _tmp_path()
    append_run(_sample_result(recall=0.5), path=path)
    append_run(_sample_result(recall=0.7), path=path)
    h = load_history(path)
    latest = get_latest(h)
    assert latest["mean_recall_at_k"] == 0.7
    os.unlink(path)


def test_get_latest_empty():
    assert get_latest({"runs": []}) is None


# ── check_regression ───────────────────────────────────────────────────

def test_no_regression():
    baseline = _sample_result(recall=1.0, mrr=1.0)
    current = _sample_result(recall=1.0, mrr=1.0)
    report = check_regression(current, baseline, threshold=0.05)
    assert not report["regressed"]
    assert report["recall_drop"] == 0.0


def test_regression_detected():
    baseline = _sample_result(n=5, recall=1.0, mrr=1.0)
    # Current: 2 queries regressed from 1→0
    rows = [
        {"id": "q0", "query": "q0", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
        {"id": "q1", "query": "q1", "recall_at_k": 0.0, "mrr": 0.0, "top_sources": ["wrong.pdf"]},
        {"id": "q2", "query": "q2", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
        {"id": "q3", "query": "q3", "recall_at_k": 0.0, "mrr": 0.0, "top_sources": ["wrong.pdf"]},
        {"id": "q4", "query": "q4", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
    ]
    current = {"k": 5, "n": 5, "mean_recall_at_k": 0.6, "mean_mrr": 0.6, "rows": rows}
    report = check_regression(current, baseline, threshold=0.05)
    assert report["regressed"]
    assert report["recall_drop"] == 0.4
    assert len(report["regressions"]) == 2
    assert report["regressions"][0]["id"] == "q1"


def test_small_drop_not_regressed():
    baseline = _sample_result(n=4, recall=1.0, mrr=1.0)
    rows = [
        {"id": "q0", "query": "q0", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
        {"id": "q1", "query": "q1", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
        {"id": "q2", "query": "q2", "recall_at_k": 1.0, "mrr": 1.0, "top_sources": []},
        {"id": "q3", "query": "q3", "recall_at_k": 0.0, "mrr": 0.0, "top_sources": []},
    ]
    current = {"k": 5, "n": 4, "mean_recall_at_k": 0.75, "mean_mrr": 0.75, "rows": rows}
    # 0.25 drop > 0.05 threshold → should be regressed
    report = check_regression(current, baseline, threshold=0.05)
    assert report["regressed"]

    # Same drop but higher threshold → not regressed
    report2 = check_regression(current, baseline, threshold=0.3)
    assert not report2["regressed"]


# ── format_history_summary ─────────────────────────────────────────────

def test_format_empty_history():
    out = format_history_summary({"runs": []})
    assert "No eval history" in out


def test_format_with_runs():
    path = _tmp_path()
    append_run(_sample_result(recall=0.9, mrr=0.8), path=path)
    append_run(_sample_result(recall=1.0, mrr=1.0), path=path)
    h = load_history(path)
    out = format_history_summary(h)
    assert "Recall@k" in out
    assert "0.9" in out
    assert "1.0" in out
    assert "Best recall" in out
    os.unlink(path)


# ── Schema: results.json structure ─────────────────────────────────────

def test_results_json_schema():
    """If results.json exists, verify it has the expected structure."""
    from src.eval.history import RESULTS_PATH

    if not os.path.exists(RESULTS_PATH):
        return  # no history yet — skip
    h = load_history()
    assert "runs" in h
    assert isinstance(h["runs"], list)
    for run in h["runs"]:
        assert "timestamp" in run
        assert "retriever" in run
        assert "k" in run
        assert "n" in run
        assert "mean_recall_at_k" in run
        assert "mean_mrr" in run
        assert "rows" in run
