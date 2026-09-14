from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.catalog.models import CatalogEntry, Category, Subcategory, Title


class ImportReleasesTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)

    def release(self, **overrides) -> str:
        values = {
            "id": "1",
            "titleid": "0004000000000001",
            "name": "Imported Game",
            "serial": "CTR-P-TEST",
            "region": "USA",
            "filename": "imported-game",
            "publisher": "Ignored Publisher",
            "trimmedsize": "123456",
        }
        values.update(overrides)
        return (
            "<release>"
            + "".join(f"<{key}>{value}</{key}>" for key, value in values.items())
            + "</release>"
        )

    def write_xml(self, *releases: str, name: str = "releases.xml") -> Path:
        path = Path(self.tempdir.name) / name
        path.write_text(f"<releases>{''.join(releases)}</releases>", encoding="utf-8")
        return path

    def run_import(self, path: Path) -> tuple[str, str]:
        stdout = StringIO()
        stderr = StringIO()
        call_command("import_releases", str(path), stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_import_maps_common_fields_and_preserves_provider_metadata(self):
        self.run_import(self.write_xml(self.release()))
        entry = CatalogEntry.objects.get(source="3dsdb", external_id="1")
        self.assertEqual(
            (
                entry.title_id,
                entry.name,
                entry.product_code,
                entry.region,
                entry.publisher,
            ),
            (
                "0004000000000001",
                "Imported Game",
                "CTR-P-TEST",
                "USA",
                "Ignored Publisher",
            ),
        )
        self.assertEqual(
            entry.metadata,
            {"filename": "imported-game", "trimmedsize": "123456"},
        )
        self.assertFalse(Title.objects.exists())

    def test_import_does_not_update_an_existing_published_title(self):
        category = Category.objects.create(
            protocol_id=1, slug="games", display_name="Games"
        )
        subcategory = Subcategory.objects.create(
            category=category,
            protocol_id=1,
            slug="north-america",
            display_name="North America",
        )
        title = Title.objects.create(
            title_id="0004000000000001",
            name="Published Name",
            region="USA",
            filename="published.cia",
            category=category,
            subcategory=subcategory,
        )

        self.run_import(self.write_xml(self.release(name="Provider Name")))

        title.refresh_from_db()
        self.assertEqual(title.name, "Published Name")
        self.assertEqual(CatalogEntry.objects.get().name, "Provider Name")

    def test_duplicate_title_ids_with_distinct_external_ids_are_retained(self):
        path = self.write_xml(
            self.release(id="1", name="Older", serial="CTR-OLD"),
            self.release(id="2", name="Newer", serial="CTR-NEW", filename="newer"),
        )
        stdout, _ = self.run_import(path)
        self.assertEqual(
            list(
                CatalogEntry.objects.order_by("external_id").values_list(
                    "external_id", "title_id", "name"
                )
            ),
            [
                ("1", "0004000000000001", "Older"),
                ("2", "0004000000000001", "Newer"),
            ],
        )
        self.assertIn("2 created, 0 updated, 0 skipped", stdout)

    def test_last_duplicate_source_external_id_wins_in_xml_order(self):
        path = self.write_xml(
            self.release(id="same", name="Older"),
            self.release(id="same", name="Newer", filename="newer"),
        )
        stdout, _ = self.run_import(path)
        entry = CatalogEntry.objects.get()
        self.assertEqual((entry.name, entry.metadata["filename"]), ("Newer", "newer"))
        self.assertIn("1 created, 1 updated, 0 skipped", stdout)

    def test_repeat_import_is_idempotent_and_reports_updates(self):
        path = self.write_xml(self.release())
        first_stdout, _ = self.run_import(path)
        second_stdout, _ = self.run_import(path)
        self.assertEqual(CatalogEntry.objects.count(), 1)
        self.assertIn("1 created, 0 updated, 0 skipped", first_stdout)
        self.assertIn("0 created, 1 updated, 0 skipped", second_stdout)

    def test_malformed_records_are_skipped_with_warning(self):
        malformed = self.release(titleid="not-a-title-id")
        missing = self.release(titleid="0004000000000002", name="")
        valid = self.release(titleid="0004000000000003", name="Valid")
        stdout, stderr = self.run_import(self.write_xml(malformed, missing, valid))
        self.assertEqual(
            list(CatalogEntry.objects.values_list("name", flat=True)), ["Valid"]
        )
        self.assertIn("1 created, 0 updated, 2 skipped", stdout)
        self.assertIn("Skipping record 1", stderr)
        self.assertIn("Skipping record 2", stderr)

    def test_region_codes_are_normalized_without_catalog_taxonomy_side_effects(self):
        self.run_import(self.write_xml(self.release(region="eur")))
        self.assertEqual(CatalogEntry.objects.get().region, "EUR")

    def test_missing_and_outside_paths_raise_clear_errors(self):
        missing = Path(self.tempdir.name) / "missing.xml"
        with self.assertRaisesMessage(CommandError, "XML file does not exist"):
            call_command("import_releases", str(missing))
        outside = settings.BASE_DIR.parent / "outside.xml"
        with self.assertRaisesMessage(CommandError, "inside the backend repository"):
            call_command("import_releases", str(outside))

    def test_parse_error_rolls_back_import(self):
        path = Path(self.tempdir.name) / "broken.xml"
        path.write_text(f"<releases>{self.release()}</releases", encoding="utf-8")
        with self.assertRaisesMessage(CommandError, "Could not parse XML"):
            call_command("import_releases", str(path))
        self.assertFalse(CatalogEntry.objects.exists())

    def test_default_path_uses_system_root(self):
        from apps.catalog.management.commands.import_releases import Command

        system_root = Path(self.tempdir.name) / "system"
        with self.settings(SYSTEM_ROOT=system_root):
            parser = Command().create_parser("manage.py", "import_releases")
            options = vars(parser.parse_args([]))
        self.assertEqual(
            Path(options["xml_path"]).resolve(),
            (system_root / "3dsreleases.xml").resolve(),
        )
