"""
Tests for GeoStory scene models.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geodata_providers.models import LayerStyleAssignment, Style
from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geostories.models import (
    MAX_SCENE_FEATURE_IDS,
    GeoStory,
    GeoStoryScene,
    GeoStorySceneLayer,
)
from tosca_api.apps.geostories.tests.scene_helpers import (
    assign_style,
    make_raster_layer,
    make_vector_layer,
)

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="scene-user", password="password")


@pytest.fixture
def story(user):
    campaign = Campaign.objects.create(title="Campaign", created_by=user)
    return GeoStory.objects.create(title="Story", campaign=campaign, author=user)


@pytest.fixture
def scene(story):
    return GeoStoryScene.objects.create(geostory=story, title="Scene")


@pytest.fixture
def vector_layer(user):
    return make_vector_layer("vec:parks", user=user, geometry_type="Polygon")


@pytest.fixture
def raster_layer(user):
    return make_raster_layer("ras:ortho", user=user)


# --- GeoStoryScene ---------------------------------------------------------


@pytest.mark.django_db
def test_scene_order_auto_increments(story):
    first = GeoStoryScene.objects.create(geostory=story, title="One")
    second = GeoStoryScene.objects.create(geostory=story, title="Two")

    assert (first.order, second.order) == (0, 1)
    assert list(story.scenes.all()) == [first, second]


@pytest.mark.django_db
def test_scene_saves_with_full_camera(story):
    scene = GeoStoryScene.objects.create(
        geostory=story,
        title="Harbour",
        center_lng=9.99,
        center_lat=53.55,
        zoom=13.4,
        bearing=-20,
        pitch=45,
        bounds=[9.9, 53.5, 10.1, 53.6],
    )

    assert scene.zoom == 13.4


@pytest.mark.django_db
def test_scene_without_camera_is_valid(story):
    scene = GeoStoryScene(geostory=story, title="Fit")
    scene.full_clean()


@pytest.mark.django_db
def test_scene_requires_center_and_zoom_together(story):
    scene = GeoStoryScene(geostory=story, title="Half", center_lng=10, center_lat=53)

    with pytest.raises(ValidationError) as exc:
        scene.full_clean()

    assert "zoom" in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("center_lng", 181),
        ("center_lat", -91),
        ("zoom", 25),
        ("bearing", 190),
        ("pitch", 86),
        ("duration_ms", 10001),
    ],
)
def test_scene_rejects_out_of_range_camera(story, field, value):
    camera = {"center_lng": 10, "center_lat": 53, "zoom": 10, field: value}
    scene = GeoStoryScene(geostory=story, title="Bad", **camera)

    with pytest.raises(ValidationError) as exc:
        scene.full_clean()

    assert field in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bounds",
    [[1, 2, 3], [10, 50, 9, 51], [9, 51, 10, 50], [9, "50", 10, 51], [True, 50, 10, 51]],
)
def test_scene_rejects_invalid_bounds(story, bounds):
    scene = GeoStoryScene(geostory=story, title="Bad", bounds=bounds)

    with pytest.raises(ValidationError) as exc:
        scene.full_clean()

    assert "bounds" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_sanitizes_text(story):
    scene = GeoStoryScene.objects.create(
        geostory=story, title="<script>x</script>Title", caption="<b>Cap</b>"
    )

    assert "<script>" not in scene.title
    assert "<" not in scene.caption


# --- GeoStorySceneLayer: layer + style -------------------------------------


@pytest.mark.django_db
def test_scene_layer_pins_default_style_and_orders(scene, vector_layer, raster_layer):
    vec = GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer)
    ras = GeoStorySceneLayer.objects.create(scene=scene, layer=raster_layer)

    assert vec.style_assignment.layer_id == vector_layer.id
    assert (vec.uses_vector_tiles, ras.uses_vector_tiles) == (True, False)
    assert vec.effective_style_layer_ids == ["parks-fill", "parks-line"]
    assert (vec.display_order, ras.display_order) == (0, 1)
    assert list(scene.scene_layers.all()) == [vec, ras]


@pytest.mark.django_db
def test_scene_layer_rejects_non_public_layer(scene, user):
    layer = make_layer("vec:private", user=user, is_public=False)

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer)

    assert "layer" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_rejects_unpublished_layer(scene, user):
    layer = make_layer("vec:draft", user=user, publishing_state="DRAFT")

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer)

    assert "layer" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_requires_a_style(scene, user):
    layer = make_layer("vec:unstyled", user=user)

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer)

    assert "style_assignment" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_rejects_another_layers_style(scene, vector_layer, raster_layer):
    foreign = raster_layer.style_assignments.get()

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene, layer=vector_layer, style_assignment=foreign
        )

    assert "style_assignment" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_accepts_alternate_style(scene, vector_layer, user):
    alternate = assign_style(
        vector_layer,
        user,
        fmt=Style.StyleFormat.MBSTYLE,
        name="parks-alt",
        style_layer_ids=["parks-line"],
        role=LayerStyleAssignment.Role.ALTERNATE,
    )

    scene_layer = GeoStorySceneLayer.objects.create(
        scene=scene, layer=vector_layer, style_assignment=alternate
    )

    assert scene_layer.effective_style_layer_ids == ["parks-line"]


@pytest.mark.django_db
def test_vector_layer_accepts_sld_style_rendered_by_geoserver(scene, user):
    layer = make_layer("vec:roads", user=user)
    assign_style(layer, user, fmt=Style.StyleFormat.SLD, name="roads-sld")

    scene_layer = GeoStorySceneLayer.objects.create(scene=scene, layer=layer)

    assert scene_layer.uses_vector_tiles is False


@pytest.mark.django_db
def test_vector_layer_with_sld_rejects_render_layer_ids(scene, user):
    layer = make_layer("vec:roads", user=user)
    assign_style(layer, user, fmt=Style.StyleFormat.SLD, name="roads-sld")

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer, render_layer_ids=["x"])

    assert "render_layer_ids" in exc.value.message_dict


@pytest.mark.django_db
def test_raster_layer_rejects_mbstyle_style(scene, raster_layer, user):
    mbstyle = assign_style(
        raster_layer,
        user,
        fmt=Style.StyleFormat.MBSTYLE,
        name="ortho-mbstyle",
        style_layer_ids=["parks-fill"],
        role=LayerStyleAssignment.Role.ALTERNATE,
    )

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene, layer=raster_layer, style_assignment=mbstyle
        )

    assert "SLD" in exc.value.message_dict["style_assignment"][0]


@pytest.mark.django_db
def test_raster_layer_rejects_render_layer_ids(scene, raster_layer):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene, layer=raster_layer, render_layer_ids=["parks-fill"]
        )

    assert "render_layer_ids" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_render_rule_subset(scene, vector_layer):
    scene_layer = GeoStorySceneLayer.objects.create(
        scene=scene, layer=vector_layer, render_layer_ids=["parks-line"]
    )

    assert scene_layer.effective_style_layer_ids == ["parks-line"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "render_layer_ids",
    [["missing"], ["bg"], ["parks-fill", "parks-fill"], [""]],
)
def test_scene_layer_rejects_invalid_render_rules(scene, vector_layer, render_layer_ids):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene, layer=vector_layer, render_layer_ids=render_layer_ids
        )

    assert "render_layer_ids" in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_rejects_opacity_out_of_range(scene, vector_layer):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer, opacity=1.5)

    assert "opacity" in exc.value.message_dict


# --- GeoStorySceneLayer: features ------------------------------------------


@pytest.mark.django_db
def test_vector_layer_accepts_feature_selection(scene, vector_layer):
    scene_layer = GeoStorySceneLayer.objects.create(
        scene=scene,
        layer=vector_layer,
        feature_mode=GeoStorySceneLayer.FeatureMode.HIGHLIGHT,
        feature_id_attribute="name",
        feature_ids=["Stadtpark", 42],
    )

    assert scene_layer.feature_ids == ["Stadtpark", 42]


@pytest.mark.django_db
def test_raster_layer_rejects_feature_selection(scene, raster_layer):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene,
            layer=raster_layer,
            feature_mode=GeoStorySceneLayer.FeatureMode.ONLY,
            feature_id_attribute="id",
            feature_ids=[1],
        )

    assert "feature_mode" in exc.value.message_dict


@pytest.mark.django_db
def test_sld_vector_layer_rejects_feature_selection(scene, user):
    layer = make_layer("vec:roads", user=user)
    assign_style(layer, user, fmt=Style.StyleFormat.SLD, name="roads-sld")

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene,
            layer=layer,
            feature_mode=GeoStorySceneLayer.FeatureMode.ONLY,
            feature_id_attribute="id",
            feature_ids=[1],
        )

    assert "MBStyle" in exc.value.message_dict["feature_mode"][0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("attrs", "field"),
    [
        ({"feature_mode": "only", "feature_ids": [1]}, "feature_id_attribute"),
        ({"feature_mode": "only", "feature_id_attribute": "id"}, "feature_ids"),
        ({"feature_mode": "all", "feature_ids": [1]}, "feature_ids"),
        (
            {"feature_mode": "only", "feature_id_attribute": "id", "feature_ids": [1, 1]},
            "feature_ids",
        ),
        (
            {"feature_mode": "only", "feature_id_attribute": "id", "feature_ids": [True]},
            "feature_ids",
        ),
        (
            {"feature_mode": "only", "feature_id_attribute": "id", "feature_ids": [{"a": 1}]},
            "feature_ids",
        ),
    ],
)
def test_scene_layer_rejects_invalid_feature_selection(scene, vector_layer, attrs, field):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer, **attrs)

    assert field in exc.value.message_dict


@pytest.mark.django_db
def test_scene_layer_caps_feature_ids(scene, vector_layer):
    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene,
            layer=vector_layer,
            feature_mode=GeoStorySceneLayer.FeatureMode.ONLY,
            feature_id_attribute="id",
            feature_ids=list(range(MAX_SCENE_FEATURE_IDS + 1)),
        )

    assert "feature_ids" in exc.value.message_dict


@pytest.mark.django_db
def test_deleting_story_cascades_scenes(story, scene, vector_layer):
    GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer)

    story.delete()

    assert not GeoStoryScene.objects.exists()
    assert not GeoStorySceneLayer.objects.exists()


@pytest.mark.django_db
def test_style_in_use_cannot_be_deleted_alone(scene, vector_layer):
    from django.db.models import RestrictedError

    scene_layer = GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer)

    with pytest.raises(RestrictedError):
        scene_layer.style_assignment.delete()


@pytest.mark.django_db
def test_deleting_layer_removes_it_from_scenes(scene, vector_layer):
    GeoStorySceneLayer.objects.create(scene=scene, layer=vector_layer)

    vector_layer.delete()

    assert not scene.scene_layers.exists()


@pytest.mark.django_db
def test_feature_attribute_must_be_a_known_layer_attribute(scene, vector_layer):
    from tosca_api.apps.geodata_providers.models import Layer

    Layer.objects.filter(pk=vector_layer.pk).update(
        attributes=[{"name": "name", "type": "String"}, {"name": "objectid", "type": "Long"}]
    )
    vector_layer.refresh_from_db()

    with pytest.raises(ValidationError) as exc:
        GeoStorySceneLayer.objects.create(
            scene=scene,
            layer=vector_layer,
            feature_mode=GeoStorySceneLayer.FeatureMode.ONLY,
            feature_id_attribute="missing",
            feature_ids=[1],
        )

    assert "not an attribute of this layer" in exc.value.message_dict["feature_id_attribute"][0]
    GeoStorySceneLayer.objects.create(
        scene=scene,
        layer=vector_layer,
        feature_mode=GeoStorySceneLayer.FeatureMode.ONLY,
        feature_id_attribute="objectid",
        feature_ids=[1],
    )
