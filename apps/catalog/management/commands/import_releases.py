"""Import 3DS release metadata into the local catalog."""

from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category, Subcategory, Title
from seeds.catalog import seed_catalog_taxonomy

REGION_SLUGS = {
    "USA": "north-america",
    "EUR": "europe",
    "JPN": "japan",
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
REQUIRED_FIELDS = ("titleid", "name", "serial", "region", "filename")


class Command(BaseCommand):
    help = "Idempotently import title metadata from a 3dsreleases XML file."

    def add_arguments(self, parser):
        parser.add_argument(
            "xml_path",
            nargs="?",
            default=str(settings.BASE_DIR / "3dsreleases.xml"),
            help="XML file inside the backend repository (default: 3dsreleases.xml).",
        )

    def handle(self, *args, **options):
        path = Path(options["xml_path"])
        if not path.is_absolute():
            path = settings.BASE_DIR / path
        path = path.resolve()
        base_dir = settings.BASE_DIR.resolve()
        if not path.is_relative_to(base_dir):
            raise CommandError("The XML path must be inside the backend repository.")
        if not path.is_file():
            raise CommandError(f"XML file does not exist: {path}")

        created = updated = skipped = 0
        try:
            with transaction.atomic():
                seed_catalog_taxonomy()
                games = Category.objects.get(slug="games")
                subcategories = {
                    subcategory.slug: subcategory
                    for subcategory in Subcategory.objects.filter(category=games)
                }

                for index, element in enumerate(self._releases(path), start=1):
                    values = {
                        child.tag: (child.text or "").strip() for child in element
                    }
                    try:
                        missing = [
                            field for field in REQUIRED_FIELDS if not values.get(field)
                        ]
                        if missing:
                            raise ValidationError(
                                f"missing required field(s): {', '.join(missing)}"
                            )

                        region_code = values["region"].upper()
                        subcategory_slug = REGION_SLUGS.get(region_code, "other")
                        if subcategory_slug == "other":
                            self.stderr.write(
                                self.style.WARNING(
                                    f"Record {index} has unexpected region "
                                    f"'{values['region']}'; using Other."
                                )
                            )
                        defaults = {
                            "name": values["name"],
                            "product_code": values["serial"],
                            "region": values["region"],
                            "filename": values["filename"],
                            "category": games,
                            "subcategory": subcategories[subcategory_slug],
                        }
                        candidate = Title(title_id=values["titleid"], **defaults)
                        candidate.full_clean(
                            validate_unique=False, validate_constraints=False
                        )
                        _, was_created = Title.objects.update_or_create(
                            title_id=values["titleid"], defaults=defaults
                        )
                    except (KeyError, ValidationError, ValueError) as error:
                        skipped += 1
                        self.stderr.write(
                            self.style.WARNING(f"Skipping record {index}: {error}")
                        )
                    else:
                        created += was_created
                        updated += not was_created
                    finally:
                        element.clear()
        except ElementTree.ParseError as error:
            raise CommandError(f"Could not parse XML: {error}") from error
        except OSError as error:
            raise CommandError(f"Could not read XML: {error}") from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {created} created, {updated} updated, "
                f"{skipped} skipped."
            )
        )

    @staticmethod
    def _releases(path: Path):
        for _, element in ElementTree.iterparse(path, events=("end",)):
            if element.tag == "release":
                yield element
