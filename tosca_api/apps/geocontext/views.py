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
import ipaddress
import socket
import uuid
from typing import Iterable, Tuple
from urllib.parse import urlparse

import requests
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from tosca_api.apps.core.image_policy import (
    MAX_FILE_SIZE_BYTES,
    validate_inline_image,
)
from tosca_api.apps.core.models import MediaAsset


_UPLOAD_SUBDIR = "geocontext/editorjs"
_DOWNLOAD_TIMEOUT_SECONDS = 5
_MAX_REDIRECTS = 1
_ALLOWED_REMOTE_SCHEMES = {"http", "https"}
_BLOCKED_URL_MESSAGE = "Remote URL resolves to a private or internal address."


class EditorJSUploadThrottle(UserRateThrottle):
    """Rate-limit the (expensive) EditorJS upload endpoints per user."""

    scope = "editorjs_upload"


class EditorJSMediaThrottle(UserRateThrottle):
    """Rate-limit the EditorJS media picker listing per user."""

    scope = "editorjs_media"


def _storage_for_alias(alias: str):
    return storages[alias]

_UPLOAD_SUCCESS_SERIALIZER = inline_serializer(
    name="EditorJSImageUploadSuccess",
    fields={
        "success": serializers.IntegerField(),
        "file": inline_serializer(
            name="EditorJSImageFile",
            fields={
                "url": serializers.URLField(),
                "mime": serializers.CharField(),
                "width": serializers.IntegerField(),
                "height": serializers.IntegerField(),
            },
        ),
    },
)
_UPLOAD_FAILURE_SERIALIZER = inline_serializer(
    name="EditorJSImageUploadFailure",
    fields={
        "success": serializers.IntegerField(),
        "error": serializers.CharField(),
    },
)
_UPLOAD_BY_FILE_REQUEST_SERIALIZER = inline_serializer(
    name="EditorJSImageUploadByFileRequest",
    fields={"image": serializers.ImageField()},
)
_UPLOAD_BY_URL_REQUEST_SERIALIZER = inline_serializer(
    name="EditorJSImageUploadByUrlRequest",
    fields={"url": serializers.URLField()},
)
_MEDIA_LIBRARY_SERIALIZER = inline_serializer(
    name="EditorJSImageMediaLibrary",
    fields={
        "results": serializers.ListField(
            child=inline_serializer(
                name="EditorJSImageMediaItem",
                fields={
                    "url": serializers.URLField(),
                    "mime": serializers.CharField(),
                    "width": serializers.IntegerField(),
                    "height": serializers.IntegerField(),
                    "name": serializers.CharField(),
                },
            )
        )
    },
)


