from datetime import datetime

from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
    sha256_bytes,
)

ARCHIVE_COLLECTOR_SCHEMA_VERSION = "hyperliquid-archive-object-v1"
_ARCHIVE_PREFIX = "s3://hyperliquid-archive/"


def raw_object_ref_from_archive_bytes(
    payload: bytes,
    *,
    instrument: str,
    dataset_type: DatasetType,
    source_locator: str,
    acquired_at: datetime,
    decoder_schema_version: str,
    source_start_ns: int | None = None,
    source_end_ns: int | None = None,
) -> RawObjectRef:
    if not source_locator.startswith(_ARCHIVE_PREFIX):
        raise ValueError("archive source locator must use the official hyperliquid-archive bucket")
    if not payload:
        raise ValueError("archive payload must be non-empty")

    digest = sha256_bytes(payload)
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=dataset_type,
            instrument=instrument,
            sha256=digest,
        ),
        byte_length=len(payload),
        acquired_at=acquired_at,
        source_locator=source_locator,
        collector_schema_version=ARCHIVE_COLLECTOR_SCHEMA_VERSION,
        decoder_schema_version=decoder_schema_version,
        source_start_ns=source_start_ns,
        source_end_ns=source_end_ns,
    )


__all__ = ["ARCHIVE_COLLECTOR_SCHEMA_VERSION", "raw_object_ref_from_archive_bytes"]
