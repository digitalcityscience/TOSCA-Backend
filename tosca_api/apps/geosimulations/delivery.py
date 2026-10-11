"""Browser URLs for stored simulation files.

Callers must check that the reader may see the run *before* asking for a URL.
Under S3, ``storage.url()`` returns a presigned URL on the browser-facing
endpoint; it expires after the storage's ``querystring_expire`` seconds, which
is reported so clients know when to fetch a fresh one. With filesystem storage
the URL is a stable media path made absolute for the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.files.storage import storages
from django.utils import timezone


@dataclass(frozen=True)
class FileUrl:
    url: str
    expires_at: datetime | None


def file_url(storage_alias: str, storage_path: str, request=None, *, now: datetime | None = None) -> FileUrl:
    storage = storages[storage_alias]
    url = storage.url(storage_path)
    if request is not None:
        url = request.build_absolute_uri(url)
    expires_at = None
    if getattr(storage, "querystring_auth", False):
        seconds = int(getattr(storage, "querystring_expire", 3600))
        expires_at = (now or timezone.now()) + timedelta(seconds=seconds)
    return FileUrl(url=url, expires_at=expires_at)


def asset_url(asset, request=None, *, now: datetime | None = None) -> FileUrl:
    return file_url(asset.storage_alias, asset.storage_path, request, now=now)
