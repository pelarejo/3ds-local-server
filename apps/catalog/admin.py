from django.contrib import admin

from .models import CatalogEntry, Category, Subcategory, Title


@admin.register(CatalogEntry)
class CatalogEntryAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "external_id",
        "name",
        "title_id",
        "product_code",
        "region",
        "updated_at",
    )
    list_filter = ("source", "region")
    search_fields = ("external_id", "name", "title_id", "product_code", "publisher")
    readonly_fields = ("created_at", "updated_at")


class SubcategoryInline(admin.TabularInline):
    model = Subcategory
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slug", "protocol_id", "priority")
    inlines = (SubcategoryInline,)


@admin.register(Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ("display_name", "category", "slug", "protocol_id", "ordering")
    list_filter = ("category",)
    search_fields = ("display_name", "slug", "category__display_name")
    ordering = (
        "category__priority",
        "category__protocol_id",
        "display_name",
        "pk",
    )


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "title_id",
        "category",
        "subcategory",
        "region",
        "listed",
    )
    list_filter = ("listed", "category", "subcategory", "region")
    search_fields = ("name", "title_id", "product_code")
