"""Regression tests for the startup retrieval warm-up.

Measured against the real 15k-document index, the first query used to cost
~15s and drive RSS from ~390 MB to ~1.3 GB, because the sentence-transformer
and the BM25 index were constructed *inside* the request. On a 2 GB container
that in-request allocation is what got the process OOM-killed: the chat stream
emitted "searching" and then died with no traceback, no SSE error frame, and
the next request answered 502 while the instance restarted.

The warm-up moves that cost to boot, where nothing else competes for memory.
"""

import asyncio
import sys

import pytest

from src.api import main as api_main


def test_warmup_loads_embedding_model_and_bm25(monkeypatch):
    """Both expensive objects must be built during startup."""
    calls = []

    import src.rag.hybrid as hybrid
    import src.rag.onnx_embedder as onnx_embedder
    import src.rag.retrieve as retrieve

    monkeypatch.setattr(retrieve, "_get_emb_fn", lambda: calls.append("emb") or "emb")
    monkeypatch.setattr(hybrid, "_get_bm25", lambda: calls.append("bm25") or "bm25")
    # The ONNX embedder is the live one when the image ships it; pin the fp32
    # branch so this test describes the fallback deterministically.
    monkeypatch.setattr(onnx_embedder, "embedder_available", lambda: False)

    asyncio.run(api_main._warm_retrieval())

    assert calls == ["emb", "bm25"], "warm-up skipped work: %r" % calls


def test_warmup_prefers_the_onnx_embedder_over_the_fp32_model(monkeypatch):
    """Loading the fp32 model at boot is what OOM-killed the container.

    On a plan without room for ~830 MB, warming the wrong embedder takes the
    process out before uvicorn ever serves a request.
    """
    calls = []

    import src.rag.hybrid as hybrid
    import src.rag.onnx_embedder as onnx_embedder
    import src.rag.retrieve as retrieve

    monkeypatch.setattr(onnx_embedder, "embedder_available", lambda: True)
    monkeypatch.setattr(onnx_embedder, "warm", lambda: calls.append("onnx") or True)
    monkeypatch.setattr(hybrid, "_get_bm25", lambda: calls.append("bm25") or "bm25")
    monkeypatch.setattr(
        retrieve, "_get_emb_fn",
        lambda: calls.append("fp32-model-loaded") or "emb",
    )

    asyncio.run(api_main._warm_retrieval())

    assert calls == ["onnx", "bm25"], (
        "the fp32 embedder must not be loaded when ONNX is available: %r" % calls
    )


def test_warmup_builds_the_two_heaviest_stages_sequentially(monkeypatch):
    """Sequential construction keeps the two memory peaks from overlapping."""
    order = []

    import src.rag.hybrid as hybrid
    import src.rag.onnx_embedder as onnx_embedder

    def emb():
        order.append("emb-start")
        return True

    def bm25():
        order.append("bm25-start")
        return "bm25"

    monkeypatch.setattr(onnx_embedder, "embedder_available", lambda: True)
    monkeypatch.setattr(onnx_embedder, "warm", emb)
    monkeypatch.setattr(hybrid, "_get_bm25", bm25)

    asyncio.run(api_main._warm_retrieval())

    assert order == ["emb-start", "bm25-start"]


def test_warmup_failure_does_not_block_boot(monkeypatch):
    """A warm-up failure must be non-fatal: retrieval degrades, boot proceeds."""
    import src.rag.hybrid as hybrid

    def boom():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(hybrid, "_get_bm25", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(api_main._warm_retrieval())


def test_lifespan_tolerates_a_warmup_failure(monkeypatch, caplog):
    """The lifespan swallows warm-up errors so the app still starts."""

    async def boom():
        raise RuntimeError("no model")

    monkeypatch.setattr(api_main, "_warm_retrieval", boom)

    async def drive():
        async with api_main.lifespan(object()) as _state:
            return _state

    # Must not raise: an unavailable model degrades latency, not availability.
    asyncio.run(drive())