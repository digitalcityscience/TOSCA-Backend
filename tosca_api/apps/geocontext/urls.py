from django.urls import path

from .views import (
    EditorJSImageLibraryView,
    EditorJSImageUploadByFileView,
    EditorJSImageUploadByUrlView,
)

urlpatterns = [
    path(
        "content/editorjs/upload-by-file/",
        EditorJSImageUploadByFileView.as_view(),
        name="content-editorjs-upload-by-file",
    ),
    path(
        "content/editorjs/upload-by-url/",
        EditorJSImageUploadByUrlView.as_view(),
        name="content-editorjs-upload-by-url",
    ),
    path(
        "content/editorjs/media/",
        EditorJSImageLibraryView.as_view(),
        name="content-editorjs-media",
    ),
    # Temporary aliases for clients deployed before feature-owned content.
    path(
        "geocontext/editorjs/upload-by-file/",
        EditorJSImageUploadByFileView.as_view(),
        name="legacy-geocontext-editorjs-upload-by-file",
    ),
    path(
        "geocontext/editorjs/upload-by-url/",
        EditorJSImageUploadByUrlView.as_view(),
        name="legacy-geocontext-editorjs-upload-by-url",
    ),
    path(
        "geocontext/editorjs/media/",
        EditorJSImageLibraryView.as_view(),
        name="legacy-geocontext-editorjs-media",
    ),
]
