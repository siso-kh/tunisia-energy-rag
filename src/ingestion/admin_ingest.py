"""Admin-driven PDF ingestion: upload/download -> triage -> route -> index.

Orchestrates the pieces of the existing pipeline for a single document:

    save_uploaded_pdf / download_pdf_from_url   -> data/raw/
    triage_file                                  -> judge (LLM two-gate)
    move to data/filtered/ or data/blacklisted/  -> route
    index_pdf_into_chroma (if accepted)          -> searchable by the chat

The heavy LLM + ChromaDB work is synchronous, so the FastAPI endpoints run
these functions via ``run_in_threadpool`` (see ``src/api/main.py``).
"""

import logging
import re
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from src.ingestion.indexer import index_pdf_into_chroma
from src.utils.triage import (
    BLACKLISTED_DIR,
    FILTERED_DIR,
    RAW_DIR,
    _move_pdf,
    triage_file,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
PDF_MAGIC = b"%PDF"

# Mirror of collector.sanitize_filename (kept local to avoid importing the
# collector's module-level side effects into the API).
def sanitize_filename(name: str) -> str:
    filename = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", Path(name).name or "document.pdf")
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    return filename[:100]


def is_valid_pdf(content: bytes) -> bool:
    """Cheap magic-byte check for uploaded content (not a full parse)."""
    return content.startswith(PDF_MAGIC)


def save_uploaded_pdf(content: bytes, filename: str) -> Path:
    """Validate + persist an uploaded PDF into ``data/raw/``. Returns the path."""
    if not is_valid_pdf(content):
        raise ValueError("Uploaded file is not a valid PDF (missing %PDF header).")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / sanitize_filename(filename)
    if dest.exists():
        # Re-upload replaces the previous copy (fresh version to re-triage).
        dest.unlink()
    dest.write_bytes(content)
    return dest


def download_pdf_from_url(url: str) -> Path:
    """Download a PDF from a URL into ``data/raw/``. Returns the path."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are supported.")

    filename = sanitize_filename(Path(parsed.path).name or "document.pdf")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / filename

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, stream=True, timeout=20)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" not in content_type and not url.lower().endswith(".pdf"):
        raise ValueError(f"URL did not return a PDF (Content-Type: {content_type or 'unknown'}).")

    if dest.exists():
        dest.unlink()  # fresh download replaces the previous copy
    with open(dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # Guard against downloads that aren't actually PDFs despite the header.
    with open(dest, "rb") as f:
        head = f.read(4)
    if not is_valid_pdf(head):
        dest.unlink()
        raise ValueError("Downloaded content is not a valid PDF.")
    return dest


def ingest_pdf(pdf_path: Path, triage_fn=None, index_fn=None) -> Dict:
    """Full admin flow for one PDF: triage -> route -> (if accepted) index.

    ``triage_fn`` / ``index_fn`` are injectable for tests; they default to
    the real ``triage_file`` / ``index_pdf_into_chroma``.

    Returns the triage decision enriched with the routing + indexing outcome:
    ``{filename, status, gate1_score, gate2_score, master_score, total_pages,
    dest, chunks_indexed}``.
    """
    triage_fn = triage_fn or triage_file
    index_fn = index_fn or index_pdf_into_chroma

    decision = dict(triage_fn(pdf_path))

    # An ERROR verdict means the LLM never produced a usable score (provider
    # outage, auth failure, unparseable response). Leave the file in data/raw
    # so it can be re-triaged later -- moving it to blacklisted/ would record a
    # transport failure as "this document is not pertinent".
    if decision.get("status") == "ERROR" or decision.get("dest") is None:
        decision["chunks_indexed"] = 0
        return decision

    dest_dir = FILTERED_DIR if decision["dest"] == "filtered" else BLACKLISTED_DIR
    _move_pdf(pdf_path, dest_dir)

    decision["chunks_indexed"] = 0
    if decision["status"] == "PASSED":
        try:
            moved = dest_dir / decision["filename"]
            decision["chunks_indexed"] = index_fn(moved)
        except Exception as e:
            logger.exception("Indexing failed for %s: %s", decision["filename"], e)
            decision["index_error"] = str(e)

    return decision
