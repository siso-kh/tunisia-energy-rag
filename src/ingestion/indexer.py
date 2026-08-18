"""Chunk + embed a PDF into the ChromaDB vector store (admin ingestion).

Single-file counterpart of the batch ingest pipeline: it reuses the same
splitting settings as ``src/ingestion/ingest_chunks.py`` and indexes a
document that just passed triage so it becomes immediately searchable.

Design notes
------------
* Lazy imports: nothing heavy (pdfplumber, the splitter, chroma) is loaded
  until the first call, so importing this module costs nothing.
* Stale-chunk cleanup: chunks previously indexed for the same filename are
  deleted first, so re-uploading a document replaces its old embeddings
  instead of duplicating them.
* The collection is opened with the app's shared client so the same
  embeddings function (paraphrase-multilingual-MiniLM-L12-v2) is used for
  both indexing and querying.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
SEPARATORS = ["\n\n", "\n", " ", ""]

# Regex for Arabic Presentation Forms (same as the batch pipeline).
PRESENTATION_FORM_REGEX = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")

_ocr_reader: Optional[Any] = None


def _get_ocr_reader():
    """Lazily build the EasyOCR reader (Arabic + English) on first use.

    Importing easyocr/torch at module scope would slow down the API startup
    and eat memory even when no OCR is ever needed, so it is deferred.
    """
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        import torch

        use_gpu = torch.cuda.is_available()
        _ocr_reader = easyocr.Reader(["ar", "en"], gpu=use_gpu)
    return _ocr_reader


def _detects_presentation_forms(text: str, threshold: int = 1) -> bool:
    if not text or not text.strip():
        return True
    return len(PRESENTATION_FORM_REGEX.findall(text)) >= threshold


def _ocr_page(pdf_path: Path, page_number: int) -> str:
    """OCR a single page to recover Arabic text (pdfplumber can't)."""
    try:
        import numpy as np
        from pdf2image import convert_from_path

        poppler = Path(__file__).resolve().parents[2] / "poppler" / "Library" / "bin"
        images = convert_from_path(
            pdf_path,
            first_page=page_number,
            last_page=page_number,
            dpi=200,
            poppler_path=str(poppler) if poppler.exists() else None,
        )
        if not images:
            return ""
        reader = _get_ocr_reader()
        results = reader.readtext(np.array(images[0]), detail=0)
        return " ".join(results).strip()
    except Exception as e:
        logger.error("OCR failed for %s page %d: %s", pdf_path.name, page_number, e)
        return ""


def chunk_pdf(pdf_path: Path) -> List[Dict[str, Any]]:
    """Split a PDF into overlapping text chunks (same settings as the batch pipeline).

    Returns a list of ``{"content", "source", "page", "is_tunisia_specific"}``
    dicts ready to be embedded. Pages whose text contains Arabic presentation
    forms (or that fail text extraction) are OCR'd.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from src.ingestion.ingest_chunks import process_pdf

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
    )
    chunks: List[Dict[str, Any]] = []
    for record in process_pdf(pdf_path):
        for chunk_text in splitter.split_text(record["text"]):
            chunks.append(
                {
                    "content": chunk_text,
                    "source": record["source"],
                    "page": record["page"],
                    "is_tunisia_specific": record["is_tunisia_specific"],
                }
            )
    return chunks


def index_pdf_into_chroma(pdf_path: Path) -> int:
    """Chunk + embed a single PDF into ChromaDB. Returns the chunk count.

    Existing chunks for the same source filename are removed first (upsert
    semantics per document), so re-uploading a file replaces its old
    embeddings instead of accumulating duplicates.
    """
    from src.rag.retrieve import chroma_client, collection, emb_fn

    chunks = chunk_pdf(pdf_path)
    if not chunks:
        logger.warning("No extractable text in %s — nothing indexed.", pdf_path.name)
        return 0

    source = chunks[0]["source"]
    try:
        collection.delete(where={"source": source})
    except Exception as e:
        logger.warning("Could not delete stale chunks for %s: %s", source, e)

    ids = [f"{source}::{c['page']}::{i}" for i, c in enumerate(chunks)]
    documents = [c["content"] for c in chunks]
    metadatas = [
        {"source": c["source"], "page": c["page"], "is_tunisia_specific": c["is_tunisia_specific"]}
        for c in chunks
    ]

    # The collection stores explicit vectors (the app queries with explicit
    # query_embeddings), so compute them here with the shared embedding fn.
    raw_vectors = emb_fn(documents)
    embeddings = [
        v.tolist() if hasattr(v, "tolist") else list(v) for v in raw_vectors
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    logger.info("Indexed %d chunks from %s into ChromaDB", len(chunks), source)
    return len(chunks)


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        n = index_pdf_into_chroma(Path(p))
        print(f"{p}: {n} chunks indexed")
