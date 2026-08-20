"""Exercise Django's configured S3 storage against the local Garage service."""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages


PATH = "garage-e2e/byte-match.txt"
PUBLIC_PATH = "garage-e2e/public-byte-match.txt"
ARCHIVE_PATH = "garage-e2e/archive-byte-match.txt"
PAYLOAD = b"Epic 11 Garage persistence check\n"


def _unsigned_object_url(url: str) -> str:
    """Strip any query-string signature so the request is truly anonymous."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _http_get(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _assert_http_get(
    label: str, url: str, *, expect_status: int, expect_body: bytes | None = None
) -> None:
    status, body = _http_get(url)
    if status != expect_status:
        raise AssertionError(
            f"{label}: expected HTTP {expect_status}, got {status} for {url}"
        )
    if expect_body is not None and body != expect_body:
        raise AssertionError(f"{label}: response body did not match expected payload")
    print(f"HTTP_OK {label} status={status}")


def _assert_signed(label: str, url: str) -> None:
    if "X-Amz-Signature" not in url:
        raise AssertionError(f"{label}: expected a presigned URL, got {url}")
    if "X-Amz-Expires=3600" not in url:
        raise AssertionError(
            f"{label}: expected the pinned 3600s TTL (X-Amz-Expires=3600), got {url}"
        )


def run_http_checks() -> None:
    """SS9 rows 10-12: real HTTP GETs against the live Garage container -- not
    storage.open(). Every alias (default/media_public/media_archive) rejects
    unsigned GETs and accepts Django's presigned URL: no bucket is
    anonymously readable, "public" media is only reachable through a
    presigned URL Django issues after checking publication state.
    """
    public_storage = storages["media_public"]
    archive_storage = storages["media_archive"]

    for label, storage, path in (
        ("public", public_storage, PUBLIC_PATH),
        ("private", default_storage, PATH),
        ("archive", archive_storage, ARCHIVE_PATH),
    ):
        signed_url = storage.url(path)
        _assert_signed(f"{label} asset", signed_url)
        _assert_http_get(
            f"{label} asset signed GET",
            signed_url,
            expect_status=200,
            expect_body=PAYLOAD,
        )
        unsigned_url = _unsigned_object_url(signed_url)
        status, _ = _http_get(unsigned_url)
        if status not in (403, 404):
            raise AssertionError(
                f"{label} asset unsigned GET expected 403/404, got {status}"
            )
        print(f"HTTP_OK {label} asset unsigned GET rejected status={status}")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"write", "read", "http"}:
        print("usage: garage_e2e.py write|read|http", file=sys.stderr)
        return 2

    if sys.argv[1] == "http":
        run_http_checks()
        print("GARAGE_HTTP_OK")
        return 0

    if sys.argv[1] == "write":
        public_storage = storages["media_public"]
        archive_storage = storages["media_archive"]
        for storage, path in (
            (default_storage, PATH),
            (public_storage, PUBLIC_PATH),
            (archive_storage, ARCHIVE_PATH),
        ):
            if storage.exists(path):
                storage.delete(path)
            saved = storage.save(path, ContentFile(PAYLOAD))
            if saved != path:
                raise AssertionError(f"unexpected saved path: {saved}")
        print(f"GARAGE_WRITE_OK sha256={hashlib.sha256(PAYLOAD).hexdigest()}")
        return 0

    public_storage = storages["media_public"]
    archive_storage = storages["media_archive"]
    for storage, path in (
        (default_storage, PATH),
        (public_storage, PUBLIC_PATH),
        (archive_storage, ARCHIVE_PATH),
    ):
        with storage.open(path, "rb") as stored:
            actual = stored.read()
        if actual != PAYLOAD:
            raise AssertionError(f"Garage readback bytes differ for {path}")
    print(f"GARAGE_READ_OK sha256={hashlib.sha256(PAYLOAD).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
