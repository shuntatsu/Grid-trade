from datetime import datetime

from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
    sha256_bytes,
)

NODE_DATA_COLLECTOR_SCHEMA_VERSION = "hyperliquid-node-data-object-v1"
_NODE_DATA_PREFIX = "s3://hl-mainnet-node-data/"


def raw_object_ref_from_node_bytes(
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
    if not source_locator.startswith(_NODE_DATA_PREFIX):
        raise ValueError(
            "node-data source locator must use the official hl-mainnet-node-data bucket"
        )
    if not payload:
        raise ValueError("node-data payload must be non-empty")

    digest = sha256_bytes(payload)
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.NODE,
            dataset_type=dataset_type,
            instrument=instrument,
            sha256=digest,
        ),
        byte_length=len(payload),
        acquired_at=acquired_at,
        source_locator=source_locator,
        collector_schema_version=NODE_DATA_COLLECTOR_SCHEMA_VERSION,
        decoder_schema_version=decoder_schema_version,
        source_start_ns=source_start_ns,
        source_end_ns=source_end_ns,
    )


__all__ = ["NODE_DATA_COLLECTOR_SCHEMA_VERSION", "raw_object_ref_from_node_bytes"]
