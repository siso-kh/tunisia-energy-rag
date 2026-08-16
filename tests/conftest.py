"""Shared test fixtures for the backend suite."""

import pytest

from src.api.ratelimit import limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the shared in-memory rate-limit counters before every test.

    Without this, requests made by one test (TestClient always originates
    from the same host) would count toward the limits seen by later tests.
    """
    limiter.reset()
    yield
    limiter.reset()
