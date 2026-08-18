"""Download PDFs from tracked URLs and ingest accepted ones into ChromaDB.

This module implements the two-phase admin workflow:

1. **Research** — download + validate each pending source, store in data/raw/
2. **Ingest** — run the two-gate LLM triage on downloaded PDFs and index
   accepted ones into ChromaDB.

Heavy work (HTTP downloads, LLM calls, ChromaDB upserts) is synchronous and
meant to be called via ``run_in_threadpool`` from the FastAPI endpoints.

Design note: these functions work with **plain data** (URLs, paths, dicts),
never with ORM objects. This avoids thread-safety issues when called from
``run_in_threadpool`` inside async endpoints that hold an async session.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from src.ingestion.admin_ingest import (
    MAX_UPLOAD_BYTES,
    PDF_MAGIC,
    ingest_pdf,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"


def crawl_website_for_pdfs(url: str, max_depth: int = 1) -> List[str]:
    """Fetch a web page and extract all PDF links found on it.

    Returns a list of absolute PDF URLs (deduplicated). Handles both
    ``<a href="...pdf">`` links and ``src`` attributes. Only follows
    links on the same domain as the input URL.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to crawl %s: %s", url, e)
        return []

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        logger.info("Non-HTML page at %s (Content-Type: %s), no PDFs to extract.", url, content_type)
        return []

    try:
        from html.parser import HTMLParser

        parsed_base = urlparse(url)
        base_domain = parsed_base.netloc
        pdf_urls: set = set()

        class PDFLinkExtractor(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for attr_name, attr_val in attrs:
                    if not attr_val:
                        continue
                    # Check href and src attributes for PDF links.
                    if attr_name in ("href", "src"):
                        candidate = attr_val.strip()
                        if candidate.lower().endswith(".pdf"):
                            absolute = urljoin(url, candidate)
                            abs_parsed = urlparse(absolute)
                            # Only include same-domain links.
                            if abs_parsed.netloc == base_domain or abs_parsed.netloc == "":
                                pdf_urls.add(absolute)

        parser = PDFLinkExtractor()
        parser.feed(response.text)

        # Also try regex for cases the parser might miss (e.g. in JS strings).
        import re
        for match in re.finditer(r'["\']([^"\'"]+\.pdf)["\']', response.text, re.IGNORECASE):
            candidate = match.group(1)
            absolute = urljoin(url, candidate)
            abs_parsed = urlparse(absolute)
            if abs_parsed.netloc == base_domain or abs_parsed.netloc == "":
                pdf_urls.add(absolute)

        result = sorted(pdf_urls)
        logger.info("Found %d PDF link(s) on %s", len(result), url)
        return result

    except Exception as e:
        logger.warning("Failed to parse HTML from %s: %s", url, e)
        return []


def download_pdf_from_source(url: str) -> Dict[str, Any]:
    """Download a PDF from a URL into RAW_DIR.

    Returns a dict with keys: ``filename``, ``file_size``, ``error``.
    Does **not** touch any database — the caller handles Source updates.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"filename": None, "file_size": None, "error": "Only http/https URLs are supported."}

    raw_name = Path(parsed.path).name or "document.pdf"
    filename = sanitize_filename(raw_name)
    dest = RAW_DIR / filename

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        return {"filename": None, "file_size": None, "error": f"Download failed: {e}"}

    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" not in content_type and not url.lower().endswith(".pdf"):
        return {"filename": None, "file_size": None, "error": f"Not a PDF (Content-Type: {content_type or 'unknown'})"}

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists():
            dest.unlink()
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except OSError as e:
        return {"filename": None, "file_size": None, "error": f"Write failed: {e}"}

    with open(dest, "rb") as f:
        head = f.read(4)
    if not head.startswith(PDF_MAGIC):
        dest.unlink(missing_ok=True)
        return {"filename": None, "file_size": None, "error": "Downloaded content is not a valid PDF."}

    file_size = dest.stat().st_size
    if file_size > MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        return {"filename": None, "file_size": None, "error": f"File too large ({file_size // (1024 * 1024)} MB, max 50 MB)."}

    logger.info("Downloaded %s → %s (%d bytes)", url, filename, file_size)
    return {"filename": filename, "file_size": file_size, "error": None}


def ingest_downloaded_pdf(filename: str) -> Dict[str, Any]:
    """Run triage + index on a single downloaded PDF.

    Returns a dict with keys: ``status`` (indexed/triage_rejected/failed),
    ``total_pages``, ``gate1_score``, ``master_score``, ``chunks_indexed``,
    ``error``.
    Does **not** touch any database — the caller handles Source updates.
    """
    pdf_path = RAW_DIR / filename
    if not pdf_path.exists():
        return {
            "status": "failed",
            "total_pages": None,
            "gate1_score": None,
            "master_score": None,
            "chunks_indexed": None,
            "error": f"File not found: {filename}",
        }

    try:
        decision = ingest_pdf(pdf_path)
    except Exception as e:
        logger.exception("Triage failed for %s: %s", filename, e)
        return {
            "status": "failed",
            "total_pages": None,
            "gate1_score": None,
            "master_score": None,
            "chunks_indexed": None,
            "error": f"Triage failed: {e}",
        }

    if decision.get("index_error"):
        return {
            "status": "failed",
            "total_pages": decision.get("total_pages"),
            "gate1_score": decision.get("gate1_score"),
            "master_score": decision.get("master_score"),
            "chunks_indexed": decision.get("chunks_indexed", 0),
            "error": f"Index error: {decision['index_error']}",
        }

    if decision["status"] == "PASSED":
        return {
            "status": "indexed",
            "total_pages": decision.get("total_pages"),
            "gate1_score": decision.get("gate1_score"),
            "master_score": decision.get("master_score"),
            "chunks_indexed": decision.get("chunks_indexed", 0),
            "error": None,
        }

    return {
        "status": "triage_rejected",
        "total_pages": decision.get("total_pages"),
        "gate1_score": decision.get("gate1_score"),
        "master_score": decision.get("master_score"),
        "chunks_indexed": 0,
        "error": None,
    }
