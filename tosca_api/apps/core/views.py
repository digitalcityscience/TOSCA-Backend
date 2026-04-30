from __future__ import annotations

from django.core.files.storage import default_storage
from django.http import FileResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.core.image_derivatives import (
    DerivativeFormatUnavailable,
    DerivativeSourceMissing,
    UnsupportedDerivativeFormat,
    UnsupportedDerivativeSource,
    UnsupportedDerivativeWidth,
    generate_derivative,
)


class ImageDerivativeView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=["media"],
        operation_id="media_image_derivative",
        summary="Serve an optimized image derivative",
        parameters=[
            OpenApiParameter(
                name="src",
                description="Django storage path for the original image.",
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="fmt",
                description="Derivative format: webp or avif.",
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="w",
                description="Optional width: 480, 960, 1440, or 1920.",
                required=False,
                type=int,
            ),
        ],
        responses={
            200: OpenApiResponse(description="Optimized image body."),
            400: OpenApiResponse(description="Unsupported format or width."),
            404: OpenApiResponse(description="Original image not found."),
            501: OpenApiResponse(description="Requested encoder is unavailable."),
        },
    )
    def get(self, request, *args, **kwargs):
        source = request.query_params.get("src")
        fmt = request.query_params.get("fmt")
        width = request.query_params.get("w")

        if not source or not fmt:
            return Response(
                {"detail": "Both 'src' and 'fmt' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = generate_derivative(source, fmt=fmt, width=width or None)
        except (
            UnsupportedDerivativeFormat,
            UnsupportedDerivativeSource,
            UnsupportedDerivativeWidth,
        ) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DerivativeSourceMissing:
            return Response(
                {"detail": "Original image not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DerivativeFormatUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)

        response = FileResponse(
            default_storage.open(result.storage_path, "rb"),
            content_type=result.content_type,
        )
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
