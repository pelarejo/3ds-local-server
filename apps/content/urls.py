from django.urls import path

from . import views

urlpatterns = [
    path("<int:id>/request", views.request_download, name="request-download"),
    path("<int:id>", views.download, name="download"),
]
