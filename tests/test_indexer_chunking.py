"""Regression tests for admin PDF ingestion (src/ingestion/indexer.py).

Two deployment-shaped bugs are pinned here:

* ``chunk_pdf`` imported ``src.ingestion.ingest_chunks``, whose module-level
  ``easyocr`` / ``pdf2image`` / ``torch`` imports are absent from
  ``deploy/render/requirements.runtime.txt``. Admin ingest therefore died with
  ``No module named 'langchain_text_splitters'`` right after triage succeeded,
  leaving documents triaged but never indexed.
* Pages whose text contains Arabic presentation forms are routed to OCR. With no
  OCR available the page produced empty text and was dropped entirely, so an
  Arabic PDF indexed zero chunks and was reported as a silent no-op.

The tests run with the heavy modules blocked, mirroring the deployed image.
"""

import sys
import types

import pytest

from src.ingestion import indexer


# ---------------------------------------------------------------------------
# Blockers mirroring the deployed image
# ---------------------------------------------------------------------------

# Arabic Presentation Forms (U+FE70-U+FEFF) are what trigger the OCR path;
# plain Arabic letters do not, so the fixture must contain the real block.
_ARABIC_PRESENTATION_FORMS = "ﹰﹱﹲﹳ " * 60


class _Blocker:
    """Import hook that makes the absent packages unimportable."""

    BLOCKED = {"easyocr", "pdf2image", "torch", "src.ingestion.ingest_chunks"}

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ImportError("not installed in the deployed image: %s" % name)
        return None


@pytest.fixture
def image_like_imports():
    """Forbid the packages the runtime image does not ship."""
    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    saved = {name: sys.modules.get(name) for name in ("easyocr", "pdf2image", "torch")}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod


# ---------------------------------------------------------------------------
# Fake pdfplumber page
# ---------------------------------------------------------------------------

class FakePage:
    def __init__(self, text, raises=False):
        self._text = text
        self._raises = raises

    def extract_text(self):
        if self._raises:
            raise ValueError("malformed page structure")
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_pdfplumber(monkeypatch):
    """Install a pdfplumber stub returning the supplied pages."""
    state = {"pages": [], "opened": 0}

    def _open(_path, *_a, **_kw):
        state["opened"] += 1
        return FakePdf(state["pages"])

    stub = types.ModuleType("pdfplumber")
    stub.open = _open
    monkeypatch.setitem(sys.modules, "pdfplumber", stub)
    return state


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def test_extraction_does_not_import_the_heavy_pipeline(image_like_imports, fake_pdfplumber):
    """chunk_pdf must not pull in ingest_chunks/easyocr/pdf2image."""
    fake_pdfplumber["pages"] = [FakePage("Texte sur l'energie tunisienne." * 20)]
    indexer._extract_pages(__import__("pathlib").Path("rapport.pdf"))

    for mod in ("easyocr", "pdf2image", "torch", "src.ingestion.ingest_chunks"):
        assert mod not in sys.modules, "%s was imported" % mod


def test_plain_text_pages_are_extracted(fake_pdfplumber):
    from pathlib import Path

    fake_pdfplumber["pages"] = [FakePage("Energy report content." * 30)]
    pages = indexer._extract_pages(Path("rapport.pdf"))

    assert len(pages) == 1
    assert pages[0]["source"] == "rapport.pdf"
    assert pages[0]["page"] == 1
    assert "Energy report" in pages[0]["text"]


def test_arabic_page_falls_back_to_embedded_text_when_ocr_is_unavailable(
    image_like_imports, fake_pdfplumber, monkeypatch
):
    """Without OCR the embedded text must be used instead of dropping the page.

    Arabic presentation forms trigger the OCR path. When OCR is unavailable the
    page used to yield "" and vanish, so the document indexed zero chunks.
    """
    from pathlib import Path

    arabic = _ARABIC_PRESENTATION_FORMS
    fake_pdfplumber["pages"] = [FakePage(arabic)]
    calls = []

    def no_ocr(*_a, **_k):
        calls.append(1)
        return ""

    monkeypatch.setattr(indexer, "_ocr_page", no_ocr)

    pages = indexer._extract_pages(Path("rapport-tunisie.pdf"))

    assert calls, "OCR should have been attempted for an Arabic page"
    assert len(pages) == 1, "the Arabic page was dropped"
    assert pages[0]["text"].strip(), "fallback produced empty text"


def test_ocr_is_used_when_available(fake_pdfplumber, monkeypatch):
    from pathlib import Path

    arabic = _ARABIC_PRESENTATION_FORMS
    fake_pdfplumber["pages"] = [FakePage(arabic)]
    seen = []

    def fake_ocr(_pdf, page_no):
        seen.append(page_no)
        return "Texte océrise"

    monkeypatch.setattr(indexer, "_ocr_page", fake_ocr)

    pages = indexer._extract_pages(Path("rapport-tunisie.pdf"))

    assert seen == [1]
    assert pages[0]["text"] == "Texte océrise"


def test_malformed_page_does_not_abort_the_document(fake_pdfplumber, monkeypatch):
    from pathlib import Path

    fake_pdfplumber["pages"] = [FakePage("", raises=True), FakePage("Good page content.")]
    monkeypatch.setattr(indexer, "_ocr_page", lambda *_a, **_k: "")

    pages = indexer._extract_pages(Path("broken.pdf"))

    assert any("Good page" in p["text"] for p in pages)


def test_tunisia_flag_uses_filename(fake_pdfplumber):
    from pathlib import Path

    fake_pdfplumber["pages"] = [FakePage("contenu")]
    assert indexer._extract_pages(Path("rapport-tunisie-2019.pdf"))[0]["is_tunisia_specific"]
    assert not indexer._extract_pages(Path("rapport-europe.pdf"))[0]["is_tunisia_specific"]


# ---------------------------------------------------------------------------
# Splitting settings
# ---------------------------------------------------------------------------

def test_splitting_settings_match_the_batch_pipeline():
    """indexer.py must stay in step with ingest_chunks.py."""
    assert indexer.CHUNK_SIZE == 1000
    assert indexer.CHUNK_OVERLAP == 150
    assert indexer.SEPARATORS == ["\n\n", "\n", " ", ""]