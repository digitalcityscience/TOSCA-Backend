"""
EditorJS image authoring endpoints for GeoContext.

Two upload routes match the ``@editorjs/image`` contract:

- ``POST /api/v1/geocontext/editorjs/upload-by-file/`` — multipart, field ``image``
- ``POST /api/v1/geocontext/editorjs/upload-by-url/`` — JSON ``{"url": "..."}``

A third route lists previously uploaded images so the admin UI can offer a
"select existing" picker:

- ``GET /api/v1/geocontext/editorjs/media/``

All success bodies follow ``{"success": 1, "file": {"url", "mime", "width",
"height"}}``; failure bodies use ``{"success": 0, "error": "<msg>"}`` so the
EditorJS image tool can surface the message inline.
"""

from __future__ import annotations

import io
import uuid
from typing import Iterable, Tuple
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.core.image_policy import (
    MAX_FILE_SIZE_BYTES,
    validate_inline_image,
)


_UPLOAD_SUBDIR = "geocontext/editorjs"
_DOWNLOAD_TIMEOUT_SECONDS = 5
_MAX_REDIRECTS = 1
_ALLOWED_REMOTE_SCHEMES = {"http", "https"}


def _media_url_prefix() -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    if not media_url.endswith("/"):
        media_url += "/"
    return media_url


def _absolute_url(request, storage_path: str) -> str:
    return request.build_absolute_uri(default_storage.url(storage_path))


def _failure(message: str, *, http_status: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"success": 0, "error": message}, status=http_status)


def _validation_error_message(exc: DjangoValidationError) -> str:
    if hasattr(exc, "message_dict"):
        parts: list[str] = []
        for messages in exc.message_dict.values():
            parts.extend(messages)
        return "; ".join(parts) or "Image rejected by validation policy."
    return "; ".join(exc.messages) or "Image rejected by validation policy."


def _store_validated_upload(
    file_obj, *, request, original_name: str | None = None
) -> Response:
    """Validate, persist, and return the EditorJS success response."""
    try:
        mime, (width, height) = validate_inline_image(file_obj)
    except DjangoValidationError as exc:
        return _failure(_validation_error_message(exc))

    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime, "")
    filename = f"{uuid.uuid4().hex}{extension or _suffix_from_name(original_name)}"
    relative_path = f"{_UPLOAD_SUBDIR}/{uuid.uuid4()}/{filename}"

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    storage_path = default_storage.save(relative_path, file_obj)

    return Response(
        {
            "success": 1,
            "file": {
                "url": _absolute_url(request, storage_path),
                "mime": mime,
                "width": width,
                "height": height,
            },
        }
    )


def _suffix_from_name(name: str | None) -> str:
    if not name or "." not in name:
        return ""
    suffix = name.rsplit(".", 1)[-1].lower()
    return f".{suffix}" if suffix.isalnum() and len(suffix) <= 5 else ""


class EditorJSImageUploadByFileView(APIView):
    """``@editorjs/image`` ``byFile`` endpoint."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request, *args, **kwargs):
        # ``@editorjs/image`` defaults the multipart field name to ``image``;
        # accept legacy ``file`` for resilience.
        upload = request.FILES.get("image") or request.FILES.get("file")
        if upload is None:
            return _failure("No file uploaded.")
        return _store_validated_upload(
            upload, request=request, original_name=upload.name
        )


class EditorJSImageUploadByUrlView(APIView):
    """``@editorjs/image`` ``byUrl`` endpoint — downloads and rehosts."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        url = (request.data or {}).get("url")
        if not isinstance(url, str) or not url.strip():
            return _failure("Missing 'url'.")

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_REMOTE_SCHEMES:
            return _failure(f"URL scheme '{parsed.scheme}' is not allowed.")

        # Block self-rehost loops: same-origin URLs would just copy our own
        # files. Authors can paste them but the right path is the picker.
        if _is_same_origin(parsed, request):
            return _failure("Same-origin URLs are not accepted; use the picker.")

        try:
            content, original_name = _download_with_caps(url)
        except _DownloadError as exc:
            return _failure(str(exc))

        # Wrap downloaded bytes as an UploadedFile so the validator sees a
        # familiar interface.
        wrapped = SimpleUploadedFile(
            name=original_name or "remote-image",
            content=content,
            content_type="application/octet-stream",
        )
        return _store_validated_upload(
            wrapped, request=request, original_name=original_name
        )


class EditorJSImageLibraryView(APIView):
    """List previously uploaded EditorJS images for the admin picker."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        items = list(_list_existing_uploads(request, limit=100))
        return Response({"results": items})


def _list_existing_uploads(request, *, limit: int) -> Iterable[dict]:
    seen = 0
    for absolute_path in _walk_storage(_UPLOAD_SUBDIR):
        if seen >= limit:
            break
        rel = absolute_path
        try:
            with default_storage.open(rel, "rb") as fh:
                head = fh.read(1024 * 32)
        except FileNotFoundError:
            continue
        try:
            with Image.open(io.BytesIO(head)) as img:
                fmt = (img.format or "").upper()
                width, height = img.size
        except Exception:
            continue
        mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt)
        if mime is None:
            continue
        yield {
            "url": _absolute_url(request, rel),
            "mime": mime,
            "width": int(width),
            "height": int(height),
            "name": rel.rsplit("/", 1)[-1],
        }
        seen += 1


def _walk_storage(prefix: str):
    """Recursively yield file paths under ``prefix`` from default_storage."""
    try:
        dirs, files = default_storage.listdir(prefix)
    except FileNotFoundError:
        return
    for name in files:
        yield f"{prefix}/{name}"
    for sub in dirs:
        yield from _walk_storage(f"{prefix}/{sub}")


def _is_same_origin(parsed_url, request) -> bool:
    request_host = request.get_host().split(":")[0]
    return (parsed_url.hostname or "").lower() == request_host.lower()


class _DownloadError(Exception):
    pass


def _download_with_caps(url: str) -> Tuple[bytes, str]:
    """Stream-download with size, timeout, and redirect caps."""
    try:
        resp = requests.get(
            url,
            stream=True,
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise _DownloadError(f"Download failed: {exc}") from exc

    if len(resp.history) > _MAX_REDIRECTS:
        raise _DownloadError("Too many redirects on remote URL.")
    final_scheme = urlparse(resp.url).scheme
    if final_scheme not in _ALLOWED_REMOTE_SCHEMES:
        raise _DownloadError(f"Redirect target uses disallowed scheme '{final_scheme}'.")
    if resp.status_code != 200:
        raise _DownloadError(f"Remote URL returned status {resp.status_code}.")

    buf = io.BytesIO()
    received = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > MAX_FILE_SIZE_BYTES:
            raise _DownloadError(
                f"Remote file exceeds {MAX_FILE_SIZE_BYTES} bytes."
            )
        buf.write(chunk)

    name_guess = urlparse(url).path.rsplit("/", 1)[-1] or "remote-image"
    return buf.getvalue(), name_guess
