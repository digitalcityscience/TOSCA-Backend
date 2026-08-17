"""Exercise Django's configured S3 storage against the local Garage service."""

from __future__ import annotations

import hashlib
import sys

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages


PATH = "garage-e2e/byte-match.txt"
PUBLIC_PATH = "garage-e2e/public-byte-match.txt"
ARCHIVE_PATH = "garage-e2e/archive-byte-match.txt"
PAYLOAD = b"Epic 11 Garage persistence check\n"


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"write", "read"}:
        print("usage: garage_e2e.py write|read", file=sys.stderr)
        return 2

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
