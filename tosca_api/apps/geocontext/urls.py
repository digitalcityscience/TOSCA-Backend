from django.urls import path

from .views import (
    EditorJSImageLibraryView,
    EditorJSImageUploadByFileView,
    EditorJSImageUploadByUrlView,
)

urlpatterns = [
    path(
        "geocontext/editorjs/upload-by-file/",
        EditorJSImageUploadByFileView.as_view(),
        name="geocontext-editorjs-upload-by-file",
    ),
    path(
        "geocontext/editorjs/upload-by-url/",
        EditorJSImageUploadByUrlView.as_view(),
        name="geocontext-editorjs-upload-by-url",
    ),
    path(
        "geocontext/editorjs/media/",
        EditorJSImageLibraryView.as_view(),
        name="geocontext-editorjs-media",
    ),
]
