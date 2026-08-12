import json
import unicodedata
from pathlib import Path
from bidi.algorithm import get_display

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_JSON_PATH = BASE_DIR / "data" / "processed" / "processed_chunks.json"

def clean_arabic_for_nlp(text: str) -> str:
    """Normalizes Arabic Presentation Forms and restores logical reading order for LLMs."""
    if not text:
        return text
    
    # 1. Normalize Presentation Forms back to standard Arabic Unicode
    normalized_text = unicodedata.normalize("NFKC", text)
    
    # 2. Reverse the visual Arabic text back to logical order
    logical_text = get_display(normalized_text)
    
    return logical_text

def main():
    if not PROCESSED_JSON_PATH.exists():
        print(f"Error: {PROCESSED_JSON_PATH} not found.")
        return

    with open(PROCESSED_JSON_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    
    for chunk in chunks:
        if "content" in chunk:
            chunk["content"] = clean_arabic_for_nlp(chunk["content"])
        elif "page_content" in chunk:
            chunk["page_content"] = clean_arabic_for_nlp(chunk["page_content"])
        elif "text" in chunk:
            chunk["text"] = clean_arabic_for_nlp(chunk["text"])

    with open(PROCESSED_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
        
    print("Successfully normalized Arabic chunks!")
    
    # Verification printout
    if chunks and "content" in chunks[0]:
        print("\n--- Verification Sample (First 150 chars) ---")
        print(chunks[0]["content"][:150])

if __name__ == "__main__":
    main()