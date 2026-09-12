"""Canonical catalog taxonomy seed definitions and update logic."""

from django.apps import apps as global_apps


CATEGORIES = (
    (1, "games", "Games", 1),
    (2, "updates", "Updates", 2),
    (3, "dlc", "DLC", 3),
)

SUBCATEGORIES = (
    (1, "north-america", "North America"),
    (2, "europe", "Europe"),
    (3, "japan", "Japan"),
    (4, "other", "Other"),
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
