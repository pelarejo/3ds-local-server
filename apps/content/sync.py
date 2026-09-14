"""Read-only CONTENT_ROOT discovery and database synchronization."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import CatalogEntry, Category, Subcategory, Title

from .cia import InvalidCIAError, parse_cia_metadata
from .models import ContentArtifact

REGION_SUBCATEGORY_SLUGS = {
    "AUS": "australia",
    "CAN": "canada",
    "CHN": "china",
    "EUR": "europe",
    "FRA": "france",
    "GER": "germany",
    "ITA": "italy",
    "JPN": "japan",
    "KOR": "korea",
    "NLD": "netherlands",
    "RUS": "russia",
    "SPA": "spain",
    "TWN": "taiwan",
    "UKV": "united-kingdom",
    "USA": "north-america",
    "WLD": "worldwide",
}
REGION_ALIASES = {
    "aus": "AUS",
    "australia": "AUS",
    "can": "CAN",
    "canada": "CAN",
    "chn": "CHN",
    "china": "CHN",
    "e": "EUR",
    "eu": "EUR",
    "eur": "EUR",
    "europe": "EUR",
    "fra": "FRA",
    "france": "FRA",
    "ger": "GER",
    "germany": "GER",
    "ita": "ITA",
    "italy": "ITA",
    "j": "JPN",
    "japan": "JPN",
    "jp": "JPN",
    "jpn": "JPN",
    "kor": "KOR",
    "korea": "KOR",
    "netherlands": "NLD",
    "nld": "NLD",
    "rus": "RUS",
    "russia": "RUS",
    "spa": "SPA",
    "spain": "SPA",
    "taiwan": "TWN",
    "twn": "TWN",
    "u": "USA",
    "united states": "USA",
    "us": "USA",
    "usa": "USA",
    "uk": "UKV",
    "ukv": "UKV",
    "united kingdom": "UKV",
    "w": "WLD",
    "wld": "WLD",
    "world": "WLD",
    "worldwide": "WLD",
}


@dataclass
class SyncResult:
    """Counters and human-readable details from one explicit synchronization."""

    scanned: int = 0
    titles_created: int = 0
    titles_updated: int = 0
    artifacts_created: int = 0
    artifacts_updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncOutcome:
    """Database changes made for one unambiguous scanned CIA."""

    artifact_status: str
    title_created: bool = False
    title_updated: bool = False
    detail: str | None = None


def _region_code(value: str) -> str | None:
    normalized = " ".join(value.strip().casefold().split())
    return REGION_ALIASES.get(normalized)


def _inferred_region(filename: str) -> str | None:
    regions = {
        region
        for group in re.findall(r"\(([^()]*)\)", filename)
        if (region := _region_code(group)) is not None
    }
    return regions.pop() if len(regions) == 1 else None


def _catalog_entry(path: Path, title_id: str) -> tuple[CatalogEntry, bool]:
    entries = list(
        CatalogEntry.objects.filter(title_id__iexact=title_id).order_by(
            "source", "external_id", "pk"
        )
    )
    if not entries:
        raise ValueError("no matching staging catalog entry")
    if len(entries) == 1:
        return entries[0], False

    inferred_region = _inferred_region(path.name)
    matches = [
        entry
        for entry in entries
        if inferred_region is not None and _region_code(entry.region) == inferred_region
    ]
    if len(matches) == 1:
        return matches[0], False
    return entries[0], True


def _title_values(
    path: Path, title_id: str, *, require_entry: bool
) -> tuple[dict, str | None]:
    try:
        entry, ambiguous = _catalog_entry(path, title_id)
    except ValueError:
        if require_entry:
            raise
        return {"filename": path.name}, None
    try:
        category = Category.objects.get(slug="games")
    except Category.DoesNotExist as error:
        raise ValueError("Games category is missing") from error
    if ambiguous:
        subcategory_slug = "uncategorised"
    else:
        subcategory_slug = REGION_SUBCATEGORY_SLUGS.get(
            _region_code(entry.region) or entry.region.upper()
        )
        if subcategory_slug is None:
            raise ValueError(f"region '{entry.region}' cannot be mapped safely")
    try:
        subcategory = Subcategory.objects.get(category=category, slug=subcategory_slug)
    except Subcategory.DoesNotExist as error:
        raise ValueError(
            f"subcategory '{subcategory_slug}' is missing from Games"
        ) from error

    values = {
        "name": entry.name,
        "product_code": entry.product_code,
        "region": _region_code(entry.region) or entry.region.upper(),
        "filename": path.name,
        "category": category,
        "subcategory": subcategory,
    }
    detail = None
    if ambiguous:
        detail = (
            f"Ambiguous catalog match for {path.name}; selected "
            f"{entry.source}/{entry.external_id} deterministically and assigned "
            "Uncategorised."
        )
    return values, detail


def _new_title(path: Path, title_id: str) -> tuple[Title, str | None]:
    values, detail = _title_values(path, title_id, require_entry=True)
    title = Title(title_id=title_id, **values)
    title.full_clean()
    return title, detail


def _refresh_title(title: Title, path: Path, title_id: str) -> tuple[bool, str | None]:
    values, detail = _title_values(path, title_id, require_entry=False)
    if len(values) > 1:
        candidate = Title(title_id=title_id, **values)
        candidate.full_clean(validate_unique=False)
    else:
        Title._meta.get_field("filename").clean(values["filename"], title)

    changed_fields = []
    for field_name, value in values.items():
        current = getattr(title, field_name)
        current_id = getattr(current, "pk", current)
        value_id = getattr(value, "pk", value)
        if current_id != value_id:
            setattr(title, field_name, value)
            changed_fields.append(field_name)
    if changed_fields:
        title.save(update_fields=changed_fields)
    return bool(changed_fields), detail


def _sync_file(
    path: Path, relative_path: str, title_id: str, byte_size: int, *, force: bool
) -> SyncOutcome:
    titles = list(Title.objects.filter(title_id__iexact=title_id).order_by("pk")[:2])
    if len(titles) > 1:
        raise ValueError("multiple existing titles match this title ID")
    title = titles[0] if titles else None

    path_artifacts = list(
        ContentArtifact.objects.filter(relative_path=relative_path)
        .select_related("title")
        .order_by("pk")[:2]
    )
    if len(path_artifacts) > 1:
        raise ValueError("multiple artifacts already use this relative path")
    path_artifact = path_artifacts[0] if path_artifacts else None
    title_artifact = None
    if title is not None:
        title_artifact = ContentArtifact.objects.filter(title=title).first()

    if path_artifact is not None and (
        title is None or path_artifact.title_id != title.pk
    ):
        raise ValueError("relative path is associated with a different title")
    if title_artifact is not None and path_artifact not in (None, title_artifact):
        raise ValueError("title and relative path point to conflicting artifacts")

    with transaction.atomic():
        if title is None:
            title, detail = _new_title(path, title_id)
            title.save()
            ContentArtifact.objects.create(
                title=title,
                relative_path=relative_path,
                byte_size=byte_size,
            )
            return SyncOutcome("created", title_created=True, detail=detail)
        if title_artifact is not None and not force:
            return SyncOutcome("unchanged")

        title_updated = False
        detail = None
        if force:
            title_updated, detail = _refresh_title(title, path, title_id)
        if title_artifact is None:
            ContentArtifact.objects.create(
                title=title,
                relative_path=relative_path,
                byte_size=byte_size,
            )
            return SyncOutcome("created", title_updated=title_updated, detail=detail)
        changed = (
            title_artifact.relative_path != relative_path
            or title_artifact.byte_size != byte_size
        )
        if changed:
            title_artifact.relative_path = relative_path
            title_artifact.byte_size = byte_size
            title_artifact.save(update_fields=("relative_path", "byte_size"))
            return SyncOutcome("updated", title_updated=title_updated, detail=detail)
    return SyncOutcome("unchanged", title_updated=title_updated, detail=detail)


def synchronize_content_root(*, force: bool = False) -> SyncResult:
    """Synchronize CIA files into database records without modifying the files."""
    result = SyncResult()
    root = Path(settings.CONTENT_ROOT).resolve()
    parsed: dict[str, list[tuple[Path, str, int]]] = defaultdict(list)

    if not root.is_dir():
        result.details.append("CONTENT_ROOT does not exist or is not a directory.")
        return result

    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.suffix.lower() == ".cia" and path.is_file()
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    for path in candidates:
        relative_path = path.relative_to(root).as_posix()
        result.scanned += 1
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("path resolves outside CONTENT_ROOT")
            byte_size = path.stat().st_size
            with path.open("rb") as cia:
                title_id = parse_cia_metadata(cia, byte_size).title_id
        except (InvalidCIAError, OSError, ValueError) as error:
            result.skipped += 1
            result.details.append(f"Skipped {relative_path}: {error}")
            continue
        parsed[title_id].append((path, relative_path, byte_size))

    for title_id, files in parsed.items():
        if len(files) > 1:
            result.skipped += len(files)
            paths = ", ".join(relative_path for _, relative_path, _ in files)
            result.details.append(f"Skipped duplicate title ID {title_id}: {paths}")
            continue
        path, relative_path, byte_size = files[0]
        try:
            outcome = _sync_file(path, relative_path, title_id, byte_size, force=force)
        except (IntegrityError, OSError, ValidationError, ValueError) as error:
            result.skipped += 1
            result.details.append(f"Skipped {relative_path}: {error}")
            continue
        if outcome.detail:
            result.details.append(outcome.detail)
        if outcome.title_updated:
            result.titles_updated += 1
        if outcome.title_created:
            result.titles_created += 1
        if outcome.artifact_status == "created":
            result.artifacts_created += 1
        elif outcome.artifact_status == "updated":
            result.artifacts_updated += 1
        else:
            result.unchanged += 1

    return result
