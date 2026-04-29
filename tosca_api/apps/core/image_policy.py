"""
Tiered server-side image validation policy.

Two callables are exposed:

- ``validate_hero_image(file)`` — strict tier for GeoStory hero images.
- ``validate_inline_image(file)`` — relaxed tier for EditorJS inline image
  blocks.

Both share MIME, size, and decode rules and differ only in dimension bounds.

Design notes:

- MIME is determined by reading the file *header bytes* via Pillow
  (``Image.open(...).format``). Request ``Content-Type`` is advisory only and
  is never trusted.
- Validation is **read-only**. The validator never rewrites or replaces the
  upload bytes — the file written to storage is byte-for-byte identical to
  what the client sent. EXIF/metadata stripping is handled later, at
  derivative re-encode time (Task 9.4), not on ingest.
- Alt text is enforced at the use-site (hero in Tasks 9.1/9.3, inline image
  block in Task 9.5), not at this validation layer.
"""

from __future__ import annotations

from typing import IO, Any, Tuple

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_FILE_SIZE_BYTES: int = 8 * 1024 * 1024  # 8 MB

# Pillow format string -> canonical MIME
_PILLOW_FORMAT_TO_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(_PILLOW_FORMAT_TO_MIME.values())

HERO_MIN_DIMENSIONS: Tuple[int, int] = (800, 450)
HERO_MAX_DIMENSIONS: Tuple[int, int] = (6000, 6000)

INLINE_MIN_DIMENSIONS: Tuple[int, int] = (200, 200)
INLINE_MAX_DIMENSIONS: Tuple[int, int] = (6000, 6000)


def _file_size(file: Any) -> int:
    size = getattr(file, "size", None)
    if isinstance(size, int):
        return size
    pos = file.tell() if hasattr(file, "tell") else None
    file.seek(0, 2)
    size = file.tell()
    if pos is not None:
        file.seek(pos)
    else:
        file.seek(0)
    return size


def _rewind(file: Any) -> None:
    if hasattr(file, "seek"):
        file.seek(0)


def _open_image(file: Any) -> Tuple[str, Tuple[int, int]]:
    """Return (mime, (width, height)) by inspecting the file header.

    Raises ValidationError on decode failure or unsupported format.
    """
    _rewind(file)
    try:
        # Use a fresh open for ``verify`` (which renders the image unusable
        # afterwards) and a second open to read dimensions.
        with Image.open(file) as probe:
            probe.verify()
    except UnidentifiedImageError as exc:
        raise ValidationError(
            {"image": f"Unable to decode image file: {exc}"}
        ) from exc
    except Exception as exc:  # Pillow may raise SyntaxError/OSError on bad data
        raise ValidationError(
            {"image": f"Unable to decode image file: {exc}"}
        ) from exc

    _rewind(file)
    try:
        with Image.open(file) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
    except UnidentifiedImageError as exc:
        raise ValidationError(
            {"image": f"Unable to read image dimensions: {exc}"}
        ) from exc
    except Exception as exc:
        raise ValidationError(
            {"image": f"Unable to read image dimensions: {exc}"}
        ) from exc
    finally:
        _rewind(file)

    mime = _PILLOW_FORMAT_TO_MIME.get(fmt)
    if mime is None:
        raise ValidationError(
            {
                "image": (
                    f"Unsupported image format '{fmt or 'unknown'}'. "
                    f"Allowed: {sorted(ALLOWED_MIME_TYPES)}."
                )
            }
        )
    return mime, (width, height)


def _validate_common(file: Any) -> Tuple[str, Tuple[int, int]]:
    """Run shared MIME / size / decode rules and return (mime, dimensions)."""
    if file is None:
        raise ValidationError({"image": "No image file provided."})

    size = _file_size(file)
    if size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(
            {
                "image": (
                    f"Image file is {size} bytes; maximum allowed is "
                    f"{MAX_FILE_SIZE_BYTES} bytes (8 MB)."
                )
            }
        )

    return _open_image(file)


def _validate_dimensions(
    dimensions: Tuple[int, int],
    *,
    minimum: Tuple[int, int],
    maximum: Tuple[int, int],
    tier: str,
) -> None:
    width, height = dimensions
    min_w, min_h = minimum
    max_w, max_h = maximum
    if width < min_w or height < min_h:
        raise ValidationError(
            {
                "image": (
                    f"Image dimensions {width}x{height} are below the "
                    f"{tier} minimum {min_w}x{min_h}."
                )
            }
        )
    if width > max_w or height > max_h:
        raise ValidationError(
            {
                "image": (
                    f"Image dimensions {width}x{height} exceed the "
                    f"{tier} maximum {max_w}x{max_h}."
                )
            }
        )


def validate_hero_image(file: IO[bytes]) -> Tuple[str, Tuple[int, int]]:
    """Validate a GeoStory hero image. Returns (mime, (width, height))."""
    mime, dimensions = _validate_common(file)
    _validate_dimensions(
        dimensions,
        minimum=HERO_MIN_DIMENSIONS,
        maximum=HERO_MAX_DIMENSIONS,
        tier="hero",
    )
    return mime, dimensions


def validate_inline_image(file: IO[bytes]) -> Tuple[str, Tuple[int, int]]:
    """Validate an inline EditorJS image. Returns (mime, (width, height))."""
    mime, dimensions = _validate_common(file)
    _validate_dimensions(
        dimensions,
        minimum=INLINE_MIN_DIMENSIONS,
        maximum=INLINE_MAX_DIMENSIONS,
        tier="inline",
    )
    return mime, dimensions
