"""Prometheus metrics for the Tunisia Energy RAG API.

Exposes counters, histograms, and gauges that Prometheus can scrape via
``GET /metrics``.

Metrics included:
  - ``http_request_duration_seconds``  — per-endpoint request latency histogram
  - ``http_requests_total``            — total request count by method/path/status
  - ``llm_tokens_total``               — LLM token usage (prompt + completion)
  - ``llm_requests_total``             — total LLM calls (by model + endpoint)
  - ``purge_runs_total``               — outage purge runs
  - ``purge_deleted_total``            — total outage reports purged
  - ``active_requests``                — currently in-flight requests
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    labelnames=["method", "path", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "path", "status"],
)

ACTIVE_REQUESTS = Gauge(
    "active_requests",
    "Number of requests currently being processed",
)

# ---------------------------------------------------------------------------
# LLM metrics
# ---------------------------------------------------------------------------

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens consumed by LLM calls",
    labelnames=["model", "type"],  # type = "prompt" | "completion"
)

LLM_REQUESTS = Counter(
    "llm_requests_total",
    "Total LLM API calls",
    labelnames=["model", "endpoint"],  # endpoint = "rewrite" | "generate" | "triage"
)

# ---------------------------------------------------------------------------
# Purge metrics
# ---------------------------------------------------------------------------

PURGE_RUNS = Counter(
    "purge_runs_total",
    "Total outage purge runs",
)

PURGE_DELETED = Counter(
    "purge_deleted_total",
    "Total outage reports deleted by purge",
)

# ---------------------------------------------------------------------------
# Ingestion metrics
# ---------------------------------------------------------------------------

INGESTIONS = Counter(
    "ingestions_total",
    "Documents ingested or rejected",
    labelnames=["result"],  # result = "indexed" | "rejected" | "failed"
)


def normalize_path(path: str) -> str:
    """Collapse path parameters into placeholders for high-cardinality control.

    ``/api/conversations/550e8400-e29b-41d4-a716-446655440000``
    becomes ``/api/conversations/{id}``
    """
    parts = path.strip("/").split("/")
    out = []
    for i, part in enumerate(parts):
        # UUID-like or numeric IDs
        if (
            len(part) > 8
            and "-" in part  # UUID
        ) or (
            part.isdigit() and i == len(parts) - 1  # trailing numeric id
        ):
            out.append("{id}")
        else:
            out.append(part)
    return "/" + "/".join(out)
