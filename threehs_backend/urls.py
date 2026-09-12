"""URL configuration for the 3HS backend."""
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("admin/", admin.site.urls),
    path("nbapi/", include("apps.catalog.urls")),
    path("nbcontent/", include("apps.content.urls")),
]
