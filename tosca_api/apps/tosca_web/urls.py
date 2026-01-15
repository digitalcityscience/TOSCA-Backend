from django.urls import include, path

from .layers.urls import urlpatterns as layer_urls
from .participation.urls import urlpatterns as participation_urls

app_name = "tosca_web"

urlpatterns = [
    path("layers/", include((layer_urls, "layers"), namespace="layers")),
    path("participation/", include((participation_urls, "participation"), namespace="participation")),
]
