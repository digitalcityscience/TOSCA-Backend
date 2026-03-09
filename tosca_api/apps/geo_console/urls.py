from django.urls import path
from .views import console

urlpatterns = [
    path('', console, name='geo_console'),
]