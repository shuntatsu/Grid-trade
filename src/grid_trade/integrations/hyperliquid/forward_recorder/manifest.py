import json
from datetime import datetime

from grid_trade.integrations.hyperliquid.forward_recorder.contracts import ForwardSegment

FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION = "hyperliquid-forward-segment-manifest-v1"


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def canonical_forward_segment_manifest_bytes(segment: ForwardSegment) -> bytes:
    raw = segment.raw_object
    payload = {
        "continuity_epoch": segment.continuity_epoch,
        "manifest_schema_version": FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION,
        "raw_object": {
            "acquired_at": _iso_utc(raw.acquired_at),
            "byte_length": raw.byte_length,
            "collector_schema_version": raw.collector_schema_version,
            "complete": raw.complete,
            "dataset_type": raw.identity.dataset_type.value,
            "decoder_schema_version": raw.decoder_schema_version,
            "instrument": raw.identity.instrument,
            "receive_end_ns": raw.receive_end_ns,
            "receive_start_ns": raw.receive_start_ns,
            "sha256": raw.identity.sha256,
            "source_end_ns": raw.source_end_ns,
            "source_family": raw.identity.source_family.value,
            "source_locator": raw.source_locator,
            "source_start_ns": raw.source_start_ns,
        },
        "record_count": segment.record_count,
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


__all__ = [
    "FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION",
    "canonical_forward_segment_manifest_bytes",
]
