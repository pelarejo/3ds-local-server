import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.catalog.models import Title


class ContentArtifact(models.Model):
    """Filesystem-backed CIA associated with a catalog title."""

    title = models.OneToOneField(
        Title, related_name="artifact", on_delete=models.CASCADE
    )
    relative_path = models.CharField(max_length=500)
    byte_size = models.PositiveBigIntegerField()
    enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.title} ({self.relative_path})"


class DownloadGrant(models.Model):
    """Hashed, expiring authorization token for downloading one artifact."""

    artifact = models.ForeignKey(
        ContentArtifact, related_name="grants", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="download_grants",
        on_delete=models.CASCADE,
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, *, artifact: ContentArtifact, user) -> tuple["DownloadGrant", str]:
        raw_token = secrets.token_urlsafe(32)
        grant = cls.objects.create(
            artifact=artifact,
            user=user,
            token_hash=cls.hash_token(raw_token),
            expires_at=timezone.now() + settings.DOWNLOAD_TOKEN_TTL,
        )
        return grant, raw_token

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("ascii", errors="strict")).hexdigest()

    @property
    def is_valid(self) -> bool:
        return self.expires_at > timezone.now() and self.artifact.enabled
