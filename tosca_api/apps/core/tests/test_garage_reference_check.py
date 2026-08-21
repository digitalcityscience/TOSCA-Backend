"""Tests for the warning-only Garage reference check (P0 ticket 05, spec §6.2).

Covers ``core.garage_reference_check``: DB references are enumerated from
MediaAsset + GeoStory.hero_image, a missing object is reported (never
raised), and the check never blocks -- ``run_reference_check`` always
returns a result, it never raises for a missing key.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.garage_reference_check import (
    GarageReference,
    iter_db_media_references,
    run_reference_check,
)
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


class _FakeStorage:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    def exists(self, path: str) -> bool:
        return path in self._present


def _storage_for_alias(present_by_alias: dict[str, set[str]]):
    def resolver(alias: str) -> _FakeStorage:
        return _FakeStorage(present_by_alias.get(alias, set()))

    return resolver


def _make_asset(path: str, **overrides) -> MediaAsset:
    defaults = dict(
        storage_path=path,
        original_name=path.rsplit("/", 1)[-1],
        mime="image/png",
        width=10,
        height=10,
        size=100,
    )
    defaults.update(overrides)
    return MediaAsset.objects.create(**defaults)


@pytest.fixture
def org(db):
    return Organization.objects.create(slug="acme", name="Acme")


@pytest.fixture
def campaign(org, django_user_model):
    user = django_user_model.objects.create_user(username="owner")
    return Campaign.objects.create(title="Camp", created_by=user, organization=org)


def test_run_reference_check_counts_and_reports_missing():
    refs = [
        GarageReference(label="a", alias="default", path="orgs/1/a.png"),
        GarageReference(label="b", alias="default", path="orgs/1/missing.png"),
        GarageReference(label="c", alias="media_public", path="pub/c.png"),
    ]
    resolver = _storage_for_alias(
        {"default": {"orgs/1/a.png"}, "media_public": {"pub/c.png"}}
    )

    result = run_reference_check(refs, storage_for_alias=resolver)

    assert result.checked == 3
    assert result.missing == ["default:orgs/1/missing.png"]
    assert result.missing_count == 1


def test_run_reference_check_treats_lookup_error_as_missing():
    refs = [GarageReference(label="a", alias="default", path="x.png")]

    def resolver(alias: str):
        raise RuntimeError("Garage unreachable")

    result = run_reference_check(refs, storage_for_alias=resolver)

    assert result.checked == 1
    assert result.missing == ["default:x.png"]


def test_run_reference_check_never_raises_for_missing_objects():
    refs = [GarageReference(label="a", alias="default", path="gone.png")]
    resolver = _storage_for_alias({})

    result = run_reference_check(refs, storage_for_alias=resolver)

    assert result.missing_count == 1


def test_iter_db_media_references_includes_media_asset_and_hero_image(
    campaign, django_user_model
):
    _make_asset("orgs/1/campaigns/1/misc/a.png", storage_alias="default")
    author = django_user_model.objects.create_user(username="author")
    story = GeoStory(
        title="Story",
        campaign=campaign,
        author=author,
        hero_image_alt="alt text",
    )
    story.hero_image.name = "geostories/x/hero/img.png"
    story.save()

    refs = list(iter_db_media_references())

    by_path = {ref.path: ref for ref in refs}
    assert by_path["orgs/1/campaigns/1/misc/a.png"].alias == "default"
    assert "geostories/x/hero/img.png" in by_path
    assert by_path["geostories/x/hero/img.png"].alias == story.hero_image_storage_alias


def test_iter_db_media_references_skips_stories_without_hero_image(
    campaign, django_user_model
):
    author = django_user_model.objects.create_user(username="author2")
    GeoStory.objects.create(title="NoHero", campaign=campaign, author=author)

    refs = list(iter_db_media_references())

    assert refs == []


def test_command_prints_checked_and_missing_line():
    _make_asset("orgs/1/campaigns/1/misc/a.png", storage_alias="default")

    out = io.StringIO()
    call_command("check_garage_references", stdout=out)

    output = out.getvalue()
    assert "1 referans kontrol edildi, 1 eksik" in output
    assert "default:orgs/1/campaigns/1/misc/a.png" in output


def test_command_reports_zero_missing_when_nothing_to_check():
    out = io.StringIO()
    call_command("check_garage_references", stdout=out)

    assert "0 referans kontrol edildi, 0 eksik" in out.getvalue()
