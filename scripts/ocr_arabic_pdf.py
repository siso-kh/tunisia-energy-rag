# src/ocr_arabic_pdf.py
from pdf2image import convert_from_path
import easyocr
import numpy as np

# Initialize EasyOCR for Arabic and English
reader = easyocr.Reader(['ar', 'en'])

def ocr_pdf(pdf_path: str) -> str:
    images = convert_from_path(pdf_path)
    full_text = []
    
    for page_num, image in enumerate(images):
        img_np = np.array(image)
        # EasyOCR automatically returns logical, connected Arabic text
        results = reader.readtext(img_np, detail=0)
        page_text = " ".join(results)
        full_text.append(page_text)
        
    return "\n\n".join(full_text)