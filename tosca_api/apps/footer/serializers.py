from __future__ import annotations

from django.core.files.storage import default_storage
from rest_framework import serializers

from tosca_api.apps.core.editorjs import render_content_media_urls
from tosca_api.apps.footer.models import Footer


def _absolute_url(request, path: str) -> str:
    url = default_storage.url(path)
    return request.build_absolute_uri(url) if request is not None else url


def serialize_published_footer(footer: Footer, request=None) -> dict:
    snapshot = footer.published_snapshot or {}
    return {
        "id": str(footer.pk),
        "name": footer.name,
        "version": footer.publication_version,
        "published_at": footer.published_at,
        "logos": [
            {
                **logo,
                "url": _absolute_url(request, logo["image"]),
            }
            for logo in snapshot.get("logos", [])
        ],
        # Content is served per document so the footer payload stays small
        # however many documents are published.
        "documents": [
            {key: value for key, value in document.items() if key != "content"}
            for document in snapshot.get("documents", [])
        ],
    }


def serialize_published_document(footer: Footer, slug: str, request=None) -> dict | None:
    snapshot = footer.published_snapshot or {}
    document = next(
        (item for item in snapshot.get("documents", []) if item["slug"] == slug),
        None,
    )
    if document is None:
        return None
    return {
        **document,
        "content": render_content_media_urls(document["content"], request),
    }


class PublishedFooterLogoSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    image = serializers.CharField()
    url = serializers.URLField()
    alt_text = serializers.CharField()
    destination_url = serializers.CharField(allow_blank=True)
    display_order = serializers.IntegerField()


class PublishedFooterDocumentLinkSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    slug = serializers.SlugField()
    path = serializers.CharField()
    display_order = serializers.IntegerField()


class PublishedFooterDocumentSerializer(PublishedFooterDocumentLinkSerializer):
    content = serializers.DictField()


class PublishedFooterSerializer(serializers.Serializer):
    """OpenAPI shape for the immutable public footer snapshot."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    version = serializers.IntegerField()
    published_at = serializers.DateTimeField()
    logos = PublishedFooterLogoSerializer(many=True)
    documents = PublishedFooterDocumentLinkSerializer(many=True)
