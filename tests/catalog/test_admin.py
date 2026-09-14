from django.contrib import admin
from django.test import SimpleTestCase

from apps.catalog.admin import CatalogEntryAdmin
from apps.catalog.models import CatalogEntry


class CatalogEntryAdminTests(SimpleTestCase):
    def test_staging_entries_are_registered_with_searchable_source_fields(self):
        model_admin = admin.site._registry[CatalogEntry]

        self.assertIsInstance(model_admin, CatalogEntryAdmin)
        self.assertIn("source", model_admin.list_filter)
        self.assertIn("external_id", model_admin.search_fields)
        self.assertIn("title_id", model_admin.search_fields)
