"""Safe image metadata extraction using Pillow.

This path indexes image identity and embedded metadata. It does not claim OCR or
visual understanding; those remain separate capabilities.
"""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ExifTags, UnidentifiedImageError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "TIFF", "BMP"}
_TAGS = {value: key for key, value in ExifTags.TAGS.items()}


class ImageExtractionError(ValueError):
    pass


def extract_image_metadata(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not raw:
        raise ImageExtractionError("image body must not be empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise OverflowError("image exceeds the 20 MiB upload limit")
    try:
        with Image.open(BytesIO(raw)) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_FORMATS:
                raise ImageExtractionError("unsupported image format")
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise OverflowError("image dimensions exceed the 40 megapixel limit")
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            exif = image.getexif()
            metadata = {}
            for key, value in exif.items():
                name = ExifTags.TAGS.get(key, str(key))
                if name in {"ImageDescription", "Artist", "Copyright", "DateTime",
                            "DateTimeOriginal", "Make", "Model", "Software"}:
                    text = str(value).strip()
                    if text and len(text) <= 2048:
                        metadata[name] = text
            return {
                "format": image_format, "width": width, "height": height,
                "mode": image.mode, "frames": int(getattr(image, "n_frames", 1)),
                "exif": metadata,
            }
    except (ImageExtractionError, OverflowError):
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageExtractionError("uploaded content is not a safe, readable image") from exc


def image_metadata_text(result: dict) -> str:
    lines = [
        f"Image format: {result['format']}",
        f"Dimensions: {result['width']}x{result['height']}",
        f"Color mode: {result['mode']}",
        f"Frames: {result['frames']}",
    ]
    lines.extend(f"{key}: {value}" for key, value in sorted(result["exif"].items()))
    lines.append("Visual content has not been OCR- or vision-processed.")
    return "\n".join(lines)
