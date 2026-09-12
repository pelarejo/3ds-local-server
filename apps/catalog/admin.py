from django.contrib import admin

from .models import Category, Subcategory, Title


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


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "title_id", "category", "subcategory", "region", "listed")
    list_filter = ("listed", "category", "subcategory", "region")
    search_fields = ("name", "title_id", "product_code")
