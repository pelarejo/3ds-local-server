from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse

from apps.catalog.models import Category, Subcategory, Title

from .cia import InvalidCIAError, parse_cia_metadata
from .models import ContentArtifact, DownloadGrant
from .paths import resolve_content_path
from .sync import synchronize_content_root


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


class ContentArtifactImportForm(forms.Form):
    """Collect an artifact upload and either an existing or new title."""

    cia_file = forms.FileField(label="CIA file")
    enabled = forms.BooleanField(required=False, initial=True)
    existing_title = forms.ModelChoiceField(
        queryset=Title.objects.none(), required=False
    )
    new_title_id = forms.CharField(
        max_length=16, required=False, label="New title - Title ID"
    )
    new_name = forms.CharField(
        max_length=255, required=False, label="New title - Name*"
    )
    new_product_code = forms.CharField(
        max_length=32, required=False, label="New title - Product code"
    )
    new_region = forms.CharField(
        max_length=64, required=False, label="New title - Region*"
    )
    new_category = forms.ModelChoiceField(
        queryset=Category.objects.none(), required=False, label="New title - Category*"
    )
    new_subcategory = forms.ModelChoiceField(
        queryset=Subcategory.objects.none(),
        required=False,
        label="New title - Subcategory*",
    )

    def __init__(self, *args, admin_site=admin.site, can_add_title=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_add_title = can_add_title
        existing_title_field = self.fields["existing_title"]
        existing_title_field.queryset = Title.objects.all()
        existing_title_field.widget = AutocompleteSelect(
            ContentArtifact._meta.get_field("title"), admin_site
        )
        existing_title_field.widget.choices = existing_title_field.choices
        self.fields["new_category"].queryset = Category.objects.all()
        subcategory_field = self.fields["new_subcategory"]
        subcategory_field.queryset = Subcategory.objects.all()
        subcategory_field.widget = AutocompleteSelect(
            Title._meta.get_field("subcategory"), admin_site
        )
        subcategory_field.widget.choices = subcategory_field.choices
        if not can_add_title:
            for field_name in (
                "new_title_id",
                "new_name",
                "new_product_code",
                "new_region",
                "new_category",
                "new_subcategory",
            ):
                self.fields[field_name].disabled = True
            self.fields["new_title_id"].help_text = (
                "You do not have permission to add titles."
            )

    def clean_cia_file(self):
        upload = self.cleaned_data["cia_file"]
        basename = Path(upload.name).name
        if not basename or Path(basename).suffix.lower() != ".cia":
            raise forms.ValidationError("Select a file with a .cia extension.")
        try:
            destination = resolve_content_path(basename)
        except (OSError, ValueError) as error:
            raise forms.ValidationError(str(error)) from error
        if destination.exists():
            raise forms.ValidationError(
                "A file with this name already exists in CONTENT_ROOT."
            )
        if ContentArtifact.objects.filter(relative_path=basename).exists():
            raise forms.ValidationError(
                "A content artifact already uses this destination filename."
            )
        upload.name = basename
        return upload

    def clean(self):
        cleaned = super().clean()
        existing_title = cleaned.get("existing_title")
        new_field_names = (
            "new_title_id",
            "new_name",
            "new_product_code",
            "new_region",
            "new_category",
            "new_subcategory",
        )
        submitted_new_title = any(self.data.get(name) for name in new_field_names)
        if submitted_new_title and not self.can_add_title:
            raise forms.ValidationError("You do not have permission to add titles.")
        has_new_title = any(cleaned.get(name) for name in new_field_names)

        if existing_title and has_new_title:
            raise forms.ValidationError(
                "Choose an existing title or enter a new title, not both."
            )
        if not existing_title and not has_new_title:
            raise forms.ValidationError(
                "Choose an existing title or enter the required new title fields."
            )
        if existing_title:
            if ContentArtifact.objects.filter(title=existing_title).exists():
                self.add_error(
                    "existing_title",
                    "The selected title already has a content artifact.",
                )
            return cleaned

        required = {
            "new_name": "Name",
            "new_region": "Region",
            "new_category": "Category",
            "new_subcategory": "Subcategory",
        }
        for field_name, label in required.items():
            if not cleaned.get(field_name):
                self.add_error(field_name, f"{label} is required for a new title.")

        title_id = cleaned.get("new_title_id")
        if not title_id and cleaned.get("cia_file"):
            upload = cleaned["cia_file"]
            try:
                title_id = parse_cia_metadata(upload, upload.size).title_id
            except (InvalidCIAError, OSError) as error:
                self.add_error(
                    "new_title_id",
                    f"Title ID could not be read from the CIA: {error}",
                )
            finally:
                upload.seek(0)
        if title_id:
            title_id = title_id.upper()
            cleaned["new_title_id"] = title_id
            try:
                Title._meta.get_field("title_id").run_validators(title_id)
            except ValidationError as error:
                self.add_error("new_title_id", error)
            if Title.objects.filter(title_id__iexact=title_id).exists():
                self.add_error(
                    "new_title_id", "A title with this title ID already exists."
                )

        category = cleaned.get("new_category")
        subcategory = cleaned.get("new_subcategory")
        if category and subcategory and subcategory.category_id != category.pk:
            self.add_error(
                "new_subcategory",
                "The subcategory must belong to the selected category.",
            )
        return cleaned


@admin.register(ContentArtifact)
class ContentArtifactAdmin(admin.ModelAdmin):
    form = ContentArtifactAdminForm
    list_display = ("title", "relative_path", "byte_size", "enabled")
    list_filter = ("enabled",)
    readonly_fields = ("byte_size",)
    search_fields = ("title__name", "title__title_id", "relative_path")
    change_list_template = "admin/content/contentartifact/change_list.html"

    def manual_import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        can_add_title = request.user.has_perm("catalog.add_title")
        form = ContentArtifactImportForm(
            request.POST or None,
            request.FILES or None,
            admin_site=self.admin_site,
            can_add_title=can_add_title,
        )
        if request.method == "POST" and form.is_valid():
            try:
                artifact = self._save_import(form)
            except (IntegrityError, OSError, ValidationError) as error:
                form.add_error(None, str(error))
            else:
                self.message_user(
                    request,
                    "The content artifact was added successfully.",
                    messages.SUCCESS,
                )
                return redirect(
                    reverse("admin:content_contentartifact_change", args=(artifact.pk,))
                )
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Add content artifact",
            "form": form,
            "media": self.media + form.media,
        }
        return render(
            request, "admin/content/contentartifact/manual_import.html", context
        )

    @staticmethod
    def _save_import(form):
        upload = form.cleaned_data["cia_file"]
        basename = Path(upload.name).name
        destination = resolve_content_path(basename)
        destination_created = False
        try:
            with transaction.atomic():
                if destination.exists():
                    raise ValidationError(
                        "A file with this name already exists in CONTENT_ROOT."
                    )
                if ContentArtifact.objects.filter(relative_path=basename).exists():
                    raise ValidationError(
                        "A content artifact already uses this destination filename."
                    )

                title = form.cleaned_data.get("existing_title")
                if title is None:
                    title_id = form.cleaned_data["new_title_id"]
                    if Title.objects.filter(title_id__iexact=title_id).exists():
                        raise ValidationError(
                            "A title with this title ID already exists."
                        )
                    title = Title(
                        title_id=title_id,
                        name=form.cleaned_data["new_name"],
                        product_code=form.cleaned_data["new_product_code"],
                        region=form.cleaned_data["new_region"],
                        filename=basename,
                        category=form.cleaned_data["new_category"],
                        subcategory=form.cleaned_data["new_subcategory"],
                    )
                    title.full_clean()
                    title.save()
                elif ContentArtifact.objects.filter(title=title).exists():
                    raise ValidationError(
                        "The selected title already has a content artifact."
                    )

                upload.seek(0)
                with destination.open("xb") as output:
                    destination_created = True
                    for chunk in upload.chunks():
                        output.write(chunk)
                artifact = ContentArtifact(
                    title=title,
                    relative_path=basename,
                    byte_size=destination.stat().st_size,
                    enabled=form.cleaned_data["enabled"],
                )
                artifact.full_clean()
                artifact.save()
            return artifact
        except Exception:
            if destination_created:
                destination.unlink(missing_ok=True)
            raise

    def get_urls(self):
        return [
            path(
                "manual-import/",
                self.admin_site.admin_view(self.manual_import_view),
                name="content_contentartifact_manual_import",
            ),
            path(
                "synchronize/",
                self.admin_site.admin_view(self.synchronize_view),
                name="content_contentartifact_synchronize",
            ),
        ] + super().get_urls()

    def synchronize_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            raise PermissionDenied
        result = None
        if request.method == "POST":
            result = synchronize_content_root(force=request.POST.get("mode") == "force")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Synchronize CIA artifacts",
            "result": result,
        }
        return render(
            request, "admin/content/contentartifact/synchronize.html", context
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "title":
            kwargs["queryset"] = Title.objects.order_by("name", "pk")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(DownloadGrant)
class DownloadGrantAdmin(admin.ModelAdmin):
    list_display = ("artifact", "user", "expires_at", "created_at")
    list_filter = ("expires_at",)
    readonly_fields = ("token_hash", "created_at")
