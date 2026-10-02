"""Regression tests for the LLM fallback pool (src/rag/retrieve.py).

Background: the provider serves only a subset of the configured models. Two of
the pool entries returned HTTP 404 (not served at all) and three returned
402/429 (per-model quota exhausted). The pool previously applied a single flat
120 s cooldown to every failure, so those dead entries were retried forever and
most requests spent their time walking them before reaching a model that could
answer. ``combo/freemodels`` additionally fails ~17% of calls with a
status-less ``APIError`` from the router.

These tests pin the cause-aware behaviour: permanent disable on 404, a longer
pause on quota errors, a single same-model retry for transient failures, and a
default model list containing only models measured as usable.

No network access: the OpenAI client is replaced with a stub that raises
synthetic exceptions.
"""

import asyncio

import httpx
import pytest
from openai import APIError, APIStatusError, APITimeoutError

from src.rag import retrieve


# ---------------------------------------------------------------------------
# Real OpenAI exceptions
#
# The pool classifies failures by isinstance and by `exc.response.status_code`,
# so these tests use the genuine SDK exception types rather than look-alikes:
# a stand-in class would not be recognised by _is_retryable/_quarantine and
# would make the assertions meaningless.
# ---------------------------------------------------------------------------

def _request():
    return httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def status_error(code, message="synthetic failure"):
    """A provider error that carries an HTTP status (404, 429, ...)."""
    return APIStatusError(
        message,
        response=httpx.Response(status_code=code, request=_request()),
        body=None,
    )


def api_error(message="The model rejected this request."):
    """A router rejection with no HTTP status attached (the flaky combo case)."""
    return APIError(message, request=_request(), body=None)


def timeout_error():
    """A timeout; carries no response at all."""
    return APITimeoutError(request=_request())


# ---------------------------------------------------------------------------
# Stub client plumbing
# ---------------------------------------------------------------------------

class StubCompletions:
    """Replays a scripted sequence of results per model."""

    def __init__(self, script, calls):
        self._script = script
        self._calls = calls

    async def create(self, *, model, messages, stream=False, timeout=None, **kwargs):
        self._calls.append(model)
        queue = self._script.setdefault(model, [])
        outcome = queue.pop(0) if queue else self._script.get("__default__", [])
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, list):
            async def gen(items=outcome):
                for text in items:
                    yield text
            return gen()
        return outcome


def make_pool(script, models=None, calls=None):
    """Build a pool whose OpenAI client is replaced by a scripted stub."""
    pool = retrieve.ModelFallbackPool(
        "key", "https://example.invalid/v1",
        models if models is not None else retrieve._DEFAULT_MODELS,
    )
    calls = calls if calls is not None else []
    pool._client = type("StubClient", (), {"is_closed": False, "chat": type(
        "Chat", (), {"completions": StubCompletions(script, calls)}
    )})()
    return pool, calls


# ---------------------------------------------------------------------------
# Default model list
# ---------------------------------------------------------------------------

def test_default_models_exclude_models_the_provider_does_not_serve():
    """404 models must not reappear in the default pool.

    glm-5.3-flash-free and nemotron-3-ultra returned 404 on every call, and
    deepseek-v4-flash / qwen3.8-27b / stepfun-3.7-flash returned 402/429.
    """
    dead = {
        "glm-5.3-flash-free",
        "nemotron-3-ultra",
        "deepseek-v4-flash",
        "qwen3.8-27b",
        "stepfun-3.7-flash",
    }
    assert not (dead & set(retrieve._DEFAULT_MODELS)), (
        "dead models are back in the default pool: %s"
        % (dead & set(retrieve._DEFAULT_MODELS))
    )


def test_default_models_start_with_a_reliable_one():
    """The measured 6/6 models must precede the flaky combo route."""
    assert retrieve._DEFAULT_MODELS[0] == "agnes-2.5-flash"
    assert "combo/freemodels" in retrieve._DEFAULT_MODELS


# ---------------------------------------------------------------------------
# 404 -> disable permanently
# ---------------------------------------------------------------------------

def test_404_disables_model_and_it_is_never_picked_again():
    script = {"glm-5.3-flash-free": [status_error(404, "model does not exist")],
              "agnes-2.5-flash": [[]]}
    pool, calls = make_pool(script, models=["glm-5.3-flash-free", "agnes-2.5-flash"])

    assert pool._pick_model() == "glm-5.3-flash-free"
    pool._quarantine("glm-5.3-flash-free", status_error(404))

    assert "glm-5.3-flash-free" in pool.disabled_models
    assert "glm-5.3-flash-free" not in pool.alive_models
    # Subsequent picks never return to the 404 model.
    picks = {pool._pick_model() for _ in range(5)}
    assert "glm-5.3-flash-free" not in picks


def test_404_detected_from_message_when_no_status_is_attached():
    exc = api_error("The requested model does not exist.")
    pool, _ = make_pool({}, models=["m1"])
    pool._quarantine("m1", exc)
    assert "m1" in pool.disabled_models


