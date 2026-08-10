import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SourceFamily(StrEnum):
    ARCHIVE = "archive"
    NODE = "node"
    WEBSOCKET = "websocket"
    INFO = "info"


class DatasetType(StrEnum):
    L2_BOOK = "l2_book"
    TRADES = "trades"
    FUNDING_REFERENCE = "funding_reference"
    VENUE_METADATA = "venue_metadata"


class DatasetAcceptance(StrEnum):
    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    REJECTED = "rejected"


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_sha256(value: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_optional_range(
    start: int | None,
    end: int | None,
    *,
    label: str,
) -> None:
    for value in (start, end):
        if value is not None and value < 0:
            raise ValueError(f"{label} values must be non-negative")
    if start is not None and end is not None and start > end:
        raise ValueError(f"{label} range must not be reversed")


def sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class RawObjectIdentity:
    source_family: SourceFamily
    dataset_type: DatasetType
    instrument: str
    sha256: str

    def __post_init__(self) -> None:
        _require_non_empty(self.instrument, field="instrument")
        _require_sha256(self.sha256)


@dataclass(frozen=True, slots=True)
class RawObjectRef:
    identity: RawObjectIdentity
    byte_length: int
    acquired_at: datetime
    source_locator: str
    collector_schema_version: str
    decoder_schema_version: str
    source_start_ns: int | None = None
    source_end_ns: int | None = None
    receive_start_ns: int | None = None
    receive_end_ns: int | None = None
    complete: bool = True

    def __post_init__(self) -> None:
        if self.byte_length < 0:
            raise ValueError("byte_length must be non-negative")
        if self.complete and self.byte_length == 0:
            raise ValueError("complete raw object must contain bytes")
        _require_utc(self.acquired_at, field="acquired_at")
        _require_non_empty(self.source_locator, field="source_locator")
        _require_non_empty(self.collector_schema_version, field="collector_schema_version")
        _require_non_empty(self.decoder_schema_version, field="decoder_schema_version")
        _validate_optional_range(
            self.source_start_ns,
            self.source_end_ns,
            label="source timestamp",
        )
        _validate_optional_range(
            self.receive_start_ns,
            self.receive_end_ns,
            label="receive timestamp",
        )


__all__ = [
    "DatasetAcceptance",
    "DatasetType",
    "RawObjectIdentity",
    "RawObjectRef",
    "SourceFamily",
    "sha256_bytes",
]
