import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.authentication.models import HSAPIToken

RAW_TOKEN = "7K3M-P9RX-4D2W-H8JF-Q6TY"


class CreateClientCommandTests(TestCase):
    def run_command(self, **options):
        output = StringIO()
        call_command(
            "create_client",
            username=options.pop("username", "client"),
            token_name=options.pop("token_name", "Test console"),
            stdout=output,
            **options,
        )
        return output.getvalue()

    def test_new_user_has_unusable_password_and_token_is_printed_once(self):
        with self.patch_token():
            output = self.run_command()

        user = get_user_model().objects.get(username="client")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(output.count(RAW_TOKEN), 1)
        self.assertNotEqual(user.hsapi_tokens.get().token_hash, RAW_TOKEN)

    def test_existing_user_is_preserved_and_gets_an_additional_token(self):
        user = get_user_model().objects.create_user(
            username="Client", password="keep-password", is_staff=True
        )
        HSAPIToken.issue(user=user, name="Existing")

        with self.patch_token():
            self.run_command(username="client")

        user.refresh_from_db()
        self.assertEqual(user.username, "Client")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password("keep-password"))
        self.assertEqual(user.hsapi_tokens.count(), 2)

    def test_inactive_existing_user_is_rejected(self):
        get_user_model().objects.create_user(username="client", is_active=False)

        with self.assertRaisesMessage(CommandError, "existing user is inactive"):
            self.run_command()
        self.assertFalse(HSAPIToken.objects.exists())

    def test_rotate_revokes_existing_tokens_before_issuing_replacement(self):
        user = get_user_model().objects.create_user(username="client")
        old_token, _ = HSAPIToken.issue(user=user, name="Old")

        with self.patch_token():
            self.run_command(rotate=True)

        old_token.refresh_from_db()
        self.assertIsNotNone(old_token.revoked_at)
        self.assertEqual(user.hsapi_tokens.filter(revoked_at__isnull=True).count(), 1)

    def test_json_output_is_machine_safe_and_raw_token_authenticates(self):
        with self.patch_token():
            output = self.run_command(format="json")

        self.assertEqual(json.loads(output), {"username": "client", "token": RAW_TOKEN})
        response = self.client.get(
            "/nbapi/title-index",
            HTTP_X_AUTH_USER="client",
            HTTP_X_AUTH_PASSWORD=RAW_TOKEN,
        )
        self.assertEqual((response.status_code, response.content[:4]), (200, b"TIDX"))

    def test_ambiguous_case_insensitive_username_is_rejected(self):
        User = get_user_model()
        User.objects.create_user(username="client")
        User.objects.create_user(username="CLIENT")

        with self.assertRaisesMessage(CommandError, "Multiple users match"):
            self.run_command(username="Client")

    def test_empty_username_and_token_name_are_rejected(self):
        for options, message in (
            ({"username": " "}, "Username must not be empty"),
            ({"token_name": " "}, "Token name must not be empty"),
        ):
            with self.subTest(options=options):
                with self.assertRaisesMessage(CommandError, message):
                    self.run_command(**options)

        with self.assertRaisesMessage(CommandError, "Invalid token name"):
            self.run_command(token_name="x" * 101)

    @staticmethod
    def patch_token():
        from unittest.mock import patch

        return patch.object(HSAPIToken, "generate_raw_token", return_value=RAW_TOKEN)
