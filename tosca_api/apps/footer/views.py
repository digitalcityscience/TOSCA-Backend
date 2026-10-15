from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.footer.models import Footer
from tosca_api.apps.footer.serializers import (
    PublishedFooterDocumentSerializer,
    PublishedFooterSerializer,
    serialize_published_document,
    serialize_published_footer,
)


def _published_footer() -> Footer | None:
    return Footer.objects.filter(is_published=True).first()


def _cache_headers(footer: Footer) -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "ETag": f'"footer-{footer.pk}-{footer.publication_version}"',
    }


def _not_modified(request, footer: Footer) -> Response | None:
    headers = _cache_headers(footer)
    if request.headers.get("If-None-Match") == headers["ETag"]:
        return Response(status=304, headers=headers)
    return None


class PublishedFooterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: PublishedFooterSerializer})
    def get(self, request):
        footer = _published_footer()
        if footer is None:
            return Response({"footer": None}, headers={"Cache-Control": "no-cache"})
        if response := _not_modified(request, footer):
            return response
        return Response(
            serialize_published_footer(footer, request),
            headers=_cache_headers(footer),
        )


class PublishedFooterDocumentView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: PublishedFooterDocumentSerializer})
    def get(self, request, slug: str):
        footer = get_object_or_404(Footer, is_published=True)
        if response := _not_modified(request, footer):
            return response
        document = serialize_published_document(footer, slug, request)
        if document is None:
            raise NotFound("This document is not available in the published footer.")
        return Response(document, headers=_cache_headers(footer))
