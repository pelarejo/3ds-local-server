import re
from collections.abc import Iterator
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from apps.authentication.views import header_auth_required
from threehs_backend.nb import (
    ResultNamespace,
    ResultReason,
    result_response,
    token_payload,
    versioned_response,
)

from .models import ContentArtifact, DownloadGrant

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)\Z")


def _artifact_path(artifact: ContentArtifact) -> Path:
    root = Path(settings.CONTENT_ROOT).resolve()
    relative = Path(artifact.relative_path)
    if relative.is_absolute():
        raise ValueError("absolute artifact paths are forbidden")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("artifact path escapes CONTENT_ROOT")
    return resolved


def _file_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            chunk = source.read(min(settings.CONTENT_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if size == 0:
        raise IndexError("unsatisfiable byte range")
    if "," in value:
        raise ValueError("multiple ranges are unsupported")
    match = RANGE_RE.fullmatch(value.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise ValueError("invalid byte range")
    first, last = match.groups()
    if first:
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or end < start:
            raise IndexError("unsatisfiable byte range")
        return start, min(end, size - 1)
    suffix = int(last)
    if suffix <= 0:
        raise IndexError("unsatisfiable byte range")
    return max(size - suffix, 0), size - 1


@header_auth_required(ResultNamespace.TOKEN)
def request_download(request: HttpRequest, id: int) -> HttpResponse:
    try:
        artifact = ContentArtifact.objects.select_related("title").get(
            title_id=id, enabled=True, title__listed=True
        )
        path = _artifact_path(artifact)
        if not path.is_file() or path.stat().st_size != artifact.byte_size:
            raise OSError(
                "artifact is absent or its size does not match catalog metadata"
            )
    except (ContentArtifact.DoesNotExist, OSError, ValueError):
        return result_response(
            ResultNamespace.TITLE,
            ResultReason.NOT_FOUND,
            "Download is unavailable.",
            status=404,
        )
    grant, raw_token = DownloadGrant.issue(artifact=artifact, user=request.user)
    return versioned_response(token_payload(grant, raw_token))


def download(request: HttpRequest, id: int) -> HttpResponse:
    raw_token = request.GET.get("token", "")
    try:
        token_hash = DownloadGrant.hash_token(raw_token)
        grant = DownloadGrant.objects.select_related("artifact", "artifact__title").get(
            token_hash=token_hash, artifact__title_id=id
        )
        if not grant.is_valid:
            raise DownloadGrant.DoesNotExist
        path = _artifact_path(grant.artifact)
        actual_size = path.stat().st_size
        if not path.is_file() or actual_size != grant.artifact.byte_size:
            raise OSError
    except (UnicodeError, DownloadGrant.DoesNotExist, OSError, ValueError):
        return result_response(
            ResultNamespace.TOKEN,
            ResultReason.UNAUTHORIZED,
            "Invalid or expired download token.",
            status=401,
        )

    try:
        byte_range = _parse_range(request.headers.get("Range"), actual_size)
    except ValueError:
        return result_response(
            ResultNamespace.TOKEN,
            ResultReason.INVALID_ARGUMENT,
            "Only one valid byte range is supported.",
            status=400,
        )
    except IndexError:
        response = result_response(
            ResultNamespace.TOKEN,
            ResultReason.INVALID_ARGUMENT,
            "Requested byte range is unsatisfiable.",
            status=416,
        )
        response["Accept-Ranges"] = "bytes"
        response["Content-Range"] = f"bytes */{actual_size}"
        return response

    if byte_range is None:
        start, end, status = 0, actual_size - 1, 200
    else:
        start, end, status = *byte_range, 206
    length = max(end - start + 1, 0)
    response = StreamingHttpResponse(
        _file_chunks(path, start, length),
        status=status,
        content_type="application/octet-stream",
    )
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    safe_filename = (
        Path(grant.artifact.title.filename)
        .name.replace('"', "_")
        .replace("\r", "_")
        .replace("\n", "_")
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    response["x-minimum"] = settings.NB_MINIMUM_VERSION
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{actual_size}"
    return response
