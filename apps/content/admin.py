from django.contrib import admin

from .models import ContentArtifact, DownloadGrant


@admin.register(ContentArtifact)
class ContentArtifactAdmin(admin.ModelAdmin):
    list_display = ("title", "relative_path", "byte_size", "enabled")
    list_filter = ("enabled",)
    search_fields = ("title__name", "title__title_id", "relative_path")


@admin.register(DownloadGrant)
class DownloadGrantAdmin(admin.ModelAdmin):
    list_display = ("artifact", "user", "expires_at", "created_at")
    list_filter = ("expires_at",)
    readonly_fields = ("token_hash", "created_at")
