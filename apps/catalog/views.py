import secrets

from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.authentication.views import header_auth_required
from threels_server.nb import (
    ResultNamespace,
    ResultReason,
    index_payload,
    partial_titles_payload,
    result_response,
    title_payload,
    versioned_response,
)

from .models import Category, Title


def _title_error(reason: ResultReason, message: str, *, status: int) -> HttpResponse:
    return result_response(ResultNamespace.TITLE, reason, message, status=status)


@header_auth_required(ResultNamespace.INDEX)
def title_index(request: HttpRequest) -> HttpResponse:
    categories = Category.objects.prefetch_related(
        "subcategories",
        Prefetch(
            "titles",
            queryset=Title.objects.filter(
                listed=True, artifact__enabled=True
            ).select_related("artifact"),
            to_attr="eligible_titles",
        ),
    ).all()
    return versioned_response(
        index_payload(categories, int(timezone.now().timestamp()))
    )


@header_auth_required(ResultNamespace.TITLE)
def titles_in_category(
    request: HttpRequest, category: str, subcategory: str
) -> HttpResponse:
    titles = Title.objects.filter(
        listed=True,
        artifact__enabled=True,
        category__slug=category,
        subcategory__slug=subcategory,
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
def random_title(request: HttpRequest) -> HttpResponse:
    if request.method != "GET":
        response = _title_error(
            ResultReason.INVALID_OPERATION,
            "Only GET is supported.",
            status=405,
        )
        response["Allow"] = "GET"
        return response
    if request.GET or request.body:
        return _title_error(
            ResultReason.INVALID_ARGUMENT,
            "Query parameters and request body are not supported.",
            status=400,
        )

    titles = (
        Title.objects.filter(listed=True, artifact__enabled=True)
        .select_related("category", "subcategory", "artifact")
        .order_by("pk")
    )
    count = titles.count()
    if not count:
        return _title_error(ResultReason.NOT_FOUND, "Title not found.", status=404)
    try:
        title = titles[secrets.randbelow(count)]
    except IndexError:
        return _title_error(ResultReason.NOT_FOUND, "Title not found.", status=404)

    response = versioned_response(title_payload(title))
    response["Cache-Control"] = "no-store"
    return response


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
