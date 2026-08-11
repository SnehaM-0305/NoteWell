"""
Video-to-Notes AI Platform — text extraction for uploaded PDF / DOCX files.

Both functions take raw file bytes (as read from an UploadFile) and return
plain text, ready to be handed to the same chunk -> summarize -> build_notes
pipeline that the video/text/audio sources already use in main.py.
"""

import io

from fastapi import HTTPException


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Pull plain text out of a PDF. Scanned/image-only PDFs will yield little
    or no text — there's no OCR step here, callers should treat empty output
    as a user-facing error rather than a silent success."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="pypdf is not installed. Run 'pip install -r requirements.txt' inside backend/ and try again.",
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read that PDF: {exc}") from exc

    return "\n".join(pages_text).strip()


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Pull plain text (paragraphs, in order) out of a .docx file."""
    try:
        from docx import Document
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="python-docx is not installed. Run 'pip install -r requirements.txt' inside backend/ and try again.",
        ) from exc

    try:
        document = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read that Word document: {exc}") from exc

    return "\n".join(paragraphs).strip()
