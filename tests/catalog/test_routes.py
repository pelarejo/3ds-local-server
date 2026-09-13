import struct
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from apps.authentication.models import HSAPIToken
from apps.catalog.models import Category, Subcategory, Title
from apps.content.models import ContentArtifact
from seeds.catalog import seed_catalog_taxonomy
from threehs_backend.nb import ResultNamespace, ResultReason, title_payload


class CatalogRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_catalog_taxonomy()
        cls.user = get_user_model().objects.create_user(
            username="client", password="secret"
        )
        _, raw_token = HSAPIToken.issue(user=cls.user, name="Test client")
        cls.category = Category.objects.get(slug="games")
        cls.subcategory = Subcategory.objects.get(
            category=cls.category, slug="north-america"
        )
        cls.title = Title.objects.create(
            title_id="0004000000123456",
            name="Test Game",
            alternative_name="Alternative",
            alternative_names=["Alternative", "別名"],
            preferred_alternative_index=0,
            product_code="CTR-P-TEST",
            region="North America",
            description="A test title",
            filename="test.cia",
            version=7,
            content_type=1,
            flags=5,
            download_count=9,
            seed=b"s" * 16,
            file_checksum=b"c" * 32,
            category=cls.category,
            subcategory=cls.subcategory,
        )
        ContentArtifact.objects.create(
            title=cls.title, relative_path="test.cia", byte_size=1234
        )
        cls.auth = {
            "HTTP_X_AUTH_USER": "client",
            "HTTP_X_AUTH_PASSWORD": raw_token,
        }

    def test_partial_title_array_matches_frontend_layout(self):
        response = self.client.get(
            "/nbapi/title/category/games/north-america", **self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["x-minimum"], "1.5.11")
        magic, array_header_size, count, element_size, blob_size = struct.unpack_from(
            "<4sIIII", response.content
        )
        self.assertEqual(
            (magic, array_header_size, count, element_size), (b"NBAR", 20, 1, 64)
        )
        self.assertEqual(len(response.content), 20 + 64 + blob_size)
        values = struct.unpack_from("<QQQQIIIIHBBB3xII", response.content, 20)
        self.assertEqual(
            values[:5], (int(self.title.title_id, 16), 1234, 5, 9, self.title.pk)
        )
        self.assertEqual(values[8:12], (7, 1, 1, 1))
        blob = response.content[84:]
        self.assertEqual(blob[values[5] :].split(b"\0", 1)[0], b"Test Game")

    def test_lists_and_index_exclude_titles_without_enabled_artifacts(self):
        excluded = (
            ("0004000000123457", False, True),
            ("0004000000123458", True, None),
            ("0004000000123459", True, False),
        )
        for title_id, listed, artifact_enabled in excluded:
            title = Title.objects.create(
                title_id=title_id,
                name=f"Excluded {title_id}",
                region="North America",
                filename=f"{title_id}.cia",
                listed=listed,
                category=self.category,
                subcategory=self.subcategory,
            )
            if artifact_enabled is not None:
                ContentArtifact.objects.create(
                    title=title,
                    relative_path=f"{title_id}.cia",
                    byte_size=1,
                    enabled=artifact_enabled,
                )

        titles_response = self.client.get(
            "/nbapi/title/category/games/north-america", **self.auth
        )
        self.assertEqual(struct.unpack_from("<I", titles_response.content, 8)[0], 1)
        self.assertEqual(
            struct.unpack_from("<I", titles_response.content, 52)[0], self.title.pk
        )

        index_response = self.client.get("/nbapi/title-index", **self.auth)
        self.assertEqual(
            struct.unpack_from("<III4xQQ", index_response.content, 16),
            (1, 1, 1, 1234, 9),
        )
        categories_offset = struct.unpack_from("<I", index_response.content, 48)[0]
        outer_blob = index_response.content[64:]
        self.assertEqual(
            struct.unpack_from("<III4xQQ", outer_blob, categories_offset + 20),
            (1, 1, 1, 1234, 9),
        )

    def test_title_object_matches_frontend_layout(self):
        response = self.client.get(f"/nbapi/title/{self.title.pk}", **self.auth)
        magic, object_header_size, header_size, blob_size = struct.unpack_from(
            "<4sIII", response.content
        )
        self.assertEqual((magic, object_header_size, header_size), (b"TITL", 16, 144))
        self.assertEqual(len(response.content), 16 + 144 + blob_size)
        self.assertEqual(struct.unpack_from("<Q", response.content, 32)[0], 1234)
        self.assertEqual(
            struct.unpack_from("<Q", response.content, 40)[0],
            int(self.title.title_id, 16),
        )
        self.assertEqual(response.content[16:32], b"s" * 16)
        self.assertEqual(response.content[124:156], b"c" * 32)

    def test_index_has_aligned_nested_arrays(self):
        response = self.client.get("/nbapi/title-index", **self.auth)
        magic, object_header_size, header_size, blob_size = struct.unpack_from(
            "<4sIII", response.content
        )
        self.assertEqual((magic, object_header_size, header_size), (b"TIDX", 16, 48))
        self.assertEqual(len(response.content), 64 + blob_size)
        category_offset = struct.unpack_from("<I", response.content, 48)[0]
        outer_blob = response.content[64:]
        self.assertEqual(outer_blob[category_offset : category_offset + 4], b"NBAR")
        _, _, category_count, category_size, _ = struct.unpack_from(
            "<4sIIII", outer_blob, category_offset
        )
        self.assertEqual((category_count, category_size), (3, 56))

    def test_unknown_taxonomy_returns_result(self):
        response = self.client.get("/nbapi/title/category/nope/nope", **self.auth)
        self.assertEqual((response.status_code, response.content[:4]), (404, b"RSLT"))
        self.assertEqual(response["x-minimum"], "1.5.11")


class RandomTitleRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_catalog_taxonomy()
        cls.user = get_user_model().objects.create_user(username="random-client")
        _, raw_token = HSAPIToken.issue(user=cls.user, name="Random client")
        cls.auth = {
            "HTTP_X_AUTH_USER": cls.user.username,
            "HTTP_X_AUTH_PASSWORD": raw_token,
        }
        cls.category = Category.objects.get(slug="games")
        cls.subcategory = Subcategory.objects.get(
            category=cls.category, slug="north-america"
        )
        cls.title = cls.create_title(1, name="Eligible title")
        cls.artifact = ContentArtifact.objects.create(
            title=cls.title,
            relative_path="eligible.cia",
            byte_size=4321,
        )

    @classmethod
    def create_title(cls, number: int, **overrides) -> Title:
        values = {
            "title_id": f"{number:016X}",
            "name": f"Title {number}",
            "alternative_name": f"Alternative {number}",
            "region": "North America",
            "description": f"Description {number}",
            "filename": f"title-{number}.cia",
            "version": number,
            "category": cls.category,
            "subcategory": cls.subcategory,
        }
        values.update(overrides)
        return Title.objects.create(**values)

    def result_code(self, response) -> int:
        self.assertEqual(response.content[:4], b"RSLT")
        return struct.unpack_from("<I", response.content, 16)[0]

    def test_auth_failure_is_binary_title_result(self):
        response = self.client.get("/nbapi/title/random")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.result_code(response),
            (ResultNamespace.TITLE << 16) | ResultReason.UNAUTHORIZED,
        )

    def test_single_eligible_title_succeeds(self):
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(response.status_code, 200)

    def test_success_is_versioned_full_title_object(self):
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(response.content[:4], b"TITL")
        self.assertEqual(response["x-minimum"], "1.5.11")

    def test_success_contains_selected_database_id_and_full_metadata(self):
        selected = Title.objects.select_related(
            "category", "subcategory", "artifact"
        ).get(pk=self.title.pk)
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(struct.unpack_from("<I", response.content, 80)[0], selected.pk)
        self.assertEqual(response.content, title_payload(selected))

    @patch("apps.catalog.views.secrets.randbelow", return_value=1)
    def test_random_offset_selection_is_deterministic_when_patched(self, randbelow):
        second = self.create_title(2)
        ContentArtifact.objects.create(
            title=second, relative_path="second.cia", byte_size=22
        )
        response = self.client.get("/nbapi/title/random", **self.auth)
        randbelow.assert_called_once_with(2)
        self.assertEqual(struct.unpack_from("<I", response.content, 80)[0], second.pk)

    def test_unlisted_missing_artifact_and_disabled_artifact_are_excluded(self):
        unlisted = self.create_title(2, listed=False)
        ContentArtifact.objects.create(
            title=unlisted, relative_path="unlisted.cia", byte_size=2
        )
        self.create_title(3)
        disabled = self.create_title(4)
        ContentArtifact.objects.create(
            title=disabled,
            relative_path="disabled.cia",
            byte_size=4,
            enabled=False,
        )
        with patch("apps.catalog.views.secrets.randbelow", return_value=0) as randbelow:
            response = self.client.get("/nbapi/title/random", **self.auth)
        randbelow.assert_called_once_with(1)
        self.assertEqual(
            struct.unpack_from("<I", response.content, 80)[0], self.title.pk
        )

    def test_empty_eligible_pool_returns_versioned_not_found(self):
        self.artifact.enabled = False
        self.artifact.save(update_fields=("enabled",))
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.result_code(response),
            (ResultNamespace.TITLE << 16) | ResultReason.NOT_FOUND,
        )
        self.assertEqual(response["x-minimum"], "1.5.11")
        self.assertIn(b"Title not found.\0", response.content)

    @patch("apps.catalog.views.Title.objects.filter")
    def test_deletion_between_count_and_offset_returns_not_found(self, filter_titles):
        queryset = MagicMock()
        filter_titles.return_value.select_related.return_value.order_by.return_value = (
            queryset
        )
        queryset.count.return_value = 1
        queryset.__getitem__.side_effect = IndexError
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.result_code(response),
            (ResultNamespace.TITLE << 16) | ResultReason.NOT_FOUND,
        )

    def test_success_disables_caching(self):
        response = self.client.get("/nbapi/title/random", **self.auth)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_literal_random_url_resolves_before_integer_detail_route(self):
        match = resolve(reverse("title-random"))
        self.assertEqual(match.url_name, "title-random")
        self.assertEqual(match.func.__name__, "random_title")

    def test_query_parameters_and_request_body_are_rejected(self):
        responses = (
            self.client.get("/nbapi/title/random?unexpected=1", **self.auth),
            self.client.generic(
                "GET",
                "/nbapi/title/random",
                data=b"unexpected",
                content_type="application/octet-stream",
                **self.auth,
            ),
        )
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    self.result_code(response),
                    (ResultNamespace.TITLE << 16) | ResultReason.INVALID_ARGUMENT,
                )
                self.assertEqual(response["x-minimum"], "1.5.11")

    def test_non_get_method_returns_binary_title_error(self):
        response = self.client.post("/nbapi/title/random", **self.auth)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        self.assertEqual(
            self.result_code(response),
            (ResultNamespace.TITLE << 16) | ResultReason.INVALID_OPERATION,
        )
        self.assertEqual(response["x-minimum"], "1.5.11")
