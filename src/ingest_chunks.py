import os
import json
import re
import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Define paths based on your project structure
FILTERED_DIR = os.path.join("data", "filtered")
PROCESSED_DIR = os.path.join("data", "processed")
OUTPUT_FILE = os.path.join(PROCESSED_DIR, "processed_chunks.json")

# Ensure the output directory exists
os.makedirs(PROCESSED_DIR, exist_ok=True)

def clean_text(text: str) -> str:
    """Removes excessive whitespace and standardizes newlines."""
    if not text:
        return ""
    # Replace multiple spaces/newlines with a single space
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def process_pdfs():
    print(f"Starting optimized chunking process for files in {FILTERED_DIR}...")
    
    if not os.path.exists(FILTERED_DIR):
        print(f"Error: Directory {FILTERED_DIR} not found. Ensure your triage script outputs here.")
        return

    pdf_files = [f for f in os.listdir(FILTERED_DIR) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"No PDFs found in {FILTERED_DIR}.")
        return

    # Initialize LangChain's RecursiveCharacterTextSplitter
    # It prioritizes splitting by paragraphs ("\n\n"), then lines ("\n"), then spaces (" "), then characters.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        length_function=len,
        separators=["\n\n", "\n", " ", ""]
    )

    all_chunks = []
    total_files = len(pdf_files)
    
    # Iterate through every filtered PDF
    for idx, filename in enumerate(pdf_files, 1):
        filepath = os.path.join(FILTERED_DIR, filename)
        print(f"[{idx}/{total_files}] Processing: {filename}")
        
        try:
            # Open PDF with pdfplumber for accurate text extraction
            with pdfplumber.open(filepath) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    raw_text = page.extract_text()
                    
                    if not raw_text:
                        continue
                    
                    cleaned_text = clean_text(raw_text)
                    
                    # Skip pages that are mostly empty (e.g., cover pages, blank separators)
                    if len(cleaned_text) < 50: 
                        continue
                        
                    # Split the cleaned page text using the intelligent recursive splitter
                    page_chunks = text_splitter.split_text(cleaned_text)
                    
                    # Attach metadata to each chunk
                    for chunk in page_chunks:
                        all_chunks.append({
                            "source": filename,
                            "page": page_num,
                            "content": chunk,
                            "is_tunisia_specific": "tunis" in filename.lower() or "tunis" in chunk.lower()
                        })
                        
        except Exception as e:
            print(f"  └─ Error processing {filename}: {e}")

    # Save the finalized chunks to a JSON file for the RAG engine
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
        
    print("\n✅ Recursive Chunking Complete!")
    print(f"Total files processed: {total_files}")
    print(f"Total semantic chunks generated: {len(all_chunks)}")
    print(f"Output saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    process_pdfs()