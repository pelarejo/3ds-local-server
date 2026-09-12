import struct

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Category, Subcategory, Title
from seeds.catalog import seed_catalog_taxonomy
from apps.content.models import ContentArtifact


class CatalogRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_catalog_taxonomy()
        cls.user = get_user_model().objects.create_user(username="client", password="secret")
        cls.category = Category.objects.get(slug="games")
        cls.subcategory = Subcategory.objects.get(category=cls.category, slug="north-america")
        cls.title = Title.objects.create(
            title_id="0004000000123456", name="Test Game", alternative_name="Alternative",
            alternative_names=["Alternative", "別名"], preferred_alternative_index=0,
            product_code="CTR-P-TEST", region="North America", description="A test title",
            filename="test.cia", version=7, content_type=1, flags=5, download_count=9,
            seed=b"s" * 16, file_checksum=b"c" * 32,
            category=cls.category, subcategory=cls.subcategory,
        )
        ContentArtifact.objects.create(title=cls.title, relative_path="test.cia", byte_size=1234)
        cls.auth = {"HTTP_X_AUTH_USER": "client", "HTTP_X_AUTH_PASSWORD": "secret"}

    def test_partial_title_array_matches_frontend_layout(self):
        response = self.client.get("/nbapi/title/category/games/north-america", **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["x-minimum"], "1.5.11")
        magic, array_header_size, count, element_size, blob_size = struct.unpack_from("<4sIIII", response.content)
        self.assertEqual((magic, array_header_size, count, element_size), (b"NBAR", 20, 1, 64))
        self.assertEqual(len(response.content), 20 + 64 + blob_size)
        values = struct.unpack_from("<QQQQIIIIHBBB3xII", response.content, 20)
        self.assertEqual(values[:5], (int(self.title.title_id, 16), 1234, 5, 9, self.title.pk))
        self.assertEqual(values[8:12], (7, 1, 1, 1))
        blob = response.content[84:]
        self.assertEqual(blob[values[5]:].split(b"\0", 1)[0], b"Test Game")

    def test_title_object_matches_frontend_layout(self):
        response = self.client.get(f"/nbapi/title/{self.title.pk}", **self.auth)
        magic, object_header_size, header_size, blob_size = struct.unpack_from("<4sIII", response.content)
        self.assertEqual((magic, object_header_size, header_size), (b"TITL", 16, 144))
        self.assertEqual(len(response.content), 16 + 144 + blob_size)
        self.assertEqual(struct.unpack_from("<Q", response.content, 32)[0], 1234)
        self.assertEqual(struct.unpack_from("<Q", response.content, 40)[0], int(self.title.title_id, 16))
        self.assertEqual(response.content[16:32], b"s" * 16)
        self.assertEqual(response.content[124:156], b"c" * 32)

    def test_index_has_aligned_nested_arrays(self):
        response = self.client.get("/nbapi/title-index", **self.auth)
        magic, object_header_size, header_size, blob_size = struct.unpack_from("<4sIII", response.content)
        self.assertEqual((magic, object_header_size, header_size), (b"TIDX", 16, 48))
        self.assertEqual(len(response.content), 64 + blob_size)
        category_offset = struct.unpack_from("<I", response.content, 48)[0]
        outer_blob = response.content[64:]
        self.assertEqual(outer_blob[category_offset:category_offset + 4], b"NBAR")
        _, _, category_count, category_size, _ = struct.unpack_from("<4sIIII", outer_blob, category_offset)
        self.assertEqual((category_count, category_size), (3, 56))

    def test_unknown_taxonomy_returns_result(self):
        response = self.client.get("/nbapi/title/category/nope/nope", **self.auth)
        self.assertEqual((response.status_code, response.content[:4]), (404, b"RSLT"))
