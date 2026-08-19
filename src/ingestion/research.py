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
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
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

# Safety defaults for recursive crawling.
DEFAULT_MAX_DEPTH = 1
DEFAULT_MAX_PAGES = 50
CRAWL_DELAY = 0.5  # seconds between requests (be polite)
LINK_EXTRACTOR_RE = re.compile(
    r"""(?:href|src)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def _extract_links(html: str, base_url: str, base_domain: str) -> Tuple[List[str], List[str]]:
    """Extract all same-domain links and PDF links from HTML.

    Returns (page_urls, pdf_urls) — both are absolute URLs.
    """
    page_urls: List[str] = []
    pdf_urls: List[str] = []

    for match in LINK_EXTRACTOR_RE.finditer(html):
        candidate = match.group(1).strip()

        # Skip fragments, mailto, tel, javascript.
        if candidate.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(base_url, candidate)
        parsed = urlparse(absolute)

        # Only same-domain links.
        if parsed.netloc and parsed.netloc != base_domain:
            continue

        # Normalize: remove fragment.
        clean = parsed._replace(fragment="").geturl()

        if clean.lower().endswith(".pdf") or clean.lower().endswith(".pdf?"):
            pdf_urls.append(clean)
        else:
            # Only follow http/https pages.
            if parsed.scheme in ("http", "https"):
                page_urls.append(clean)

    return page_urls, pdf_urls


def crawl_website_for_pdfs(
    url: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Dict[str, Any]:
    """Recursively crawl a website and extract all PDF links.

    Uses BFS (breadth-first) to discover pages across subdirectories and
    endpoints. Follows same-domain links only, with configurable depth and
    page limits to prevent runaway crawling.

    Returns a dict with:
        - ``pdf_urls``: list of deduplicated PDF URLs found
        - ``pages_crawled``: number of HTML pages actually fetched
        - ``pages_visited``: total pages seen (including skipped)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    parsed_start = urlparse(url)
    base_domain = parsed_start.netloc

    if not base_domain:
        return {"pdf_urls": [], "pages_crawled": 0, "pages_visited": 0}

    # BFS state.
    visited: Set[str] = set()
    all_pdfs: Set[str] = set()
    queue: deque[Tuple[str, int]] = deque()  # (url, depth)
    queue.append((url, 0))
    pages_crawled = 0

    while queue:
        current_url, depth = queue.popleft()

        # Skip if already visited.
        if current_url in visited:
            continue
        visited.add(current_url)

        # Depth limit.
        if depth > max_depth:
            continue

        # Page limit.
        if pages_crawled >= max_pages:
            logger.info("Reached max_pages limit (%d), stopping crawl.", max_pages)
            break

        # Polite delay between requests.
        if pages_crawled > 0:
            time.sleep(CRAWL_DELAY)

        # Fetch the page.
        try:
            response = requests.get(current_url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to crawl %s: %s", current_url, e)
            continue

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            # Non-HTML — skip but still count as visited.
            continue

        pages_crawled += 1
        logger.info(
            "Crawled [%d/%d] depth=%d: %s",
            pages_crawled,
            max_pages,
            depth,
            current_url,
        )

        # Extract links.
        page_urls, pdf_urls = _extract_links(response.text, current_url, base_domain)

        # Collect PDFs.
        for pdf_url in pdf_urls:
            all_pdfs.add(pdf_url)

        # Enqueue discovered pages for next depth level.
        if depth < max_depth:
            for page_url in page_urls:
                if page_url not in visited:
                    queue.append((page_url, depth + 1))

    result = sorted(all_pdfs)
    logger.info(
        "Crawl complete: %d PDFs found across %d pages (visited %d total)",
        len(result),
        pages_crawled,
        len(visited),
    )
    return {
        "pdf_urls": result,
        "pages_crawled": pages_crawled,
        "pages_visited": len(visited),
    }


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
