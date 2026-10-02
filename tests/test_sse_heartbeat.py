"""Regression tests for SSE keep-alives on the chat stream.

The production failure was structural silence: the pipeline emits the
"searching" status, then blocks for ~40 s inside a threadpool while hybrid
retrieval runs, sending zero bytes. Render's edge drops a response that goes
quiet, so the client saw a permanent spinner and the next request got a 502 --
with no error frame, because upstream was perfectly healthy.

The fix wraps the event generator in a queue-backed pump plus a ticker task, so
a `: keep-alive` comment goes out on a fixed interval regardless of what the
pipeline is doing.
"""

import asyncio

import pytest

from src.api import main as api_main


def _collect(source, interval=0.05):
    async def run():
        return [frame async for frame in api_main._with_heartbeats(source)]

    return asyncio.run(run())


def test_serialize_heartbeat_is_a_comment(monkeypatch):
    """Heartbeats must not be data frames: a client's SSE parser would choke."""
    monkeypatch.setattr(api_main, "HEARTBEAT_INTERVAL", 0.05)
    assert api_main._sse({"type": "heartbeat"}) == ": keep-alive\n\n"


def test_silent_source_gets_keepalives(monkeypatch):
    """A source that never yields must still produce periodic bytes."""
    monkeypatch.setattr(api_main, "HEARTBEAT_INTERVAL", 0.05)

    async def mute():
        await asyncio.sleep(0.35)
        yield api_main._sse({"type": "done"})

    frames = _collect(mute())

    heartbeats = [f for f in frames if f == ": keep-alive\n\n"]
    assert heartbeats, "stream went silent: no keep-alive was emitted"
    assert frames[-1].startswith("data: "), "real frames must still get through"
    assert len(heartbeats) >= 2, f"expected several keep-alives, got {len(heartbeats)}"


def test_ordering_is_preserved(monkeypatch):
    """Pumping through a queue must not reorder or drop pipeline frames."""
    monkeypatch.setattr(api_main, "HEARTBEAT_INTERVAL", 0.01)

    async def source():
        for i in range(5):
            await asyncio.sleep(0.02)
            yield api_main._sse({"type": "token", "i": i})

    frames = _collect(source())
    data = [f for f in frames if f.startswith("data: ")]
    assert len(data) == 5
    for idx, frame in enumerate(data):
        assert f'"i": {idx}' in frame, f"out of order at {idx}: {frame}"


def test_source_exception_reaches_the_caller(monkeypatch):
    """The pump must forward a failure, not swallow it into a clean close."""
    monkeypatch.setattr(api_main, "HEARTBEAT_INTERVAL", 0.01)

    async def boom():
        yield api_main._sse({"type": "status", "message": "searching"})
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        _collect(boom())


def test_early_client_disconnect_cleans_up(monkeypatch):
    """Abandoning the stream must close the pipeline, not orphan it.

    An orphaned pipeline keeps a concurrency slot and the whole retrieval stack
    busy, which is how one abandoned request turns into a queue of 502s.
    """
    monkeypatch.setattr(api_main, "HEARTBEAT_INTERVAL", 0.01)

    async def run():
        closed = asyncio.Event()

        async def source():
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield api_main._sse({"type": "status", "message": "searching"})
            except asyncio.CancelledError:
                raise
            finally:
                closed.set()

        gen = api_main._with_heartbeats(source())

        async def consume():
            async for _ in gen:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # This is what Starlette does when the client hangs up: cancel the task
        # that is iterating the response body.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), timeout=1.0)

        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not leftover, f"stream tasks leaked: {leftover}"

    asyncio.run(run())