# ---------------------------------------------------------------------------
# 402 / 429 -> long pause, not a permanent disable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [402, 429])
def test_quota_errors_pause_longer_but_do_not_disable(status):
    pool, _ = make_pool({}, models=["m1"])
    pool._quarantine("m1", status_error(status, "insufficient credits"))

    assert "m1" not in pool.disabled_models
    assert pool._is_alive("m1") is False
    # The cooldown must exceed the short transient TTL.
    stored = pool._dead["m1"]
    import time as _t
    assert _t.time() - stored < retrieve.QUOTA_TTL
    assert retrieve.QUOTA_TTL > retrieve.DEAD_TTL


def test_quota_detected_from_message_when_no_status_is_attached():
    pool, _ = make_pool({}, models=["m1"])
    pool._quarantine("m1", api_error("Insufficient credits. Please top up."))
    assert "m1" not in pool.disabled_models
    assert pool._is_alive("m1") is False


# ---------------------------------------------------------------------------
# Transient errors -> retry once on the same model
# ---------------------------------------------------------------------------

def test_statusless_api_error_is_retried_on_the_same_model():
    """combo/freemodels rejects ~17% of valid requests; one retry fixes it."""
    script = {"agnes-2.5-flash": [api_error(), []]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash"])

    result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result[1] == "agnes-2.5-flash"
    assert calls == ["agnes-2.5-flash", "agnes-2.5-flash"]


def test_persistent_api_error_still_fails_over_to_the_next_model():
    script = {"agnes-2.5-flash": [api_error(), api_error()],
              "laguna-s-2.1": [[]]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash", "laguna-s-2.1"])

    result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result[1] == "laguna-s-2.1"
    # Two attempts on the first model (initial + one retry), then failover.
    assert calls.count("agnes-2.5-flash") == 2


def test_timeouts_are_retried():
    script = {"agnes-2.5-flash": [timeout_error(), []]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash"])
    result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))
    assert result[1] == "agnes-2.5-flash"
    assert calls == ["agnes-2.5-flash", "agnes-2.5-flash"]


def test_server_errors_are_retried():
    script = {"agnes-2.5-flash": [status_error(503), []]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash"])
    result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))
    assert result[1] == "agnes-2.5-flash"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_permanent_errors_are_not_retried_on_the_same_model(status):
    """A second identical attempt cannot fix these, so skip straight to failover."""
    script = {"agnes-2.5-flash": [status_error(status)],
              "laguna-s-2.1": [[]]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash", "laguna-s-2.1"])

    result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result[1] == "laguna-s-2.1"
    assert calls.count("agnes-2.5-flash") == 1


def test_same_model_retry_count_is_configurable():
    script = {"m1": [api_error(), api_error(), []]}
    pool, calls = make_pool(script, models=["m1"])
    original = retrieve.SAME_MODEL_RETRIES
    retrieve.SAME_MODEL_RETRIES = 2
    try:
        result = asyncio.run(pool.chat(messages=[{"role": "user", "content": "hi"}]))
    finally:
        retrieve.SAME_MODEL_RETRIES = original
    assert result[1] == "m1"
    assert calls == ["m1", "m1", "m1"]


# ---------------------------------------------------------------------------
# Streaming path
# ---------------------------------------------------------------------------

def test_chat_stream_retries_a_transient_router_rejection():
    script = {"agnes-2.5-flash": [api_error(), [["ok", "!"]]]}
    pool, calls = make_pool(script, models=["agnes-2.5-flash"])

    stream, model = asyncio.run(
        pool.chat_stream(messages=[{"role": "user", "content": "hi"}])
    )
    assert model == "agnes-2.5-flash"
    assert len(calls) == 2


def test_chat_stream_fails_over_past_a_404_model():
    script = {"glm-5.3-flash-free": [status_error(404)], "agnes-2.5-flash": [[["x"]]]}
    pool, _ = make_pool(script, models=["glm-5.3-flash-free", "agnes-2.5-flash"])

    _stream, model = asyncio.run(
        pool.chat_stream(messages=[{"role": "user", "content": "hi"}])
    )
    assert model == "agnes-2.5-flash"


def test_chat_stream_raises_when_every_model_is_disabled():
    """A dead pool must surface an error rather than silently returning None."""
    script = {"m1": [status_error(404)]}
    pool, _ = make_pool(script, models=["m1"])
    pool._quarantine("m1", status_error(404))
    # Allow the pool to reset and try anyway, then fail on the 404 again.
    script["m1"] = [status_error(404), status_error(404)]
    with pytest.raises(APIStatusError):
        asyncio.run(pool.chat_stream(messages=[{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# Pool health reporting
# ---------------------------------------------------------------------------

def test_disabled_models_are_exposed_for_observability():
    pool, _ = make_pool({}, models=["a", "b"])
    pool._quarantine("b", status_error(404))
    assert pool.disabled_models == ["b"]
    assert pool.alive_models == ["a"]


def test_all_models_disabled_resets_instead_of_returning_nothing():
    """Even if the provider renames everything, the pool must still try."""
    pool, _ = make_pool({}, models=["a", "b"])
    pool._quarantine("a", status_error(404))
    pool._quarantine("b", status_error(404))
    assert pool._pick_model() in {"a", "b"}