"""Geostory tools: list, read and write the stories of the signed-in user.

Reading follows ``GET /api/v1/stories/`` (``GeoStoryViewSet._scope_by_visibility``):
published stories of public campaigns for everyone, plus every story of the caller's own
organization (drafts and archived too), and everything for platform admins.

Writing (stories and their map scenes) goes through the models, whose ``clean``/``save``
hold the rules the Django admin and the REST API apply (sanitizing, Editor.js content,
scene anchors, public + published scene layers, style checks). Only stories of the
caller's own organization's campaigns are written; ``authorization.authorize`` is the one
place for further rules (still OPEN, see that module).

A story's text is an Editor.js document. A map scene is shown while the reader is at its
``mapScene`` anchor block; the first scene also shows before the first anchor. Tools that
add a scene append its anchor (after the first scene) at the end of the text.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import anyio.to_thread
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.catalog_api.services.v1.visibility_service import CatalogVisibilityService
from tosca_api.apps.core.editorjs import description_document_from_text, description_document_to_text
from tosca_api.apps.geodata_providers.models import LayerStyleAssignment
from tosca_api.apps.geodata_providers.services.queries import ProviderQueryService
from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene, GeoStorySceneLayer

from ..authorization import WriteAction, authorize, django_user
from ..map_commands import MapCommand
from ..principal import McpPrincipal, current_principal
from .catalog import MAX_PAGE_SIZE, READ_ONLY, _shorten, database_tool
from .map import send_map_command

WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
REMOVES = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
MAX_SCENE_LAYERS = 20

StoryId = Annotated[str, Field(description="Story id from list_geostories or create_geostory.")]


class Camera(BaseModel):
    center: list[float] = Field(description="[longitude, latitude].", min_length=2, max_length=2)
    zoom: float = Field(ge=0, le=24)
    bearing: float = Field(default=0, ge=-180, le=180)
    pitch: float = Field(default=0, ge=0, le=85)


class SceneLayer(BaseModel):
    provider_id: str = Field(description="Provider id from search_layers.")
    workspace: str
    name: str = Field(description="Layer name (not the title). Must be public and published.")
    style: str = Field(default="", description="Style name from describe_layer; empty uses the default style.")
    opacity: float = Field(default=1.0, ge=0, le=1)


# -- reading -------------------------------------------------------------------


def visible_stories(principal: McpPrincipal) -> QuerySet[GeoStory]:
    """The stories ``GeoStoryViewSet`` would list for this caller."""
    stories = GeoStory.objects.all()
    if principal.exempt:
        return stories
    if not principal.org_slug:
        return stories.published()
    return stories.filter(
        Q(pk__in=GeoStory.objects.published().values("pk"))
        | Q(campaign__organization__slug=principal.org_slug)
    )


def list_geostories_for(principal: McpPrincipal, *, query: str = "", limit: int = 25, offset: int = 0) -> dict[str, Any]:
    stories = visible_stories(principal).select_related("campaign").order_by("-created_at")
    for word in query.split():
        stories = stories.filter(Q(title__icontains=word) | Q(summary__icontains=word))
    total = stories.count()
    page = stories[offset:offset + limit]
    return {
        "total": total,
        "next_offset": offset + limit if offset + limit < total else None,
        "stories": [
            {
                "id": str(story.id),
                "title": story.title,
                "summary": _shorten(story.summary),
                "status": story.status,
                "campaign": story.campaign.title if story.campaign_id else None,
                "created_at": story.created_at.isoformat(),
            }
            for story in page
        ],
    }


def _uuid(value: str, name: str = "story_id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ToolError(f"`{name}` must be an id as list_geostories / describe_geostory return it.") from exc


def _scene_payload(scene: GeoStoryScene) -> dict[str, Any]:
    camera = None
    if scene.center_lng is not None:
        camera = {
            "center": [scene.center_lng, scene.center_lat], "zoom": scene.zoom,
            "bearing": scene.bearing, "pitch": scene.pitch,
        }
    return {
        "id": str(scene.id),
        "order": scene.order,
        "title": scene.title,
        "caption": scene.caption,
        "camera": camera,
        "bounds": scene.bounds,
        "layers": [
            {
                "provider_id": str(item.layer.workspace.geodata_engine_id),
                "workspace": item.layer.workspace.name,
                "name": item.layer.name,
                "style": item.style_assignment.style.name if item.style_assignment_id else None,
                "opacity": item.opacity,
            }
            for item in scene.scene_layers.select_related("layer__workspace", "style_assignment__style")
        ],
    }


def _text_outline(story: GeoStory) -> list[dict[str, Any]]:
    """The story text as plain paragraphs and scene anchors, in order."""
    outline: list[dict[str, Any]] = []
    for block in (story.content or {}).get("blocks", []):
        if block.get("type") == "mapScene":
            outline.append({"scene": block.get("data", {}).get("scene_id")})
        elif block.get("type") in {"paragraph", "header", "list"}:
            text = description_document_to_text({"blocks": [block]})
            if text:
                outline.append({"text": _shorten(text)})
        else:
            outline.append({"block": block.get("type")})
    return outline


def describe_geostory_for(principal: McpPrincipal, *, story_id: str) -> dict[str, Any]:
    story = visible_stories(principal).select_related("campaign").filter(pk=_uuid(story_id)).first()
    if story is None:
        raise ToolError("Story not found.")
    return {
        "id": str(story.id),
        "title": story.title,
        "summary": story.summary,
        "status": story.status,
        "campaign": {"id": str(story.campaign_id), "title": story.campaign.title},
        "text": _text_outline(story),
        "scenes": [_scene_payload(scene) for scene in story.scenes.all()],
        "unreachable_scenes": [str(scene.id) for scene in story.unreachable_scenes()],
    }


def list_campaigns_for(principal: McpPrincipal) -> dict[str, Any]:
    campaigns = Campaign.objects.select_related("organization").order_by("title")
    if not principal.exempt:
        campaigns = campaigns.filter(organization__slug=principal.org_slug) if principal.org_slug else campaigns.none()
    return {
        "campaigns": [
            {
                "id": str(campaign.id),
                "title": campaign.title,
                "summary": _shorten(campaign.summary),
                "status": campaign.status,
                "visibility": campaign.visibility,
                "organization": campaign.organization.slug if campaign.organization_id else None,
            }
            for campaign in campaigns
        ]
    }


# -- writing -------------------------------------------------------------------


def _messages(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        return "; ".join(f"{field}: {' '.join(texts)}" for field, texts in error.message_dict.items())
    return " ".join(error.messages)


def _own_story(principal: McpPrincipal, story_id: str) -> GeoStory:
    story = visible_stories(principal).select_related("campaign__organization").filter(pk=_uuid(story_id)).first()
    if story is None:
        raise ToolError("Story not found.")
    authorize(principal, WriteAction.WRITE_GEOSTORY, campaign=story.campaign)
    return story


def _paragraphs(text: str) -> list[dict[str, Any]]:
    return description_document_from_text(text)["blocks"]


def _append_blocks(story: GeoStory, blocks: list[dict[str, Any]]) -> None:
    content = dict(story.content or {})
    content["blocks"] = [*(content.get("blocks") or []), *blocks]
    story.content = content


def create_geostory_for(
    principal: McpPrincipal, *, campaign_id: str, title: str, summary: str = "", text: str = "",
    status: str = GeoStory.Status.DRAFT,
) -> dict[str, Any]:
    try:
        campaign = Campaign.objects.select_related("organization").get(pk=uuid.UUID(str(campaign_id)))
    except (ValueError, Campaign.DoesNotExist) as exc:
        raise ToolError("Campaign not found. list_campaigns names the ones you can use.") from exc
    authorize(principal, WriteAction.WRITE_GEOSTORY, campaign=campaign)
    story = GeoStory(
        campaign=campaign, author=django_user(principal), title=title, summary=summary, status=status,
        content={"blocks": _paragraphs(text)},
    )
    try:
        story.full_clean()
        story.save()
    except ValidationError as exc:
        raise ToolError(f"The story was not saved: {_messages(exc)}") from exc
    return describe_geostory_for(principal, story_id=str(story.id))


def update_geostory_for(
    principal: McpPrincipal, *, story_id: str, title: str | None = None, summary: str | None = None,
    status: str | None = None, append_text: str = "", content: dict | None = None,
) -> dict[str, Any]:
    story = _own_story(principal, story_id)
    if title is not None:
        story.title = title
    if summary is not None:
        story.summary = summary
    if status is not None:
        story.status = status
    if content is not None:
        story.content = content
    if append_text:
        _append_blocks(story, _paragraphs(append_text))
    try:
        story.full_clean()
        story.save()
    except ValidationError as exc:
        raise ToolError(f"The story was not saved: {_messages(exc)}") from exc
    return describe_geostory_for(principal, story_id=str(story.id))


def _scene_layer(principal: McpPrincipal, scene: GeoStoryScene, wanted: SceneLayer, order: int) -> GeoStorySceneLayer:
    try:
        layer = CatalogVisibilityService.get_visible_layer(
            workspace_name=wanted.workspace, layer_name=wanted.name, provider_id=wanted.provider_id,
            viewer=principal.viewer,
        )
    except Exception as exc:
        raise ToolError(f"Layer '{wanted.workspace}:{wanted.name}' was not found in the catalog.") from exc
    assignment = None
    if wanted.style:
        assignment = (
            LayerStyleAssignment.objects.select_related("style")
            .filter(layer=layer, is_active=True, style__name=wanted.style)
            .order_by("role")
            .first()
        )
        if assignment is None:
            raise ToolError(f"Layer '{wanted.workspace}:{wanted.name}' has no style '{wanted.style}'.")
    return GeoStorySceneLayer(
        scene=scene, layer=layer, style_assignment=assignment, opacity=wanted.opacity, display_order=order,
    )


def _write_scene(
    principal: McpPrincipal, scene: GeoStoryScene, *, title: str | None, caption: str | None,
    camera: Camera | None, bounds: list[float] | None, layers: list[SceneLayer] | None,
) -> None:
    if title is not None:
        scene.title = title
    if caption is not None:
        scene.caption = caption
    if camera is not None:
        scene.center_lng, scene.center_lat = camera.center
        scene.zoom, scene.bearing, scene.pitch = camera.zoom, camera.bearing, camera.pitch
    if bounds is not None:
        scene.bounds = bounds or None
    scene.save()
    if layers is not None:
        if len(layers) > MAX_SCENE_LAYERS:
            raise ToolError(f"A scene takes at most {MAX_SCENE_LAYERS} layers.")
        scene.scene_layers.all().delete()
        for order, wanted in enumerate(layers, start=1):
            _scene_layer(principal, scene, wanted, order).save()


def add_scene_for(
    principal: McpPrincipal, *, story_id: str, title: str, caption: str = "", camera: Camera | None = None,
    bounds: list[float] | None = None, layers: list[SceneLayer] | None = None, text_before: str = "",
) -> dict[str, Any]:
    story = _own_story(principal, story_id)
    try:
        with transaction.atomic():
            first = not story.scenes.exists()
            scene = GeoStoryScene(geostory=story, title=title)
            _write_scene(principal, scene, title=title, caption=caption, camera=camera, bounds=bounds, layers=layers or [])
            blocks = _paragraphs(text_before) if text_before else []
            if not first:
                blocks.append({"type": "mapScene", "data": {"scene_id": str(scene.id)}})
            if blocks:
                _append_blocks(story, blocks)
                story.full_clean()
                story.save()
    except ValidationError as exc:
        raise ToolError(f"The scene was not saved: {_messages(exc)}") from exc
    return {"scene": _scene_payload(scene), "anchored": not first, "story": str(story.id)}


def update_scene_for(
    principal: McpPrincipal, *, story_id: str, scene_id: str, title: str | None = None, caption: str | None = None,
    camera: Camera | None = None, bounds: list[float] | None = None, layers: list[SceneLayer] | None = None,
) -> dict[str, Any]:
    story = _own_story(principal, story_id)
    scene = story.scenes.filter(pk=_uuid(scene_id, "scene_id")).first() if scene_id else None
    if scene is None:
        raise ToolError("Scene not found in this story.")
    try:
        with transaction.atomic():
            _write_scene(principal, scene, title=title, caption=caption, camera=camera, bounds=bounds, layers=layers)
    except ValidationError as exc:
        raise ToolError(f"The scene was not saved: {_messages(exc)}") from exc
    return {"scene": _scene_payload(scene)}


def delete_scene_for(principal: McpPrincipal, *, story_id: str, scene_id: str) -> dict[str, Any]:
    story = _own_story(principal, story_id)
    scene = story.scenes.filter(pk=_uuid(scene_id, "scene_id")).first()
    if scene is None:
        raise ToolError("Scene not found in this story.")
    with transaction.atomic():
        content = dict(story.content or {})
        content["blocks"] = [
            block for block in content.get("blocks") or []
            if not (block.get("type") == "mapScene" and block.get("data", {}).get("scene_id") == str(scene.id))
        ]
        story.content = content
        scene.delete()
        story.save()
    return {"deleted": str(scene_id), "story": str(story.id)}


def scene_from_map_state(principal: McpPrincipal, state: dict[str, Any]) -> tuple[Camera, list[float] | None, list[SceneLayer], list[str]]:
    """Camera and catalog layers of the open map (``get_map_state``); other layers are reported as skipped."""
    view = state.get("view") or {}
    try:
        camera = Camera(
            center=view["center"], zoom=view["zoom"], bearing=view.get("bearing", 0) or 0,
            pitch=view.get("pitch", 0) or 0,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("The map did not report its view.") from exc
    bounds = view.get("bounds") if isinstance(view.get("bounds"), list) else None
    providers = None
    layers: list[SceneLayer] = []
    skipped: list[str] = []
    for item in state.get("layers") or []:
        catalog = item.get("catalog") if isinstance(item, dict) else None
        if not catalog or item.get("visible") is False or item.get("kind") == "group":
            skipped.append(str(item.get("title") or item.get("id")))
            continue
        provider_id = catalog.get("providerId")
        if not provider_id:
            providers = providers or [str(entry["id"]) for entry in ProviderQueryService.list_providers()]
            provider_id = providers[0] if len(providers) == 1 else ""
        if not provider_id:
            skipped.append(str(item.get("title") or item.get("id")))
            continue
        layers.append(SceneLayer(
            provider_id=str(provider_id), workspace=catalog["workspace"], name=catalog["name"],
            style=str(catalog.get("style") or ""), opacity=float(item.get("opacity", 1) or 1),
        ))
    return camera, bounds, layers, skipped


# -- registration --------------------------------------------------------------


def register(server: MCPServer, map_commands: dict[str, MapCommand]) -> None:
    get_map_state = map_commands["getMapState"]

    @server.tool(
        title="List geostories",
        description=(
            "Find geostories (guided map stories) the user may open. `query` matches words in the title or "
            "summary (all must match; empty lists all). `status` is draft, published or archived; drafts are "
            "only listed for the user's own organization. Paged like search_layers. Open one with open_geostory, "
            "read its text and scenes with describe_geostory."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def list_geostories(
        query: Annotated[str, Field(description="Words to look for, e.g. 'green spaces'. Empty lists all.")] = "",
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size.")] = 25,
        offset: Annotated[int, Field(ge=0, description="Number of results to skip.")] = 0,
    ) -> dict[str, Any]:
        return list_geostories_for(current_principal(), query=query, limit=limit, offset=offset)

    @server.tool(
        title="Read a geostory",
        description="A geostory's title, summary, status, text (paragraphs and scene anchors in order) and map scenes.",
        annotations=READ_ONLY,
    )
    @database_tool
    def describe_geostory(story_id: StoryId) -> dict[str, Any]:
        return describe_geostory_for(current_principal(), story_id=story_id)

    @server.tool(
        title="List campaigns",
        description="The campaigns of the user's organization. A new geostory belongs to one (create_geostory).",
        annotations=READ_ONLY,
    )
    @database_tool
    def list_campaigns() -> dict[str, Any]:
        return list_campaigns_for(current_principal())

    @server.tool(
        title="Create a geostory",
        description=(
            "Create a geostory in one of the organization's campaigns (list_campaigns). `text` becomes the story "
            "text, one paragraph per blank-line-separated block. Starts as a draft unless `status` says otherwise. "
            "Add map scenes with add_scene or add_scene_from_map."
        ),
        annotations=WRITES,
    )
    @database_tool
    def create_geostory(
        campaign_id: Annotated[str, Field(description="Campaign id from list_campaigns.")],
        title: Annotated[str, Field(min_length=1, max_length=255)],
        summary: Annotated[str, Field(description="Short introduction.")] = "",
        text: Annotated[str, Field(description="Story text (plain text; blank lines separate paragraphs).")] = "",
        status: Annotated[str, Field(pattern="^(draft|published|archived)$")] = "draft",
    ) -> dict[str, Any]:
        return create_geostory_for(
            current_principal(), campaign_id=campaign_id, title=title, summary=summary, text=text, status=status
        )

    @server.tool(
        title="Change a geostory",
        description=(
            "Change a geostory of the organization: title, summary, status (draft, published, archived), add "
            "paragraphs at the end (`append_text`), or replace the whole text with an Editor.js document "
            "(`content`; keep its mapScene blocks or the scenes become unreachable). Ask before publishing."
        ),
        annotations=WRITES,
    )
    @database_tool
    def update_geostory(
        story_id: StoryId,
        title: Annotated[str | None, Field(max_length=255)] = None,
        summary: str | None = None,
        status: Annotated[str | None, Field(pattern="^(draft|published|archived)$")] = None,
        append_text: Annotated[str, Field(description="Paragraphs to add at the end of the text.")] = "",
        content: Annotated[dict | None, Field(description="Full Editor.js document replacing the text.")] = None,
    ) -> dict[str, Any]:
        return update_geostory_for(
            current_principal(), story_id=story_id, title=title, summary=summary, status=status,
            append_text=append_text, content=content,
        )

    @server.tool(
        title="Add a map scene",
        description=(
            "Add a map scene to a geostory: camera and/or bounds, and catalog layers (public and published; "
            "search_layers). The scene is shown from its place in the text: after the first scene, an anchor is "
            "added at the end of the text, after `text_before`. To capture what the user's map shows now, use "
            "add_scene_from_map instead."
        ),
        annotations=WRITES,
    )
    @database_tool
    def add_scene(
        story_id: StoryId,
        title: Annotated[str, Field(min_length=1, max_length=255)],
        caption: str = "",
        camera: Camera | None = None,
        bounds: Annotated[list[float] | None, Field(description="[west, south, east, north]; the map fits it.")] = None,
        layers: list[SceneLayer] | None = None,
        text_before: Annotated[str, Field(description="Paragraphs added just before the scene's anchor.")] = "",
    ) -> dict[str, Any]:
        return add_scene_for(
            current_principal(), story_id=story_id, title=title, caption=caption, camera=camera, bounds=bounds,
            layers=layers, text_before=text_before,
        )

    @server.tool(
        title="Change a map scene",
        description="Change a scene's title, caption, camera, bounds, or replace its layers (a given list replaces all).",
        annotations=WRITES,
    )
    @database_tool
    def update_scene(
        story_id: StoryId,
        scene_id: Annotated[str, Field(description="Scene id from describe_geostory.")],
        title: Annotated[str | None, Field(max_length=255)] = None,
        caption: str | None = None,
        camera: Camera | None = None,
        bounds: list[float] | None = None,
        layers: list[SceneLayer] | None = None,
    ) -> dict[str, Any]:
        return update_scene_for(
            current_principal(), story_id=story_id, scene_id=scene_id, title=title, caption=caption,
            camera=camera, bounds=bounds, layers=layers,
        )

    @server.tool(
        title="Delete a map scene",
        description="Delete a scene from a geostory, with its anchor in the text. Ask the user first.",
        annotations=REMOVES,
    )
    @database_tool
    def delete_scene(
        story_id: StoryId,
        scene_id: Annotated[str, Field(description="Scene id from describe_geostory.")],
    ) -> dict[str, Any]:
        return delete_scene_for(current_principal(), story_id=story_id, scene_id=scene_id)

    @server.tool(
        title="Save the current map view as a scene",
        description=(
            "Add a scene to a geostory from the user's open TOSCA map (needs connect_map): its camera and its "
            "visible catalog layers with their style and opacity. Drawings, GeoJSON and external layers cannot "
            "be part of a scene and are listed as skipped."
        ),
        annotations=WRITES,
    )
    async def add_scene_from_map(
        story_id: StoryId,
        title: Annotated[str, Field(min_length=1, max_length=255)],
        caption: str = "",
        text_before: Annotated[str, Field(description="Paragraphs added just before the scene's anchor.")] = "",
    ) -> dict[str, Any]:
        principal = current_principal()
        state = await send_map_command(principal, get_map_state, {})
        camera, bounds, layers, skipped = scene_from_map_state(principal, state if isinstance(state, dict) else {})

        @database_tool
        def write() -> dict[str, Any]:
            return add_scene_for(
                principal, story_id=story_id, title=title, caption=caption, camera=camera, bounds=bounds,
                layers=layers, text_before=text_before,
            )

        result = await anyio.to_thread.run_sync(write)
        return {**result, "skipped_layers": skipped}
