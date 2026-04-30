from django.urls import path

from .views import ImageDerivativeView

urlpatterns = [
    path("media/derivative/", ImageDerivativeView.as_view(), name="media-image-derivative"),
]
