import os
import re
import json
import requests
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from ddgs import DDGS
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Ensure a .env exists at project root and load it (accept Path or string)
ENV_PATH = PROJECT_ROOT / ".env"
if not ENV_PATH.exists():
    with open(ENV_PATH, "w", encoding="utf-8") as _f:
        _f.write("# SerpApi key (for search integrations)\n# Example:\n# SERPAPI_KEY=your_serp_api_key_here\n")

load_dotenv(dotenv_path=str(ENV_PATH))

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
COLLECTION_LOG_FILE = PROJECT_ROOT / "data" / "collection_log.json"

# Accept several common environment variable names for SerpApi
SERPAPI_KEY = (
    os.getenv("SERPAPI_KEY")
    or os.getenv("SERP_API_KEY")
    or os.getenv("SERPAPI")
    or os.getenv("SERP_API")
)
if not SERPAPI_KEY:
    print(f"Warning: SERPAPI key not found in {ENV_PATH}. Add SERPAPI_KEY to .env or your environment.")

TARGET_QUERIES = [
    'site:ademe.fr filetype:pdf "efficacité énergétique" OR "optimisation" "bâtiment"',
    'site:rcreee.org filetype:pdf "energy efficiency" OR "ترشيد الطاقة" OR "النجاعة الطاقية"',
    'site:irena.org filetype:pdf "MENA" "renewable energy" OR "energy efficiency"',
    'filetype:pdf "gestion de l\'eau" "efficacité énergétique" "pompage"',
    'filetype:pdf "audit énergétique" "guide pratique" "industrie"',
    'filetype:pdf "ترشيد استهلاك الكهرباء" OR "النجاعة الطاقية في المباني"'
]


def sanitize_filename(url: str) -> str:
    path = urlparse(url).path
    filename = Path(path).name
    if not filename.endswith(".pdf"):
        filename = f"{filename}.pdf"
    
    filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
    return filename[:100]


def fetch_pdf_urls_serpapi(query: str, max_results: int = 10) -> list[str]:
    """Fetches PDF URLs via SerpApi."""
    if not SERPAPI_KEY:
        return []

    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": max_results
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    results = response.json()

    if "error" in results:
        raise RuntimeError(f"SerpApi Error: {results['error']}")

    pdf_urls = []
    for result in results.get("organic_results", []):
        link = result.get("link", "")
        if link.lower().endswith(".pdf") or "filetype:pdf" in query:
            pdf_urls.append(link)
    
    return pdf_urls


def fetch_pdf_urls_ddg(query: str, max_results: int = 10) -> list[str]:
    """Fallback: Fetches PDF URLs via DuckDuckGo without an API key."""
    pdf_urls = []
    results = DDGS().text(query, max_results=max_results)
    for r in results:
        link = r.get("href", "")
        if link.lower().endswith(".pdf") or ".pdf?" in link.lower():
            pdf_urls.append(link)
    return pdf_urls


def get_pdf_urls(query: str, max_results: int = 10) -> list[str]:
    """Hybrid Router: Tries SerpApi first; falls back to DuckDuckGo if rate limited or key is missing."""
    if SERPAPI_KEY:
        try:
            urls = fetch_pdf_urls_serpapi(query, max_results=max_results)
            print("  └─ [Source: SerpApi]")
            return urls
        except Exception as e:
            print(f"  └─ [SerpApi Failed/Exhausted: {e}] -> Switching to DuckDuckGo fallback...")
    else:
        print("  └─ [SerpApi Key Missing] -> Using DuckDuckGo search...")

    try:
        urls = fetch_pdf_urls_ddg(query, max_results=max_results)
        print("  └─ [Source: DuckDuckGo]")
        return urls
    except Exception as e:
        print(f"  └─ [DuckDuckGo Error]: {e}")
        return []


def download_pdf(url: str) -> Path | None:
    try:
        filename = sanitize_filename(url)
        dest_path = RAW_DATA_DIR / filename

        if dest_path.exists():
            print(f"Skipping (Already exists): {filename}")
            return dest_path

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers, stream=True, timeout=20)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "application/pdf" not in content_type and not url.lower().endswith(".pdf"):
            print(f"Skipping non-PDF response from {url}")
            return None

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"Successfully downloaded: {filename}")
        return dest_path

    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return None


def run_collection_pipeline():
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    download_log = {}
    if COLLECTION_LOG_FILE.exists():
        with open(COLLECTION_LOG_FILE, "r", encoding="utf-8") as f:
            download_log = json.load(f)

    total_downloaded = 0
    print(f"Starting Robust Hybrid Search across {len(TARGET_QUERIES)} target queries...\n")

    for query in TARGET_QUERIES:
        print(f"Executing Query: {query}")
        pdf_urls = get_pdf_urls(query, max_results=10)
        
        for url in pdf_urls:
            if url in download_log:
                continue
            
            saved_path = download_pdf(url)
            if saved_path:
                download_log[url] = {
                    "filename": saved_path.name,
                    "query": query
                }
                total_downloaded += 1

    with open(COLLECTION_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(download_log, f, indent=2, ensure_ascii=False)

    print(f"\nCollection Finished: {total_downloaded} new PDF files saved to {RAW_DATA_DIR}.")


if __name__ == "__main__":
    run_collection_pipeline()