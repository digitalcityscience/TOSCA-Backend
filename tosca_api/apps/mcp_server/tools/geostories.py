"""Geostory tools: the stories the signed-in user may open on the map.

Visibility is the same as ``GET /api/v1/stories/`` (``GeoStoryViewSet._scope_by_visibility``):
published stories of public campaigns for everyone, plus every story of the caller's own
organization (drafts and archived too), and everything for platform admins.
"""

from __future__ import annotations

from typing import Annotated, Any

from django.db.models import Q, QuerySet
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from tosca_api.apps.geostories.models import GeoStory

from ..principal import McpPrincipal, current_principal
from .catalog import MAX_PAGE_SIZE, READ_ONLY, _shorten, database_tool


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


def register(server: MCPServer) -> None:
    @server.tool(
        title="List geostories",
        description=(
            "Find geostories (guided map stories) the user may open. `query` matches words in the title or "
            "summary (all must match; empty lists all). `status` is draft, published or archived; drafts are "
            "only listed for the user's own organization. Paged like search_layers. Open one with open_geostory."
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
