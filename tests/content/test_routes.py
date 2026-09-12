import struct
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Category, Subcategory, Title
from apps.content.models import ContentArtifact, DownloadGrant
from seeds.catalog import seed_catalog_taxonomy


class ContentRouteTests(TestCase):
    def setUp(self):
        seed_catalog_taxonomy()
        self.tempdir = tempfile.TemporaryDirectory(dir=settings.BASE_DIR)
        self.addCleanup(self.tempdir.cleanup)
        self.settings_override = self.settings(CONTENT_ROOT=Path(self.tempdir.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(
            username="client", password="secret"
        )
        category = Category.objects.get(slug="games")
        subcategory = Subcategory.objects.get(category=category, slug="north-america")
        self.title = Title.objects.create(
            title_id="0004000000ABCDEF",
            name="Download",
            region="North America",
            filename="download.cia",
            category=category,
            subcategory=subcategory,
        )
        self.data = b"0123456789abcdef"
        (Path(self.tempdir.name) / "download.cia").write_bytes(self.data)
        self.artifact = ContentArtifact.objects.create(
            title=self.title, relative_path="download.cia", byte_size=16
        )
        self.auth = {"HTTP_X_AUTH_USER": "client", "HTTP_X_AUTH_PASSWORD": "secret"}

    def issue_token(self) -> str:
        response = self.client.get(f"/nbcontent/{self.title.pk}/request", **self.auth)
        self.assertEqual((response.status_code, response.content[:4]), (200, b"TOKN"))
        self.assertEqual(
            struct.unpack_from("<I", response.content, 24)[0], self.title.pk
        )
        token_offset = struct.unpack_from("<I", response.content, 28)[0]
        return response.content[32 + token_offset :].split(b"\0", 1)[0].decode()

    @staticmethod
    def body(response) -> bytes:
        return b"".join(response.streaming_content)

    def test_token_is_hashed_and_full_download_streams(self):
        token = self.issue_token()
        self.assertNotEqual(DownloadGrant.objects.get().token_hash, token)
        response = self.client.get(f"/nbcontent/{self.title.pk}?token={token}")
        self.assertEqual(
            (
                response.status_code,
                response["Content-Length"],
                response["Accept-Ranges"],
            ),
            (200, "16", "bytes"),
        )
        self.assertEqual(self.body(response), self.data)
        again = self.client.get(
            f"/nbcontent/{self.title.pk}?token={token}", HTTP_RANGE="bytes=8-"
        )
        self.assertEqual((again.status_code, self.body(again)), (206, self.data[8:]))

    def test_explicit_and_suffix_ranges(self):
        token = self.issue_token()
        response = self.client.get(
            f"/nbcontent/{self.title.pk}?token={token}", HTTP_RANGE="bytes=2-5"
        )
        self.assertEqual(
            (response.status_code, response["Content-Range"]), (206, "bytes 2-5/16")
        )
        self.assertEqual(
            (response["Content-Length"], self.body(response)), ("4", b"2345")
        )
        suffix = self.client.get(
            f"/nbcontent/{self.title.pk}?token={token}", HTTP_RANGE="bytes=-4"
        )
        self.assertEqual((suffix.status_code, self.body(suffix)), (206, b"cdef"))

    def test_invalid_multiple_and_unsatisfiable_ranges(self):
        token = self.issue_token()
        multiple = self.client.get(
            f"/nbcontent/{self.title.pk}?token={token}", HTTP_RANGE="bytes=0-1,4-5"
        )
        self.assertEqual((multiple.status_code, multiple.content[:4]), (400, b"RSLT"))
        invalid = self.client.get(
            f"/nbcontent/{self.title.pk}?token={token}", HTTP_RANGE="bytes=99-"
        )
        self.assertEqual(
            (invalid.status_code, invalid["Content-Range"]), (416, "bytes */16")
        )

    def test_missing_expired_and_wrong_title_tokens_are_rejected(self):
        token = self.issue_token()
        grant = DownloadGrant.objects.get()
        grant.expires_at = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=("expires_at",))
        self.assertEqual(
            self.client.get(f"/nbcontent/{self.title.pk}?token={token}").status_code,
            401,
        )
        grant.expires_at = timezone.now() + timedelta(minutes=1)
        grant.save(update_fields=("expires_at",))
        self.assertEqual(
            self.client.get(
                f"/nbcontent/{self.title.pk + 1}?token={token}"
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(f"/nbcontent/{self.title.pk}").status_code, 401
        )

    def test_path_traversal_and_size_mismatch_do_not_issue_tokens(self):
        self.artifact.relative_path = "../Pipfile"
        self.artifact.save(update_fields=("relative_path",))
        self.assertEqual(
            self.client.get(
                f"/nbcontent/{self.title.pk}/request", **self.auth
            ).status_code,
            404,
        )
        self.artifact.relative_path = "download.cia"
        self.artifact.byte_size = 17
        self.artifact.save(update_fields=("relative_path", "byte_size"))
        self.assertEqual(
            self.client.get(
                f"/nbcontent/{self.title.pk}/request", **self.auth
            ).status_code,
            404,
        )
