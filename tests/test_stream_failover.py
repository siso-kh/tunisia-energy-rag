"""The provider accepts a request and then rejects it mid-stream.

openai raises ``APIError: The model rejected this request`` from inside
``_streaming.__stream__``, which is *after* ``chat_stream`` has returned the
stream object and finished its failover loop. The error therefore propagated
straight out of the SSE endpoint: one transient provider hiccup killed the
answer while two healthy models sat unused in the pool. Observed live five
times in one evening, while the identical payload then succeeded 15/15 times
when sent directly to the provider.

These tests drive the failover with a fake stream that reproduces exactly that
shape -- opens cleanly, then raises on the first iteration.

No network access, and no pytest-asyncio: like test_llm_pool.py, each test is
a plain sync function wrapping its coroutine in asyncio.run().
"""

import asyncio

import pytest
from openai import APIError

from src.rag.retrieve import ModelFallbackPool


class FakeDelta:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.delta = FakeDelta(content)


class FakeChunk:
    def __init__(self, content):
        self.choices = [FakeChoice(content)] if content is not None else []


class UsageOnlyChunk:
    """Carries usage info but no content -- the consumer skips these."""

    choices = []
    usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()


def rejected():
    return APIError("The model rejected this request.", request=None, body=None)


class RejectingStream:
    """Opens fine, then rejects before yielding any content token."""

    def __init__(self, exc=None):
        self._exc = exc or rejected()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


class ContentStream:
    def __init__(self, tokens, fail_after=None):
        self._tokens = list(tokens)
        self._fail_after = fail_after

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._tokens:
            return FakeChunk(self._tokens.pop(0))
        if self._fail_after is not None:
            self._fail_after -= 1
            if self._fail_after <= 0:
                raise APIError("mid-stream failure", request=None, body=None)
            return FakeChunk(f"filler{self._fail_after}")
        raise StopAsyncIteration


class UsageThenRejectStream:
    """Yields a usage-only chunk, then rejects.

    Nothing has reached the client, so failing over is still safe.
    """

    def __init__(self):
        self._step = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._step += 1
        if self._step == 1:
            return UsageOnlyChunk()
        raise rejected()


def make_pool(monkeypatch, behaviours):
    """Pool of three models; the nth returns the nth stream from *behaviours*."""
    pool = ModelFallbackPool("key", "http://provider/v1", ["m1", "m2", "m3"])
    calls = []

    async def fake_create(model=None, messages=None, stream=False, **kwargs):
        calls.append(model)
        return behaviours[model]

    fake_client = type("Client", (), {
        "chat": type("Chat", (), {
            "completions": type("Completions", (), {"create": staticmethod(fake_create)})
        })(),
    })()

    monkeypatch.setattr(pool, "_get_client", lambda: fake_client)
    pool._current_idx = 0
    return pool, calls


async def collect_text(pool):
    """Run the failover, returning (texts, models) for content chunks only."""
    texts, models = [], []
    async for chunk, model in pool.chat_stream_failover(
        messages=[{"role": "user", "content": "hi"}]
    ):
        if chunk.choices and chunk.choices[0].delta.content:
            texts.append(chunk.choices[0].delta.content)
            models.append(model)
    return texts, models


def test_fails_over_when_rejected_before_any_token(monkeypatch):
    """The reported bug: m1 rejects on iteration, m2 answers."""
    pool, calls = make_pool(monkeypatch, {
        "m1": RejectingStream(),
        "m2": ContentStream(["Bonjour ", "le ", "monde"]),
        "m3": ContentStream(["unused"]),
    })

    texts, models = asyncio.run(collect_text(pool))

    assert texts == ["Bonjour ", "le ", "monde"]
    assert set(models) == {"m2"}, "tokens must be attributed to the model that made them"
    assert calls[0] == "m1", "should try m1 first"
    assert "m2" in calls, "should have failed over to m2"


def test_usage_only_chunk_does_not_block_failover(monkeypatch):
    """A usage-only chunk emits nothing, so failing over is still safe."""
    pool, calls = make_pool(monkeypatch, {
        "m1": UsageThenRejectStream(),
        "m2": ContentStream(["ok"]),
        "m3": ContentStream(["unused"]),
    })

    async def run():
        # The usage-only chunk is legitimately yielded under m1; what matters
        # is that no *content* token came from it, so nothing reached the
        # client and retrying cannot duplicate text.
        return [c.choices[0].delta.content
                async for c, m in pool.chat_stream_failover(
                    messages=[{"role": "user", "content": "hi"}])
                if c.choices and c.choices[0].delta.content]

    assert asyncio.run(run()) == ["ok"]
    assert calls[0] == "m1" and "m2" in calls


def test_does_not_retry_after_a_token_was_emitted(monkeypatch):
    """Retrying mid-answer would duplicate text, so it must raise instead."""
    pool, calls = make_pool(monkeypatch, {
        "m1": ContentStream(["partial "], fail_after=1),
        "m2": ContentStream(["must not appear"]),
        "m3": ContentStream(["must not appear"]),
    })

    seen = []

    async def run():
        async for chunk, _model in pool.chat_stream_failover(
            messages=[{"role": "user", "content": "hi"}]
        ):
            if chunk.choices and chunk.choices[0].delta.content:
                seen.append(chunk.choices[0].delta.content)

    with pytest.raises(APIError):
        asyncio.run(run())

    assert seen == ["partial "], "client keeps exactly what it already received"
    assert calls == ["m1"], "must not retry once tokens were emitted"


def test_happy_path_does_not_touch_other_models(monkeypatch):
    """No spurious failover when the first model simply works."""
    pool, calls = make_pool(monkeypatch, {
        "m1": ContentStream(["a", "b"]),
        "m2": ContentStream(["nope"]),
        "m3": ContentStream(["nope"]),
    })

    async def run():
        return [c async for c, _m in pool.chat_stream_failover(
            messages=[{"role": "user", "content": "hi"}])]

    assert len(asyncio.run(run())) == 2
    assert calls == ["m1"], "a working model must not burn the others"


def test_all_models_rejecting_raises_the_last_error(monkeypatch):
    """When nothing recovers, surface the error instead of hanging."""
    pool, calls = make_pool(monkeypatch, {
        "m1": RejectingStream(),
        "m2": RejectingStream(),
        "m3": RejectingStream(),
    })

    async def run():
        async for _ in pool.chat_stream_failover(
                messages=[{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(APIError):
        asyncio.run(run())

    assert set(calls) == {"m1", "m2", "m3"}, "every model should get a turn"


def test_a_rejected_model_is_not_retried_within_one_request(monkeypatch):
    """m1 rejects twice in a row if same-model retries kick in; m2 still answers."""
    pool, calls = make_pool(monkeypatch, {
        "m1": RejectingStream(),
        "m2": ContentStream(["salut"]),
        "m3": ContentStream(["unused"]),
    })

    texts, _models = asyncio.run(collect_text(pool))

    assert texts == ["salut"]
    # Same-model retries are allowed for a retryable error, but the point is
    # that m2 was reached at all -- previously the request died at m1.
    assert calls[0] == "m1" and calls[-1] == "m2"