"""Warning-only Garage reference check (P0 snapshot/restore ticket 05, spec §6.2).

Walks the DB's media references -- ``MediaAsset.storage_path`` and
``GeoStory.hero_image`` -- and HEADs each one (via ``storage.exists()``,
mirroring the ``storages[alias]`` pattern ``scripts/list_media_buckets.py``
already uses) against the storage alias the DB row says currently holds it.
Reports how many resolved and how many are missing.

This is a heads-up only: full Garage backup/versioning is explicitly out of
P0 scope, so a missing reference is reported, never treated as a failure --
callers must not let it affect the restore's exit status.

Derivative images (``tosca_api.apps.core.image_derivatives``) are a lazily
generated, content-addressed cache keyed off an original's path. Nothing in
the DB references a specific derivative key, and a missing one is
regenerated transparently on next request -- so they are not part of this
check; only DB-referenced originals are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class GarageReference:
    label: str
    alias: str
    path: str


@dataclass
class GarageReferenceCheckResult:
    checked: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return len(self.missing)


def iter_db_media_references() -> Iterable[GarageReference]:
    from tosca_api.apps.core.models import MediaAsset
    from tosca_api.apps.geostories.models import GeoStory

    for asset_id, storage_path, storage_alias in MediaAsset.objects.values_list(
        "id", "storage_path", "storage_alias"
    ):
        if storage_path:
            yield GarageReference(
                label=f"MediaAsset:{asset_id}", alias=storage_alias, path=storage_path
            )

    for story_id, hero_name, hero_alias in GeoStory.objects.exclude(
        hero_image=""
    ).values_list("id", "hero_image", "hero_image_storage_alias"):
        if hero_name:
            yield GarageReference(
                label=f"GeoStory.hero_image:{story_id}", alias=hero_alias, path=hero_name
            )


def run_reference_check(
    references: Iterable[GarageReference] | None = None,
    *,
    storage_for_alias=None,
) -> GarageReferenceCheckResult:
    """HEAD every reference; never raises -- a lookup error counts as missing."""
    if storage_for_alias is None:
        from django.core.files.storage import storages

        storage_for_alias = lambda alias: storages[alias]  # noqa: E731

    result = GarageReferenceCheckResult()
    refs = list(references) if references is not None else list(iter_db_media_references())
    for ref in refs:
        result.checked += 1
        try:
            exists = storage_for_alias(ref.alias).exists(ref.path)
        except Exception:
            exists = False
        if not exists:
            result.missing.append(f"{ref.alias}:{ref.path}")
    return result
