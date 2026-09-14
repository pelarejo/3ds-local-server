"""Minimal parsing for unencrypted metadata embedded in CIA containers."""

from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from pyctr.common import PyCTRError
from pyctr.type.tmd import TitleMetadataReader

CIA_HEADER_SIZE = 0x2020
SECTION_ALIGNMENT = 0x40


class InvalidCIAError(ValueError):
    """Raised when required CIA or TMD metadata is malformed or truncated."""


@dataclass(frozen=True)
class CIAMetadata:
    """Non-cryptographic metadata extracted from a CIA's plaintext TMD."""

    title_id: str


def _aligned(size: int) -> int:
    return (size + SECTION_ALIGNMENT - 1) & ~(SECTION_ALIGNMENT - 1)


def parse_cia_metadata(cia: BinaryIO, file_size: int) -> CIAMetadata:
    """Read the CIA header and plaintext TMD without loading title keys."""
    cia.seek(0)
    header = cia.read(0x20)
    if len(header) != 0x20:
        raise InvalidCIAError("CIA header is truncated.")

    header_size = int.from_bytes(header[0x00:0x04], "little")
    if header_size != CIA_HEADER_SIZE:
        raise InvalidCIAError("CIA header size is invalid.")

    certificate_size = int.from_bytes(header[0x08:0x0C], "little")
    ticket_size = int.from_bytes(header[0x0C:0x10], "little")
    tmd_size = int.from_bytes(header[0x10:0x14], "little")
    tmd_offset = (
        _aligned(header_size) + _aligned(certificate_size) + _aligned(ticket_size)
    )
    if not tmd_size or tmd_offset + tmd_size > file_size:
        raise InvalidCIAError("CIA TMD is missing or truncated.")

    cia.seek(tmd_offset)
    tmd_data = cia.read(tmd_size)
    if len(tmd_data) != tmd_size:
        raise InvalidCIAError("CIA TMD is truncated.")
    try:
        tmd = TitleMetadataReader.load(BytesIO(tmd_data))
    except (PyCTRError, UnicodeError, ValueError, IndexError) as error:
        raise InvalidCIAError(f"CIA TMD is invalid: {error}") from error

    return CIAMetadata(title_id=tmd.title_id.upper())
