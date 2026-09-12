from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.authentication.views import header_auth_required
from threehs_backend.nb import (
    ResultNamespace,
    ResultReason,
    index_payload,
    partial_titles_payload,
    result_response,
    title_payload,
    versioned_response,
)

from .models import Category, Title


@header_auth_required(ResultNamespace.INDEX)
def title_index(request: HttpRequest) -> HttpResponse:
    categories = Category.objects.prefetch_related(
        "subcategories", "titles__artifact"
    ).all()
    return versioned_response(
        index_payload(categories, int(timezone.now().timestamp()))
    )


@header_auth_required(ResultNamespace.TITLE)
def titles_in_category(
    request: HttpRequest, category: str, subcategory: str
) -> HttpResponse:
    titles = Title.objects.filter(
        listed=True, category__slug=category, subcategory__slug=subcategory
    ).select_related("category", "subcategory", "artifact")
    if not Category.objects.filter(
        slug=category, subcategories__slug=subcategory
    ).exists():
        return result_response(
            ResultNamespace.SUBCATEGORY,
            ResultReason.NOT_FOUND,
            "Category or subcategory not found.",
            status=404,
        )
    return versioned_response(partial_titles_payload(titles))


@header_auth_required(ResultNamespace.TITLE)
def title_detail(request: HttpRequest, id: int) -> HttpResponse:
    try:
        title = Title.objects.select_related("category", "subcategory", "artifact").get(
            pk=id, listed=True
        )
    except Title.DoesNotExist:
        return result_response(
            ResultNamespace.TITLE,
            ResultReason.NOT_FOUND,
            "Title not found.",
            status=404,
        )
    return versioned_response(title_payload(title))
