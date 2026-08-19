"""Baseline & regression tests for the authorization/media-security tickets
(2026-08-17-authorization-media-security-tickets.md, ticket 01).

Three things live here, deliberately kept separate from behavior-changing
tests elsewhere:

1. S2 characterization -- records *today's* (not-yet-fixed) EditorJS upload
   alias behavior so ticket 13 has something concrete to prove it changed.
2. A golden snapshot of the DRF permission classes currently wired to the
   Campaign/Event/GeoStory/Workspace viewsets, so later tickets in the ARCH
   track can diff their intended change against a recorded starting point.
3. A CI guard asserting the confirmed-dead legacy permission classes
   (``IsSuperAdmin``/``IsAdmin``/``IsEditor``/``IsViewer``) stay uncalled.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from PIL import Image
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.campaigns.views import CampaignViewSet
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.events.views import EventSeriesViewSet, EventViewSet
from tosca_api.apps.geodata_providers.api.views import WorkspaceViewSet
from tosca_api.apps.geostories.views import GeoStoryViewSet
from tosca_api.apps.organizations.models import Organization

User = get_user_model()


# ---------------------------------------------------------------------------
# A1/A2/A3 -- pin the enum/label facts the rest of the tickets rely on.
# ---------------------------------------------------------------------------


def test_feedback_app_label_is_feedback():
    """A1: the feedback app's Django label (used by TOSCA_PERMISSION_MODELS)."""
    from django.apps import apps

    assert apps.get_app_config("feedback").label == "feedback"


def test_geostory_status_enum_and_published_semantics():
    """A2: GeoStory.status choices + .published() filters to PUBLISHED only."""
    from tosca_api.apps.geostories.models import GeoStory

    assert {c[0] for c in GeoStory.Status.choices} == {"draft", "published", "archived"}
    assert (
        GeoStory.objects.published().query.where.children[0].rhs
        == GeoStory.Status.PUBLISHED
    )


def test_campaign_visibility_value_set():
    """A3: Campaign.visibility choices."""
    assert {c[0] for c in Campaign.Visibility.choices} == {"public", "private"}


# ---------------------------------------------------------------------------
# S2 characterization -- current (broken) EditorJS upload alias behavior.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def uploader(db):
    return User.objects.create_user(username="uploader")


@pytest.fixture
def private_campaign(db, uploader):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return Campaign.objects.create(
        title="Private Campaign",
        organization=org,
        visibility=Campaign.Visibility.PRIVATE,
        created_by=uploader,
    )


def _png_upload():
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(1, 2, 3)).save(buf, format="PNG")
    buf.seek(0)
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("inline.png", buf.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_s2_editorjs_upload_lands_private_by_default(api_client, uploader, private_campaign):
    """S2 fix (security tickets ticket 13).

    An EditorJS upload has **no** owning-Campaign/GeoStory context at upload
    time -- the image isn't embedded in any saved ``GeoContext.content``
    until the author saves the story/event, so nothing is resolvable yet.
    ``geocontext/views.py::_store_validated_upload`` therefore always writes
    through the private (``default``) alias regardless of any campaign's
    eventual visibility; ``core.media_lifecycle`` promotes it to the public
    alias later, once it's actually linked to a public+published entity
    (ticket 14). Before this fix, every upload landed in ``media_public``
    unconditionally -- the confirmed S2 root cause (a private/draft story's
    inline image was reachable at a stable unsigned URL). ``private_campaign``
    here stands in for "any campaign, or none at all" -- the point is that
    the upload alias no longer depends on (or leaks through) campaign state
    it doesn't yet know about.
    """
    api_client.force_authenticate(user=uploader)
    response = api_client.post(
        "/api/v1/geocontext/editorjs/upload-by-file/",
        {"image": _png_upload()},
        format="multipart",
    )
    assert response.status_code == 200, response.data

    asset = MediaAsset.objects.latest("created_at")
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
    assert response.data["file"]["url"].startswith("http")


# ---------------------------------------------------------------------------
# Golden snapshot -- current permission-class wiring per resource.
# ---------------------------------------------------------------------------


def _class_names(classes):
    return sorted(c.__name__ for c in classes)


def test_golden_snapshot_permission_classes_per_resource():
    """Records the DRF permission classes wired to each viewset today.

    A change here is not necessarily wrong -- tickets 08-11 are expected to
    change this deliberately -- but it should be a *reviewed* diff, not a
    silent side effect of unrelated work.
    """
    assert _class_names(CampaignViewSet.permission_classes) == [
        "IsAuthenticated",
        "OrgScopedPermission",
        "ViewGatedModelPermissions",
    ]
    assert _class_names(GeoStoryViewSet.permission_classes) == [
        "CampaignScopedPermission",
        "DjangoModelPermissionsOrAnonReadOnly",
    ]
    assert _class_names(EventViewSet.permission_classes) == [
        "CampaignScopedPermission",
        "DjangoModelPermissionsOrAnonReadOnly",
    ]
    # EventSeries *is* a TOSCA_PERMISSION_MODELS entry (registry drift fix,
    # security tickets ticket 04) -- the admin already required WRITER+, but
    # this viewset previously used IsAuthenticatedOrReadOnly, which let any
    # authenticated user (READER included) write a series. Now uses the same
    # DjangoModelPermissionsOrAnonReadOnly -> has_perm() capability gate as
    # EventViewSet/GeoStoryViewSet.
    assert _class_names(EventSeriesViewSet.permission_classes) == [
        "CampaignScopedPermission",
        "DjangoModelPermissionsOrAnonReadOnly",
    ]
    assert _class_names(WorkspaceViewSet.permission_classes) == [
        "IsAuthenticated",
        "OrgScopedPermission",
        "ViewGatedModelPermissions",
    ]


# ---------------------------------------------------------------------------
# CI guard -- confirmed-dead legacy permission classes stay deleted.
# ---------------------------------------------------------------------------

_DEAD_CLASSES = ("IsSuperAdmin", "IsAdmin", "IsEditor", "IsViewer")
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_dead_permission_classes_stay_deleted():
    """Re-asserts (security tickets ticket 03) that
    ``tosca_api/apps/authentication/permissions.py`` -- which defined the
    zero-call-site ``IsSuperAdmin``/``IsAdmin``/``IsEditor``/``IsViewer``
    classes ticket 01 first characterized as dead -- was deleted, and that
    none of those names have resurfaced anywhere else in the codebase.
    """
    perms_file = _REPO_ROOT / "tosca_api/apps/authentication/permissions.py"
    assert not perms_file.exists()
    this_file = Path(__file__).resolve()

    offenders = []
    for path in _REPO_ROOT.rglob("*.py"):
        rel = "/" + str(path.relative_to(_REPO_ROOT))
        if path == this_file:
            continue
        if any(part in rel for part in ("/.venv/", "/venv/", "/build/", "/.git/")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in _DEAD_CLASSES:
            if re.search(rf"\b{name}\b", text):
                offenders.append((str(path.relative_to(_REPO_ROOT)), name))

    assert offenders == [], (
        "Dead permission class name resurfaced outside this guard -- either "
        "it's actually wired up now (update this guard) or the reference "
        f"should be removed: {offenders}"
    )
