import os
import re
import json
import random
import shutil
import logging
import pdfplumber
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from openai import OpenAI
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Resolve repository root: file is in src/utils, repo root is two parents up
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.json"

# Load Configuration
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# Paths (use repository root)
RAW_DIR = REPO_ROOT / CONFIG["paths"]["raw_dir"]
FILTERED_DIR = REPO_ROOT / CONFIG["paths"]["filtered_dir"]
BLACKLISTED_DIR = REPO_ROOT / CONFIG["paths"]["blacklisted_dir"]
SCORES_FILE = REPO_ROOT / CONFIG["paths"]["scores_file"]
REPORT_FILE = REPO_ROOT / CONFIG["paths"]["report_file"]

ENV_PATH = REPO_ROOT / ".env"


def _load_env() -> None:
    """Create .env if missing and load it (side-effect free on plain import)."""
    if not ENV_PATH.exists():
        with open(ENV_PATH, "w", encoding="utf-8") as _f:
            _f.write("# Add your API key for the OpenAI-compatible client\n# Example:\n# CUSTOM_API_KEY=sk-...\n# Optionally set your provider base URL:\n# OPENAI_BASE_URL=https://api.example.com\n")
    load_dotenv(dotenv_path=str(ENV_PATH))


# Read API key from environment (loaded from .env)
API_KEY = os.environ.get("CUSTOM_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "YOUR_THIRD_PARTY_API_ENDPOINT")

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """Lazily build the sync OpenAI client (used by the CLI and the API alike).

    The client is created on first use instead of at import time so the API
    can import this module without requiring a CUSTOM_API_KEY to be present.
    """
    global _client, API_KEY, BASE_URL
    if _client is None:
        _load_env()
        API_KEY = os.environ.get("CUSTOM_API_KEY")
        BASE_URL = os.environ.get("OPENAI_BASE_URL", "YOUR_THIRD_PARTY_API_ENDPOINT")
        if not API_KEY:
            print(f"Warning: no CUSTOM_API_KEY found in {ENV_PATH}. Fill it before running the pipeline.")
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=TRIAGE_TIMEOUT)
    return _client


# ---------------------------------------------------------------------------
# Model resolution for triage
# ---------------------------------------------------------------------------
# Triage used to hardcode "agnes-2.0-flash". That model is no longer served by
# the provider, so every gate call raised, the `except` branches returned the
# 0.0/50.0 fallbacks, and the weighted master score collapsed below
# `gate2.master_pass_threshold` -- flagging *every* document as non-pertinent.
#
# Triage now resolves models from the same env vars as the chat pipeline
# (LLM_MODELS / LLM_MODEL) and walks the list on failure, so triage and chat
# always talk to a model the provider actually serves.
TRIAGE_TIMEOUT = float(os.getenv("TRIAGE_TIMEOUT", "30"))

# Matches the chat pool: only models measured as usable on this provider.
_TRIAGE_DEFAULT_MODELS = ["agnes-2.5-flash", "laguna-s-2.1", "combo/freemodels"]


def _triage_models() -> list:
    """Ordered list of models to try for triage, most preferred first."""
    raw = os.getenv("TRIAGE_MODELS") or os.getenv("LLM_MODELS") or ""
    models = [m.strip() for m in raw.split(",") if m.strip()]
    if models:
        return models
    primary = os.getenv("TRIAGE_MODEL") or os.getenv("LLM_MODEL") or ""
    if primary:
        return [primary] + [m for m in _TRIAGE_DEFAULT_MODELS if m != primary]
    return list(_TRIAGE_DEFAULT_MODELS)


class TriageModelError(RuntimeError):
    """Raised when no triage model could produce a score.

    Surfaced instead of being swallowed, so a provider outage shows up as an
    error rather than as a silent "this document is not pertinent" verdict.
    """


