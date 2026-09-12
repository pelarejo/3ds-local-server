from django.core.management.base import BaseCommand

from seeds.catalog import seed_catalog_taxonomy


class Command(BaseCommand):
    help = "Idempotently create or refresh the stable 3HS catalog taxonomy."

    def handle(self, *args, **options):
        categories_created, subcategories_created = seed_catalog_taxonomy()
        self.stdout.write(
            self.style.SUCCESS(
                f"Catalog taxonomy ready ({categories_created} categories and "
                f"{subcategories_created} subcategories created)."
            )
        )
