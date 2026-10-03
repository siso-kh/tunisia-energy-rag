"""The app has no shell on Render, so /health is the only diagnostic channel.

These tests pin that the probe stays green (it is the deploy health check)
while still reporting enough to tell apart the failure modes seen in
production: a stream the app abandoned, a stream the edge cancelled, and a
process that died mid-request.
"""

import asyncio
from contextlib import aclosing

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main


def test_health_stays_green_and_reports_process_facts():
    with TestClient(api_main.app) as client:
        payload = client.get("/health").json()

    assert payload["status"] == "healthy", "the deploy health check must not go red"
    assert payload["api"] == "online"
    assert payload["boot_id"] and payload["uptime_s"] >= 0
    assert "rss_mb" in payload and "peak_rss_mb" in payload
    assert "mem_limit_mb" in payload
    assert set(payload["streams"]) == {
        "started", "done", "truncated", "timeout", "cancelled", "error",
    }
    assert "retrieval" in payload
    assert "models" in payload


def test_memory_limit_is_read_from_cgroups(monkeypatch, tmp_path):
    """A 512 MB plan is the difference between a working app and an OOM kill."""
    cgroup = tmp_path / "memory.max"
    cgroup.write_text("536870912\n", encoding="utf-8")
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("memory.max"):
            return real_open(str(cgroup), *a, **k)
        raise OSError("not found")

    monkeypatch.setattr("builtins.open", fake_open)
    assert api_main._memory_limit_mb() == 512

    cgroup.write_text("max\n", encoding="utf-8")
    assert api_main._memory_limit_mb() is None, "'max' means unlimited, not 0"


def test_undersized_container_is_reported_not_ignored(monkeypatch, caplog):
    monkeypatch.setattr(api_main, "_memory_limit_mb", lambda: 512)
    with caplog.at_level("ERROR", logger="src.api.main"):
        limit = api_main._check_memory_budget()

    assert limit == 512
    assert any("memory limit is 512 MB" in r.message for r in caplog.records), (
        "an undersized container must be reported, not silently accepted"
    )


def test_stream_outcomes_are_counted_separately():
    """done / truncated / cancelled must be distinguishable after a request."""
    before = dict(api_main._STREAM_STATS)

    async def one(sse_frames, cancelled=False):
        async def gen():
            for frame in sse_frames:
                yield frame
                if cancelled:
                    raise asyncio.CancelledError()

        # Exercise the same accounting the endpoint performs.
        api_main._STREAM_STATS["started"] += 1
        saw_done = False
        try:
            async for frame in api_main._with_heartbeats(gen()):
                if frame.startswith('data: {"type": "done"'):
                    saw_done = True
        except asyncio.CancelledError:
            api_main._STREAM_STATS["cancelled"] += 1
        else:
            api_main._STREAM_STATS["done" if saw_done else "truncated"] += 1

    asyncio.run(one([api_main._sse({"type": "status", "message": "searching"})]))
    asyncio.run(one([api_main._sse({"type": "done", "answer": "x", "sources": []})]))
    asyncio.run(one([api_main._sse({"type": "status", "message": "searching"})],
                    cancelled=True))

    after = dict(api_main._STREAM_STATS)
    assert after["started"] - before["started"] == 3
    assert after["truncated"] - before["truncated"] == 1
    assert after["done"] - before["done"] == 1
    assert after["cancelled"] - before["cancelled"] == 1


def test_retrieval_stats_record_success_and_failure(monkeypatch):
    """retrieve.retrieval_stats() is what /health uses to explain a dead stream."""
    import src.rag.retrieve as retrieve

    monkeypatch.setattr(retrieve, "_LAST_RETRIEVAL", dict(retrieve.retrieval_stats()))

    async def no_rewrite(query, history):
        return query

    monkeypatch.setattr(retrieve, "rewrite_query_with_history", no_rewrite)

    def ok(*_a, **_k):
        return [{
            "title": "t", "content": "c", "source_file": "f.pdf",
            "page": 1, "score": 0.5,
        }]

    monkeypatch.setattr(retrieve, "retrieve_context_hybrid", ok)

    async def drain():
        # Stop at "sources": generation would call the live provider, and the
        # retrieval bookkeeping is all this test is about.
        async with aclosing(retrieve.stream_pipeline("What is STEG in Tunisia?")) as events:
            seen = []
            async for event in events:
                seen.append(event["type"])
                if event["type"] == "sources":
                    break
            return seen

    seen = asyncio.run(drain())
    assert seen[:2] == ["status", "status"]
    stats = retrieve.retrieval_stats()
    assert stats["last_sources"] == 1
    assert stats["last_error"] is None
    assert stats["last_seconds"] is not None

    def boom(*_a, **_k):
        raise RuntimeError("chroma exploded")

    monkeypatch.setattr(retrieve, "retrieve_context_hybrid", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(drain())

    failed = retrieve.retrieval_stats()
    assert "chroma exploded" in failed["last_error"]
    assert failed["last_error_seconds"] is not None
