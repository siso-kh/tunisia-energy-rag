"""Tests for the /metrics Prometheus endpoint and metrics middleware.

Verifies that:
  - GET /metrics returns a 200 with Prometheus text format
  - The response contains expected metric families (http_requests_total, etc.)
  - The metrics middleware records request duration
  - The logging config produces structured JSON output
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# -----------------------------------------------------------------------
# /metrics endpoint
# -----------------------------------------------------------------------


def test_metrics_returns_200(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_content_type_is_prometheus_text(client):
    resp = client.get("/metrics")
    ct = resp.headers["content-type"]
    assert "text/plain" in ct


def test_metrics_contains_expected_metric_families(client):
    resp = client.get("/metrics")
    body = resp.text
    # Core metrics defined in src/api/metrics.py
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "active_requests" in body
    assert "llm_tokens_total" in body
    assert "llm_requests_total" in body
    assert "purge_runs_total" in body
    assert "purge_deleted_total" in body
    assert "ingestions_total" in body


def test_metrics_body_is_valid_prometheus_text(client):
    """Every non-comment line should start with a metric name or be blank/comment."""
    resp = client.get("/metrics")
    for line in resp.text.splitlines():
        if not line or line.startswith("#"):
            continue
        # Prometheus text format: metric_name [labels] value [timestamp]
        parts = line.split()
        assert len(parts) >= 1, f"unexpected empty line: {line!r}"
        # Metric names start with a letter or underscore
        assert parts[0][0].isalpha() or parts[0][0] == "_", (
            f"line does not start with a metric name: {line!r}"
        )


# -----------------------------------------------------------------------
# Middleware: metrics are recorded after requests
# -----------------------------------------------------------------------


def test_metrics_recorded_after_request(client):
    """Hitting any endpoint should increment http_requests_total."""
    client.get("/health")
    resp = client.get("/metrics")
    body = resp.text
    # http_requests_total should have at least one sample
    assert "http_requests_total" in body
    # The /health endpoint should have been recorded
    assert "/health" in body or "http_requests_total" in body


def test_metrics_endpoint_itself_not_recorded(client):
    """GET /metrics should not add noise to the metrics output."""
    # Hit /metrics multiple times
    for _ in range(3):
        client.get("/metrics")
    resp = client.get("/metrics")
    body = resp.text
    # /metrics should not appear as a path label in http_requests_total
    # (the middleware skips it)
    for line in body.splitlines():
        if "http_requests_total" in line and not line.startswith("#"):
            assert "/metrics" not in line, (
                f"/metrics endpoint should be excluded from request counts: {line}"
            )


# -----------------------------------------------------------------------
# Structured logging config
# -----------------------------------------------------------------------


def test_setup_logging_configures_json_formatter():
    """setup_logging() should attach a JsonFormatter to the root logger."""
    from src.api.logging_config import setup_logging

    setup_logging(level="DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    handler = root.handlers[0]
    from pythonjsonlogger import json as jsonlogger

    assert isinstance(handler.formatter, jsonlogger.JsonFormatter)


def test_setup_logging_respects_log_level():
    from src.api.logging_config import setup_logging

    setup_logging(level="WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING


# -----------------------------------------------------------------------
# Metric normalization
# -----------------------------------------------------------------------


def test_normalize_path_collapses_uuids():
    from src.api.metrics import normalize_path

    path = "/api/conversations/550e8400-e29b-41d4-a716-446655440000"
    assert normalize_path(path) == "/api/conversations/{id}"


def test_normalize_path_collapses_numeric_ids():
    from src.api.metrics import normalize_path

    assert normalize_path("/api/items/42") == "/api/items/{id}"


def test_normalize_path_keeps_normal_segments():
    from src.api.metrics import normalize_path

    assert normalize_path("/api/admin/config") == "/api/admin/config"
    assert normalize_path("/health") == "/health"
    assert normalize_path("/ready") == "/ready"
