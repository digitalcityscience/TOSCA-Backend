from django.urls import reverse

from tosca_api.apps.geodata_providers.models import Store

from .render_manifest import (
    build_render_layers,
    build_source,
    build_sprite_entry,
    build_style_entry,
)


class LayerGroupV1Builder:
    """Build an ordered, assignment-centric group manifest for TOSCA-2."""

    @classmethod
    def build_group_list(cls, *, request, groups, provider_id) -> dict:
        return {
            "groups": {
                "group": [
                    {
                        "id": str(group.id),
                        "name": group.name,
                        "title": group.title or group.name,
                        "description": group.description,
                        "description_content": group.description_content,
                        "composition": group.composition,
                        "member_count": len(group.members.all()),
                        "has_legend": bool(group.legend_image),
                        "legend_stale": group.legend_is_stale,
                        "warnings": group.publication_warnings(),
                        "href": request.build_absolute_uri(
                            reverse(
                                "catalog-v1-provider-workspace-group-detail",
                                kwargs={
                                    "provider_id": provider_id,
                                    "workspace_name": group.workspace.name,
                                    "group_name": group.name,
                                },
                            )
                        ),
                    }
                    for group in groups
                ]
            }
        }

    @classmethod
    def build_group_detail(cls, *, request, group, provider_id) -> dict:
        members = list(group.members.all())
        source_keys = cls._build_source_keys(members)
        sources = {}
        for member in members:
            source_key = source_keys[member.id]
            if source_key not in sources:
                sources[source_key] = cls._build_source(group=group, member=member)
        render_layers = cls._build_render_layers(
            members=members,
            source_keys=source_keys,
        )
        styles = cls._build_styles(request=request, members=members, provider_id=provider_id)
        sprites = cls._build_sprites(
            request=request,
            members=members,
            provider_id=provider_id,
        )

        return {
            "group": {
                "id": str(group.id),
                "name": group.name,
                "title": group.title or group.name,
                "description": group.description,
                "description_content": group.description_content,
                "composition": group.composition,
                "legend": cls._build_legend(
                    request=request,
                    group=group,
                    provider_id=provider_id,
                ),
                "warnings": group.publication_warnings(),
                "workspace": {
                    "id": str(group.workspace_id),
                    "name": group.workspace.name,
                },
                "provider": {
                    "id": str(group.workspace.geodata_engine_id),
                    "name": group.workspace.geodata_engine.name,
                    "base_url": group.workspace.geodata_engine.public_url.rstrip("/"),
                },
                "members": [
                    cls._build_member(
                        request=request,
                        group=group,
                        member=member,
                        provider_id=provider_id,
                        source_key=source_keys[member.id],
                    )
                    for member in members
                ],
                "sources": sources,
                "layers": render_layers,
                "styles": styles,
                "sprites": sprites,
            }
        }

    @staticmethod
    def _build_legend(*, request, group, provider_id) -> dict | None:
        if not group.legend_image:
            return None
        href = request.build_absolute_uri(
            reverse(
                "catalog-v1-provider-workspace-group-legend",
                kwargs={
                    "provider_id": provider_id,
                    "workspace_name": group.workspace.name,
                    "group_name": group.name,
                },
            )
        )
        return {
            "url": f"{href}?v={group.legend_content_hash}",
            "content_hash": group.legend_content_hash,
            "stale": group.legend_is_stale,
        }

    @classmethod
    def _build_member(cls, *, request, group, member, provider_id, source_key) -> dict:
        assignment = member.style_assignment
        style = assignment.style
        is_raster = member.layer.store.store_type == Store.StoreType.GEOTIFF
        return {
            "id": str(member.id),
            "layer_id": str(member.layer_id),
            "name": member.layer.name,
            "title": member.display_title,
            "layer_title": member.layer.title or member.layer.name,
            "source_alias": member.source_alias,
            "source_key": source_key,
            "order": member.order,
            "data_type": "RASTER" if is_raster else "VECTOR",
            "geometry_type": member.layer.geometry_type,
            "style_assignment": {
                "id": str(assignment.id),
                "style_id": str(style.id),
                "style_layer_ids": assignment.style_layer_ids,
            },
            "render_layer_ids": member.render_layer_ids,
            "effective_style_layer_ids": member.effective_style_layer_ids,
            "resource_href": request.build_absolute_uri(
                reverse(
                    "catalog-v1-provider-layer-detail",
                    kwargs={
                        "provider_id": provider_id,
                        "workspace_name": group.workspace.name,
                        "layer_name": member.layer.name,
                    },
                )
            ),
        }

    @classmethod
    def _build_styles(cls, *, request, members, provider_id) -> dict:
        styles: dict[str, dict] = {}
        for member in members:
            style = member.style_assignment.style
            if str(style.id) not in styles:
                styles[str(style.id)] = build_style_entry(
                    request=request, style=style, provider_id=provider_id
                )
        return styles

    @classmethod
    def _build_sprites(cls, *, request, members, provider_id) -> dict:
        sprites: dict[str, dict] = {}
        for member in members:
            sprite_asset = member.style_assignment.style.sprite_asset
            if sprite_asset is None or str(sprite_asset.id) in sprites:
                continue
            sprites[str(sprite_asset.id)] = build_sprite_entry(
                request=request, sprite_asset=sprite_asset, provider_id=provider_id
            )
        return sprites

    @classmethod
    def _build_render_layers(cls, *, members, source_keys) -> list[dict]:
        render_layers: list[dict] = []
        for member in members:
            render_layers.extend(
                build_render_layers(
                    layer=member.layer,
                    style_assignment=member.style_assignment,
                    style_layer_ids=member.effective_style_layer_ids,
                    source_key=source_keys[member.id],
                    raster_id=f"member-{member.id}",
                    metadata={
                        "tosca:member-id": str(member.id),
                        "tosca:style-id": str(member.style_assignment.style_id),
                    },
                )
            )
        return render_layers

    @staticmethod
    def _build_source_keys(members) -> dict:
        """Share one vector source across repeated render passes of a layer."""
        source_keys = {}
        vector_sources_by_layer = {}
        for member in members:
            if member.layer.store.store_type == Store.StoreType.GEOTIFF:
                source_keys[member.id] = member.source_alias
                continue
            source_keys[member.id] = vector_sources_by_layer.setdefault(
                member.layer_id,
                member.source_alias,
            )
        return source_keys

    @staticmethod
    def _build_source(*, group, member) -> dict:
        return build_source(layer=member.layer, style_assignment=member.style_assignment)
