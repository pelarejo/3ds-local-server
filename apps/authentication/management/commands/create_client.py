import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.authentication.models import HSAPIToken


class Command(BaseCommand):
    help = "Create or reuse a client user and issue a one-time HSAPI token."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--token-name", required=True)
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Revoke all existing tokens for this user before issuing a new one.",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format. The raw token is always shown exactly once.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        token_name = options["token_name"].strip()
        if not username:
            raise CommandError("Username must not be empty.")
        if not token_name:
            raise CommandError("Token name must not be empty.")
        try:
            token_name = HSAPIToken._meta.get_field("name").clean(token_name, None)
        except ValidationError as error:
            raise CommandError(f"Invalid token name: {error}") from error

        User = get_user_model()
        with transaction.atomic():
            matches = list(User.objects.filter(username__iexact=username)[:2])
            if len(matches) > 1:
                raise CommandError(
                    "Multiple users match this username case-insensitively."
                )
            if matches:
                user = matches[0]
                if not user.is_active:
                    raise CommandError("The existing user is inactive.")
            else:
                user = User(username=username, is_active=True, is_staff=False)
                user.set_unusable_password()
                user.full_clean()
                user.save()

            if options["rotate"]:
                HSAPIToken.objects.filter(user=user, revoked_at__isnull=True).update(
                    revoked_at=timezone.now()
                )
            _, raw_token = HSAPIToken.issue(user=user, name=token_name)

        if options["format"] == "json":
            self.stdout.write(
                json.dumps({"username": user.username, "token": raw_token})
            )
        else:
            self.stdout.write("3LS client credentials")
            self.stdout.write(f"Username: {user.username}")
            self.stdout.write(f"Token: {raw_token}")
            self.stdout.write("Store this token now; it cannot be shown again.")
