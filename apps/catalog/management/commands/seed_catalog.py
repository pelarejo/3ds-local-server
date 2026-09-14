from django.apps import apps as global_apps
from django.core.management.base import BaseCommand

CATEGORIES = (
    (1, "games", "Games", 1),
    (2, "updates", "Updates", 2),
    (3, "dlc", "DLC", 3),
    (4, "virtual_console", "Virtual Console", 4),
    (5, "dsiware", "DSiWare", 5),
    (6, "videos", "Videos", 6),
)

SUBCATEGORIES = (
    (1, "europe", "Europe"),
    (2, "north-america", "North America"),
    (3, "australia", "Australia"),
    (4, "canada", "Canada"),
    (5, "china", "China"),
    (6, "france", "France"),
    (7, "germany", "Germany"),
    (8, "italy", "Italy"),
    (9, "japan", "Japan"),
    (10, "korea", "Korea"),
    (11, "netherlands", "Netherlands"),
    (12, "russia", "Russia"),
    (13, "spain", "Spain"),
    (14, "taiwan", "Taiwan"),
    (15, "united-kingdom", "United Kingdom"),
    (16, "worldwide", "Worldwide"),
    (17, "other", "Other"),
    (18, "uncategorised", "Uncategorised"),
)


def seed_catalog_taxonomy(app_registry=global_apps) -> tuple[int, int]:
    """Create or refresh the stable protocol taxonomy and return create counts."""

    Category = app_registry.get_model("catalog", "Category")
    Subcategory = app_registry.get_model("catalog", "Subcategory")
    categories_created = 0
    subcategories_created = 0

    for category_id, slug, display_name, priority in CATEGORIES:
        category, created = Category.objects.update_or_create(
            protocol_id=category_id,
            defaults={
                "slug": slug,
                "display_name": display_name,
                "description": f"Browse {display_name.lower()} by region.",
                "priority": priority,
            },
        )
        categories_created += created
        for subcategory_id, sub_slug, sub_display_name in SUBCATEGORIES:
            _, created = Subcategory.objects.update_or_create(
                category=category,
                protocol_id=subcategory_id,
                defaults={
                    "slug": sub_slug,
                    "display_name": sub_display_name,
                    "description": f"{display_name} for {sub_display_name}.",
                    "ordering": subcategory_id,
                },
            )
            subcategories_created += created

    return categories_created, subcategories_created


class Command(BaseCommand):
    help = "Idempotently create or refresh the stable 3LS catalog taxonomy."

    def handle(self, *args, **options):
        categories_created, subcategories_created = seed_catalog_taxonomy()
        self.stdout.write(
            self.style.SUCCESS(
                f"Catalog taxonomy ready ({categories_created} categories and "
                f"{subcategories_created} subcategories created)."
            )
        )
