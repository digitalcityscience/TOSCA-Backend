from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.core.files.storage import default_storage
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from tosca_api.apps.geocontext import views
from tosca_api.apps.core.models import MediaAsset


def _image_bytes(*, width: int = 240, height: int = 240, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(80, 120, 160)).save(buf, format=fmt)
    return buf.getvalue()


def _upload_file(name: str, data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


@pytest.fixture
def api_client():
    client = APIClient()
    client.force_authenticate(user=SimpleNamespace(is_authenticated=True, pk=1, is_active=True))
    return client


@pytest.mark.django_db
def test_upload_by_file_stores_original_bytes_and_returns_editorjs_contract(api_client, tmp_path):
    data = _image_bytes()
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-file/",
            {"image": _upload_file("inline.png", data)},
            format="multipart",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] == 1
        file_info = payload["file"]
        assert file_info["url"].startswith("http")
        assert file_info["mime"] == "image/png"
        assert file_info["width"] == 240
        assert file_info["height"] == 240

        storage_path = MediaAsset.objects.latest("created_at").storage_path
        assert storage_path.startswith("geocontext/editorjs/")
        asset = MediaAsset.objects.get(storage_path=storage_path)
        assert asset.original_name == "inline.png"
        assert asset.mime == "image/png"
        assert asset.width == 240
        assert asset.height == 240
        assert asset.size == len(data)
        with default_storage.open(storage_path, "rb") as stored:
            assert stored.read() == data


def test_upload_by_file_rejects_invalid_image(api_client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-file/",
            {"image": _upload_file("tiny.png", _image_bytes(width=100, height=100))},
            format="multipart",
        )

    assert response.status_code == 400
    assert response.json()["success"] == 0
    assert "minimum" in response.json()["error"]


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        (
            "/api/v1/content/editorjs/upload-by-file/",
            lambda: {"image": _upload_file("inline.png", _image_bytes())},
        ),
        (
            "/api/v1/content/editorjs/upload-by-url/",
            lambda: {"url": "https://remote.test/inline.png"},
        ),
    ],
)
def test_upload_endpoints_require_authentication(client, tmp_path, endpoint, payload):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = client.post(endpoint, payload())

    assert response.status_code in {401, 403}


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://remote.test/image.png",
        status_code: int = 200,
        history: list | None = None,
    ):
        self.body = body
        self.url = url
        self.status_code = status_code
        self.history = history or []

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]


@pytest.mark.django_db
def test_upload_by_url_downloads_rehosts_and_preserves_bytes(api_client, monkeypatch, tmp_path):
    data = _image_bytes(fmt="WEBP")

    def fake_get(url, **kwargs):
        assert kwargs["stream"] is True
        assert kwargs["allow_redirects"] is True
        return _FakeResponse(data, url=url)

    monkeypatch.setattr(views.requests, "get", fake_get)

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": "https://remote.test/path/inline.webp"},
            format="json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] == 1
        assert payload["file"]["mime"] == "image/webp"

        storage_path = MediaAsset.objects.latest("created_at").storage_path
        assert storage_path.startswith("geocontext/editorjs/")
        with default_storage.open(storage_path, "rb") as stored:
            assert stored.read() == data


@pytest.mark.parametrize(
    "url,expected",
    [
        ("ftp://remote.test/image.png", "not allowed"),
        ("http://testserver/media/geocontext/editorjs/a.png", "picker"),
    ],
)
def test_upload_by_url_rejects_disallowed_sources(api_client, tmp_path, url, expected):
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": url},
            format="json",
        )

    assert response.status_code == 400
    assert response.json()["success"] == 0
    assert expected in response.json()["error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://127.0.0.1/secret.png",
        "http://[::1]/secret.png",
        "http://10.0.0.5/internal.png",
        "http://192.168.1.10/internal.png",
    ],
)
def test_upload_by_url_blocks_ip_literals_to_internal_hosts(api_client, monkeypatch, tmp_path, url):
    # requests.get must never be reached for a blocked literal address.
    monkeypatch.setattr(
        views.requests,
        "get",
        lambda *a, **k: pytest.fail("blocked URL should not be fetched"),
    )
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": url},
            format="json",
        )

    assert response.status_code == 400
    assert response.json()["success"] == 0
    assert "internal" in response.json()["error"]


