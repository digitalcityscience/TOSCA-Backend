"""Object storage operations for simulation files.

Django's ``Storage.save()`` cannot set per-object headers, and a download-and-
re-upload move would drop them, so S3 aliases use boto3 directly:

- uploads set ``Content-Type``, ``Content-Encoding`` and ``Cache-Control``
  (Garage ignores ``response-cache-control`` overrides on presigned URLs, so
  headers must be stored with the object);
- moves between buckets are server-side copies that keep those headers.

Filesystem aliases (local development without S3) fall back to plain
``Storage`` calls.
"""

from __future__ import annotations

from pathlib import Path

from django.core.files import File
from django.core.files.storage import storages

# Presigned URLs live one hour; a browser may reuse a file for that long.
CACHE_CONTROL = "private, max-age=3600"


def _s3(storage) -> bool:
    return hasattr(storage, "bucket_name") and hasattr(storage, "connection")


def _key(storage, name: str) -> str:
    # Applies the storage's configured location prefix, exactly as its own reads do.
    return storage._normalize_name(name)


def upload_file(
    alias: str,
    name: str,
    path: Path,
    *,
    content_type: str,
    content_encoding: str | None = None,
) -> str:
    """Store ``path`` under ``name``; returns the stored name (names are unique per run)."""
    storage = storages[alias]
    if not _s3(storage):
        with path.open("rb") as handle:
            return storage.save(name, File(handle, name=Path(name).name))
    extra = {"ContentType": content_type, "CacheControl": CACHE_CONTROL}
    if content_encoding:
        extra["ContentEncoding"] = content_encoding
    storage.connection.meta.client.upload_file(
        str(path), storage.bucket_name, _key(storage, name), ExtraArgs=extra
    )
    return name


def same_place(alias_a: str, alias_b: str) -> bool:
    """Whether two aliases store objects in the same place (local dev: one MEDIA_ROOT)."""
    first, second = storages[alias_a], storages[alias_b]
    if _s3(first) and _s3(second):
        return (first.bucket_name, first.location) == (second.bucket_name, second.location)
    if _s3(first) or _s3(second):
        return False
    return Path(first.location).resolve() == Path(second.location).resolve()


def copy_between(source_alias: str, target_alias: str, name: str) -> int:
    """Copy one object to another alias and return the copy's size."""
    source, target = storages[source_alias], storages[target_alias]
    if _s3(source) and _s3(target):
        client = target.connection.meta.client
        client.copy_object(
            Bucket=target.bucket_name,
            Key=_key(target, name),
            CopySource={"Bucket": source.bucket_name, "Key": _key(source, name)},
            MetadataDirective="COPY",
        )
    else:
        with source.open(name, "rb") as handle:
            target.save(name, File(handle, name=Path(name).name))
    return target.size(name)


def delete(alias: str, name: str) -> None:
    storages[alias].delete(name)
