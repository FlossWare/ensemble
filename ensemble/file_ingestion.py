from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 1000
MAX_PDF_TEXT_CHARS = 4_000_000


class PDFExtractionError(ValueError):
    pass


def extract_pdf(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not raw:
        raise PDFExtractionError("PDF body must not be empty")
    if len(raw) > MAX_PDF_BYTES:
        raise OverflowError("PDF exceeds the 20 MiB upload limit")
    if not raw.startswith(b"%PDF-"):
        raise PDFExtractionError("uploaded content is not a PDF")
    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise PDFExtractionError("encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise OverflowError("PDF exceeds the 1000 page limit")
        pages = []
        total = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            total += len(text)
            if total > MAX_PDF_TEXT_CHARS:
                raise OverflowError("extracted PDF text exceeds the 4000000 character limit")
            pages.append({"page": number, "text": text})
    except (PDFExtractionError, OverflowError):
        raise
    except Exception as exc:
        raise PDFExtractionError("PDF could not be parsed") from exc
    if not pages:
        raise PDFExtractionError("PDF contains no extractable text; scanned PDFs require OCR")
    content = "\n\n".join(f"[Page {item['page']}]\n{item['text']}" for item in pages)
    return {"text": content, "page_count": len(reader.pages), "extracted_pages": len(pages)}
