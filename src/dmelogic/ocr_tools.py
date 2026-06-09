"""
ocr_tools.py — OCR Extraction Module

This module isolates the actual OCR logic (PyMuPDF + pytesseract).
It ensures the indexer can reuse it without touching UI code.

Purpose: Extract all readable text from a PDF (OCR if needed).

Note: Tesseract configuration is handled by dmelogic.config.configure_tesseract()
      Call that once at application startup.
"""

import os
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import tempfile

# Tesseract is configured by dmelogic.config.configure_tesseract()
# No hard-coded paths here - keeps configuration centralized


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extracts visible text from the PDF file using PyMuPDF first.
    If pages have no text, falls back to pytesseract OCR.

    Args:
        pdf_path (str): Path to the PDF file

    Returns:
        str: Full text content (concatenated across pages)
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    full_text = ""
    
    try:
        # Open the PDF document
        doc = fitz.open(pdf_path)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # First try regular text extraction
            page_text = page.get_text()
            
            if page_text.strip():
                # Found regular text, use it
                full_text += f"\n--- Page {page_num + 1} ---\n"
                full_text += page_text
                print(f"Page {page_num}: Extracted {len(page_text)} characters via text extraction")
            else:
                # No text found, try OCR
                print(f"Page {page_num}: No text found, attempting OCR...")
                
                try:
                    # Get page as image
                    mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better OCR
                    pix = page.get_pixmap(matrix=mat)
                    img_data = pix.tobytes("png")
                    
                    # Convert to PIL Image
                    image = Image.open(io.BytesIO(img_data))
                    
                    # Perform OCR
                    ocr_text = pytesseract.image_to_string(image, config='--psm 6')
                    
                    if ocr_text.strip():
                        full_text += f"\n--- Page {page_num + 1} (OCR) ---\n"
                        full_text += ocr_text
                        print(f"Page {page_num}: OCR extracted {len(ocr_text)} characters")
                    else:
                        print(f"Page {page_num}: OCR found no text")
                        
                except Exception as ocr_error:
                    print(f"Page {page_num}: OCR failed - {ocr_error}")
        
        doc.close()
        
    except Exception as e:
        print(f"Error processing PDF {pdf_path}: {e}")
        raise
    
    return full_text.strip()


def get_last_modified(pdf_path: str) -> float:
    """
    Returns last modified timestamp for file.
    Used by ocr_indexer to detect changes.

    Args:
        pdf_path (str): Path to the PDF file

    Returns:
        float: Last modified timestamp (Unix timestamp)
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"File not found: {pdf_path}")
    
    return os.path.getmtime(pdf_path)


def extract_text_from_page(pdf_path: str, page_num: int) -> str:
    """
    Extract text from a specific page of a PDF.
    Useful for targeted searches without processing the entire document.

    Args:
        pdf_path (str): Path to the PDF file
        page_num (int): Page number (0-based)

    Returns:
        str: Text content from the specified page
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    try:
        doc = fitz.open(pdf_path)
        
        if page_num >= len(doc) or page_num < 0:
            doc.close()
            raise ValueError(f"Page {page_num} does not exist in document with {len(doc)} pages")
        
        page = doc.load_page(page_num)
        
        # First try regular text extraction
        page_text = page.get_text()
        
        if page_text.strip():
            doc.close()
            return page_text
        else:
            # No text found, try OCR
            try:
                # Get page as image
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better OCR
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                
                # Convert to PIL Image
                image = Image.open(io.BytesIO(img_data))
                
                # Perform OCR
                ocr_text = pytesseract.image_to_string(image, config='--psm 6')
                doc.close()
                
                return ocr_text if ocr_text.strip() else ""
                
            except Exception as ocr_error:
                doc.close()
                print(f"OCR failed for page {page_num}: {ocr_error}")
                return ""
        
    except Exception as e:
        print(f"Error processing page {page_num} of {pdf_path}: {e}")
        raise


def check_ocr_availability() -> bool:
    """
    Check if OCR functionality is available (Tesseract installed and accessible).

    Returns:
        bool: True if OCR is available, False otherwise
    """
    try:
        # Try to get Tesseract version
        version = pytesseract.get_tesseract_version()
        print(f"Tesseract version: {version}")
        return True
    except Exception as e:
        print(f"OCR not available: {e}")
        return False


if __name__ == "__main__":
    # Simple test when run directly
    print("OCR Tools Module Test")
    print("=" * 30)
    
    # Check OCR availability
    if check_ocr_availability():
        print("✓ OCR is available")
    else:
        print("✗ OCR is not available")
    
    # Test with a sample file (if it exists)
    test_file = "faxes/+17185522278-1016-200109-048.pdf"
    if os.path.exists(test_file):
        print(f"\nTesting with: {test_file}")
        try:
            text = extract_text_from_pdf(test_file)
            print(f"Extracted {len(text)} characters")
            print(f"First 200 characters: {text[:200]}...")
        except Exception as e:
            print(f"Test failed: {e}")
    else:
        print(f"\nTest file not found: {test_file}")