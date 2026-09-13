from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.catalog.models import Category, Subcategory, Title
from apps.content.models import ContentArtifact
from seeds.catalog import seed_catalog_taxonomy


class ImportReleasesTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)

    def release(self, **overrides) -> str:
        values = {
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

    def test_import_maps_supported_fields_and_games_taxonomy(self):
        self.run_import(self.write_xml(self.release()))
        title = Title.objects.get(title_id="0004000000000001")
        self.assertEqual(
            (
                title.name,
                title.product_code,
                title.region,
                title.filename,
                title.category.slug,
                title.subcategory.slug,
            ),
            (
                "Imported Game",
                "CTR-P-TEST",
                "USA",
                "imported-game",
                "games",
                "north-america",
            ),
        )
        self.assertFalse(ContentArtifact.objects.exists())

    def test_unsupported_xml_fields_are_ignored(self):
        self.run_import(self.write_xml(self.release()))
        title = Title.objects.get()
        self.assertEqual(title.description, "")
        self.assertFalse(hasattr(title, "publisher"))

    def test_last_duplicate_title_id_wins_in_xml_order(self):
        path = self.write_xml(
            self.release(name="Older", serial="CTR-OLD"),
            self.release(name="Newer", serial="CTR-NEW", filename="newer"),
        )
        stdout, _ = self.run_import(path)
        title = Title.objects.get()
        self.assertEqual(
            (title.name, title.product_code, title.filename),
            ("Newer", "CTR-NEW", "newer"),
        )
        self.assertIn("1 created, 1 updated, 0 skipped", stdout)

    def test_repeat_import_is_idempotent_and_reports_updates(self):
        path = self.write_xml(self.release())
        first_stdout, _ = self.run_import(path)
        second_stdout, _ = self.run_import(path)
        self.assertEqual(Title.objects.count(), 1)
        self.assertIn("1 created, 0 updated, 0 skipped", first_stdout)
        self.assertIn("0 created, 1 updated, 0 skipped", second_stdout)

    def test_malformed_records_are_skipped_with_warning(self):
        malformed = self.release(titleid="not-a-title-id")
        missing = self.release(titleid="0004000000000002", name="")
        valid = self.release(titleid="0004000000000003", name="Valid")
        stdout, stderr = self.run_import(self.write_xml(malformed, missing, valid))
        self.assertEqual(list(Title.objects.values_list("name", flat=True)), ["Valid"])
        self.assertIn("1 created, 0 updated, 2 skipped", stdout)
        self.assertIn("Skipping record 1", stderr)
        self.assertIn("Skipping record 2", stderr)

    def test_xml_specific_regions_are_seeded_and_mapped(self):
        regions = {
            "CHN": "china",
            "FRA": "france",
            "GER": "germany",
            "ITA": "italy",
            "KOR": "korea",
            "NLD": "netherlands",
            "RUS": "russia",
            "SPA": "spain",
            "TWN": "taiwan",
            "UKV": "united-kingdom",
            "WLD": "worldwide",
        }
        releases = [
            self.release(titleid=f"{index:016X}", region=code)
            for index, code in enumerate(regions, start=10)
        ]
        self.run_import(self.write_xml(*releases))
        self.assertEqual(
            {
                title.region: title.subcategory.slug
                for title in Title.objects.select_related("subcategory")
            },
            regions,
        )
        self.assertEqual(
            list(
                Subcategory.objects.filter(category__slug="games")
                .order_by("protocol_id")
                .values_list("protocol_id", flat=True)
            ),
            list(range(1, 16)),
        )

    def test_unexpected_region_falls_back_to_other_with_warning(self):
        _, stderr = self.run_import(self.write_xml(self.release(region="MARS")))
        title = Title.objects.get()
        self.assertEqual((title.region, title.subcategory.slug), ("MARS", "other"))
        self.assertIn("unexpected region 'MARS'; using Other", stderr)

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
        self.assertFalse(Title.objects.exists())

    def test_default_path_points_to_repository_xml(self):
        from apps.catalog.management.commands.import_releases import Command

        parser = Command().create_parser("manage.py", "import_releases")
        options = vars(parser.parse_args([]))
        self.assertEqual(
            Path(options["xml_path"]).resolve(),
            (settings.BASE_DIR / "3dsreleases.xml").resolve(),
        )


class CatalogRegionSeedTests(TestCase):
    def test_existing_region_ids_remain_stable_and_new_ids_follow_them(self):
        seed_catalog_taxonomy()
        games = Category.objects.get(slug="games")
        self.assertEqual(
            list(
                Subcategory.objects.filter(category=games)
                .order_by("protocol_id")
                .values_list("protocol_id", "slug")
            )[:5],
            [
                (1, "north-america"),
                (2, "europe"),
                (3, "japan"),
                (4, "other"),
                (5, "china"),
            ],
        )
