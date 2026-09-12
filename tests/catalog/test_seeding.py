from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.models import Category, Subcategory


class CatalogSeedTests(TestCase):
    def test_command_is_repeatable_and_refreshes_stable_rows(self):
        initial_output = StringIO()
        call_command("seed_catalog", stdout=initial_output)
        games = Category.objects.get(protocol_id=1)
        games.display_name = "Changed"
        games.save(update_fields=("display_name",))

        first_output = StringIO()
        call_command("seed_catalog", stdout=first_output)
        first_pks = list(Category.objects.order_by("protocol_id").values_list("pk", flat=True))
        call_command("seed_catalog", stdout=StringIO())

        self.assertEqual(Category.objects.count(), 3)
        self.assertEqual(Subcategory.objects.count(), 12)
        self.assertEqual(Category.objects.get(protocol_id=1).display_name, "Games")
        self.assertEqual(first_pks, list(Category.objects.order_by("protocol_id").values_list("pk", flat=True)))
        self.assertIn("0 categories and 0 subcategories created", first_output.getvalue())
        self.assertIn("3 categories and 12 subcategories created", initial_output.getvalue())