def _score_from_response(res) -> float:
    """Extract the 0-100 score from a triage completion.

    Providers wrap JSON differently (bare object, markdown fence, or prose), so
    parse defensively rather than letting json.loads raise on a stray character.
    """
    content = (res.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("```")[1] if "```" in content[3:] else content[3:]
        if content.lstrip().lower().startswith("json"):
            content = content.lstrip()[4:]
        content = content.rsplit("```", 1)[0]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise TriageModelError(f"triage model returned non-JSON output: {content[:200]!r}")
        data = json.loads(match.group(0))
    if "score" not in data:
        raise TriageModelError(f"triage model returned JSON without a 'score' key: {data!r}")
    return max(0.0, min(100.0, float(data["score"])))


DOMAIN_KEYWORDS = ["énergie", "efficacité", "photovoltaïque", "steg", "anme", "consommation", "طاقة", "نجاعة", "استهلاك"]

# Reusable prompt template for both Gate 1 (metadata) and Gate 2 (text samples).
PROMPT_TEMPLATE = """
You are an expert energy systems engineer. Evaluate whether this document is relevant to national energy, water, or gas system optimization, energy efficiency, green economy, or sustainability policies.

It can be specific to Tunisia OR a general international technical guide/standard applicable to energy optimization.

METADATA / EXCERPT:
{text_sample}

Output STRICT JSON:
{{
    "score": <integer 0-100>,
    "reason": "<one concise sentence explanation>"
}}
"""


def build_chroma_metadata(item: dict) -> dict:
        """Build metadata for ChromaDB indexing from an item dict.

        Expects `item["metadata"]["source_file"]` and `item["metadata"]["page_number"]` to exist.
        The `is_tunisia_specific` flag is a simple heuristic based on filename containing
        'tunisie' or 'anme'. Adapt as needed for other indicators.
        """
        src = item.get("metadata", {}).get("source_file", "")
        page = item.get("metadata", {}).get("page_number")
        return {
                "source_file": src,
                "page_number": page,
                "is_tunisia_specific": ("tunisie" in src.lower()) or ("anme" in src.lower())
        }


def extract_metadata(pdf_path: Path) -> dict:
    """Extracts filename, title, subtitles, and total page count."""
    title = ""
    subtitles = []
    total_pages = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

            # Safe text extraction for title/subtitles
            if total_pages > 0:
                first_page_text = pdf.pages[0].extract_text() or ""
                lines = [line.strip() for line in first_page_text.split("\n") if line.strip()]
                if lines:
                    title = lines[0]
                    # Take the next few lines as subtitles if available
                    if len(lines) > 1:
                        subtitles = lines[1:5]
    except Exception as e:
        print(f"Error reading metadata from {pdf_path.name}: {e}")

    return {
        "filename": pdf_path.name,
        "title": title[:200],
        "subtitles": subtitles[:10],
        "total_pages": total_pages
    }


def evaluate_gate1_metadata(meta: dict, client: Optional[OpenAI] = None) -> float:
    """Gate 1: Fast evaluation based purely on metadata.

    Tries each configured triage model in order. Raises TriageModelError if
    none of them answers, so callers never mistake a provider failure for a
    low-relevance verdict.
    """
    combined_meta_text = f"Filename: {meta['filename']}\nTitle: {meta['title']}\nSubtitles: {' | '.join(meta['subtitles'])}"
    prompt = PROMPT_TEMPLATE.format(text_sample=combined_meta_text)
    client = client or get_client()

    last_exc: Optional[Exception] = None
    for model in _triage_models():
        try:
            res = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                timeout=TRIAGE_TIMEOUT,
            )
            return _score_from_response(res)
        except Exception as exc:  # noqa: BLE001 - try the next model
            last_exc = exc
            logger.warning("Triage gate 1: model %s failed: %s", model, exc)
    raise TriageModelError(f"all triage models failed for gate 1: {last_exc}")


