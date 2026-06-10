"""Lightweight image preprocessing.

Jenny: agents reject labels shot at bad angles / poor lighting / with glare.
We can't fully de-warp a curved bottle, but cheap, fast fixes recover a lot of
otherwise-rejected images before they ever reach OCR:

  - EXIF auto-orientation (phone photos are often sideways)
  - grayscale + autocontrast (rescues dim / low-contrast shots)
  - mild sharpening

All operations are local (Pillow), add only a few milliseconds, and never call
out to the network, which keeps us well inside the latency budget.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, ImageFilter

MAX_DIM = 1600          # ~matches TTB's 1.5 MB / 120-170 dpi label spec; keeps
                        # small print legible while cutting the VLM's image tiling
                        # (fewer 512px tiles => faster + cheaper, within the 5s bar).
MAX_PIXELS = 50_000_000  # ~50 MP ceiling; anything larger is treated as a bomb.

# Belt-and-suspenders: also cap PIL's own decompression-bomb guard so a malicious
# image can't force a gigantic decode even if validation is bypassed.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class InvalidImage(ValueError):
    """Raised when an upload is not a usable, in-bounds image."""


def validate_image(data: bytes) -> None:
    """Reject non-images and decompression bombs *before* we decode pixels or
    ship bytes to Azure. Raises InvalidImage on anything unsafe."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()  # structural check; does not load full pixels
        # verify() leaves the object unusable, so reopen to read dimensions.
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Image.DecompressionBombError as e:
        raise InvalidImage("Image exceeds the maximum allowed size.") from e
    except Exception as e:
        raise InvalidImage("File is not a readable image.") from e
    if w * h > MAX_PIXELS:
        raise InvalidImage("Image exceeds the maximum allowed dimensions "
                           f"({MAX_PIXELS // 1_000_000} MP).")


def preprocess(image_bytes: bytes) -> bytes:
    """Return cleaned-up JPEG bytes ready for OCR / VLM. Best-effort: on any
    failure we return the original bytes untouched."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)  # honor camera rotation
        img = img.convert("RGB")

        if max(img.size) > MAX_DIM:
            img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)

        # Contrast normalization helps glare / dim lighting without destroying text.
        enhanced = ImageOps.autocontrast(img, cutoff=1)
        enhanced = enhanced.filter(ImageFilter.SHARPEN)

        out = io.BytesIO()
        enhanced.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return image_bytes