def test_upload_by_url_blocks_hostname_resolving_to_private_ip(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(views, "_resolve_host_ips", lambda host: ["10.1.2.3"])
    monkeypatch.setattr(
        views.requests,
        "get",
        lambda *a, **k: pytest.fail("blocked URL should not be fetched"),
    )
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": "https://intranet.private.example/logo.png"},
            format="json",
        )

    assert response.status_code == 400
    assert "internal" in response.json()["error"]


@pytest.mark.django_db
def test_upload_by_url_blocks_redirect_to_internal_host(api_client, monkeypatch, tmp_path):
    data = _image_bytes()

    # First hop resolves public; the response reports a final URL on an
    # internal literal address, simulating a redirect into the metadata host.
    monkeypatch.setattr(views, "_resolve_host_ips", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        views.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(data, url="http://169.254.169.254/latest/meta-data/"),
    )
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": "https://public.example/inline.png"},
            format="json",
        )

    assert response.status_code == 400
    assert "internal" in response.json()["error"]


def test_editorjs_endpoints_declare_scoped_throttles():
    from django.conf import settings

    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert "editorjs_upload" in rates
    assert "editorjs_media" in rates
    assert views.EditorJSImageUploadByFileView.throttle_classes == [views.EditorJSUploadThrottle]
    assert views.EditorJSImageUploadByUrlView.throttle_classes == [views.EditorJSUploadThrottle]
    assert views.EditorJSImageLibraryView.throttle_classes == [views.EditorJSMediaThrottle]
    assert views.EditorJSUploadThrottle.scope == "editorjs_upload"
    assert views.EditorJSMediaThrottle.scope == "editorjs_media"


def test_upload_by_url_rejects_oversized_download(api_client, monkeypatch, tmp_path):
    data = b"x" * (views.MAX_FILE_SIZE_BYTES + 1)

    monkeypatch.setattr(
        views.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(data),
    )

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        response = api_client.post(
            "/api/v1/content/editorjs/upload-by-url/",
            {"url": "https://remote.test/large.png"},
            format="json",
        )

    assert response.status_code == 400
    assert response.json()["success"] == 0
    assert "exceeds" in response.json()["error"]


@pytest.mark.django_db
def test_media_library_lists_previous_editorjs_uploads(api_client, tmp_path, monkeypatch):
    data = _image_bytes()
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        storage_path = default_storage.save(
            "geocontext/editorjs/context-id/library.png",
            _upload_file("library.png", data),
        )
        MediaAsset.objects.create(
            storage_path=storage_path,
            original_name="library.png",
            mime="image/png",
            width=240,
            height=240,
            size=len(data),
        )
        monkeypatch.setattr(
            default_storage,
            "listdir",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("media library must not scan storage")
            ),
        )
        monkeypatch.setattr(
            default_storage,
            "open",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("media library must not open storage objects")
            ),
        )

        response = api_client.get("/api/v1/content/editorjs/media/")

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["name"] == "library.png"
    assert results[0]["mime"] == "image/png"
    assert storage_path in results[0]["url"]


def test_openapi_schema_documents_editorjs_image_endpoints(client):
    response = client.get("/api/v1/schema/")
    assert response.status_code == 200

    schema = response.content.decode()
    assert "/api/v1/content/editorjs/upload-by-file/" in schema
    assert "/api/v1/content/editorjs/upload-by-url/" in schema
    assert "/api/v1/content/editorjs/media/" in schema
    assert "EditorJSImageUploadSuccess" in schema
