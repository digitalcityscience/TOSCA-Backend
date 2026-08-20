"""List configured Django media storage contents."""

from __future__ import annotations

import os
from pathlib import Path

import django
from django.core.files.storage import FileSystemStorage, storages


ALIASES = (
    ("private", "default"),
    ("public", "media_public"),
    ("archive", "media_archive"),
)
STATIC_ALIASES = (("static", "staticfiles"),)


def _list_s3_storage(storage) -> list[tuple[str, int | None, str]]:
    client = storage.connection.meta.client
    bucket_name = storage.bucket_name
    prefix = (getattr(storage, "location", "") or "").strip("/")
    kwargs = {"Bucket": bucket_name}
    if prefix:
        kwargs["Prefix"] = f"{prefix}/"

    rows: list[tuple[str, int | None, str]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rows.append((key, obj.get("Size"), obj.get("LastModified", "")))
    return rows


def _list_filesystem_storage(storage: FileSystemStorage) -> list[tuple[str, int | None, str]]:
    root = Path(storage.location)
    if not root.exists():
        return []

    rows: list[tuple[str, int | None, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            rows.append((str(path.relative_to(root)), stat.st_size, ""))
    return rows


def _list_storage(alias: str) -> list[tuple[str, int | None, str]]:
    storage = storages[alias]
    if hasattr(storage, "bucket_name") and hasattr(storage, "connection"):
        return _list_s3_storage(storage)
    if isinstance(storage, FileSystemStorage):
        return _list_filesystem_storage(storage)
    raise RuntimeError(f"Unsupported storage backend for {alias}: {storage.__class__}")


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tosca_api.settings.development")
    django.setup()

    aliases = STATIC_ALIASES if os.environ.get("LIST_STATIC") else ALIASES
    for label, alias in aliases:
        print(f"\n[{label}] alias={alias}")
        try:
            storage = storages[alias]
        except Exception as exc:
            print(f"  unavailable: {exc}")
            continue

        bucket_name = getattr(storage, "bucket_name", None)
        if bucket_name:
            print(f"  bucket={bucket_name}")

        rows = _list_storage(alias)
        if not rows:
            print("  empty")
            continue

        for key, size, modified in rows:
            size_text = "-" if size is None else f"{size} B"
            modified_text = f"  {modified}" if modified else ""
            print(f"  {size_text:>12}  {key}{modified_text}")


if __name__ == "__main__":
    main()
