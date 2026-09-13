from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib import admin
from django.test import TestCase

from apps.catalog.models import Category, Subcategory, Title
from apps.content.admin import ContentArtifactAdmin, ContentArtifactAdminForm
from apps.content.models import ContentArtifact
from seeds.catalog import seed_catalog_taxonomy


class ContentArtifactAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_catalog_taxonomy()
        category = Category.objects.get(slug="games")
        subcategory = Subcategory.objects.get(category=category, slug="north-america")
        for number, name in ((1, "Zulu"), (2, "Alpha"), (3, "Alpha")):
            Title.objects.create(
                title_id=f"{number:016X}",
                name=name,
                region="USA",
                filename=f"title-{number}.cia",
                category=category,
                subcategory=subcategory,
            )

    def setUp(self):
        self.tempdir = TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)
        self.content_root = Path(self.tempdir.name) / "content"
        self.content_root.mkdir()
        self.settings_override = self.settings(CONTENT_ROOT=self.content_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.title = Title.objects.order_by("pk").first()

    def form(self, relative_path: str, *, instance=None):
        return ContentArtifactAdminForm(
            data={
                "title": self.title.pk,
                "relative_path": relative_path,
                "enabled": True,
            },
            instance=instance,
        )

    def test_title_choices_are_ordered_by_name_then_primary_key(self):
        model_admin = ContentArtifactAdmin(ContentArtifact, admin.site)
        title_field = ContentArtifact._meta.get_field("title")
        form_field = model_admin.formfield_for_foreignkey(title_field, request=None)

        self.assertEqual(form_field.queryset.query.order_by, ("name", "pk"))
        self.assertEqual(
            list(form_field.queryset.values_list("name", flat=True)),
            ["Alpha", "Alpha", "Zulu"],
        )

    def test_create_infers_byte_size_from_existing_file(self):
        (self.content_root / "game.cia").write_bytes(b"1234567")
        form = self.form("game.cia")

        self.assertTrue(form.is_valid(), form.errors)
        artifact = form.save()
        self.assertEqual(artifact.byte_size, 7)

    def test_edit_refreshes_byte_size_from_current_file(self):
        path = self.content_root / "game.cia"
        path.write_bytes(b"old")
        artifact = ContentArtifact.objects.create(
            title=self.title,
            relative_path="game.cia",
            byte_size=3,
        )
        path.write_bytes(b"new-content")
        form = self.form("game.cia", instance=artifact)

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        artifact.refresh_from_db()
        self.assertEqual(artifact.byte_size, 11)

    def test_missing_file_is_rejected_on_relative_path_field(self):
        form = self.form("missing.cia")
        self.assertFalse(form.is_valid())
        self.assertIn("relative_path", form.errors)
        self.assertIn("existing regular file", form.errors["relative_path"][0])

    def test_directory_is_rejected_on_relative_path_field(self):
        (self.content_root / "folder").mkdir()
        form = self.form("folder")
        self.assertFalse(form.is_valid())
        self.assertIn("existing regular file", form.errors["relative_path"][0])

    def test_absolute_traversal_and_escaping_paths_are_rejected(self):
        inside = self.content_root / "inside.cia"
        inside.write_bytes(b"inside")
        outside = Path(self.tempdir.name) / "outside.cia"
        outside.write_bytes(b"outside")
        paths = (str(inside), "nested/../inside.cia", "../outside.cia")

        for relative_path in paths:
            with self.subTest(relative_path=relative_path):
                form = self.form(relative_path)
                self.assertFalse(form.is_valid())
                self.assertIn("relative_path", form.errors)
