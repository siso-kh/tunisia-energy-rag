import os
import re
import json
import random
import shutil
import pdfplumber
from pathlib import Path
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

# Load Configuration
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# Paths
RAW_DIR = PROJECT_ROOT / CONFIG["paths"]["raw_dir"]
FILTERED_DIR = PROJECT_ROOT / CONFIG["paths"]["filtered_dir"]
BLACKLISTED_DIR = PROJECT_ROOT / CONFIG["paths"]["blacklisted_dir"]
SCORES_FILE = PROJECT_ROOT / CONFIG["paths"]["scores_file"]
REPORT_FILE = PROJECT_ROOT / CONFIG["paths"]["report_file"]

# Ensure a .env exists at project root and load it
ENV_PATH = PROJECT_ROOT / ".env"
if not ENV_PATH.exists():
    with open(ENV_PATH, "w", encoding="utf-8") as _f:
        _f.write("# Add your API key for the OpenAI-compatible client\n# Example:\n# CUSTOM_API_KEY=sk-...\n# Optionally set your provider base URL:\n# OPENAI_BASE_URL=https://api.example.com\n")

load_dotenv(dotenv_path=str(ENV_PATH))

# Read API key from environment (loaded from .env)
API_KEY = os.environ.get("CUSTOM_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "YOUR_THIRD_PARTY_API_ENDPOINT")

if not API_KEY:
    print(f"Warning: no CUSTOM_API_KEY found in {ENV_PATH}. Fill it before running the pipeline.")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

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


def evaluate_gate1_metadata(meta: dict) -> float:
    """Gate 1: Fast evaluation based purely on metadata."""
    combined_meta_text = f"Filename: {meta['filename']}\nTitle: {meta['title']}\nSubtitles: {' | '.join(meta['subtitles'])}"
    prompt = PROMPT_TEMPLATE.format(text_sample=combined_meta_text)
    try:
        res = client.chat.completions.create(
            model="agnes-2.0-flash",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        data = json.loads(res.choices[0].message.content)
        return float(data.get("score", 0))
    except Exception:
        return 50.0  # Fallback to middle value if API fails


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

def evaluate_gate2_deep(pdf_path: Path, sample_text: str) -> float:
    """Gate 2: Deep evaluation of extracted page text."""
    prompt = PROMPT_TEMPLATE.format(text_sample=sample_text[:3500])
    try:
        res = client.chat.completions.create(
            model="agnes-2.0-flash",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        data = json.loads(res.choices[0].message.content)
        return float(data.get("score", 0))
    except Exception:
        return 0.0


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
        meta = extract_metadata(pdf)
        
        # GATE 1
        g1_score = evaluate_gate1_metadata(meta)
        print(f"[{pdf.name}] Gate 1 Score: {g1_score}%")

        if g1_score < CONFIG["gate1"]["instant_reject_threshold"]:
            status, master_score = "BLACKLISTED", g1_score
            dest = BLACKLISTED_DIR / pdf.name
            try:
                shutil.move(str(pdf), str(dest))
            except Exception:
                shutil.copy(str(pdf), str(dest))
                try:
                    pdf.unlink()
                except Exception:
                    pass
            stats["blacklisted"] += 1
            stats["gate1_shortcircuited"] += 1

        elif g1_score > CONFIG["gate1"]["instant_pass_threshold"]:
            status, master_score = "PASSED", g1_score
            dest = FILTERED_DIR / pdf.name
            try:
                shutil.move(str(pdf), str(dest))
            except Exception:
                shutil.copy(str(pdf), str(dest))
                try:
                    pdf.unlink()
                except Exception:
                    pass
            stats["passed"] += 1
            stats["gate1_shortcircuited"] += 1

        else:
            # GATE 2 DEEP ANALYSIS
            sample_text = sample_proportional_pages(pdf, meta["total_pages"], g1_score)
            g2_score = evaluate_gate2_deep(pdf, sample_text)
            
            master_score = (g1_score * CONFIG["gate2"]["weight_algo"]) + (g2_score * CONFIG["gate2"]["weight_ai"])
            
            if master_score >= CONFIG["gate2"]["master_pass_threshold"]:
                status = "PASSED"
                dest = FILTERED_DIR / pdf.name
                try:
                    shutil.move(str(pdf), str(dest))
                except Exception:
                    shutil.copy(str(pdf), str(dest))
                    try:
                        pdf.unlink()
                    except Exception:
                        pass
                stats["passed"] += 1
            else:
                status = "BLACKLISTED"
                dest = BLACKLISTED_DIR / pdf.name
                try:
                    shutil.move(str(pdf), str(dest))
                except Exception:
                    shutil.copy(str(pdf), str(dest))
                    try:
                        pdf.unlink()
                    except Exception:
                        pass
                stats["blacklisted"] += 1

        # Save score log
        scores_history[pdf.name] = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "gate1_score": g1_score,
            "master_score": round(master_score, 2),
            "total_pages": meta["total_pages"]
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