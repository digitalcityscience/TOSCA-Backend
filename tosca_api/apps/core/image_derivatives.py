from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

ALLOWED_DERIVATIVE_FORMATS = {"webp", "avif"}
ALLOWED_DERIVATIVE_WIDTHS = {480, 960, 1440, 1920}


class DerivativeError(ValueError):
    """Base class for derivative request errors."""


class UnsupportedDerivativeFormat(DerivativeError):
    """Raised when a requested derivative format is not allowed."""


class UnsupportedDerivativeWidth(DerivativeError):
    """Raised when a requested derivative width is not allowed."""


class UnsupportedDerivativeSource(DerivativeError):
    """Raised when a requested source path is outside storage-path shape."""


class DerivativeSourceMissing(FileNotFoundError):
    """Raised when the source image is not present in storage."""


class DerivativeFormatUnavailable(RuntimeError):
    """Raised when Pillow cannot encode an otherwise allowed format."""


@dataclass(frozen=True)
class DerivativeResult:
    storage_path: str
    content_type: str


def derivative_url(original_path: str, *, fmt: str, width: int | None = None) -> str:
    params = {"src": original_path, "fmt": fmt}
    if width is not None:
        params["w"] = str(width)
    return f"/api/v1/media/derivative/?{urlencode(params)}"


def image_url_with_derivatives(original_path: str, request) -> dict:
    original_url = default_storage.url(original_path)
    if request is not None:
        original_url = request.build_absolute_uri(original_url)

    variants: dict[str, dict[str, str | dict[int, str]]] = {"original": original_url}
    for fmt in sorted(ALLOWED_DERIVATIVE_FORMATS):
        variants[fmt] = {
            "original": _absolute_derivative_url(
                request, derivative_url(original_path, fmt=fmt)
            ),
            "widths": {
                width: _absolute_derivative_url(
                    request, derivative_url(original_path, fmt=fmt, width=width)
                )
                for width in sorted(ALLOWED_DERIVATIVE_WIDTHS)
            },
        }
    return variants


def generate_derivative(
    original_path: str, *, fmt: str, width: int | None = None
) -> DerivativeResult:
    normalized_path = _normalize_source_path(original_path)
    normalized_format = _normalize_format(fmt)
    normalized_width = _normalize_width(width)
    cache_path = _cache_path(normalized_path, fmt=normalized_format, width=normalized_width)
    content_type = f"image/{normalized_format}"

    if default_storage.exists(cache_path):
        return DerivativeResult(storage_path=cache_path, content_type=content_type)

    if not default_storage.exists(normalized_path):
        raise DerivativeSourceMissing(normalized_path)

    try:
        with default_storage.open(normalized_path, "rb") as source:
            with Image.open(source) as image:
                derivative = ImageOps.exif_transpose(image)
                derivative.load()
    except FileNotFoundError as exc:
        raise DerivativeSourceMissing(normalized_path) from exc

    if normalized_width is not None and derivative.width > normalized_width:
        height = max(1, round(derivative.height * (normalized_width / derivative.width)))
        derivative = derivative.resize((normalized_width, height), Image.Resampling.LANCZOS)

    encoded = _encode_derivative(derivative, normalized_format)
    if default_storage.exists(cache_path):
        return DerivativeResult(storage_path=cache_path, content_type=content_type)

    saved_path = default_storage.save(cache_path, ContentFile(encoded))
    return DerivativeResult(storage_path=saved_path, content_type=content_type)


def _normalize_format(fmt: str) -> str:
    normalized = (fmt or "").lower()
    if normalized not in ALLOWED_DERIVATIVE_FORMATS:
        raise UnsupportedDerivativeFormat(f"Unsupported derivative format '{fmt}'.")
    return normalized


def _normalize_source_path(original_path: str) -> str:
    if not original_path or original_path.startswith("/"):
        raise UnsupportedDerivativeSource("Source must be a relative storage path.")
    parts = original_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsupportedDerivativeSource("Source must be a normalized storage path.")
    return original_path


def _normalize_width(width: int | None) -> int | None:
    if width is None:
        return None
    try:
        normalized = int(width)
    except (TypeError, ValueError) as exc:
        raise UnsupportedDerivativeWidth("Derivative width must be an integer.") from exc
    if normalized not in ALLOWED_DERIVATIVE_WIDTHS:
        raise UnsupportedDerivativeWidth(f"Unsupported derivative width '{width}'.")
    return normalized


def _cache_path(original_path: str, *, fmt: str, width: int | None) -> str:
    digest = hashlib.sha1(original_path.encode("utf-8")).hexdigest()
    width_key = str(width) if width is not None else "original"
    return f"derivatives/{digest}/{width_key}.{fmt}"


def _encode_derivative(image: Image.Image, fmt: str) -> bytes:
    save_format = fmt.upper()
    output = io.BytesIO()
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    try:
        image.save(output, format=save_format)
    except (KeyError, OSError, ValueError) as exc:
        if fmt == "avif":
            raise DerivativeFormatUnavailable("AVIF encoding is not available.") from exc
        raise
    return output.getvalue()


def _absolute_derivative_url(request, url: str) -> str:
    if request is None:
        return url
    return request.build_absolute_uri(url)
