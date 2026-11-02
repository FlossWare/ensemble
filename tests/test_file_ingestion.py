from io import BytesIO

import pytest
from pypdf import PdfWriter

from ensemble.file_ingestion import PDFExtractionError, extract_pdf


def _blank_pdf():
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def test_pdf_extraction_rejects_non_pdf_and_scanned_or_empty_documents():
    with pytest.raises(PDFExtractionError, match="not a PDF"):
        extract_pdf(b"not a pdf")
    with pytest.raises(PDFExtractionError, match="no extractable text"):
        extract_pdf(_blank_pdf())


def test_pdf_extraction_enforces_upload_limit():
    with pytest.raises(OverflowError, match="20 MiB"):
        extract_pdf(b"%PDF-" + (b"x" * (20 * 1024 * 1024)))
