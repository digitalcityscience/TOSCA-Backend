"""Tests for the GeoStory scene admin surfaces."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene, GeoStorySceneLayer
from tosca_api.apps.geostories.tests.scene_helpers import make_vector_layer
from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)

User = get_user_model()

ADD_URL = reverse("admin:geostories_geostoryscene_add")


def _org(slug):
    organization, _ = Organization.objects.get_or_create(slug=slug, defaults={"name": slug})
    OrganizationAppEntitlement.objects.get_or_create(
        organization=organization, app_label="geostories"
    )
    return organization


def _org_staff_client(username, org_slug, level):
    _org(org_slug)
    user = User.objects.create_user(username=username, password="x", is_staff=True)
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={org_slug: level}, default_org=org_slug, synced_at=timezone.now()
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def superuser():
    return User.objects.create_user(
        username="scene-admin", password="x", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(superuser):
    client = Client()
    client.force_login(superuser)
    return client


@pytest.fixture
def story(superuser):
    campaign = Campaign.objects.create(
        title="Campaign", created_by=superuser, organization=_org("dcs")
    )
    return GeoStory.objects.create(title="Story", campaign=campaign, author=superuser)


@pytest.fixture
def layer(superuser):
    return make_vector_layer("ws:admin_parks", user=superuser)


def _scene_post(story, *layer_rows):
    data = {
        "geostory": str(story.pk),
        "title": "Harbour",
        "caption": "",
        "order": "0",
        "center_lng": "9.99",
        "center_lat": "53.55",
        "zoom": "13",
        "bearing": "0",
        "pitch": "30",
        "bounds": "",
        "transition": "fly",
        "duration_ms": "1500",
        "scene_layers-TOTAL_FORMS": str(len(layer_rows)),
        "scene_layers-INITIAL_FORMS": "0",
        "scene_layers-MIN_NUM_FORMS": "0",
        "scene_layers-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(layer_rows):
        defaults = {
            "style_assignment": "",
            "render_layer_ids": "[]",
            "display_order": str(index),
            "opacity": "1",
            "feature_mode": "all",
            "feature_id_attribute": "",
            "feature_ids": "[]",
        }
        for key, value in {**defaults, **row}.items():
            data[f"scene_layers-{index}-{key}"] = value
    return data


@pytest.mark.django_db
def test_story_change_page_lists_scenes_and_add_link(client, story, layer):
    scene = GeoStoryScene.objects.create(geostory=story, title="Overview")
    GeoStorySceneLayer.objects.create(scene=scene, layer=layer)

    response = client.get(reverse("admin:geostories_geostory_change", args=[story.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert f"{ADD_URL}?geostory={story.pk}" in content
    assert "Overview" in content
    assert "Fit to layers" in content
    assert reverse("admin:geostories_geostoryscene_change", args=[scene.pk]) in content


@pytest.mark.django_db
def test_story_add_page_asks_to_save_before_scenes(client):
    response = client.get(reverse("admin:geostories_geostory_add"))

    assert response.status_code == 200
    assert "Save the story first to add map scenes." in response.content.decode()


@pytest.mark.django_db
def test_scene_add_page_prefills_story_and_tags_style_options(client, story, layer):
    response = client.get(f"{ADD_URL}?geostory={story.pk}")

    assert response.status_code == 200
    assert response.context["adminform"].form.initial["geostory"] == str(story.pk)
    assert f'data-layer-id="{layer.pk}"' in response.content.decode()


@pytest.mark.django_db
def test_scene_add_saves_layers_and_returns_to_story(client, story, layer):
    response = client.post(ADD_URL, _scene_post(story, {"layer": str(layer.pk)}))

    assert response.status_code == 302
    assert response.url == reverse("admin:geostories_geostory_change", args=[story.pk])
    scene = GeoStoryScene.objects.get(geostory=story)
    assert (scene.center_lng, scene.center_lat, scene.zoom, scene.pitch) == (9.99, 53.55, 13, 30)
    scene_layer = scene.scene_layers.get()
    assert scene_layer.layer == layer
    assert scene_layer.style_assignment == layer.style_assignments.get()


@pytest.mark.django_db
def test_second_scene_can_be_added_with_default_order(client, story, layer):
    GeoStoryScene.objects.create(geostory=story, title="First")

    page = client.get(f"{ADD_URL}?geostory={story.pk}")
    response = client.post(ADD_URL, _scene_post(story, {"layer": str(layer.pk)}))

    assert page.context["adminform"].form.initial["order"] == 1
    assert response.status_code == 302
    assert list(story.scenes.values_list("title", "order")) == [("First", 0), ("Harbour", 1)]


@pytest.mark.django_db
def test_scene_add_ignores_malformed_story_param(client):
    response = client.get(f"{ADD_URL}?geostory=not-a-uuid")

    assert response.status_code == 200


@pytest.mark.django_db
def test_scene_add_save_and_continue_stays_on_scene(client, story, layer):
    data = {**_scene_post(story, {"layer": str(layer.pk)}), "_continue": "1"}

    response = client.post(ADD_URL, data)

    scene = GeoStoryScene.objects.get(geostory=story)
    assert response.url == reverse("admin:geostories_geostoryscene_change", args=[scene.pk])


@pytest.mark.django_db
def test_scene_add_shows_model_errors_on_inline(client, story, superuser):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    private = make_layer("ws:admin_private", user=superuser, is_public=False)

    response = client.post(ADD_URL, _scene_post(story, {"layer": str(private.pk)}))

    assert response.status_code == 200
    assert not GeoStoryScene.objects.exists()
    assert "not public" in response.content.decode()


@pytest.mark.django_db
def test_scene_admin_hidden_from_index(client):
    response = client.get(reverse("admin:index"))

    assert ADD_URL not in response.content.decode()


@pytest.mark.django_db
def test_org_writer_can_add_scene_to_own_story(story, layer):
    client = _org_staff_client("dcs-writer", "dcs", "WRITER")

    response = client.post(ADD_URL, _scene_post(story, {"layer": str(layer.pk)}))

    assert response.status_code == 302
    assert GeoStoryScene.objects.filter(geostory=story).exists()


@pytest.mark.django_db
def test_other_org_cannot_open_scene(story):
    scene = GeoStoryScene.objects.create(geostory=story, title="Private")
    client = _org_staff_client("other-writer", "other", "WRITER")

    response = client.get(reverse("admin:geostories_geostoryscene_change", args=[scene.pk]))

    assert response.status_code == 302
    assert response.url == reverse("admin:index")


# --- Scene editor -------------------------------------------------------------

PREVIEW_URL = reverse("admin:geostories_geostoryscene_preview")


def _preview(client, *rows):
    import json

    return client.post(PREVIEW_URL, json.dumps({"layers": list(rows)}), content_type="application/json")


@pytest.mark.django_db
def test_scene_editor_page_ships_map_and_config(client, story):
    response = client.get(f"{ADD_URL}?geostory={story.pk}")

    content = response.content.decode()
    assert 'id="scene-editor-map"' in content
    assert 'type="module"' in content and "geostories/js/scene_editor.mjs" in content
    assert "geostories/vendor/maplibre-gl/maplibre-gl.css" in content
    assert 'id="scene-editor-config"' in content
    assert response.context["scene_editor_config"]["previewUrl"] == PREVIEW_URL
    assert response.context["scene_editor_config"]["basemap"]["version"] == 8


@pytest.mark.django_db
def test_scene_editor_basemap_is_configurable(client, story, settings):
    settings.GEOSTORY_SCENE_EDITOR_BASEMAP = "https://tiles.example.com/style.json"

    response = client.get(f"{ADD_URL}?geostory={story.pk}")

    assert response.context["scene_editor_config"]["basemap"] == "https://tiles.example.com/style.json"


@pytest.mark.django_db
def test_preview_renders_unsaved_rows_in_display_order(client, superuser):
    from tosca_api.apps.geodata_providers.models import Layer
    from tosca_api.apps.geostories.tests.scene_helpers import make_raster_layer

    parks = make_vector_layer("ws:preview_parks", user=superuser)
    ortho = make_raster_layer("ras:preview_ortho", user=superuser)
    Layer.objects.filter(pk=parks.pk).update(bounds=[9.9, 53.5, 10.1, 53.6])
    Layer.objects.filter(pk=ortho.pk).update(bounds=[9.7, 53.3, 10.3, 53.7])

    response = _preview(
        client,
        {"layer": str(parks.pk), "display_order": "1", "opacity": "0.5"},
        {"layer": "", "display_order": "2"},  # empty extra inline form
        {"layer": str(ortho.pk), "display_order": "0"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["errors"] == {}
    assert [layer["type"] for layer in data["render_layers"]] == ["raster", "fill", "line"]
    assert data["render_layers"][1]["paint"] == {"fill-opacity": 0.5}
    assert set(data["map"]["sources"]) == {
        layer["source"] for layer in data["render_layers"]
    }
    assert [entry["title"] for entry in data["legend"]] == ["preview_parks", "preview_ortho"]
    assert data["bounds"] == [9.7, 53.3, 10.3, 53.7]


@pytest.mark.django_db
def test_preview_reports_invalid_rows_by_index(client, superuser, layer):
    import uuid

    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    private = make_layer("ws:preview_private", user=superuser, is_public=False)

    data = _preview(
        client,
        {"layer": str(layer.pk), "opacity": "3"},
        {"layer": str(private.pk)},
        {"layer": str(uuid.uuid4())},
        {"layer": str(layer.pk), "feature_mode": "only"},
    ).json()

    assert set(data["errors"]) == {"0", "1", "2", "3"}
    assert "opacity" in data["errors"]["0"]
    assert "layer" in data["errors"]["1"]
    assert data["errors"]["2"] == {"layer": ["Unknown layer."]}
    assert "feature_id_attribute" in data["errors"]["3"]
    assert data["render_layers"] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "status"),
    [("not json", 400), ('{"layers": "x"}', 400), ('{"layers": [1]}', 400)],
)
def test_preview_rejects_malformed_payloads(client, body, status):
    response = client.post(PREVIEW_URL, body, content_type="application/json")

    assert response.status_code == status


@pytest.mark.django_db
def test_preview_requires_post(client):
    assert client.get(PREVIEW_URL).status_code == 405


@pytest.mark.django_db
def test_preview_requires_scene_permission():
    client = _org_staff_client("dcs-reader", "dcs", "READER")

    response = _preview(client)

    assert response.status_code == 403


@pytest.mark.django_db
def test_preview_requires_staff():
    client = Client()

    response = _preview(client)

    assert response.status_code == 302  # admin login redirect


# --- Feature picking -----------------------------------------------------------

ATTRIBUTES = [{"name": "objectid", "type": "Long"}, {"name": "name", "type": "String"}]


@pytest.mark.django_db
def test_attribute_dropdown_lists_layer_attributes(client, story, layer):
    from tosca_api.apps.geodata_providers.models import Layer

    Layer.objects.filter(pk=layer.pk).update(attributes=ATTRIBUTES)
    scene = GeoStoryScene.objects.create(geostory=story, title="Parks")
    GeoStorySceneLayer.objects.create(
        scene=scene,
        layer=layer,
        feature_mode="highlight",
        feature_id_attribute="name",
        feature_ids=["Stadtpark"],
    )
    # The saved attribute stays selectable after GeoServer stops reporting it.
    Layer.objects.filter(pk=layer.pk).update(attributes=ATTRIBUTES[:1])

    response = client.get(reverse("admin:geostories_geostoryscene_change", args=[scene.pk]))

    form = response.context["inline_admin_formsets"][0].formset.forms[0]
    choices = form.fields["feature_id_attribute"].widget.choices
    assert choices == [
        ("", "— choose an attribute —"),
        ("objectid", "objectid (Long)"),
        ("name", "name"),
    ]
    assert form["feature_id_attribute"].value() == "name"


@pytest.mark.django_db
def test_scene_saves_picked_features(client, story, layer):
    from tosca_api.apps.geodata_providers.models import Layer

    Layer.objects.filter(pk=layer.pk).update(attributes=ATTRIBUTES)
    row = {
        "layer": str(layer.pk),
        "feature_mode": "only",
        "feature_id_attribute": "objectid",
        "feature_ids": "[12, 40]",
    }

    response = client.post(ADD_URL, _scene_post(story, row))

    assert response.status_code == 302
    scene_layer = GeoStorySceneLayer.objects.get(scene__geostory=story)
    assert (scene_layer.feature_mode, scene_layer.feature_id_attribute, scene_layer.feature_ids) == (
        "only", "objectid", [12, 40],
    )


@pytest.mark.django_db
def test_rejected_new_row_keeps_submitted_attribute(client, story, layer):
    row = {"layer": str(layer.pk), "feature_mode": "only", "feature_id_attribute": "objectid"}

    response = client.post(ADD_URL, _scene_post(story, row))

    assert response.status_code == 200  # no feature ids picked yet
    form = response.context["inline_admin_formsets"][0].formset.forms[0]
    assert ("objectid", "objectid") in form.fields["feature_id_attribute"].widget.choices
    assert form["feature_id_attribute"].value() == "objectid"


@pytest.mark.django_db
def test_preview_reports_row_hints_for_picking(client, superuser):
    from tosca_api.apps.geodata_providers.models import Layer, Style
    from tosca_api.apps.geodata_providers.test_helpers import make_layer
    from tosca_api.apps.geostories.tests.scene_helpers import assign_style, make_raster_layer

    parks = make_vector_layer("ws:hint_parks", user=superuser)
    Layer.objects.filter(pk=parks.pk).update(attributes=ATTRIBUTES)
    roads = make_layer("ws:hint_roads", user=superuser)
    assign_style(roads, superuser, fmt=Style.StyleFormat.SLD, name="roads-sld")
    ortho = make_raster_layer("ras:hint_ortho", user=superuser)

    data = _preview(
        client,
        {
            "layer": str(parks.pk),
            "feature_mode": "highlight",
            "feature_id_attribute": "name",
            "feature_ids": ["A"],
        },
        {"layer": str(roads.pk)},
        {"layer": str(ortho.pk)},
        {"layer": str(parks.pk), "feature_mode": "only"},  # invalid: no attribute yet
    ).json()

    rows = data["rows"]
    assert rows["0"]["attributes"] == ATTRIBUTES
    assert [rows[i]["feature_selectable"] for i in "0123"] == [True, False, False, True]
    picked_layer_ids = rows["0"]["map_layer_ids"]
    assert [layer_id.rsplit("/", 1)[-1] for layer_id in picked_layer_ids] == ["parks-fill", "parks-line"]
    highlight_ids = {
        layer["id"] for layer in data["render_layers"]
        if layer["metadata"].get("tosca:role") == "highlight"
    }
    assert highlight_ids and not highlight_ids & set(picked_layer_ids)
    assert len(rows["1"]["map_layer_ids"]) == 1  # one WMS image layer
    assert "3" in data["errors"] and rows["3"]["map_layer_ids"] == []
