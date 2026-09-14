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
    """Revocable API credential used by a 3LS client on behalf of a user."""

    TOKEN_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    TOKEN_LENGTH = 20
    TOKEN_GROUP_LENGTH = 4
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
        """Generate a formatted API token with 100 bits of entropy."""
        canonical = "".join(
            secrets.choice(cls.TOKEN_ALPHABET) for _ in range(cls.TOKEN_LENGTH)
        )
        return cls.format_token(canonical)

    @classmethod
    def normalize_token(cls, raw_token: str) -> str:
        """Return the canonical token form or raise ValueError when invalid."""
        canonical = raw_token.replace("-", "").upper()
        if len(canonical) != cls.TOKEN_LENGTH or any(
            character not in cls.TOKEN_ALPHABET for character in canonical
        ):
            raise ValueError("Invalid API token format.")
        return canonical

    @classmethod
    def format_token(cls, raw_token: str) -> str:
        """Return a canonical token grouped for human-readable display."""
        canonical = cls.normalize_token(raw_token)
        return "-".join(
            canonical[index : index + cls.TOKEN_GROUP_LENGTH]
            for index in range(0, cls.TOKEN_LENGTH, cls.TOKEN_GROUP_LENGTH)
        )

    @classmethod
    def hash_token(cls, raw_token: str) -> str:
        """Return a keyed digest suitable for storage and indexed lookup."""
        canonical = cls.normalize_token(raw_token)
        return hmac.new(
            settings.SECRET_KEY.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()

    def set_token(self, raw_token: str | None = None) -> str:
        """Set a newly generated credential and return its one-time raw value."""
        raw_token = raw_token if raw_token is not None else self.generate_raw_token()
        self.token_hash = self.hash_token(raw_token)
        self.token_prefix = self.format_token(raw_token)[: self.DISPLAY_PREFIX_LENGTH]
        return raw_token

    @classmethod
    def issue(cls, *, user: User, name: str) -> tuple["HSAPIToken", str]:
        """Create a token for a user and return the raw value exactly once."""
        token = cls(user=user, name=name)
        raw_token = token.set_token()
        token.save()
        return token, raw_token
