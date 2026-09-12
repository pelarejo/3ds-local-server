"""Serializers for the little-endian, 32-bit aligned nblib wire format."""

import struct
from enum import IntEnum
from typing import Iterable

from django.conf import settings
from django.http import HttpResponse

NB_CONTENT_TYPE = "application/octet-stream"


class ResultNamespace(IntEnum):
    TITLE = 1
    CATEGORY = 2
    SUBCATEGORY = 3
    TOKEN = 4
    USER = 6
    INDEX = 7
    INTERNAL = 12


class ResultReason(IntEnum):
    SUCCESS = 0
    UNAUTHORIZED = 1
    NOT_FOUND = 2
    INVALID_ARGUMENT = 3
    EXCEPTION_OCCURRED = 5
    INVALID_OPERATION = 7


def _aligned(value: int, alignment: int = 4) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


class Blob:
    """Offset zero means absent in nblib, so every blob begins with a sentinel."""

    def __init__(self) -> None:
        self.data = bytearray(b"\0\0\0\0")

    def add_bytes(self, data: bytes) -> int:
        offset = len(self.data)
        self.data.extend(data)
        self.data.extend(b"\0" * (_aligned(len(self.data)) - len(self.data)))
        return offset

    def add_string(self, value: str) -> int:
        if not value:
            return 0
        encoded = value.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError("NB strings cannot contain NUL bytes")
        return self.add_bytes(encoded + b"\0")

    def add_string_array(self, values: Iterable[str]) -> int:
        encoded = []
        for value in values:
            raw = value.encode("utf-8")
            if not raw or b"\0" in raw:
                raise ValueError(
                    "NB string-array entries must be non-empty and cannot contain "
                    "NUL bytes"
                )
            encoded.append(raw + b"\0")
        if not encoded:
            return 0
        payload = b"\0\0\0\0" + b"".join(encoded)
        payload += b"\0" * (_aligned(len(payload)) - len(payload))
        return self.add_bytes(
            struct.pack("<4sIII", b"NBRA", 16, len(encoded), len(payload)) + payload
        )


def single_object(magic: bytes, header: bytes, blob: bytes | bytearray) -> bytes:
    if len(magic) != 4 or len(header) % 4:
        raise ValueError("invalid NB object alignment")
    return (
        struct.pack("<4sIII", magic, 16, len(header), len(blob)) + header + bytes(blob)
    )


def object_array(
    headers: Iterable[bytes], blob: bytes | bytearray, element_size: int
) -> bytes:
    headers = list(headers)
    if element_size % 4 or any(len(header) != element_size for header in headers):
        raise ValueError("invalid NB array element alignment")
    return (
        struct.pack("<4sIIII", b"NBAR", 20, len(headers), element_size, len(blob))
        + b"".join(headers)
        + bytes(blob)
    )


def result_payload(
    namespace: ResultNamespace, reason: ResultReason, message: str
) -> bytes:
    blob = Blob()
    message_offset = blob.add_string(message)
    code = (int(namespace) << 16) | int(reason)
    return single_object(b"RSLT", struct.pack("<II", code, message_offset), blob.data)


def result_response(
    namespace: ResultNamespace, reason: ResultReason, message: str, *, status: int
) -> HttpResponse:
    return HttpResponse(
        result_payload(namespace, reason, message),
        status=status,
        content_type=NB_CONTENT_TYPE,
    )


def versioned_response(
    payload: bytes, *, status: int = 200, content_type: str = NB_CONTENT_TYPE
) -> HttpResponse:
    response = HttpResponse(payload, status=status, content_type=content_type)
    response["x-minimum"] = settings.NB_MINIMUM_VERSION
    return response


def partial_titles_payload(titles) -> bytes:
    blob = Blob()
    headers = []
    for title in titles:
        alt_names = list(title.alternative_names or [])
        headers.append(
            struct.pack(
                "<QQQQIIIIHBBB3xII",
                title.title_id_int,
                title.artifact_size,
                title.flags,
                title.download_count,
                title.pk,
                blob.add_string(title.name),
                blob.add_string(title.alternative_name),
                blob.add_string(title.product_code),
                title.version,
                title.content_type,
                title.category.protocol_id,
                title.subcategory.protocol_id,
                blob.add_string_array(alt_names),
                title.preferred_alternative_index,
            )
        )
    return object_array(headers, blob.data, 64)


