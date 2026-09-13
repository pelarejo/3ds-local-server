from django import forms
from django.contrib import admin

from apps.catalog.models import Title

from .models import ContentArtifact, DownloadGrant
from .paths import resolve_content_path


class ContentArtifactAdminForm(forms.ModelForm):
    """Validate local artifact files and infer their current byte size."""

    class Meta:
        model = ContentArtifact
        fields = ("title", "relative_path", "enabled")

    def clean_relative_path(self):
        relative_path = self.cleaned_data["relative_path"]
        try:
            path = resolve_content_path(relative_path)
        except (OSError, ValueError) as error:
            raise forms.ValidationError(str(error)) from error
        if not path.is_file():
            raise forms.ValidationError(
                "Artifact path must reference an existing regular file."
            )
        try:
            self.instance.byte_size = path.stat().st_size
        except OSError as error:
            raise forms.ValidationError("Artifact file could not be read.") from error
        return relative_path


@admin.register(ContentArtifact)
class ContentArtifactAdmin(admin.ModelAdmin):
    form = ContentArtifactAdminForm
    list_display = ("title", "relative_path", "byte_size", "enabled")
    list_filter = ("enabled",)
    readonly_fields = ("byte_size",)
    search_fields = ("title__name", "title__title_id", "relative_path")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "title":
            kwargs["queryset"] = Title.objects.order_by("name", "pk")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(DownloadGrant)
class DownloadGrantAdmin(admin.ModelAdmin):
    list_display = ("artifact", "user", "expires_at", "created_at")
    list_filter = ("expires_at",)
    readonly_fields = ("token_hash", "created_at")
