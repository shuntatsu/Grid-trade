from datetime import UTC, datetime

import pytest

from grid_trade.datasets.contracts import DatasetType, SourceFamily, sha256_bytes
from grid_trade.integrations.hyperliquid.archive import raw_object_ref_from_archive_bytes
from grid_trade.integrations.hyperliquid.node_data import raw_object_ref_from_node_bytes

pytestmark = pytest.mark.research


def test_archive_bytes_are_hashed_before_normalization() -> None:
    payload = b'{"coin":"BTC","time":1,"levels":[[],[]]}\n'

    raw = raw_object_ref_from_archive_bytes(
        payload,
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        source_locator="s3://hyperliquid-archive/market_data/20260810/5/l2Book/BTC.lz4",
        acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC),
        decoder_schema_version="hyperliquid-l2book-v1",
        source_start_ns=1_000_000,
        source_end_ns=1_000_000,
    )

    assert raw.identity.source_family is SourceFamily.ARCHIVE
    assert raw.identity.sha256 == sha256_bytes(payload)
    assert raw.byte_length == len(payload)
    assert raw.complete is True


def test_archive_adapter_rejects_non_official_bucket_locator() -> None:
    with pytest.raises(ValueError, match="hyperliquid-archive"):
        raw_object_ref_from_archive_bytes(
            b"payload",
            instrument="BTC",
            dataset_type=DatasetType.L2_BOOK,
            source_locator="s3://example-bucket/not-hyperliquid",
            acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC),
            decoder_schema_version="hyperliquid-l2book-v1",
        )


def test_node_data_bytes_use_node_source_family() -> None:
    payload = b'{"coin":"BTC","side":"B","px":"1","sz":"1"}\n'

    raw = raw_object_ref_from_node_bytes(
        payload,
        instrument="BTC",
        dataset_type=DatasetType.TRADES,
        source_locator="s3://hl-mainnet-node-data/node_fills_by_block/123",
        acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC),
        decoder_schema_version="hyperliquid-node-trade-v1",
    )

    assert raw.identity.source_family is SourceFamily.NODE
    assert raw.identity.sha256 == sha256_bytes(payload)
