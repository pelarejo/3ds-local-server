"""Import provider metadata into the staging catalog."""

from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import CatalogEntry

SOURCE = "3dsdb"
COMMON_FIELDS = {"id", "titleid", "name", "serial", "region", "publisher"}
REQUIRED_FIELDS = ("id", "titleid", "name", "serial", "region")


class Command(BaseCommand):
    help = "Idempotently import provider XML metadata into the staging catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "xml_path",
            nargs="?",
            default=str(settings.SYSTEM_ROOT / "3dsreleases.xml"),
            help=(
                "XML file inside the backend repository "
                "(default: system/3dsreleases.xml)."
            ),
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

                        defaults = {
                            "name": values["name"],
                            "product_code": values["serial"],
                            "title_id": values["titleid"].upper(),
                            "region": values["region"].upper(),
                            "publisher": values.get("publisher", ""),
                            "metadata": {
                                key: value
                                for key, value in values.items()
                                if key not in COMMON_FIELDS
                            },
                        }
                        candidate = CatalogEntry(
                            source=SOURCE,
                            external_id=values["id"],
                            **defaults,
                        )
                        candidate.full_clean(
                            validate_unique=False, validate_constraints=False
                        )
                        _, was_created = CatalogEntry.objects.update_or_create(
                            source=SOURCE,
                            external_id=values["id"],
                            defaults=defaults,
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
