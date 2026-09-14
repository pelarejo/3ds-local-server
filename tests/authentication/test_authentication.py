import struct
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.authentication.models import HSAPIToken


class AuthenticationRouteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="client", password="account-password"
        )
        self.token, self.raw_token = HSAPIToken.issue(
            user=self.user, name="Living-room console"
        )

    def auth(self, username: str = "client", token: str | None = None) -> dict:
        return {
            "HTTP_X_AUTH_USER": username,
            "HTTP_X_AUTH_PASSWORD": self.raw_token if token is None else token,
        }

    def test_auth_failure_is_binary_result(self):
        response = self.client.get("/nbapi/title-index")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.content[:4], b"RSLT")
        self.assertEqual(
            struct.unpack_from("<I", response.content, 16)[0], (7 << 16) | 1
        )

    def test_matching_username_and_token_authenticate_as_owner(self):
        response = self.client.get("/nbapi/title-index", **self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:4], b"TIDX")

    def test_token_authentication_is_case_insensitive_and_hyphens_are_optional(self):
        variants = (self.raw_token.lower(), self.raw_token.replace("-", ""))
        for token in variants:
            with self.subTest(token=token):
                response = self.client.get(
                    "/nbapi/title-index", **self.auth(token=token)
                )
                self.assertEqual(
                    (response.status_code, response.content[:4]), (200, b"TIDX")
                )

    def test_wrong_username_token_and_account_password_are_rejected(self):
        attempts = (
            self.auth(username="someone-else"),
            self.auth(token="7K3M-P9RX-4D2W-H8JF-Q6TZ"),
            self.auth(token="account-password"),
            self.auth(token=""),
            self.auth(token="IIII-IIII-IIII-IIII-IIII"),
        )
        for headers in attempts:
            with self.subTest(headers=headers):
                response = self.client.get("/nbapi/title-index", **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.content[:4], b"RSLT")

    def test_inactive_user_is_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        response = self.client.get("/nbapi/title-index", **self.auth())
        self.assertEqual((response.status_code, response.content[:4]), (401, b"RSLT"))

    def test_revoked_token_is_rejected(self):
        self.token.revoked_at = timezone.now()
        self.token.save(update_fields=("revoked_at",))
        response = self.client.get("/nbapi/title-index", **self.auth())
        self.assertEqual((response.status_code, response.content[:4]), (401, b"RSLT"))

    def test_token_is_hashed_and_only_safe_prefix_is_stored(self):
        self.token.refresh_from_db()
        self.assertNotEqual(self.token.token_hash, self.raw_token)
        self.assertEqual(self.token.token_hash, HSAPIToken.hash_token(self.raw_token))
        self.assertEqual(
            self.token.token_prefix,
            self.raw_token[: HSAPIToken.DISPLAY_PREFIX_LENGTH],
        )
        self.assertNotIn(self.raw_token, str(self.token.__dict__))

    def test_generated_token_is_grouped_crockford_base32_with_100_bits(self):
        groups = self.raw_token.split("-")
        self.assertEqual([len(group) for group in groups], [4, 4, 4, 4, 4])
        self.assertTrue(set("".join(groups)).issubset(set(HSAPIToken.TOKEN_ALPHABET)))


class HSAPITokenAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin", password="password", email="admin@example.com"
        )
        self.other_user = get_user_model().objects.create_user(username="other")
        self.client.force_login(self.admin_user)

    @patch.object(
        HSAPIToken,
        "generate_raw_token",
        return_value="7K3M-P9RX-4D2W-H8JF-Q6TY",
    )
    def test_admin_assigns_owner_and_displays_raw_token_once(self, _generate_token):
        raw_token = "7K3M-P9RX-4D2W-H8JF-Q6TY"
        response = self.client.post(
            reverse("admin:authentication_hsapitoken_add"),
            {
                "name": "Admin-created token",
                "user": self.other_user.pk,
                "_save": "Save",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        token = HSAPIToken.objects.get(name="Admin-created token")
        self.assertEqual(token.user, self.admin_user)
        self.assertEqual(token.token_hash, HSAPIToken.hash_token(raw_token))
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertContains(response, raw_token)

        later_response = self.client.get(
            reverse("admin:authentication_hsapitoken_changelist")
        )
        self.assertNotContains(later_response, raw_token)
