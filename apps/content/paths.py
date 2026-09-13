"""Safe filesystem path resolution for catalog content artifacts."""

from pathlib import Path

from django.conf import settings


def resolve_content_path(relative_path: str) -> Path:
    """Resolve a traversal-free relative path contained by CONTENT_ROOT."""
    root = Path(settings.CONTENT_ROOT).resolve()
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("Absolute artifact paths are forbidden.")
    if ".." in relative.parts:
        raise ValueError("Artifact path traversal is forbidden.")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Artifact path escapes CONTENT_ROOT.")
    return resolved
