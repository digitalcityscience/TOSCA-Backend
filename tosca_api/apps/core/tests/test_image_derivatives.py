from __future__ import annotations

import hashlib
import io

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from PIL import Image

from tosca_api.apps.core.image_derivatives import generate_derivative


def _image_bytes(*, width: int = 320, height: int = 240, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(60, 90, 120)).save(buf, format=fmt)
    return buf.getvalue()


def _oriented_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", (80, 40), color=(60, 90, 120))
    exif = Image.Exif()
    exif[274] = 6
    image.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_webp_derivative_is_generated_and_cached(tmp_path):
    original = _image_bytes()
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(original))

        first = generate_derivative("uploads/original.png", fmt="webp", width=480)
        second = generate_derivative("uploads/original.png", fmt="webp", width=480)

        assert first == second
        assert first.storage_path.startswith("derivatives/")
        with default_storage.open(first.storage_path, "rb") as stored:
            with Image.open(stored) as image:
                assert image.format == "WEBP"
                assert not image.getexif()


def test_derivative_generation_does_not_mutate_original(tmp_path):
    original = _image_bytes()
    original_sha = hashlib.sha256(original).hexdigest()

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(original))
        generate_derivative("uploads/original.png", fmt="webp")

        with default_storage.open("uploads/original.png", "rb") as stored:
            assert hashlib.sha256(stored.read()).hexdigest() == original_sha


def test_derivative_view_documents_and_serves_webp(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(_image_bytes()))
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "uploads/original.png", "fmt": "webp", "w": "480"},
        )

    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert "immutable" in response["Cache-Control"]


def test_derivative_view_rejects_unknown_width(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(_image_bytes()))
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "uploads/original.png", "fmt": "webp", "w": "999"},
        )

    assert response.status_code == 400


def test_derivative_view_rejects_unknown_format(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(_image_bytes()))
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "uploads/original.png", "fmt": "jp2"},
        )

    assert response.status_code == 400


def test_derivative_view_rejects_non_normalized_source(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "../secret.png", "fmt": "webp"},
        )

    assert response.status_code == 400


def test_derivative_view_returns_404_for_missing_original(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "uploads/missing.png", "fmt": "webp"},
        )

    assert response.status_code == 404


def test_avif_returns_501_when_encoder_is_unavailable(client, monkeypatch, tmp_path):
    original_save = Image.Image.save

    def fake_save(self, fp, format=None, **params):
        if format == "AVIF":
            raise KeyError("AVIF")
        return original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", fake_save)
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/original.png", ContentFile(_image_bytes()))
        response = client.get(
            "/api/v1/media/derivative/",
            {"src": "uploads/original.png", "fmt": "avif"},
        )

    assert response.status_code == 501


def test_exif_orientation_is_baked_into_derivative(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("uploads/oriented.jpg", ContentFile(_oriented_jpeg_bytes()))

        result = generate_derivative("uploads/oriented.jpg", fmt="webp")

        with default_storage.open(result.storage_path, "rb") as stored:
            with Image.open(stored) as image:
                assert image.size == (40, 80)
                assert not image.getexif()
