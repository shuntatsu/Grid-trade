from datetime import UTC, datetime

import pytest

from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
    sha256_bytes,
)


def test_sha256_bytes_hashes_exact_payload() -> None:
    assert sha256_bytes(b"grid-trade\n") == (
        "b0f59c91f157410d19374eb876c2e15614a2835aa7520c461955d112a3b2fd54"
    )


def test_raw_object_identity_rejects_non_sha256_digest() -> None:
    with pytest.raises(ValueError, match="sha256"):
        RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=DatasetType.L2_BOOK,
            instrument="BTC",
            sha256="abc",
        )


def test_raw_object_identity_rejects_uppercase_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        RawObjectIdentity(
            source_family=SourceFamily.NODE,
            dataset_type=DatasetType.TRADES,
            instrument="BTC",
            sha256="A" * 64,
        )


def test_raw_object_identity_preserves_instrument_verbatim() -> None:
    identity = RawObjectIdentity(
        source_family=SourceFamily.WEBSOCKET,
        dataset_type=DatasetType.TRADES,
        instrument="BTC",
        sha256="0" * 64,
    )

    assert identity.instrument == "BTC"


def test_raw_object_ref_requires_utc_acquisition_time() -> None:
    identity = RawObjectIdentity(
        source_family=SourceFamily.INFO,
        dataset_type=DatasetType.FUNDING_REFERENCE,
        instrument="BTC",
        sha256="1" * 64,
    )

    with pytest.raises(ValueError, match="UTC"):
        RawObjectRef(
            identity=identity,
            byte_length=10,
            acquired_at=datetime(2026, 8, 10, 0, 0),
            source_locator="info:metaAndAssetCtxs",
            collector_schema_version="hl-info-v1",
            decoder_schema_version="hl-info-v1",
        )


def test_raw_object_ref_rejects_reversed_source_range() -> None:
    identity = RawObjectIdentity(
        source_family=SourceFamily.ARCHIVE,
        dataset_type=DatasetType.L2_BOOK,
        instrument="BTC",
        sha256="2" * 64,
    )

    with pytest.raises(ValueError, match="source timestamp range"):
        RawObjectRef(
            identity=identity,
            byte_length=10,
            acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
            source_locator="s3://hyperliquid-archive/example",
            collector_schema_version="hl-archive-v1",
            decoder_schema_version="hl-l2book-v1",
            source_start_ns=20,
            source_end_ns=10,
        )
