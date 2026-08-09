import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import pdfplumber
from pdf2image import convert_from_path
import easyocr
import torch
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Paths & Settings
BASE_DIR = Path(__file__).resolve().parent.parent
FILTERED_DATA_DIR = BASE_DIR / "data" / "filtered"
PROCESSED_JSON_PATH = BASE_DIR / "data" / "processed" / "processed_chunks.json"

# Automatically find the local Poppler binary folder we just created
POPPLER_PATH = BASE_DIR / "poppler" / "Library" / "bin"

# Regex for Arabic Presentation Forms (Threshold updated to 1)
PRESENTATION_FORM_REGEX = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")

_OCR_READER: Optional[easyocr.Reader] = None


def get_ocr_reader() -> easyocr.Reader:
    global _OCR_READER
    if _OCR_READER is None:
        use_gpu = torch.cuda.is_available()
        logger.info(f"Initializing EasyOCR reader (Arabic/English) | CUDA GPU Acceleration: {use_gpu}")
        _OCR_READER = easyocr.Reader(["ar", "en"], gpu=use_gpu)
    return _OCR_READER


def detects_presentation_forms(text: str, threshold: int = 1) -> bool:
    if not text or not text.strip():
        return True
    matches = PRESENTATION_FORM_REGEX.findall(text)
    return len(matches) >= threshold


def ocr_pdf_page(pdf_path: Path, page_number: int) -> str:
    try:
        # Pass the explicit Poppler path to pdf2image
        images = convert_from_path(
            pdf_path,
            first_page=page_number,
            last_page=page_number,
            dpi=200,
            poppler_path=str(POPPLER_PATH) if POPPLER_PATH.exists() else None
        )
        if not images:
            return ""
        
        img_np = np.array(images[0])
        reader = get_ocr_reader()
        results = reader.readtext(img_np, detail=0)
        return " ".join(results).strip()
    except Exception as e:
        logger.error(f"OCR failed for {pdf_path.name} page {page_number}: {e}")
        return ""


def process_pdf(pdf_path: Path) -> List[Dict[str, Any]]:
    pages_data = []
    filename = pdf_path.name
    is_tunisia = "tunisia" in filename.lower() or "tn" in filename.lower()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                raw_text = ""
                needs_ocr = False
                
                # Try standard extraction, catch malformed PDF crashes
                try:
                    raw_text = page.extract_text() or ""
                    needs_ocr = detects_presentation_forms(raw_text, threshold=1)
                except Exception as e:
                    logger.warning(f"PDF structure error on {filename} (Page {page_idx}): {e}. Forcing OCR bypass.")
                    needs_ocr = True

                if needs_ocr:
                    logger.info(f"Running OCR on {filename} (Page {page_idx})...")
                    clean_text = ocr_pdf_page(pdf_path, page_idx)
                else:
                    clean_text = raw_text.strip()

                if clean_text:
                    pages_data.append({
                        "text": clean_text,
                        "source": filename,
                        "page": page_idx,
                        "is_tunisia_specific": is_tunisia
                    })
    except Exception as e:
        logger.error(f"Fatal error opening {filename}: {e}. Skipping entirely.")

    return pages_data


def build_chunks() -> None:
    pdf_files = list(FILTERED_DATA_DIR.glob("*.pdf"))
    logger.info(f"Starting chunking pipeline for {len(pdf_files)} PDFs in {FILTERED_DATA_DIR}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    all_chunks = []

    for pdf_path in pdf_files:
        page_records = process_pdf(pdf_path)
        for record in page_records:
            chunks = splitter.split_text(record["text"])
            for chunk_text in chunks:
                all_chunks.append({
                    "content": chunk_text,
                    "source": record["source"],
                    "page": record["page"],
                    "is_tunisia_specific": record["is_tunisia_specific"]
                })

    PROCESSED_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    logger.info(f"Pipeline Complete: Exported {len(all_chunks)} clean chunks to {PROCESSED_JSON_PATH}")


if __name__ == "__main__":
    build_chunks()