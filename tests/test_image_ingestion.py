from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from ensemble.image_ingestion import (
    ImageExtractionError, extract_image_metadata, image_metadata_text,
)


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (32, 24), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_extracts_safe_image_metadata():
    result = extract_image_metadata(png_bytes())
    assert result["format"] == "PNG"
    assert (result["width"], result["height"]) == (32, 24)
    assert "not been OCR" in image_metadata_text(result)


def test_rejects_non_image_content():
    with pytest.raises(ImageExtractionError):
        extract_image_metadata(b"not an image")


def test_rejects_empty_image():
    with pytest.raises(ImageExtractionError):
        extract_image_metadata(b"")
