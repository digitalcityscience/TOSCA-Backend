"""Tests for the v1 story layers → Overview scene data migration (0012)."""

import importlib

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geostories.models import (
    GeoStory,
    GeoStoryLayer,
    GeoStoryScene,
    GeoStorySceneLayer,
)
from tosca_api.apps.geostories.tests.scene_helpers import make_vector_layer

migration = importlib.import_module(
    "tosca_api.apps.geostories.migrations.0012_scenes_replace_story_layers"
)

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="migration-user", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Campaign", created_by=user)


def _story(campaign, user, title="Story"):
    return GeoStory.objects.create(title=title, campaign=campaign, author=user)


@pytest.mark.django_db
def test_story_layers_become_one_overview_scene(user, campaign):
    story = _story(campaign, user)
    roads = make_vector_layer("ws:roads", user=user)
    parks = make_vector_layer("ws:parks", user=user)
    parks_style = parks.style_assignments.get()
    GeoStoryLayer.objects.create(geostory=story, layer=roads, display_order=5)
    GeoStoryLayer.objects.create(
        geostory=story, layer=parks, style_assignment=parks_style, display_order=2
    )

    migration.copy_story_layers_to_overview_scene(apps, None)

    scene = GeoStoryScene.objects.get(geostory=story)
    assert scene.title == "Overview"
    assert scene.order == 0
    assert scene.center_lng is None and scene.zoom is None
    rows = list(GeoStorySceneLayer.objects.filter(scene=scene).order_by("display_order"))
    assert [row.layer_id for row in rows] == [parks.id, roads.id]
    assert [row.display_order for row in rows] == [0, 1]
    assert rows[0].style_assignment_id == parks_style.id
    assert rows[1].style_assignment == roads.style_assignments.get()


@pytest.mark.django_db
def test_unstyled_legacy_layer_is_copied_as_is(user, campaign):
    story = _story(campaign, user)
    unstyled = make_layer("ws:unstyled", user=user)
    GeoStoryLayer.objects.create(geostory=story, layer=unstyled)

    migration.copy_story_layers_to_overview_scene(apps, None)

    row = GeoStorySceneLayer.objects.get(scene__geostory=story)
    assert row.layer_id == unstyled.id
    assert row.style_assignment_id is None


@pytest.mark.django_db
def test_story_without_layers_gets_no_scene(user, campaign):
    _story(campaign, user)

    migration.copy_story_layers_to_overview_scene(apps, None)

    assert not GeoStoryScene.objects.exists()


@pytest.mark.django_db
def test_story_that_already_has_scenes_is_skipped(user, campaign):
    story = _story(campaign, user)
    GeoStoryLayer.objects.create(geostory=story, layer=make_vector_layer("ws:a", user=user))
    GeoStoryScene.objects.create(geostory=story, title="Authored")

    migration.copy_story_layers_to_overview_scene(apps, None)

    assert list(story.scenes.values_list("title", flat=True)) == ["Authored"]


@pytest.mark.django_db
def test_reverse_clears_scenes_and_keeps_legacy_rows(user, campaign):
    story = _story(campaign, user)
    GeoStoryLayer.objects.create(geostory=story, layer=make_vector_layer("ws:b", user=user))
    migration.copy_story_layers_to_overview_scene(apps, None)

    migration.remove_scenes(apps, None)

    assert not GeoStoryScene.objects.exists()
    assert GeoStoryLayer.objects.filter(geostory=story).count() == 1
