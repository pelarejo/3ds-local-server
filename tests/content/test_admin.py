import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from pyctr.type.tmd import TitleMetadataReader

from apps.authentication.models import User
from apps.catalog.models import CatalogEntry, Category, Subcategory, Title
from apps.content.admin import (
    ContentArtifactAdmin,
    ContentArtifactAdminForm,
)
from apps.content.cia import parse_cia_metadata
from apps.content.models import ContentArtifact
from apps.content.sync import _inferred_region


def create_test_taxonomy():
    category = Category.objects.create(
        protocol_id=1, slug="games", display_name="Games"
    )
    subcategory = Subcategory.objects.create(
        category=category,
        protocol_id=1,
        slug="north-america",
        display_name="North America",
    )
    Subcategory.objects.create(
        category=category,
        protocol_id=2,
        slug="europe",
        display_name="Europe",
    )
    Subcategory.objects.create(
        category=category,
        protocol_id=18,
        slug="uncategorised",
        display_name="Uncategorised",
    )
    return category, subcategory


def cia_bytes(title_id):
    tmd_offset = 0x2040
    signature_block_size = 0x140
    tmd_header_size = 0xC4
    content_info_size = 0x900
    tmd_size = signature_block_size + tmd_header_size + content_info_size
    data = bytearray(tmd_offset + tmd_size)
    data[0:4] = (0x2020).to_bytes(4, "little")
    data[0x10:0x14] = tmd_size.to_bytes(4, "little")
    data[tmd_offset : tmd_offset + 4] = (0x00010001).to_bytes(4, "big")
    title_offset = tmd_offset + signature_block_size + 0x4C
    data[title_offset : title_offset + 8] = bytes.fromhex(title_id)
    hash_offset = tmd_offset + signature_block_size + 0xA4
    content_info = bytes(content_info_size)
    data[hash_offset : hash_offset + 32] = hashlib.sha256(content_info).digest()
    return bytes(data)


class ContentArtifactAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category, subcategory = create_test_taxonomy()
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


class ContentArtifactManualImportAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category, cls.subcategory = create_test_taxonomy()
        cls.admin_user = User.objects.create_superuser(
            username="import-admin", password="password", email="import@example.com"
        )

    def setUp(self):
        self.tempdir = TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)
        self.content_root = Path(self.tempdir.name) / "content"
        self.content_root.mkdir()
        self.settings_override = self.settings(CONTENT_ROOT=self.content_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.force_login(self.admin_user)
        self.url = reverse("admin:content_contentartifact_manual_import")

    @staticmethod
    def upload(name="manual.cia", content=b"not a parsed CIA"):
        return SimpleUploadedFile(
            name, content, content_type="application/octet-stream"
        )

    def new_title_data(self, **overrides):
        data = {
            "new_title_id": "0004000000ABCDEF",
            "new_name": "Manual title",
            "new_product_code": "CTR-P-MANUAL",
            "new_region": "USA",
            "new_category": self.category.pk,
            "new_subcategory": self.subcategory.pk,
            "enabled": "on",
        }
        data.update(overrides)
        return data

    def create_title(self, **overrides):
        values = {
            "title_id": "0004000000123456",
            "name": "Existing title",
            "region": "USA",
            "filename": "old-name.cia",
            "category": self.category,
            "subcategory": self.subcategory,
        }
        values.update(overrides)
        return Title.objects.create(**values)

    def test_add_page_uses_upload_and_manual_title_fields(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'name="existing_title"')
        self.assertContains(response, 'name="new_title_id"')
        self.assertNotContains(response, 'name="relative_path"')

    def test_standard_add_page_keeps_path_based_model_form(self):
        response = self.client.get(reverse("admin:content_contentartifact_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="relative_path"')
        self.assertContains(response, 'name="title"')
        self.assertNotContains(response, 'name="cia_file"')
        self.assertNotContains(response, 'name="new_title_id"')

    def test_changelist_links_manual_import(self):
        response = self.client.get(reverse("admin:content_contentartifact_changelist"))

        self.assertContains(response, self.url)
        self.assertContains(response, "Manual import")

    def test_existing_title_uses_admin_autocomplete_and_search_endpoint(self):
        matching = self.create_title(name="Needle Game")
        self.create_title(title_id="0004000000123457", name="Unrelated")

        page = self.client.get(self.url)
        field = page.context["form"].fields["existing_title"]
        self.assertEqual(field.widget.__class__.__name__, "AutocompleteSelect")
        self.assertContains(page, "admin-autocomplete")
        self.assertContains(page, "autocomplete.")

        response = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "content",
                "model_name": "contentartifact",
                "field_name": "title",
                "term": "Needle",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [{"id": str(matching.pk), "text": "Needle Game"}],
        )

    def test_subcategory_uses_native_admin_autocomplete_search(self):
        other_category = Category.objects.create(
            protocol_id=9, slug="other-category", display_name="Other"
        )
        matching = Subcategory.objects.create(
            category=other_category,
            protocol_id=1,
            slug="alpha-elsewhere",
            display_name="Alpha Elsewhere",
        )

        page = self.client.get(self.url)
        field = page.context["form"].fields["new_subcategory"]
        self.assertEqual(field.widget.__class__.__name__, "AutocompleteSelect")
        self.assertContains(page, "admin-autocomplete")
        self.assertContains(page, "autocomplete.")
        self.assertNotContains(page, "subcategory_autocomplete.js")

        response = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "catalog",
                "model_name": "title",
                "field_name": "subcategory",
                "term": "alpha-elsewhere",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "id": str(matching.pk),
                    "text": "Other / Alpha Elsewhere",
                }
            ],
        )

    def test_subcategory_autocomplete_orders_by_category_then_name_and_pk(self):
        later_category = Category.objects.create(
            protocol_id=8,
            slug="later-category",
            display_name="Later",
            priority=20,
        )
        earlier_category = Category.objects.create(
            protocol_id=9,
            slug="earlier-category",
            display_name="Earlier",
            priority=10,
        )
        later = Subcategory.objects.create(
            category=later_category,
            protocol_id=1,
            slug="order-later",
            display_name="Order Alpha",
        )
        earlier_zulu = Subcategory.objects.create(
            category=earlier_category,
            protocol_id=1,
            slug="order-zulu",
            display_name="Order Zulu",
        )
        earlier_alpha_first = Subcategory.objects.create(
            category=earlier_category,
            protocol_id=2,
            slug="order-alpha-first",
            display_name="Order Alpha",
        )
        earlier_alpha_second = Subcategory.objects.create(
            category=earlier_category,
            protocol_id=3,
            slug="order-alpha-second",
            display_name="Order Alpha",
        )

        response = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "catalog",
                "model_name": "title",
                "field_name": "subcategory",
                "term": "Order",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["id"] for result in response.json()["results"]],
            [
                str(earlier_alpha_first.pk),
                str(earlier_alpha_second.pk),
                str(earlier_zulu.pk),
                str(later.pk),
            ],
        )

    def test_forged_category_subcategory_mismatch_is_rejected(self):
        other_category = Category.objects.create(
            protocol_id=9, slug="other-category", display_name="Other"
        )
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(new_category=other_category.pk),
                "cia_file": self.upload(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "subcategory must belong to the selected category"
        )
        self.assertFalse(Title.objects.exists())

    def test_new_title_route_requires_title_add_permission(self):
        restricted_user = User.objects.create_user(
            username="artifact-only", password="password", is_staff=True
        )
        restricted_user.user_permissions.add(
            Permission.objects.get(codename="add_contentartifact")
        )
        self.client.force_login(restricted_user)

        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["form"].fields["new_title_id"].disabled)
        self.assertContains(page, "You do not have permission to add titles")

        response = self.client.post(
            self.url,
            {**self.new_title_data(), "cia_file": self.upload()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You do not have permission to add titles")
        self.assertFalse(Title.objects.exists())
        self.assertFalse(ContentArtifact.objects.exists())
        self.assertFalse((self.content_root / "manual.cia").exists())

        existing_title = self.create_title()
        response = self.client.post(
            self.url,
            {
                "existing_title": existing_title.pk,
                "cia_file": self.upload("existing.cia"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ContentArtifact.objects.filter(title=existing_title).exists())

    def test_upload_creates_new_title_artifact_and_file_without_parsing(self):
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(),
                "cia_file": self.upload(content=b"plain arbitrary bytes"),
            },
        )

        self.assertEqual(response.status_code, 302)
        title = Title.objects.get(title_id="0004000000ABCDEF")
        artifact = ContentArtifact.objects.get(title=title)
        self.assertEqual(title.filename, "manual.cia")
        self.assertEqual(artifact.relative_path, "manual.cia")
        self.assertEqual(artifact.byte_size, 21)
        self.assertTrue(artifact.enabled)
        self.assertEqual(
            (self.content_root / "manual.cia").read_bytes(), b"plain arbitrary bytes"
        )

    def test_blank_title_id_is_parsed_and_complete_upload_is_copied(self):
        parsed_title_id = "000400000012B900"
        content = cia_bytes(parsed_title_id)
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(new_title_id=""),
                "cia_file": self.upload(content=content),
            },
        )

        self.assertEqual(response.status_code, 302)
        title = Title.objects.get(title_id=parsed_title_id)
        self.assertEqual(title.artifact.byte_size, len(content))
        self.assertEqual((self.content_root / "manual.cia").read_bytes(), content)

    def test_explicit_title_id_bypasses_cia_parser(self):
        with patch(
            "apps.content.admin.parse_cia_metadata",
            side_effect=AssertionError("parser must not be called"),
        ) as parser:
            response = self.client.post(
                self.url,
                {
                    **self.new_title_data(new_title_id="abcdef0123456789"),
                    "cia_file": self.upload(content=b"not parseable"),
                },
            )

        self.assertEqual(response.status_code, 302)
        parser.assert_not_called()
        self.assertTrue(Title.objects.filter(title_id="ABCDEF0123456789").exists())

    def test_existing_title_route_bypasses_cia_parser(self):
        title = self.create_title()
        with patch(
            "apps.content.admin.parse_cia_metadata",
            side_effect=AssertionError("parser must not be called"),
        ) as parser:
            response = self.client.post(
                self.url,
                {"existing_title": title.pk, "cia_file": self.upload()},
            )

        self.assertEqual(response.status_code, 302)
        parser.assert_not_called()

    def test_parse_failure_is_a_title_id_error_without_side_effects(self):
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(new_title_id=""),
                "cia_file": self.upload(content=b"invalid CIA"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("new_title_id", response.context["form"].errors)
        self.assertContains(response, "Title ID could not be read from the CIA")
        self.assertFalse(Title.objects.exists())
        self.assertFalse(ContentArtifact.objects.exists())
        self.assertFalse((self.content_root / "manual.cia").exists())

    def test_parsed_duplicate_title_id_is_rejected_case_insensitively(self):
        title_id = "ABCDEF0123456789"
        self.create_title(title_id=title_id.lower())
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(new_title_id=""),
                "cia_file": self.upload(content=cia_bytes(title_id)),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A title with this title ID already exists")
        self.assertEqual(Title.objects.count(), 1)
        self.assertFalse(ContentArtifact.objects.exists())
        self.assertFalse((self.content_root / "manual.cia").exists())

    def test_upload_reuses_existing_title_without_changing_it(self):
        title = self.create_title()
        response = self.client.post(
            self.url,
            {"existing_title": title.pk, "enabled": "on", "cia_file": self.upload()},
        )

        self.assertEqual(response.status_code, 302)
        title.refresh_from_db()
        self.assertEqual(title.filename, "old-name.cia")
        self.assertEqual(title.artifact.relative_path, "manual.cia")

    def test_requires_exactly_one_title_route(self):
        title = self.create_title()
        for data, message in (
            (
                {
                    **self.new_title_data(),
                    "existing_title": title.pk,
                    "cia_file": self.upload(),
                },
                "not both",
            ),
            ({"cia_file": self.upload()}, "Choose an existing title"),
        ):
            with self.subTest(message=message):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)

    def test_duplicate_manual_title_id_is_case_insensitively_rejected(self):
        self.create_title(title_id="ABCDEF0123456789")
        response = self.client.post(
            self.url,
            {
                **self.new_title_data(new_title_id="abcdef0123456789"),
                "cia_file": self.upload(),
            },
        )

        self.assertContains(response, "A title with this title ID already exists")
        self.assertFalse((self.content_root / "manual.cia").exists())

    def test_rejects_file_and_artifact_collisions(self):
        existing_path = self.content_root / "manual.cia"
        existing_path.write_bytes(b"keep")
        response = self.client.post(
            self.url, {**self.new_title_data(), "cia_file": self.upload()}
        )
        self.assertContains(response, "already exists in CONTENT_ROOT")
        self.assertEqual(existing_path.read_bytes(), b"keep")

        existing_path.unlink()
        title = self.create_title()
        ContentArtifact.objects.create(
            title=title, relative_path="somewhere.cia", byte_size=1
        )
        response = self.client.post(
            self.url,
            {"existing_title": title.pk, "cia_file": self.upload("other.cia")},
        )
        self.assertContains(response, "already has a content artifact")
        self.assertFalse((self.content_root / "other.cia").exists())

    def test_wrong_extension_is_rejected(self):
        response = self.client.post(
            self.url,
            {**self.new_title_data(), "cia_file": self.upload("manual.txt")},
        )
        self.assertContains(response, "Select a file with a .cia extension")

    def test_database_failure_removes_copied_file_and_rolls_back_title(self):
        with patch(
            "apps.content.admin.ContentArtifact.save",
            side_effect=IntegrityError("simulated failure"),
        ):
            response = self.client.post(
                self.url,
                {**self.new_title_data(), "cia_file": self.upload()},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.content_root / "manual.cia").exists())
        self.assertFalse(Title.objects.filter(title_id="0004000000ABCDEF").exists())


