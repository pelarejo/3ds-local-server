from django.test import SimpleTestCase

from apps.authentication.models import User
from apps.catalog.models import Category, Subcategory, Title
from apps.content.models import ContentArtifact, DownloadGrant


class ModelDocumentationTests(SimpleTestCase):
    def test_every_project_model_has_a_purpose_docstring(self):
        for model in (User, Category, Subcategory, Title, ContentArtifact, DownloadGrant):
            with self.subTest(model=model.__name__):
                self.assertTrue(model.__doc__ and model.__doc__.strip())
                self.assertNotIn("model class", model.__doc__.lower())
