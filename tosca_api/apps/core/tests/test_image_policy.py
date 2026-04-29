"""
Tests for the tiered image validation policy (Task 9.2).
"""

from __future__ import annotations

import hashlib
import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from tosca_api.apps.core.image_policy import (
    MAX_FILE_SIZE_BYTES,
    validate_hero_image,
    validate_inline_image,
)


def _make_image_bytes(
    *,
    width: int,
    height: int,
    fmt: str = "PNG",
    color: tuple[int, int, int] = (200, 100, 50),
    exif: bytes | None = None,
) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    save_kwargs: dict = {"format": fmt}
    if exif is not None:
        save_kwargs["exif"] = exif
    img.save(buf, **save_kwargs)
    return buf.getvalue()


def _uploaded(
    name: str,
    content: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


# ---- Boundary dimension tests ---------------------------------------------


@pytest.mark.parametrize("fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_hero_accepts_min_boundary(fmt, mime):
    data = _make_image_bytes(width=800, height=450, fmt=fmt)
    out_mime, dims = validate_hero_image(_uploaded(f"a.{fmt.lower()}", data))
    assert out_mime == mime
    assert dims == (800, 450)


def test_hero_rejects_below_min():
    data = _make_image_bytes(width=799, height=449, fmt="PNG")
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded("a.png", data))


def test_hero_accepts_max_boundary():
    data = _make_image_bytes(width=6000, height=6000, fmt="PNG")
    _, dims = validate_hero_image(_uploaded("a.png", data))
    assert dims == (6000, 6000)


def test_hero_rejects_above_max():
    data = _make_image_bytes(width=6001, height=6001, fmt="PNG")
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded("a.png", data))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_inline_accepts_min_boundary(fmt):
    data = _make_image_bytes(width=200, height=200, fmt=fmt)
    _, dims = validate_inline_image(_uploaded(f"b.{fmt.lower()}", data))
    assert dims == (200, 200)


def test_inline_rejects_below_min():
    data = _make_image_bytes(width=199, height=199, fmt="PNG")
    with pytest.raises(ValidationError):
        validate_inline_image(_uploaded("b.png", data))


def test_inline_accepts_max_boundary():
    data = _make_image_bytes(width=6000, height=6000, fmt="PNG")
    _, dims = validate_inline_image(_uploaded("b.png", data))
    assert dims == (6000, 6000)


def test_inline_rejects_above_max():
    data = _make_image_bytes(width=6001, height=6001, fmt="PNG")
    with pytest.raises(ValidationError):
        validate_inline_image(_uploaded("b.png", data))


# ---- MIME / format rejection ----------------------------------------------


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_disallowed_pillow_formats_rejected(fmt):
    img = Image.new("RGB", (1000, 1000), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    data = buf.getvalue()
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded(f"x.{fmt.lower()}", data))
    with pytest.raises(ValidationError):
        validate_inline_image(_uploaded(f"x.{fmt.lower()}", data))


def test_svg_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000"></svg>'
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded("x.svg", svg, content_type="image/svg+xml"))
    with pytest.raises(ValidationError):
        validate_inline_image(_uploaded("x.svg", svg, content_type="image/svg+xml"))


def test_forged_content_type_does_not_bypass_mime_check():
    """A GIF body labeled image/png is still rejected — MIME comes from header."""
    img = Image.new("RGB", (1000, 1000), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded("fake.png", buf.getvalue(), content_type="image/png"))


def test_undecodable_payload_rejected():
    junk = b"this is not an image at all" * 100
    with pytest.raises(ValidationError):
        validate_hero_image(_uploaded("trash.png", junk, content_type="image/png"))
    with pytest.raises(ValidationError):
        validate_inline_image(_uploaded("trash.png", junk, content_type="image/png"))


# ---- Size cap --------------------------------------------------------------


def test_oversize_file_rejected(monkeypatch):
    # Build a small valid image, then claim a huge size via SimpleUploadedFile.
    data = _make_image_bytes(width=1000, height=1000, fmt="PNG")
    too_big = _uploaded("big.png", data)
    # Force size to exceed the cap without allocating the bytes.
    monkeypatch.setattr(too_big, "size", MAX_FILE_SIZE_BYTES + 1, raising=False)
    with pytest.raises(ValidationError):
        validate_hero_image(too_big)
    monkeypatch.setattr(too_big, "size", MAX_FILE_SIZE_BYTES + 1, raising=False)
    with pytest.raises(ValidationError):
        validate_inline_image(too_big)


# ---- Read-only / byte preservation ----------------------------------------


def test_validation_does_not_mutate_upload_bytes():
    """Stored bytes match upload bytes after validation runs (no re-encode)."""
    # Build a JPEG with EXIF metadata (camera Make/Model + Orientation).
    exif = Image.Exif()
    exif[0x010F] = "TestMake"  # Make
    exif[0x0110] = "TestModel"  # Model
    exif[0x0112] = 6  # Orientation = rotated 90 CW
    exif_blob = exif.tobytes()
    data = _make_image_bytes(width=1000, height=600, fmt="JPEG", exif=exif_blob)
    # Sanity-check the original carries EXIF before validation runs.
    with Image.open(io.BytesIO(data)) as probe:
        assert probe.getexif().get(0x0112) == 6
    sha_before = hashlib.sha256(data).hexdigest()

    upload = _uploaded("hero.jpg", data, content_type="image/jpeg")
    validate_hero_image(upload)

    upload.seek(0)
    after = upload.read()
    assert hashlib.sha256(after).hexdigest() == sha_before
    assert after == data


def test_no_provided_file_rejected():
    with pytest.raises(ValidationError):
        validate_hero_image(None)  # type: ignore[arg-type]
