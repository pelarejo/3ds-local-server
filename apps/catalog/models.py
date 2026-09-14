from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

U8_VALIDATORS = [MinValueValidator(0), MaxValueValidator(255)]
U16_VALIDATORS = [MinValueValidator(0), MaxValueValidator(65535)]
TITLE_ID_VALIDATOR = RegexValidator(
    r"\A[0-9A-Fa-f]{16}\Z", "Enter exactly 16 hexadecimal digits."
)


class CatalogEntry(models.Model):
    """Provider-sourced metadata staged for review before catalog publication."""

    source = models.CharField(max_length=64)
    external_id = models.CharField(max_length=255)
    title_id = models.CharField(max_length=16, validators=[TITLE_ID_VALIDATOR])
    name = models.CharField(max_length=255)
    product_code = models.CharField(max_length=64, blank=True)
    region = models.CharField(max_length=64, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source", "external_id")
        constraints = [
            models.UniqueConstraint(
                fields=("source", "external_id"),
                name="unique_catalog_entry_source_external_id",
            )
        ]
        verbose_name_plural = "catalog entries"

    def __str__(self) -> str:
        return f"{self.source}: {self.name}"


class Category(models.Model):
    """Top-level storefront section identified by a stable protocol ID."""

    protocol_id = models.PositiveSmallIntegerField(
        unique=True, validators=U8_VALIDATORS
    )
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    priority = models.PositiveSmallIntegerField(default=0, validators=U8_VALIDATORS)

    class Meta:
        ordering = ("priority", "protocol_id")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.display_name


class Subcategory(models.Model):
    """Region or grouping nested beneath a catalog category."""

    category = models.ForeignKey(
        Category, related_name="subcategories", on_delete=models.CASCADE
    )
    protocol_id = models.PositiveSmallIntegerField(validators=U8_VALIDATORS)
    slug = models.SlugField()
    display_name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("ordering", "protocol_id")
        constraints = [
            models.UniqueConstraint(
                fields=("category", "protocol_id"),
                name="unique_subcategory_protocol_id",
            ),
            models.UniqueConstraint(
                fields=("category", "slug"), name="unique_subcategory_slug"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category.display_name} / {self.display_name}"


class Title(models.Model):
    """Catalog metadata for one downloadable Nintendo 3DS title."""

    id = models.AutoField(primary_key=True)
    title_id = models.CharField(
        max_length=16,
        unique=True,
        validators=[TITLE_ID_VALIDATOR],
        help_text="16 hexadecimal digit Nintendo title ID",
    )
    name = models.CharField(max_length=255)
    alternative_name = models.CharField(max_length=255, blank=True)
    alternative_names = models.JSONField(default=list, blank=True)
    preferred_alternative_index = models.PositiveIntegerField(default=0)
    product_code = models.CharField(max_length=32, blank=True)
    region = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    filename = models.CharField(max_length=255)
    version = models.PositiveSmallIntegerField(default=0, validators=U16_VALIDATORS)
    content_type = models.PositiveSmallIntegerField(default=0, validators=U8_VALIDATORS)
    flags = models.PositiveBigIntegerField(default=0)
    download_count = models.PositiveBigIntegerField(default=0)
    listed = models.BooleanField(default=True)
    seed = models.BinaryField(max_length=16, default=bytes, blank=True)
    file_checksum = models.BinaryField(max_length=32, default=bytes, blank=True)
    category = models.ForeignKey(
        Category, related_name="titles", on_delete=models.PROTECT
    )
    subcategory = models.ForeignKey(
        Subcategory, related_name="titles", on_delete=models.PROTECT
    )
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if (
            self.subcategory_id
            and self.category_id
            and self.subcategory.category_id != self.category_id
        ):
            raise ValidationError(
                {"subcategory": "The subcategory must belong to the selected category."}
            )
        names = self.alternative_names or []
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name for name in names
        ):
            raise ValidationError(
                {
                    "alternative_names": (
                        "Alternative names must be a list of non-empty strings."
                    )
                }
            )
        if names and self.preferred_alternative_index >= len(names):
            raise ValidationError(
                {
                    "preferred_alternative_index": (
                        "Index is outside the alternative names list."
                    )
                }
            )

    @property
    def title_id_int(self) -> int:
        return int(self.title_id, 16)

    @property
    def artifact_size(self) -> int:
        artifact = getattr(self, "artifact", None)
        return artifact.byte_size if artifact else 0
