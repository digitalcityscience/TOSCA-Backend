from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tosca_api.apps.footer.models import (
    SOFT_LIMIT_DOCUMENTS,
    SOFT_LIMIT_LOGOS,
    Footer,
)


def _has_meaningful_content(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_meaningful_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_content(item) for item in value)
    return value not in (None, False, 0)


def footer_overflow_warnings(footer: Footer) -> list[str]:
    """Non-blocking hints for editors when the footer may not fit on one row."""
    warnings = []
    logo_count = footer.logos.filter(is_active=True).count()
    document_count = footer.documents.filter(is_active=True).count()
    if logo_count > SOFT_LIMIT_LOGOS:
        warnings.append(
            f"{logo_count} active logos: on smaller screens they will wrap onto "
            "several rows. Order them by importance."
        )
    if document_count > SOFT_LIMIT_DOCUMENTS:
        warnings.append(
            f"{document_count} active documents: the last ones by display order may "
            "move into a “More” menu on smaller screens."
        )
    return warnings


def build_footer_snapshot(footer: Footer) -> dict:
    logos = list(footer.logos.filter(is_active=True).order_by("display_order", "created_at"))
    documents = list(
        footer.documents.filter(is_active=True).order_by("display_order", "created_at")
    )
    for logo in logos:
        logo.full_clean()
    for document in documents:
        document.full_clean()
        if not _has_meaningful_content(document.content.get("blocks", [])):
            raise ValidationError(
                {"documents": f"Active document “{document.title}” has no content."}
            )

    return {
        "logos": [
            {
                "id": str(logo.pk),
                "image": logo.image.name,
                "alt_text": logo.alt_text,
                "destination_url": logo.destination_url,
                "display_order": logo.display_order,
            }
            for logo in logos
        ],
        "documents": [
            {
                "id": str(document.pk),
                "title": document.title,
                "slug": document.slug,
                "path": f"/{document.slug}",
                "content": document.content,
                "display_order": document.display_order,
            }
            for document in documents
        ],
    }


@transaction.atomic
def publish_footer(footer_id, *, user=None) -> Footer:
    footer = Footer.objects.select_for_update().get(pk=footer_id)
    snapshot = build_footer_snapshot(footer)
    now = timezone.now()

    Footer.objects.select_for_update().filter(is_published=True).exclude(pk=footer.pk).update(
        is_published=False,
        published_at=None,
        published_by=None,
    )
    footer.is_published = True
    footer.publication_version += 1
    footer.published_snapshot = snapshot
    footer.published_at = now
    footer.published_by = user if getattr(user, "is_authenticated", False) else None
    footer.save(
        update_fields=[
            "is_published",
            "publication_version",
            "published_snapshot",
            "published_at",
            "published_by",
            "updated_at",
        ]
    )
    return footer
