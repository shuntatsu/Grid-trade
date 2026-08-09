import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grid_trade.datasets.contracts import DatasetAcceptance, DatasetType, SourceFamily, sha256_bytes
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.integrations.hyperliquid.forward_recorder import (
    ForwardSegmentWriter,
    read_segment_records,
)

pytestmark = pytest.mark.research


def test_append_fsyncs_and_preserves_exact_payload_bytes(tmp_path: Path) -> None:
    sync_calls: list[int] = []

    def sync(fd: int) -> None:
        sync_calls.append(fd)
        os.fsync(fd)

    final_path = tmp_path / "btc-l2.gtseg"
    writer = ForwardSegmentWriter(
        final_path,
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-l2book-v1",
        continuity_epoch=3,
        sync_fn=sync,
    )
    payload = b'{"channel":"l2Book","data":{"coin":"BTC"}}\n'

    ordinal = writer.append(payload, receive_ts_ns=1_754_450_974_240_000_000)

    assert ordinal == 0
    assert sync_calls
    assert read_segment_records(writer.partial_path) == (
        (1_754_450_974_240_000_000, payload),
    )

    segment = writer.finalize(acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC))
    assert segment.continuity_epoch == 3
    assert segment.record_count == 1
    assert segment.raw_object.identity.source_family is SourceFamily.WEBSOCKET
    assert segment.raw_object.complete is True
    assert final_path.exists()
    assert not writer.partial_path.exists()
    assert segment.raw_object.identity.sha256 == sha256_bytes(final_path.read_bytes())


def test_interrupted_segment_remains_incomplete_and_cannot_be_accepted(tmp_path: Path) -> None:
    final_path = tmp_path / "btc-trades.gtseg"
    writer = ForwardSegmentWriter(
        final_path,
        instrument="BTC",
        dataset_type=DatasetType.TRADES,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-trades-v1",
        continuity_epoch=0,
    )
    writer.append(b'{"channel":"trades","data":[]}\n', receive_ts_ns=100)

    segment = writer.abort(acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC))

    assert segment.raw_object.complete is False
    assert writer.partial_path.exists()
    assert not final_path.exists()
    with pytest.raises(ValueError, match="incomplete"):
        DatasetManifest(
            instrument="BTC",
            raw_objects=(segment.raw_object,),
            normalization_schema_version="canonical-v1",
            ordering_schema_version="ordering-v1",
            audit_schema_version="audit-v1",
            acceptance=DatasetAcceptance.ACCEPTED,
            created_at=datetime(2026, 8, 10, 5, 1, tzinfo=UTC),
        )


def test_continuity_epoch_is_explicit_across_reconnect_segments(tmp_path: Path) -> None:
    first = ForwardSegmentWriter(
        tmp_path / "first.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-l2book-v1",
        continuity_epoch=7,
    )
    first.append(b"first", receive_ts_ns=100)
    first_segment = first.abort(acquired_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC))

    second = ForwardSegmentWriter(
        tmp_path / "second.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-l2book-v1",
        continuity_epoch=8,
    )
    second.append(b"second", receive_ts_ns=200)
    second_segment = second.finalize(acquired_at=datetime(2026, 8, 10, 5, 1, tzinfo=UTC))

    assert first_segment.continuity_epoch == 7
    assert second_segment.continuity_epoch == 8
    assert first_segment.raw_object.complete is False
    assert second_segment.raw_object.complete is True
