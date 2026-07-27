"""Image upload validation.

The client-declared ``Content-Type`` is attacker-controlled, so it can never be
the basis for trusting an upload. This module validates what the bytes actually
*are* before they reach the model or any decoder:

  1. magic-byte sniffing, cross-checked against the declared type
  2. header-only dimension read, to reject decompression bombs before decode

Both checks are cheap (they read a few hundred bytes) and run before the
expensive third-party call.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("snapworth.imagevalidation")

# Upper bound on total pixels. A 50 MP image is far beyond any phone camera
# and is the classic decompression-bomb shape: tiny compressed payload, huge
# allocation on decode. 50 MP ≈ 200 MB as RGBA.
MAX_PIXELS = 50_000_000
MAX_DIMENSION = 20_000

# Minimum useful size — anything smaller cannot produce a usable valuation and
# is almost always a probe.
MIN_DIMENSION = 16


class ImageValidationError(ValueError):
    """Raised when an upload is not a usable image. Message is user-safe."""


def sniff_format(data: bytes) -> str | None:
    """Return a canonical MIME type from the leading bytes, or None if unknown.

    Deliberately conservative: only the formats we actually accept are matched.
    """
    if len(data) < 12:
        return None

    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # WebP is a RIFF container: "RIFF" <4-byte size> "WEBP"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF: ISO-BMFF box with an 'ftyp' brand. Accepted because iOS
    # produces it natively and a client may forward it unconverted.
    if data[4:8] == b"ftyp" and data[8:12] in (
        b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"mif1", b"msf1",
    ):
        return "image/heic"
    return None


# Types the API accepts, and the sniffed types each may legitimately carry.
# JPEG/HEIC are grouped because iOS clients routinely mislabel HEIC as JPEG.
_COMPATIBLE: dict[str, set[str]] = {
    "image/jpeg": {"image/jpeg", "image/heic"},
    "image/jpg":  {"image/jpeg", "image/heic"},
    "image/png":  {"image/png"},
    "image/gif":  {"image/gif"},
    "image/webp": {"image/webp"},
    "image/heic": {"image/heic", "image/jpeg"},
    "image/heif": {"image/heic", "image/jpeg"},
}

ALLOWED_DECLARED_TYPES = frozenset(_COMPATIBLE)


def validate(data: bytes, declared_type: str) -> str:
    """Validate an upload and return the *sniffed* (trusted) MIME type.

    Raises ``ImageValidationError`` with a user-safe message on any failure.
    The returned type — not the declared one — is what should be handed to the
    model, so a mislabelled-but-valid image is still processed correctly.
    """
    if not data:
        raise ImageValidationError("Empty image file.")

    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_DECLARED_TYPES:
        raise ImageValidationError(
            f"Unsupported file type '{declared_type}'. Use JPEG, PNG, GIF, WebP, or HEIC."
        )

    sniffed = sniff_format(data)
    if sniffed is None:
        raise ImageValidationError(
            "That file isn't a readable image. Try a JPEG or PNG photo."
        )

    if sniffed not in _COMPATIBLE[declared]:
        # Mismatch between what the client claimed and what the bytes are.
        # Worth logging: it is either a broken client or a probe.
        log.warning("upload type mismatch declared=%s sniffed=%s", declared, sniffed)
        raise ImageValidationError(
            "That file isn't a readable image. Try a JPEG or PNG photo."
        )

    _check_dimensions(data, sniffed)
    return sniffed


def _check_dimensions(data: bytes, sniffed: str) -> None:
    """Reject implausible dimensions using a header-only read.

    Pillow is optional: when it isn't installed the size check is skipped rather
    than failing the request. Magic-byte validation still applies, so the
    degraded mode is weaker but not unsafe.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - exercised only without Pillow
        log.warning("Pillow unavailable — skipping image dimension validation")
        return

    # Pillow's own bomb guard; we enforce our stricter limit below.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        # `Image.open` is lazy: it parses the header and does NOT decode pixels,
        # so this stays cheap even for a hostile file.
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Image.DecompressionBombError:
        raise ImageValidationError("That image is too large to process.") from None
    except Exception:
        # HEIC needs a plugin Pillow may not have. Don't fail a validly-sniffed
        # image just because we can't read its header.
        if sniffed == "image/heic":
            return
        raise ImageValidationError(
            "That image couldn't be read. Try re-taking the photo."
        ) from None

    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise ImageValidationError("That image is too small to identify.")
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ImageValidationError("That image is too large to process.")
