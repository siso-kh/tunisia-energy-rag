"""/metrics was readable by anyone, and unreachable through the tunnel.

Two separate problems, both found by comparing the local and public responses:

1. The exposition includes per-model token counts, request rates and latency
   percentiles. On a deployment reachable from the open internet that is
   internal telemetry, not public data, so it now requires X-Admin-Key like the
   rest of the admin API.

2. dev_serve_spa.py proxied only /api, /health and /ready, so /metrics and
   /dashboard fell through to the SPA handler and came back as index.html --
   with HTTP 200. That is the dangerous shape: a scraper records a successful
   scrape and stores HTML as metrics.
"""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_metrics_requires_the_admin_key():
    assert client.get("/metrics").status_code == 401


def test_metrics_rejects_a_wrong_admin_key():
    """401, not 403, and the same message as "missing".

    require_admin_key answers identically for an absent and an incorrect key so
    the response cannot be used to probe whether one was guessed.
    """
    wrong = client.get("/metrics", headers={"X-Admin-Key": "wrong"})
    missing = client.get("/metrics")

    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert wrong.json() == missing.json()


def test_metrics_serves_prometheus_text_with_the_admin_key(monkeypatch):
    from src.api import main

    monkeypatch.setattr(main, "ADMIN_API_KEY", "test-key", raising=False)

    resp = client.get("/metrics", headers={"X-Admin-Key": "test-key"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]
    # Real exposition, not the SPA fallback.
    assert "<!doctype html>" not in resp.text.lower()
    assert "# HELP" in resp.text


def test_dashboard_is_reachable_without_a_key():
    """The dashboard renders no secrets, so it stays readable."""
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_spa_proxy_forwards_the_metrics_prefix():
    """Regression: these prefixes must reach the API, not index.html."""
    from scripts.dev_serve_spa import API_PREFIXES

    for prefix in ("/metrics", "/dashboard"):
        assert prefix in API_PREFIXES, (
            f"{prefix} must be proxied; otherwise the SPA fallback answers "
            "with index.html and HTTP 200, and a scraper ingests HTML"
        )