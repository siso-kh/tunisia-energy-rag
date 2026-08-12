import json
import re
from pathlib import Path

# Paths (resolve repo root from src/utils -> two parents up)
REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_JSON_PATH = REPO_ROOT / "data" / "processed" / "processed_chunks.json"

# Unicode Regex Boundaries
# Standard Arabic block (Expected for LLMs)
STANDARD_ARABIC_REGEX = re.compile(r"[\u0600-\u06FF]")
# Presentation Forms block (Corrupted/Visual-only Arabic)
CORRUPTED_ARABIC_REGEX = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")

def verify_dataset() -> None:
    if not PROCESSED_JSON_PATH.exists():
        print(f"Error: Could not find {PROCESSED_JSON_PATH}")
        return

    with open(PROCESSED_JSON_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    total_chunks = len(chunks)
    arabic_chunks = []
    corrupted_count = 0

    for chunk in chunks:
        text = chunk.get("content", "")
        
        has_standard = STANDARD_ARABIC_REGEX.search(text)
        has_corrupted = CORRUPTED_ARABIC_REGEX.search(text)
        
        if has_standard or has_corrupted:
            arabic_chunks.append(text)
            
        if has_corrupted:
            corrupted_count += 1
            # NEW: Print exactly where the corruption is coming from
            source = chunk.get("source", "Unknown PDF")
            page = chunk.get("page", "Unknown Page")
            print(f"Corrupted chunk found in -> File: {source} | Page: {page}")

    # Output Diagnostics
    print("==================================================")
    print("           RAG DATASET UNICODE AUDIT              ")
    print("==================================================")
    print(f"Total Chunks Scanned       : {total_chunks}")
    print(f"Chunks Containing Arabic   : {len(arabic_chunks)}")
    print(f"Chunks with Corrupted Text : {corrupted_count}")
    print("==================================================")

    if corrupted_count == 0 and arabic_chunks:
        print("\n[SUCCESS] Verification Passed!")
        print("0% Presentation Forms detected. The database is 100% LLM-ready logical Unicode.")
        print("\n--- Visual Sample (First 3 Arabic Chunks) ---")
        for i, text in enumerate(arabic_chunks[:3], 1):
            print(f"\n[Chunk {i} - First 200 chars]:")
            # The terminal may print this backwards depending on your PowerShell settings, 
            # but the script just proved the underlying bytes are correct.
            print(text[:200].replace("\n", " "))
    elif corrupted_count > 0:
        print("\n[FAILED] The dataset still contains visual presentation forms.")
        print("Ensure you deleted the old JSON and re-ran ingest_chunks.py with OCR.")

if __name__ == "__main__":
    verify_dataset()