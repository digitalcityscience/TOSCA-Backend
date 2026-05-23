import json
import uuid

from django.db.models import QuerySet

from ...models import LayerStyleAssignment, Style


class StyleQueryService:
    """Read-only query helpers for provider style catalog reads."""

    @classmethod
    def list_styles(cls, *, provider_id=None, workspace_id=None, valid_only=False, include_inactive: bool = False) -> list[dict]:
        styles = cls._catalog_queryset(include_inactive=include_inactive) if valid_only else cls._base_queryset(include_inactive=include_inactive)
        if provider_id is not None:
            styles = styles.filter(geodata_engine_id=provider_id)
        if workspace_id is not None:
            styles = styles.filter(workspace_id=workspace_id)
        return [cls._serialize_style(style) for style in styles]

    @classmethod
    def get_style_detail(cls, *, style_id, include_inactive: bool = False) -> dict:
        return cls._serialize_style(cls._base_queryset(include_inactive=include_inactive).get(id=style_id))

    @classmethod
    def get_style_content(cls, *, style_id, include_inactive: bool = False) -> str | dict:
        style = cls._catalog_queryset(include_inactive=include_inactive).get(id=style_id)
        if style.format == "mbstyle":
            return json.loads(style.file_content)
        return style.file_content

    @classmethod
    def get_layer_default_style(cls, *, layer_id, include_inactive: bool = False) -> dict | None:
        assignment = (
            LayerStyleAssignment.objects.select_related("style__geodata_engine", "style__workspace")
            .filter(layer_id=layer_id, role="default", is_active=True)
            .first()
        )
        if assignment is None:
            return None
        if not include_inactive and not assignment.style.geodata_engine.is_active:
            return None
        return cls._serialize_style(assignment.style)

    @classmethod
    def list_styles_for_layer_assignment(cls, *, layer_id, include_inactive: bool = False) -> list[dict]:
        return [
            cls._serialize_style(style)
            for style in cls._catalog_queryset(include_inactive=include_inactive).exclude(remote_state="DELETED")
        ]

    @classmethod
    def resolve_style_reference(
        cls,
        *,
        style_ref: str,
        provider_id=None,
        include_inactive: bool = False,
    ) -> Style:
        normalized_ref = (style_ref or "").strip()
        queryset = cls._catalog_queryset(include_inactive=include_inactive)
        if provider_id is not None:
            queryset = queryset.filter(geodata_engine_id=provider_id)
        try:
            style_uuid = uuid.UUID(str(normalized_ref))
        except (TypeError, ValueError):
            style_uuid = None
        if style_uuid is not None:
            return queryset.get(id=style_uuid)
        style = queryset.filter(name=normalized_ref).order_by(
            "-geodata_engine__is_default",
            "-updated_at",
            "-created_at",
        ).first()
        if style is None:
            raise Style.DoesNotExist(f"Style matching query does not exist for reference '{normalized_ref}'.")
        return style

    @classmethod
    def _base_queryset(cls, *, include_inactive: bool = False) -> QuerySet[Style]:
        queryset = Style.objects.select_related(
            "geodata_engine",
            "workspace",
        ).order_by("geodata_engine__name", "workspace__name", "name")
        if not include_inactive:
            queryset = queryset.filter(geodata_engine__is_active=True)
        return queryset

    @classmethod
    def _catalog_queryset(cls, *, include_inactive: bool = False) -> QuerySet[Style]:
        return cls._base_queryset(include_inactive=include_inactive).filter(
            validation_state="VALID",
        ).exclude(
            remote_state="DELETED",
        ).exclude(
            file_content="",
        )

    @classmethod
    def _serialize_style(cls, style: Style) -> dict:
        return {
            "id": str(style.id),
            "name": style.name,
            "qualified_name": style.qualified_name,
            "title": style.title,
            "description": style.description,
            "format": style.format,
            "scope": "global" if style.is_global else "workspace",
            "provider": {
                "id": str(style.geodata_engine_id),
                "name": style.geodata_engine.name,
                "engine_type": style.geodata_engine.engine_type,
            },
            "workspace": None if style.is_global else {
                "id": str(style.workspace_id),
                "name": style.workspace.name,
            },
            "validation_state": style.validation_state,
            "remote_state": style.remote_state,
            "content_hash": style.content_hash,
        }
