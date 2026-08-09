from datetime import UTC, datetime

import pytest

from grid_trade.datasets.contracts import (
    DatasetAcceptance,
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)
from grid_trade.datasets.manifest import DatasetManifest, canonical_manifest_bytes


def _raw_ref() -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=DatasetType.L2_BOOK,
            instrument="BTC",
            sha256="a" * 64,
        ),
        byte_length=123,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator="s3://hyperliquid-archive/market_data/example",
        collector_schema_version="hl-archive-v1",
        decoder_schema_version="hl-l2book-v1",
        source_start_ns=100,
        source_end_ns=200,
    )


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        instrument="BTC",
        raw_objects=(_raw_ref(),),
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=DatasetAcceptance.ACCEPTED,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def test_manifest_serialization_is_deterministic() -> None:
    first = canonical_manifest_bytes(_manifest())
    second = canonical_manifest_bytes(_manifest())

    assert first == second
    assert first.endswith(b"\n")
    assert b'"acceptance":"accepted"' in first
    assert b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in first


def test_manifest_requires_raw_objects_for_accepted_dataset() -> None:
    with pytest.raises(ValueError, match="raw object"):
        DatasetManifest(
            instrument="BTC",
            raw_objects=(),
            normalization_schema_version="canonical-v1",
            ordering_schema_version="ordering-v1",
            audit_schema_version="audit-v1",
            acceptance=DatasetAcceptance.ACCEPTED,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )


def test_manifest_rejects_mixed_instruments() -> None:
    mixed = RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.NODE,
            dataset_type=DatasetType.TRADES,
            instrument="ETH",
            sha256="b" * 64,
        ),
        byte_length=1,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator="node://trades/example",
        collector_schema_version="hl-node-v1",
        decoder_schema_version="hl-trades-v1",
    )

    with pytest.raises(ValueError, match="instrument"):
        DatasetManifest(
            instrument="BTC",
            raw_objects=(_raw_ref(), mixed),
            normalization_schema_version="canonical-v1",
            ordering_schema_version="ordering-v1",
            audit_schema_version="audit-v1",
            acceptance=DatasetAcceptance.ACCEPTED,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
