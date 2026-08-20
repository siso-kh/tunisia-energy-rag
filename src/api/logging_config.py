"""Structured JSON logging configuration.

Replaces the default text formatter with a JSON formatter so every log line
is a single JSON object — easy to ship to log aggregators (ELK, Loki, CloudWatch).

Usage:
    from src.api.logging_config import setup_logging
    setup_logging()  # call once at app startup
"""

import logging
import os
import sys

from pythonjsonlogger import json as jsonlogger


def setup_logging(level: str | None = None) -> None:
    """Configure root logger with JSON output to stdout.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
               Defaults to the ``LOG_LEVEL`` env var or ``INFO``.
    """
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    # JSON formatter: timestamp, level, logger, message, plus any extra fields
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # Root handler → stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Quieten noisy libraries
    for noisy in ("uvicorn.access", "httpx", "httpcore", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