def _absolute_url(
    request, storage_path: str, *, alias: str = MediaAsset.StorageAlias.PUBLIC
) -> str:
    # Public alias produces an unsigned URL on S3; private aliases (default/
    # archive) produce a presigned one; build_absolute_uri leaves an
    # already-absolute URL intact either way.
    return request.build_absolute_uri(_storage_for_alias(alias).url(storage_path))


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
    """Validate, persist, and return the EditorJS success response.

    Security tickets S2: this upload has no owning Campaign/GeoStory yet --
    the image isn't embedded in any saved GeoContext content until the
    author saves the story/event, so ``media_paths.resolve_entity`` has
    nothing to resolve against at this point (it matches on hero_image /
    embedded content references, neither of which exist yet). Every upload
    therefore lands in the **private** (``default``) alias unconditionally;
    once it's linked, ``core.media_lifecycle`` promotes it to the public
    alias iff the owning campaign is public AND the owning entity is
    published (the S2 truth table, ticket 13/14). Previously this always
    used ``media_public`` regardless of the eventual owning campaign's
    visibility -- the confirmed S2 root cause.
    """
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
    alias = MediaAsset.StorageAlias.DEFAULT
    storage = _storage_for_alias(alias)
    storage_path = storage.save(relative_path, file_obj)
    uploader = request.user if getattr(request.user, "_meta", None) else None
    MediaAsset.objects.create(
        storage_path=storage_path,
        original_name=original_name or "",
        mime=mime,
        width=width,
        height=height,
        size=storage.size(storage_path),
        uploader=uploader,
        storage_alias=alias,
    )

    return Response(
        {
            "success": 1,
            "file": {
                "url": _absolute_url(request, storage_path, alias=alias),
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
    throttle_classes = [EditorJSUploadThrottle]

    @extend_schema(
        tags=["geocontext"],
        operation_id="geocontext_editorjs_upload_by_file",
        summary="Upload an EditorJS image file",
        request=_UPLOAD_BY_FILE_REQUEST_SERIALIZER,
        responses={
            200: OpenApiResponse(
                response=_UPLOAD_SUCCESS_SERIALIZER,
                examples=[
                    OpenApiExample(
                        "Uploaded",
                        value={
                            "success": 1,
                            "file": {
                                "url": "https://example.test/media/geocontext/editorjs/context/image.png",
                                "mime": "image/png",
                                "width": 800,
                                "height": 600,
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(response=_UPLOAD_FAILURE_SERIALIZER),
        },
    )
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
    throttle_classes = [EditorJSUploadThrottle]

    @extend_schema(
        tags=["geocontext"],
        operation_id="geocontext_editorjs_upload_by_url",
        summary="Upload an EditorJS image from a remote URL",
        request=_UPLOAD_BY_URL_REQUEST_SERIALIZER,
        responses={
            200: OpenApiResponse(response=_UPLOAD_SUCCESS_SERIALIZER),
            400: OpenApiResponse(response=_UPLOAD_FAILURE_SERIALIZER),
        },
        examples=[
            OpenApiExample(
                "Remote upload request",
                value={"url": "https://remote.example.test/photos/inline.webp"},
                request_only=True,
            ),
            OpenApiExample(
                "Uploaded remote image",
                value={
                    "success": 1,
                    "file": {
                        "url": "https://example.test/media/geocontext/editorjs/context/inline.webp",
                        "mime": "image/webp",
                        "width": 960,
                        "height": 640,
                    },
                },
                response_only=True,
            ),
        ],
    )
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
    throttle_classes = [EditorJSMediaThrottle]

    @extend_schema(
        tags=["geocontext"],
        operation_id="geocontext_editorjs_media_library",
        summary="List uploaded EditorJS images",
        responses={200: _MEDIA_LIBRARY_SERIALIZER},
    )
    def get(self, request, *args, **kwargs):
        items = list(_list_existing_uploads(request, limit=100))
        return Response({"results": items})


def _list_existing_uploads(request, *, limit: int) -> Iterable[dict]:
    prefix = f"{_UPLOAD_SUBDIR}/"
    assets = MediaAsset.objects.filter(storage_path__startswith=prefix)[:limit]
    for asset in assets:
        yield {
            "url": _absolute_url(request, asset.storage_path, alias=asset.storage_alias),
            "mime": asset.mime,
            "width": asset.width,
            "height": asset.height,
            "name": asset.original_name or asset.storage_path.rsplit("/", 1)[-1],
        }


def _is_same_origin(parsed_url, request) -> bool:
    request_host = request.get_host().split(":")[0]
    return (parsed_url.hostname or "").lower() == request_host.lower()


class _DownloadError(Exception):
    pass


def _ip_is_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Unparseable address — treat as unsafe rather than let it through.
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_host_ips(host: str) -> list[str]:
    """Resolve a hostname to every address it maps to (IPv4 and IPv6)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _assert_public_url(url: str) -> None:
    """Reject URLs that target loopback, private, link-local, or reserved hosts.

    Blocks the cloud metadata endpoint (169.254.169.254) and SSRF into internal
    services. Direct IP literals are checked without DNS; hostnames are resolved
    and every returned address must be public. A hostname that cannot be
    resolved is left to fail at connection time — it cannot reach an internal
    target on its own.
    """
    host = urlparse(url).hostname
    if not host:
        raise _DownloadError("Remote URL has no host.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(str(literal)):
            raise _DownloadError(_BLOCKED_URL_MESSAGE)
        return
    try:
        resolved = _resolve_host_ips(host)
    except OSError:
        return
    if any(_ip_is_blocked(ip) for ip in resolved):
        raise _DownloadError(_BLOCKED_URL_MESSAGE)


def _download_with_caps(url: str) -> Tuple[bytes, str]:
    """Stream-download with SSRF, size, timeout, and redirect caps."""
    # Validate before connecting so a direct internal address is never dialled.
    _assert_public_url(url)
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
    # A redirect can point at an internal host even when the first hop was
    # public; re-validate the final URL the response actually came from.
    _assert_public_url(resp.url)
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