def title_payload(title) -> bytes:
    blob = Blob()
    header = bytearray(144)
    seed = bytes(title.seed or b"")
    checksum = bytes(title.file_checksum or b"")
    if len(seed) not in (0, 16) or len(checksum) not in (0, 32):
        raise ValueError("seed and checksum must be exactly 16 and 32 bytes when set")
    header[0:16] = seed.ljust(16, b"\0")
    struct.pack_into(
        "<QQQQQQ",
        header,
        16,
        title.artifact_size,
        title.title_id_int,
        int(title.added_at.timestamp()),
        int(title.updated_at.timestamp()),
        title.download_count,
        title.flags,
    )
    struct.pack_into(
        "<IIIIIII",
        header,
        64,
        title.pk,
        blob.add_string(title.name),
        blob.add_string(title.alternative_name),
        blob.add_string(title.region),
        blob.add_string(title.filename),
        blob.add_string(title.description),
        blob.add_string(title.product_code),
    )
    struct.pack_into(
        "<HBBBB",
        header,
        92,
        title.version,
        title.content_type,
        title.category.protocol_id,
        title.subcategory.protocol_id,
        title.listed,
    )
    struct.pack_into(
        "<II",
        header,
        100,
        blob.add_string_array(title.alternative_names or []),
        title.preferred_alternative_index,
    )
    header[108:140] = checksum.ljust(32, b"\0")
    return single_object(b"TITL", header, blob.data)


def token_payload(grant, raw_token: str) -> bytes:
    blob = Blob()
    header = struct.pack(
        "<QII",
        int(grant.expires_at.timestamp()),
        grant.artifact.title_id,
        blob.add_string(raw_token),
    )
    return single_object(b"TOKN", header, blob.data)


def _index_meta(titles) -> bytes:
    title_list = list(titles)
    return struct.pack(
        "<III4xQQ",
        len(title_list),
        len(title_list),
        sum(1 for title in title_list if title.content_type == 1),
        sum(title.artifact_size for title in title_list),
        sum(title.download_count for title in title_list),
    )


def index_payload(categories, generated_at: int) -> bytes:
    outer_blob = Blob()
    category_blob = Blob()
    category_headers = []
    all_titles = []
    for category in categories:
        category_titles = list(
            category.titles.filter(listed=True).select_related("artifact")
        )
        all_titles.extend(category_titles)
        sub_blob = Blob()
        sub_headers = []
        for subcategory in category.subcategories.all():
            sub_titles = [
                title
                for title in category_titles
                if title.subcategory_id == subcategory.pk
            ]
            sub_headers.append(
                _index_meta(sub_titles)
                + struct.pack(
                    "<IIII",
                    subcategory.protocol_id,
                    sub_blob.add_string(subcategory.display_name),
                    sub_blob.add_string(subcategory.slug),
                    sub_blob.add_string(subcategory.description),
                )
            )
        sub_array_offset = (
            category_blob.add_bytes(object_array(sub_headers, sub_blob.data, 48))
            if sub_headers
            else 0
        )
        category_headers.append(
            _index_meta(category_titles)
            + struct.pack(
                "<IIIIB3xI",
                category.protocol_id,
                category_blob.add_string(category.display_name),
                category_blob.add_string(category.slug),
                category_blob.add_string(category.description),
                category.priority,
                sub_array_offset,
            )
        )
    categories_offset = (
        outer_blob.add_bytes(object_array(category_headers, category_blob.data, 56))
        if category_headers
        else 0
    )
    index_header = _index_meta(all_titles) + struct.pack(
        "<I4xQ", categories_offset, generated_at
    )
    return single_object(b"TIDX", index_header, outer_blob.data)
