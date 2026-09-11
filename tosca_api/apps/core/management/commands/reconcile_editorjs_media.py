from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Iterator

from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from PIL import Image
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from tosca_api.apps.core.models import MediaAsset


EDITORJS_ROOT = "geocontext/editorjs"
MEDIA_ALIASES = (
    MediaAsset.StorageAlias.DEFAULT,
    MediaAsset.StorageAlias.PUBLIC,
    MediaAsset.StorageAlias.ARCHIVE,
)


def _iter_storage_keys(storage, directory: str) -> Iterator[str]:
    """List storage-relative object keys below ``directory``.

    ``Storage.listdir`` walks one directory level per call, which for S3
    means one ListObjects round trip per upload (each upload lives in its
    own ``<root>/<uuid>/`` prefix). S3Storage exposes the underlying bucket,
    so fetch a single flat, prefix-filtered listing there instead; other
    storages fall back to the recursive directory walk.
    """
    if isinstance(storage, S3Storage):
        # S3Storage's ``location`` (e.g. MEDIA_PRIVATE_PREFIX on the private
        # alias) is invisible to callers -- ``listdir``/``save`` transparently
        # join and strip it. Replicate that here: search under the real,
        # location-prefixed key, then report keys relative to ``directory``
        # so they match the storage_path format MediaAsset/listdir both use.
        normalized_prefix = storage._normalize_name(clean_name(directory))
        if normalized_prefix and not normalized_prefix.endswith("/"):
            normalized_prefix += "/"
        for obj in storage.bucket.objects.filter(Prefix=normalized_prefix):
            if obj.key == normalized_prefix or obj.key.endswith("/"):
                continue
            relative_key = obj.key[len(normalized_prefix):]
            yield f"{directory.rstrip('/')}/{relative_key}"
        return

    yield from _walk_storage_keys(storage, directory)


def _walk_storage_keys(storage, directory: str) -> Iterator[str]:
    try:
        directories, files = storage.listdir(directory)
    except FileNotFoundError:
        return

    for filename in sorted(files):
        yield f"{directory.rstrip('/')}/{filename}"
    for child in sorted(directories):
        yield from _walk_storage_keys(storage, f"{directory.rstrip('/')}/{child}")


def _image_metadata(storage, storage_path: str) -> tuple[str, int, int, int]:
    with storage.open(storage_path, "rb") as stored:
        with Image.open(stored) as image:
            image.load()
            width, height = image.size
            mime = Image.MIME.get(image.format or "")
    if not mime:
        mime = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
    return mime, width, height, storage.size(storage_path)


class Command(BaseCommand):
    help = (
        "Reconcile EditorJS objects in configured media storages with MediaAsset. "
        "The command is read-only unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create missing MediaAsset rows and repair unambiguous aliases.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when unresolved inconsistencies remain.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        configured_aliases = [
            alias for alias in MEDIA_ALIASES if alias in settings.STORAGES
        ]
        if not configured_aliases:
            raise CommandError("No configured media storage aliases were found.")

        inventory: dict[str, set[str]] = {}
        for alias in configured_aliases:
            try:
                inventory[alias] = set(
                    _iter_storage_keys(storages[alias], EDITORJS_ROOT)
                )
            except Exception as exc:
                raise CommandError(f"Could not list media alias {alias}: {exc}") from exc

        database_assets = {
            asset.storage_path: asset
            for asset in MediaAsset.objects.filter(
                storage_path__startswith=f"{EDITORJS_ROOT}/"
            )
        }
        object_keys = set().union(*inventory.values())
        created = 0
        aliases_updated = 0
        duplicate_objects = 0
        unreadable_objects = 0

        for storage_path in sorted(object_keys):
            present_aliases = [
                alias for alias, keys in inventory.items() if storage_path in keys
            ]
            if len(present_aliases) != 1:
                duplicate_objects += 1
                self.stderr.write(
                    f"duplicate_object path={storage_path} aliases={','.join(present_aliases)}"
                )
                continue

            actual_alias = present_aliases[0]
            asset = database_assets.get(storage_path)
            if asset is not None:
                if asset.storage_alias != actual_alias:
                    self.stdout.write(
                        f"alias_mismatch path={storage_path} "
                        f"database={asset.storage_alias} storage={actual_alias}"
                    )
                    if apply_changes:
                        asset.storage_alias = actual_alias
                        asset.save(update_fields=["storage_alias", "updated_at"])
                    aliases_updated += 1
                continue

            try:
                mime, width, height, size = _image_metadata(
                    storages[actual_alias], storage_path
                )
            except Exception as exc:
                unreadable_objects += 1
                self.stderr.write(f"unreadable_object path={storage_path} error={exc}")
                continue

            self.stdout.write(f"missing_metadata path={storage_path} alias={actual_alias}")
            if apply_changes:
                MediaAsset.objects.create(
                    storage_path=storage_path,
                    original_name=PurePosixPath(storage_path).name,
                    mime=mime,
                    width=width,
                    height=height,
                    size=size,
                    storage_alias=actual_alias,
                )
            created += 1

        missing_objects = sorted(set(database_assets) - object_keys)
        for storage_path in missing_objects:
            self.stderr.write(
                f"missing_object path={storage_path} "
                f"database_alias={database_assets[storage_path].storage_alias}"
            )

        action = "created" if apply_changes else "would_create"
        alias_action = "aliases_updated" if apply_changes else "aliases_would_update"
        self.stdout.write(
            self.style.SUCCESS(
                "editorjs_media_reconcile "
                f"objects={len(object_keys)} database_rows={len(database_assets)} "
                f"{action}={created} {alias_action}={aliases_updated} "
                f"missing_objects={len(missing_objects)} "
                f"duplicates={duplicate_objects} unreadable={unreadable_objects}"
            )
        )

        unresolved = duplicate_objects + unreadable_objects + len(missing_objects)
        if not apply_changes:
            unresolved += created + aliases_updated
        if options["strict"] and unresolved:
            raise CommandError(f"Found {unresolved} unresolved media inconsistencies.")