class ContentArtifactSynchronizationAdminTests(TestCase):
    first_title_id = "000400000012B900"
    second_title_id = "000400000012C400"

    @classmethod
    def setUpTestData(cls):
        cls.category, cls.subcategory = create_test_taxonomy()
        cls.admin_user = User.objects.create_superuser(
            username="admin", password="password", email="admin@example.com"
        )
        cls.staff_user = User.objects.create_user(
            username="staff", password="password", is_staff=True
        )

    def setUp(self):
        self.tempdir = TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)
        self.content_root = Path(self.tempdir.name) / "content"
        self.content_root.mkdir()
        self.settings_override = self.settings(CONTENT_ROOT=self.content_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.url = reverse("admin:content_contentartifact_synchronize")

    def write_cia(self, relative_path, title_id=None):
        path = self.content_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cia_bytes(title_id or self.first_title_id))
        return path

    def create_entry(self, title_id=None, **overrides):
        values = {
            "source": "provider",
            "external_id": title_id or self.first_title_id,
            "title_id": title_id or self.first_title_id,
            "name": "Staged Name",
            "product_code": "CTR-P-STAGE",
            "region": "USA",
        }
        values.update(overrides)
        return CatalogEntry.objects.create(**values)

    def create_title(self, title_id=None, **overrides):
        values = {
            "title_id": title_id or self.first_title_id,
            "name": "Existing Name",
            "region": "USA",
            "filename": "existing.cia",
            "category": self.category,
            "subcategory": self.subcategory,
        }
        values.update(overrides)
        return Title.objects.create(**values)

    def synchronize(self, *, force=False):
        self.client.force_login(self.admin_user)
        return self.client.post(self.url, {"mode": "force" if force else "normal"})

    def test_page_is_admin_only_linked_and_scan_requires_post(self):
        self.write_cia("game.cia")
        self.create_entry()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Synchronize now")
        self.assertContains(response, "Force synchronize content folder")
        self.assertFalse(Title.objects.exists())
        change_list = self.client.get(
            reverse("admin:content_contentartifact_changelist")
        )
        self.assertContains(change_list, self.url)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin_user)
        self.assertEqual(csrf_client.post(self.url).status_code, 403)

    def test_buttons_dispatch_normal_and_force_modes(self):
        self.client.force_login(self.admin_user)
        with patch("apps.content.admin.synchronize_content_root") as synchronize:
            self.client.post(self.url, {"mode": "normal"})
            synchronize.assert_called_once_with(force=False)
            synchronize.reset_mock()

            self.client.post(self.url, {"mode": "force"})
            synchronize.assert_called_once_with(force=True)

    def test_parenthesized_region_aliases_are_case_and_whitespace_tolerant(self):
        aliases = {
            "(U)": "USA",
            "( us )": "USA",
            "(USA)": "USA",
            "(United States)": "USA",
            "(E)": "EUR",
            "(EU)": "EUR",
            "(EUR)": "EUR",
            "(Europe)": "EUR",
            "(J)": "JPN",
            "(JP)": "JPN",
            "(JPN)": "JPN",
            "( japan )": "JPN",
            "(W)": "WLD",
            "(WLD)": "WLD",
            "(World)": "WLD",
            "(Worldwide)": "WLD",
            "(AUS)": "AUS",
            "(Australia)": "AUS",
            "(CAN)": "CAN",
            "(Canada)": "CAN",
            "(CHN)": "CHN",
            "(China)": "CHN",
            "(FRA)": "FRA",
            "(France)": "FRA",
            "(GER)": "GER",
            "(Germany)": "GER",
            "(ITA)": "ITA",
            "(Italy)": "ITA",
            "(KOR)": "KOR",
            "(Korea)": "KOR",
            "(NLD)": "NLD",
            "(Netherlands)": "NLD",
            "(RUS)": "RUS",
            "(Russia)": "RUS",
            "(SPA)": "SPA",
            "(Spain)": "SPA",
            "(TWN)": "TWN",
            "(Taiwan)": "TWN",
            "(UKV)": "UKV",
            "(United Kingdom)": "UKV",
        }
        for tag, expected in aliases.items():
            with self.subTest(tag=tag):
                self.assertEqual(_inferred_region(f"Title {tag}.cia"), expected)

    def test_unique_parenthesized_region_selects_catalog_entry(self):
        path = self.write_cia("nested/Game ( eUrOpE ).CIA")
        original = path.read_bytes()
        self.create_entry(
            source="a-provider",
            external_id="1",
            name="Wrong Candidate",
            region="USA",
            metadata={"filename": "Game (Europe).CIA"},
        )
        self.create_entry(
            source="z-provider",
            external_id="9",
            name="Chosen Source",
            region="EUR",
            metadata={"filename": "unrelated.cia"},
        )

        response = self.synchronize()

        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertEqual(
            (
                result.scanned,
                result.titles_created,
                result.artifacts_created,
                result.skipped,
            ),
            (1, 1, 1, 0),
        )
        title = Title.objects.get()
        artifact = ContentArtifact.objects.get()
        self.assertEqual(title.name, "Chosen Source")
        self.assertEqual(title.product_code, "CTR-P-STAGE")
        self.assertEqual(title.filename, "Game ( eUrOpE ).CIA")
        self.assertEqual(title.subcategory.slug, "europe")
        self.assertEqual(artifact.relative_path, "nested/Game ( eUrOpE ).CIA")
        self.assertEqual(artifact.byte_size, len(original))
        self.assertEqual(path.read_bytes(), original)

    def test_single_catalog_entry_uses_normal_region_mapping(self):
        self.write_cia("single.cia")
        self.create_entry(name="Only Candidate", metadata={})

        result = self.synchronize().context["result"]

        self.assertEqual(result.skipped, 0)
        title = Title.objects.get()
        self.assertEqual(title.name, "Only Candidate")
        self.assertEqual(title.subcategory, self.subcategory)
        self.assertEqual(result.details, [])

    def test_unbracketed_or_unknown_region_uses_uncategorised_fallback(self):
        self.write_cia("USA release (unknown).cia")
        chosen = self.create_entry(
            source="a-provider",
            external_id="1",
            name="Deterministic Choice",
        )
        self.create_entry(
            source="z-provider",
            external_id="2",
            name="Other Choice",
        )

        result = self.synchronize().context["result"]

        title = Title.objects.get()
        self.assertEqual(title.name, chosen.name)
        self.assertEqual(title.subcategory.slug, "uncategorised")
        self.assertIn("Ambiguous catalog match", result.details[0])
        self.assertIn("a-provider/1", result.details[0])

    def test_conflicting_parenthesized_regions_use_uncategorised_fallback(self):
        self.write_cia("same game (U) (E).cia")
        self.create_entry(
            source="a-provider",
            external_id="1",
            name="First Match",
            region="USA",
        )
        self.create_entry(
            source="b-provider",
            external_id="2",
            name="Second Match",
            region="EUR",
        )

        result = self.synchronize().context["result"]

        title = Title.objects.get()
        self.assertEqual(title.name, "First Match")
        self.assertEqual(title.subcategory.slug, "uncategorised")
        self.assertIn("Ambiguous catalog match", result.details[0])

    def test_no_candidate_in_inferred_region_uses_uncategorised_fallback(self):
        self.write_cia("game (J).cia")
        self.create_entry(source="a-provider", external_id="1", region="USA")
        self.create_entry(source="b-provider", external_id="2", region="EUR")

        result = self.synchronize().context["result"]

        self.assertEqual(Title.objects.get().subcategory.slug, "uncategorised")
        self.assertIn("Ambiguous catalog match", result.details[0])

    def test_multiple_candidates_in_inferred_region_use_uncategorised_fallback(self):
        self.write_cia("game (U).cia")
        self.create_entry(source="a-provider", external_id="1", region="USA")
        self.create_entry(source="b-provider", external_id="2", region="US")

        result = self.synchronize().context["result"]

        self.assertEqual(Title.objects.get().subcategory.slug, "uncategorised")
        self.assertIn("Ambiguous catalog match", result.details[0])

    def test_existing_title_is_reused_without_metadata_changes(self):
        path = self.write_cia("nested/reused.cia")
        title = self.create_title(name="Keep Me", filename="keep.cia")
        self.create_entry(name="Do Not Apply")

        result = self.synchronize().context["result"]

        title.refresh_from_db()
        self.assertEqual((title.name, title.filename), ("Keep Me", "keep.cia"))
        artifact = ContentArtifact.objects.get(title=title)
        self.assertEqual(artifact.relative_path, "nested/reused.cia")
        self.assertEqual(artifact.byte_size, path.stat().st_size)
        self.assertEqual(result.artifacts_created, 1)
        self.assertEqual(result.titles_created, 0)

    def test_normal_mode_leaves_existing_title_and_artifact_untouched(self):
        self.write_cia("moved/game.cia")
        title = self.create_title(description="Manual description", flags=77)
        artifact = ContentArtifact.objects.create(
            title=title, relative_path="old/game.cia", byte_size=1
        )
        title_before = tuple(
            Title.objects.filter(pk=title.pk).values_list(
                *[field.attname for field in Title._meta.concrete_fields]
            )[0]
        )
        artifact_before = tuple(
            ContentArtifact.objects.filter(pk=artifact.pk).values_list(
                *[field.attname for field in ContentArtifact._meta.concrete_fields]
            )[0]
        )

        result = self.synchronize().context["result"]

        self.assertEqual(result.unchanged, 1)
        self.assertEqual(
            tuple(
                Title.objects.filter(pk=title.pk).values_list(
                    *[field.attname for field in Title._meta.concrete_fields]
                )[0]
            ),
            title_before,
        )
        self.assertEqual(
            tuple(
                ContentArtifact.objects.filter(pk=artifact.pk).values_list(
                    *[field.attname for field in ContentArtifact._meta.concrete_fields]
                )[0]
            ),
            artifact_before,
        )

    def test_force_refreshes_derived_fields_and_preserves_manual_fields(self):
        path = self.write_cia("moved/game.cia")
        old_category = Category.objects.create(
            protocol_id=2, slug="old", display_name="Old"
        )
        old_subcategory = Subcategory.objects.create(
            category=old_category,
            protocol_id=1,
            slug="old",
            display_name="Old",
        )
        title = self.create_title(
            name="Old Name",
            product_code="OLD",
            region="OLD",
            filename="old.cia",
            category=old_category,
            subcategory=old_subcategory,
            description="Manual description",
            alternative_name="Manual alternative",
            alternative_names=["One", "Two"],
            preferred_alternative_index=1,
            version=42,
            content_type=3,
            flags=99,
            download_count=123,
            listed=False,
        )
        artifact = ContentArtifact.objects.create(
            title=title,
            relative_path="old/game.cia",
            byte_size=1,
            enabled=False,
        )
        self.create_entry(name="Refreshed", product_code="NEW", region="USA")

        result = self.synchronize(force=True).context["result"]

        title.refresh_from_db()
        artifact.refresh_from_db()
        self.assertEqual(
            (
                title.name,
                title.product_code,
                title.region,
                title.filename,
                title.category,
                title.subcategory,
            ),
            ("Refreshed", "NEW", "USA", "game.cia", self.category, self.subcategory),
        )
        self.assertEqual(
            (
                title.description,
                title.alternative_name,
                title.alternative_names,
                title.preferred_alternative_index,
                title.version,
                title.content_type,
                title.flags,
                title.download_count,
                title.listed,
            ),
            (
                "Manual description",
                "Manual alternative",
                ["One", "Two"],
                1,
                42,
                3,
                99,
                123,
                False,
            ),
        )
        self.assertEqual(
            (artifact.relative_path, artifact.byte_size, artifact.enabled),
            ("moved/game.cia", path.stat().st_size, False),
        )
        self.assertEqual((result.titles_updated, result.artifacts_updated), (1, 1))

    def test_missing_files_do_not_delete_disable_or_change_records(self):
        title = self.create_title()
        artifact = ContentArtifact.objects.create(
            title=title, relative_path="missing.cia", byte_size=123, enabled=True
        )

        result = self.synchronize(force=True).context["result"]

        artifact.refresh_from_db()
        self.assertEqual(result.scanned, 0)
        self.assertEqual(
            (artifact.relative_path, artifact.byte_size, artifact.enabled),
            ("missing.cia", 123, True),
        )

    def test_duplicate_title_ids_are_all_skipped(self):
        self.write_cia("one.cia")
        self.write_cia("nested/two.CIA")
        self.create_entry()

        result = self.synchronize().context["result"]

        self.assertEqual((result.scanned, result.skipped), (2, 2))
        self.assertIn("duplicate title ID", result.details[0])
        self.assertFalse(Title.objects.exists())
        self.assertFalse(ContentArtifact.objects.exists())

    def test_conflicting_path_association_is_skipped(self):
        self.write_cia("conflict.cia")
        other_title = self.create_title(title_id=self.second_title_id)
        ContentArtifact.objects.create(
            title=other_title, relative_path="conflict.cia", byte_size=1
        )
        self.create_entry()

        result = self.synchronize(force=True).context["result"]

        self.assertEqual(result.skipped, 1)
        self.assertIn("different title", result.details[0])
        self.assertFalse(Title.objects.filter(title_id=self.first_title_id).exists())

    def test_invalid_cia_and_non_cia_files_are_reported_or_ignored(self):
        (self.content_root / "invalid.cia").write_bytes(b"not a CIA")
        (self.content_root / "notes.txt").write_text("ignore", encoding="utf-8")

        result = self.synchronize().context["result"]

        self.assertEqual((result.scanned, result.skipped), (1, 1))
        self.assertIn("CIA header is truncated", result.details[0])

    def test_new_title_without_safe_catalog_taxonomy_mapping_is_skipped(self):
        self.write_cia("unknown.cia")
        self.create_entry(region="MARS")

        result = self.synchronize().context["result"]

        self.assertEqual(result.skipped, 1)
        self.assertIn("cannot be mapped safely", result.details[0])
        self.assertFalse(Title.objects.exists())

    def test_database_failure_rolls_back_new_title_and_reports_skip(self):
        self.write_cia("rollback.cia")
        self.create_entry()

        with patch(
            "apps.content.sync.ContentArtifact.objects.create",
            side_effect=IntegrityError("forced failure"),
        ):
            result = self.synchronize().context["result"]

        self.assertEqual(result.skipped, 1)
        self.assertIn("forced failure", result.details[0])
        self.assertFalse(Title.objects.exists())
        self.assertFalse(ContentArtifact.objects.exists())

    def test_low_level_parser_uses_pyctr_plaintext_tmd(self):
        data = cia_bytes(self.first_title_id)
        metadata = parse_cia_metadata(BytesIO(data), len(data))

        self.assertEqual(metadata.title_id, self.first_title_id)
        tmd = TitleMetadataReader.load(BytesIO(data[0x2040:]))
        self.assertEqual(tmd.title_id.upper(), self.first_title_id)
