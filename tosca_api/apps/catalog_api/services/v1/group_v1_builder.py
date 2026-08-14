from copy import deepcopy
from urllib.parse import urlencode

from django.urls import reverse

from tosca_api.apps.geodata_providers.models import Store


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
            style_id = str(style.id)
            if style_id in styles:
                continue
            styles[style_id] = {
                "id": style_id,
                "name": style.name,
                "title": style.title or style.name,
                "format": style.format,
                "content_hash": style.content_hash,
                "sprite_id": None if style.sprite_asset_id is None else str(style.sprite_asset_id),
                "href": request.build_absolute_uri(
                    reverse(
                        "catalog-v1-provider-style-detail",
                        kwargs={
                            "provider_id": provider_id,
                            "style_ref": style.id,
                        },
                    )
                ),
            }
        return styles

    @classmethod
    def _build_sprites(cls, *, request, members, provider_id) -> dict:
        sprites: dict[str, dict] = {}
        for member in members:
            sprite_asset = member.style_assignment.style.sprite_asset
            sprite_id = None if sprite_asset is None else str(sprite_asset.id)
            if sprite_id is None or sprite_id in sprites:
                continue
            sprites[sprite_id] = {
                "id": sprite_id,
                "url": request.build_absolute_uri(
                    reverse(
                        "catalog-v1-provider-sprite-versioned-stem",
                        kwargs={
                            "provider_id": provider_id,
                            "sprite_id": sprite_id,
                            "content_hash": sprite_asset.content_hash,
                        },
                    )
                ),
                "content_hash": sprite_asset.content_hash,
            }
        return sprites

    @classmethod
    def _build_render_layers(cls, *, members, source_keys) -> list[dict]:
        render_layers: list[dict] = []
        for member in members:
            assignment = member.style_assignment
            if member.layer.store.store_type == Store.StoreType.GEOTIFF:
                render_layers.append(
                    {
                        "id": f"member-{member.id}",
                        "type": "raster",
                        "source": source_keys[member.id],
                        "metadata": {
                            "tosca:member-id": str(member.id),
                            "tosca:style-id": str(assignment.style_id),
                        },
                    }
                )
                continue
            for style_layer in assignment.selected_mbstyle_layers(
                member.effective_style_layer_ids
            ):
                render_layer = deepcopy(style_layer)
                render_layer["source"] = source_keys[member.id]
                render_layer["source-layer"] = member.layer.name
                metadata = render_layer.get("metadata")
                metadata = dict(metadata) if isinstance(metadata, dict) else {}
                metadata.update(
                    {
                        "tosca:member-id": str(member.id),
                        "tosca:style-id": str(assignment.style_id),
                    }
                )
                render_layer["metadata"] = metadata
                render_layers.append(render_layer)
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
        base_url = group.workspace.geodata_engine.public_url.rstrip("/")
        workspace = group.workspace.name
        layer_name = member.layer.name
        if member.layer.store.store_type != Store.StoreType.GEOTIFF:
            params = urlencode(
                {
                    "REQUEST": "GetTile",
                    "SERVICE": "WMTS",
                    "VERSION": "1.0.0",
                    "LAYER": f"{workspace}:{layer_name}",
                    "STYLE": "",
                    "TILEMATRIX": "EPSG:900913:{z}",
                    "TILEMATRIXSET": "EPSG:900913",
                    "TILECOL": "{x}",
                    "TILEROW": "{y}",
                    "FORMAT": "application/vnd.mapbox-vector-tile",
                }
            )
            for token in ("z", "x", "y"):
                params = params.replace(f"%7B{token}%7D", f"{{{token}}}")
            return {
                "type": "vector",
                "tiles": [f"{base_url}/gwc/service/wmts?{params}"],
            }

        params = urlencode(
            {
                "REQUEST": "GetMap",
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "LAYERS": f"{workspace}:{layer_name}",
                "STYLES": member.style_assignment.style.name,
                "CRS": "EPSG:3857",
                "WIDTH": "256",
                "HEIGHT": "256",
                "transparent": "true",
                "format": "image/png",
                "TILED": "true",
            }
        )
        return {
            "type": "raster",
            "tiles": [f"{base_url}/wms?{params}&BBOX={{bbox-epsg-3857}}"],
            "tileSize": 256,
        }