def sample_proportional_pages(pdf_path: Path, total_pages: int, gate1_score: float) -> str:
    """Samples pages using normal distribution or reads full text for small docs."""
    extracted_text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Recalculate actual pages to prevent index out of range errors
            actual_pages = len(pdf.pages)
            if actual_pages == 0:
                return ""

            # Full text fallback for small documents
            if actual_pages <= 30:
                for idx, page in enumerate(pdf.pages):
                    text = page.extract_text()
                    if text:
                        extracted_text += f"\n--- [Page {idx+1}] ---\n" + text
                return extracted_text[:4000]

            # Gaussian Sampling for large documents
            sampling_cfg = CONFIG["gate2"]["sampling"]
            num_pages = max(sampling_cfg["min_pages"], int(actual_pages * sampling_cfg["base_ratio"]))
            uncertainty = 1.0 - (abs(gate1_score - 50.0) / 50.0)
            final_sample_count = min(sampling_cfg["max_pages"], int(num_pages * (1.0 + uncertainty)))

            mean_page = actual_pages * 0.3
            std_dev = actual_pages * 0.25

            selected_indices = set()
            attempts = 0
            while len(selected_indices) < min(final_sample_count, actual_pages) and attempts < 100:
                attempts += 1
                page_num = int(random.gauss(mean_page, std_dev))
                if 1 <= page_num <= actual_pages:
                    selected_indices.add(page_num)

            for idx in sorted(list(selected_indices)):
                # Safe index check against actual_pages
                if 1 <= idx <= actual_pages:
                    text = pdf.pages[idx - 1].extract_text()
                    if text:
                        extracted_text += f"\n--- [Page {idx}] ---\n" + text[:1000]

    except Exception as e:
        print(f"Error sampling pages for {pdf_path.name}: {e}")

    return extracted_text

def evaluate_gate2_deep(pdf_path: Path, sample_text: str, client: Optional[OpenAI] = None) -> float:
    """Gate 2: Deep evaluation of extracted page text.

    Tries each configured triage model in order. Raises TriageModelError if
    none of them answers, so callers never mistake a provider failure for a
    zero relevance score.
    """
    prompt = PROMPT_TEMPLATE.format(text_sample=sample_text[:3500])
    client = client or get_client()

    last_exc: Optional[Exception] = None
    for model in _triage_models():
        try:
            res = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                timeout=TRIAGE_TIMEOUT,
            )
            return _score_from_response(res)
        except Exception as exc:  # noqa: BLE001 - try the next model
            last_exc = exc
            logger.warning("Triage gate 2: model %s failed: %s", model, exc)
    raise TriageModelError(f"all triage models failed for gate 2: {last_exc}")


def triage_file(pdf_path: Path, client: Optional[OpenAI] = None) -> Dict:
    """Judge a single PDF and return the decision (no file moves).

    Runs the two-gate LLM evaluation on one file and returns the verdict +
    scores so callers (CLI pipeline or the admin upload API) can route the
    file to ``data/filtered/`` or ``data/blacklisted/`` themselves.

    Returns a dict with: filename, status ("PASSED"/"BLACKLISTED"/"ERROR"),
    the gate scores, the master score, total pages, whether gate 1
    short-circuited, and the destination directory name
    ("filtered"/"blacklisted").

    A status of "ERROR" means the LLM could not be reached or returned
    unusable output. That is deliberately distinct from "BLACKLISTED": a
    provider outage must never be recorded as "this document is not
    pertinent", which is what previously rejected every upload.
    """
    meta = extract_metadata(pdf_path)

    # GATE 1
    try:
        g1_score = evaluate_gate1_metadata(meta, client)
    except TriageModelError as exc:
        logger.error("Triage evaluation failed for %s: %s", pdf_path.name, exc)
        return {
            "filename": pdf_path.name,
            "status": "ERROR",
            "gate1_score": None,
            "gate2_score": None,
            "master_score": None,
            "total_pages": meta["total_pages"],
            "shortcircuited": False,
            "dest": None,
            "error": str(exc),
        }
    shortcircuited = False

    if g1_score < CONFIG["gate1"]["instant_reject_threshold"]:
        status = "BLACKLISTED"
        master_score = g1_score
        shortcircuited = True
    elif g1_score > CONFIG["gate1"]["instant_pass_threshold"]:
        status = "PASSED"
        master_score = g1_score
        shortcircuited = True
    else:
        # GATE 2 DEEP ANALYSIS
        sample_text = sample_proportional_pages(pdf_path, meta["total_pages"], g1_score)
        try:
            g2_score = evaluate_gate2_deep(pdf_path, sample_text, client)
        except TriageModelError as exc:
            logger.error("Triage gate 2 failed for %s: %s", pdf_path.name, exc)
            return {
                "filename": pdf_path.name,
                "status": "ERROR",
                "gate1_score": g1_score,
                "gate2_score": None,
                "master_score": None,
                "total_pages": meta["total_pages"],
                "shortcircuited": False,
                "dest": None,
                "error": str(exc),
            }

        master_score = (g1_score * CONFIG["gate2"]["weight_algo"]) + (g2_score * CONFIG["gate2"]["weight_ai"])
        status = "PASSED" if master_score >= CONFIG["gate2"]["master_pass_threshold"] else "BLACKLISTED"

    return {
        "filename": pdf_path.name,
        "status": status,
        "gate1_score": g1_score,
        "gate2_score": None if shortcircuited else g2_score,
        "master_score": round(master_score, 2),
        "total_pages": meta["total_pages"],
        "shortcircuited": shortcircuited,
        "dest": "filtered" if status == "PASSED" else "blacklisted",
    }


