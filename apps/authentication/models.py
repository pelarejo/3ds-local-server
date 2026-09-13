import hashlib
import hmac
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Project user model; intentionally small, but safe to extend later."""

    pass


class HSAPIToken(models.Model):
    """Revocable API credential used by a 3HS client on behalf of a user."""

    TOKEN_PREFIX = "hsapi_"
    DISPLAY_PREFIX_LENGTH = 14

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hsapi_tokens",
    )
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=DISPLAY_PREFIX_LENGTH, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} ({self.token_prefix}...)"

    @classmethod
    def generate_raw_token(cls) -> str:
        """Generate a cryptographically secure, URL-safe API token."""
        return f"{cls.TOKEN_PREFIX}{secrets.token_urlsafe(32)}"

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Return a keyed digest suitable for storage and indexed lookup."""
        return hmac.new(
            settings.SECRET_KEY.encode(), raw_token.encode(), hashlib.sha256
        ).hexdigest()

    def set_token(self, raw_token: str | None = None) -> str:
        """Set a newly generated credential and return its one-time raw value."""
        raw_token = raw_token or self.generate_raw_token()
        self.token_hash = self.hash_token(raw_token)
        self.token_prefix = raw_token[: self.DISPLAY_PREFIX_LENGTH]
        return raw_token

    @classmethod
    def issue(cls, *, user: User, name: str) -> tuple["HSAPIToken", str]:
        """Create a token for a user and return the raw value exactly once."""
        token = cls(user=user, name=name)
        raw_token = token.set_token()
        token.save()
        return token, raw_token
