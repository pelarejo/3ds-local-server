from django.urls import path

from . import views

urlpatterns = [
    path("title-index", views.title_index, name="title-index"),
    path("title/category/<slug:category>/<slug:subcategory>", views.titles_in_category, name="titles-in-category"),
    path("title/<int:id>", views.title_detail, name="title-detail"),
]
