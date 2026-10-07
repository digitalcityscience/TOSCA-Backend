"""Tests for the GeoStory scene MapLibre manifest."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geodata_providers.models import Layer, LayerStyleAssignment, Style
from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene, GeoStorySceneLayer
from tosca_api.apps.geostories.scene_manifest import (
    HIGHLIGHT_COLOR,
    _scale,
    build_scene_legend,
    build_scene_render_layers,
    build_story_map,
)
from tosca_api.apps.geostories.tests.scene_helpers import (
    assign_style,
    make_raster_layer,
    make_vector_layer,
)

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="manifest-user", password="x")


@pytest.fixture
def story(user):
    campaign = Campaign.objects.create(title="Campaign", created_by=user)
    return GeoStory.objects.create(
        title="Story", campaign=campaign, author=user, status=GeoStory.Status.PUBLISHED
    )


@pytest.fixture
def parks(user):
    return make_vector_layer("ws:parks", user=user, geometry_type="Polygon")


@pytest.fixture
def ortho(user):
    return make_raster_layer("ws:ortho", user=user)


def make_layer_with_sld(name, user):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    layer = make_layer(name, user=user, geometry_type="LineString")
    assign_style(layer, user, fmt=Style.StyleFormat.SLD, name=f"{layer.name}-sld")
    return layer


def _scene(story, *layers, **scene_layer_attrs):
    scene = GeoStoryScene.objects.create(geostory=story, title="Scene")
    rows = [
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer, **scene_layer_attrs)
        for layer in layers
    ]
    return scene, rows


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


# --- Story map ----------------------------------------------------------------


@pytest.mark.django_db
def test_story_map_shares_vector_sources_across_scenes(story, parks, ortho):
    _scene(story, ortho, parks)
    _scene(story, parks)

    story_map = build_story_map(request=None, scenes=story.scenes.all())

    assert set(story_map["sources"]) == {
        f"vector-{parks.id}",
        f"wms-{ortho.id}-{ortho.style_assignments.get().style_id}",
    }
    vector = story_map["sources"][f"vector-{parks.id}"]
    assert vector["type"] == "vector"
    assert "/gwc/service/wmts?" in vector["tiles"][0]
    assert "TILEMATRIX=EPSG%3A900913%3A{z}&TILEMATRIXSET" in vector["tiles"][0]
    assert "TILECOL={x}&TILEROW={y}" in vector["tiles"][0]
    assert _query(vector["tiles"][0])["LAYER"] == "ws:parks"
    assert len(story_map["styles"]) == 2
    assert story_map["sprites"] == {}


@pytest.mark.django_db
def test_raster_in_two_styles_gets_two_sources(story, ortho, user):
    alternate = assign_style(
        ortho, user, fmt=Style.StyleFormat.SLD, name="ortho-alt",
        role=LayerStyleAssignment.Role.ALTERNATE,
    )
    _scene(story, ortho)
    _scene(story, ortho, style_assignment=alternate)

    sources = build_story_map(request=None, scenes=story.scenes.all())["sources"]

    assert len(sources) == 2
    styles = {_query(source["tiles"][0])["STYLES"] for source in sources.values()}
    assert styles == {"ortho-style", "ortho-alt"}
    assert all(source["tiles"][0].endswith("&BBOX={bbox-epsg-3857}") for source in sources.values())


@pytest.mark.django_db
def test_style_entries_link_to_catalog(story, parks, rf):
    _scene(story, parks)
    style = parks.style_assignments.get().style

    styles = build_story_map(request=rf.get("/"), scenes=story.scenes.all())["styles"]

    entry = styles[str(style.id)]
    assert entry["format"] == "mbstyle"
    provider_id = parks.workspace.geodata_engine_id
    assert entry["href"] == f"http://testserver/api/v1/catalog/providers/{provider_id}/styles/{style.id}"


# --- Render layers -------------------------------------------------------------


@pytest.mark.django_db
def test_render_layers_are_ordered_unique_and_bound_to_shared_sources(story, parks, ortho):
    scene, (raster_row, vector_row) = _scene(story, ortho, parks)

    layers = build_scene_render_layers(scene)

    assert [layer["id"] for layer in layers] == [
        f"scene-layer-{raster_row.id}",
        f"scene-layer-{vector_row.id}/parks-fill",
        f"scene-layer-{vector_row.id}/parks-line",
    ]
    raster, fill, line = layers
    assert raster["type"] == "raster"
    assert raster["source"].startswith(f"wms-{ortho.id}-")
    assert (fill["type"], fill["source"], fill["source-layer"]) == (
        "fill", f"vector-{parks.id}", "parks",
    )
    assert fill["metadata"]["tosca:scene-layer-id"] == str(vector_row.id)
    assert fill["metadata"]["tosca:layer-id"] == str(parks.id)
    assert "paint" not in fill  # full opacity leaves the style untouched
    assert line["type"] == "line"


@pytest.mark.django_db
def test_sld_vector_layer_renders_as_wms_image(story, user):
    roads = make_layer_with_sld("ws:roads", user)
    scene, [row] = _scene(story, roads)

    [render_layer] = build_scene_render_layers(scene)
    sources = build_story_map(request=None, scenes=story.scenes.all())["sources"]

    key = f"wms-{roads.id}-{row.style_assignment.style_id}"
    assert render_layer == {
        "id": f"scene-layer-{row.id}",
        "type": "raster",
        "source": key,
        "metadata": {
            "tosca:scene-layer-id": str(row.id),
            "tosca:layer-id": str(roads.id),
            "tosca:style-id": str(row.style_assignment.style_id),
        },
    }
    assert sources[key]["type"] == "raster"
    assert _query(sources[key]["tiles"][0])["STYLES"] == "roads-sld"


@pytest.mark.django_db
def test_render_layers_respect_rule_subset(story, parks):
    scene, _ = _scene(story, parks, render_layer_ids=["parks-line"])

    layers = build_scene_render_layers(scene)

    assert [layer["type"] for layer in layers] == ["line"]


@pytest.mark.django_db
def test_render_layers_apply_opacity(story, parks, ortho):
    scene, _ = _scene(story, ortho, parks, opacity=0.5)

    raster, fill, line = build_scene_render_layers(scene)

    assert raster["paint"] == {"raster-opacity": 0.5}
    assert fill["paint"] == {"fill-opacity": 0.5}
    assert line["paint"] == {"line-opacity": 0.5}


@pytest.mark.django_db
def test_skips_layers_without_style_or_no_longer_public(story, parks, user):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    scene, _ = _scene(story, parks)
    # Legacy v1 rows can be migrated without a style.
    unstyled = make_layer("ws:unstyled", user=user)
    GeoStorySceneLayer.objects.bulk_create(
        [GeoStorySceneLayer(scene=scene, layer=unstyled, display_order=5)]
    )
    hidden = make_vector_layer("ws:hidden", user=user)
    GeoStorySceneLayer.objects.create(scene=scene, layer=hidden)
    Layer.objects.filter(pk=hidden.pk).update(is_public=False)

    layers = build_scene_render_layers(scene)
    sources = build_story_map(request=None, scenes=story.scenes.all())["sources"]

    assert {layer["source-layer"] for layer in layers} == {"parks"}
    assert list(sources) == [f"vector-{parks.id}"]


# --- Opacity scaling -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0.5),
        (0.8, 0.4),
        (
            ["interpolate", ["linear"], ["zoom"], 10, 0.2, 14, 1],
            ["interpolate", ["linear"], ["zoom"], 10, 0.1, 14, 0.5],
        ),
        (["step", ["zoom"], 0, 12, 1], ["step", ["zoom"], 0, 12, 0.5]),
        (["get", "alpha"], ["*", ["get", "alpha"], 0.5]),
        ({"stops": [[10, 0.2], [14, 1]]}, {"stops": [[10, 0.1], [14, 0.5]]}),
    ],
    ids=["absent", "number", "interpolate", "step", "expression", "legacy-stops"],
)
def test_scale_opacity_keeps_zoom_expressions_top_level(value, expected):
    assert _scale(value, 0.5) == expected


# --- Feature selection --------------------------------------------------------


def _with_filter(story, layer, user, existing_filter):
    """A vector layer whose only MBStyle rule carries ``existing_filter``."""
    import json

    style_layer = {"id": "rule", "type": "fill", "source": "x", "source-layer": layer.name}
    if existing_filter is not None:
        style_layer["filter"] = existing_filter
    style = Style.objects.create(
        geodata_engine=layer.workspace.geodata_engine,
        workspace=layer.workspace,
        name=f"{layer.name}-filtered",
        format=Style.StyleFormat.MBSTYLE,
        file_content=json.dumps({"version": 8, "layers": [style_layer]}),
        validation_state=Style.ValidationState.VALID,
        created_by=user,
    )
    assignment = LayerStyleAssignment.objects.create(
        layer=layer, style=style, role=LayerStyleAssignment.Role.ALTERNATE,
        style_layer_ids=["rule"], created_by=user,
    )
    scene, _ = _scene(
        story,
        layer,
        style_assignment=assignment,
        feature_mode="only",
        feature_id_attribute="name",
        feature_ids=["Stadtpark", 7],
    )
    return build_scene_render_layers(scene)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, ["in", ["get", "name"], ["literal", ["Stadtpark", 7]]]),
        (
            ["==", ["get", "kind"], "park"],
            ["all", ["==", ["get", "kind"], "park"], ["in", ["get", "name"], ["literal", ["Stadtpark", 7]]]],
        ),
        (
            ["==", "kind", "park"],
            ["all", ["==", "kind", "park"], ["in", "name", "Stadtpark", 7]],
        ),
        (
            ["all", ["has", "kind"], ["!=", "kind", "lake"]],
            ["all", ["all", ["has", "kind"], ["!=", "kind", "lake"]], ["in", "name", "Stadtpark", 7]],
        ),
    ],
    ids=["no-filter", "expression", "legacy", "legacy-all"],
)
def test_only_mode_filters_every_pass(story, parks, user, existing, expected):
    [render_layer] = _with_filter(story, parks, user, existing)

    assert render_layer["filter"] == expected


@pytest.mark.django_db
def test_highlight_mode_adds_overlay_for_polygons(story, parks):
    scene, [row] = _scene(
        story, parks, feature_mode="highlight", feature_id_attribute="name", feature_ids=["A"]
    )

    layers = build_scene_render_layers(scene)

    assert [layer["id"].rsplit("/", 1)[-1] for layer in layers] == [
        "parks-fill", "parks-line", "highlight-fill", "highlight-outline",
    ]
    assert "filter" not in layers[0]  # every feature still drawn
    highlight = layers[2]
    assert highlight["filter"] == ["in", ["get", "name"], ["literal", ["A"]]]
    assert highlight["paint"]["fill-color"] == HIGHLIGHT_COLOR
    assert highlight["metadata"]["tosca:role"] == "highlight"
    assert highlight["source"] == f"vector-{parks.id}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("geometry_type", "suffixes"),
    [
        ("Point", ["highlight-point"]),
        ("MultiLineString", ["highlight-line"]),
        (
            "GeometryCollection",
            ["highlight-fill", "highlight-outline", "highlight-line", "highlight-point"],
        ),
    ],
)
def test_highlight_overlay_matches_geometry(story, user, geometry_type, suffixes):
    layer = make_vector_layer(f"ws:geom_{geometry_type.lower()}", user=user, geometry_type=geometry_type)
    scene, _ = _scene(
        story, layer, feature_mode="highlight", feature_id_attribute="id", feature_ids=[1]
    )

    overlays = [
        item for item in build_scene_render_layers(scene)
        if item["metadata"].get("tosca:role") == "highlight"
    ]

    assert [item["id"].rsplit("/", 1)[-1] for item in overlays] == suffixes
    if geometry_type == "GeometryCollection":
        assert overlays[-1]["filter"][1] == [
            "in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]],
        ]


# --- Legend ------------------------------------------------------------------------


@pytest.mark.django_db
def test_legend_lists_top_layer_first_with_getlegendgraphic(story, parks, ortho):
    scene, _ = _scene(story, ortho, parks)

    legend = build_scene_legend(scene)

    assert [entry["layer_id"] for entry in legend] == [str(parks.id), str(ortho.id)]
    assert [entry["data_type"] for entry in legend] == ["VECTOR", "RASTER"]
    assert [entry["rendering"] for entry in legend] == ["vector-tiles", "wms"]
    query = _query(legend[0]["graphic_url"])
    assert query["REQUEST"] == "GetLegendGraphic"
    assert query["LAYER"] == "ws:parks"
    assert query["STYLE"] == "parks-style"
    assert legend[0]["graphic_url"].startswith("http://example.com/geoserver/wms?")


# --- API ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_detail_exposes_map_and_scene_render_layers(story, parks, ortho):
    _scene(story, ortho, parks)

    response = APIClient().get(f"/api/v1/stories/{story.id}/")

    assert response.status_code == 200
    data = response.data
    [scene] = data["scenes"]
    assert {layer["source"] for layer in scene["render_layers"]} <= set(data["map"]["sources"])
    assert [entry["title"] for entry in scene["legend"]] == ["parks", "ortho"]
    assert {item["source_key"] for item in scene["layers"]} == set(data["map"]["sources"])