def _move_pdf(pdf_path: Path, dest_dir: Path) -> None:
    """Move a PDF into dest_dir (copy + delete fallback for cross-volume)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf_path.name
    try:
        shutil.move(str(pdf_path), str(dest))
    except Exception:
        shutil.copy(str(pdf_path), str(dest))
        try:
            pdf_path.unlink()
        except Exception:
            pass


def run_pipeline():
    FILTERED_DIR.mkdir(parents=True, exist_ok=True)
    BLACKLISTED_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing historical scores
    scores_history = {}
    if SCORES_FILE.exists():
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            scores_history = json.load(f)

    pdf_files = list(RAW_DIR.glob("*.pdf"))
    stats = {"total_files": len(pdf_files), "passed": 0, "blacklisted": 0, "gate1_shortcircuited": 0, "total_pages_analyzed": 0}

    print(f"Starting Multi-Gate Filtering on {len(pdf_files)} files...\n")

    for pdf in pdf_files:
        decision = triage_file(pdf)
        print(f"[{pdf.name}] Gate 1 Score: {decision['gate1_score']}% -> {decision['status']}")

        if decision["status"] == "PASSED":
            status = "PASSED"
            _move_pdf(pdf, FILTERED_DIR)
            stats["passed"] += 1
        else:
            status = "BLACKLISTED"
            _move_pdf(pdf, BLACKLISTED_DIR)
            stats["blacklisted"] += 1

        if decision["shortcircuited"]:
            stats["gate1_shortcircuited"] += 1

        # Save score log
        scores_history[pdf.name] = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "gate1_score": decision["gate1_score"],
            "master_score": decision["master_score"],
            "total_pages": decision["total_pages"],
        }

    # Write persistent JSON log
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores_history, f, indent=2, ensure_ascii=False)

    # Generate Markdown Summary Report
    generate_markdown_report(stats)
    print(f"\nProcessing Complete. Detailed report written to {REPORT_FILE}")


def generate_markdown_report(stats: dict):
    report_content = f"""# PDF Triage Analysis Report
    **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    ## Execution Metrics
    * **Total Documents Evaluated:** {stats['total_files']}
    * **Accepted (Moved to `data/filtered/`):** {stats['passed']}
    * **Blacklisted (Moved to `data/blacklisted/`):** {stats['blacklisted']}
    * **Gate 1 Short-circuited (Token Saved):** {stats['gate1_shortcircuited']}

    ## Configuration Parameters Used
    ```json
    {json.dumps(CONFIG, indent=2)}
    ```
    """

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)


if __name__ == "__main__":
    run_pipeline()
