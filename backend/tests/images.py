"""Real image bytes for tests.

Upload validation now inspects magic bytes and reads image headers, so fixtures
must be genuinely decodable images rather than a signature prefix followed by
zero padding. Generating them keeps no binaries in the repo.
"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image

# Comfortably above imagevalidation.MIN_DIMENSION.
DEFAULT_SIZE = (64, 64)


@lru_cache(maxsize=None)
def image_bytes(fmt: str = "JPEG", size: tuple[int, int] = DEFAULT_SIZE) -> bytes:
    """Return encoded bytes for a small, valid image in `fmt`."""
    mode = "P" if fmt == "GIF" else "RGB"
    img = Image.new(mode, size, color=128 if mode == "P" else (120, 90, 60))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def padded_image_bytes(fmt: str = "JPEG", total_size: int = 1024) -> bytes:
    """A valid image padded to at least `total_size` bytes.

    Trailing bytes after the image data are ignored by decoders, so this stays
    valid while letting a test control the payload size.
    """
    data = image_bytes(fmt)
    if len(data) >= total_size:
        return data
    return data + b"\x00" * (total_size - len(data))


VALID_JPEG = image_bytes("JPEG")
VALID_PNG = image_bytes("PNG")
VALID_GIF = image_bytes("GIF")
VALID_WEBP = image_bytes("WEBP")
