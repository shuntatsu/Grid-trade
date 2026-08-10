from datetime import UTC, datetime
from pathlib import Path

import pytest

from grid_trade.datasets.contracts import DatasetType, SourceFamily
from grid_trade.integrations.hyperliquid.forward_recorder import (
    ForwardRecorderConfig,
    ForwardRecorderSession,
    ForwardSegmentWriter,
    HyperliquidForwardTransport,
    read_segment_records,
)

pytestmark = pytest.mark.research


class _FixtureTransport(HyperliquidForwardTransport):
    def __init__(self, reference_payload: bytes = b'[{"universe":[]},[]]') -> None:
        self.sent: list[bytes] = []
        self.info_requests: list[bytes] = []
        self.reference_payload = reference_payload

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def fetch_info(self, payload: bytes) -> bytes:
        self.info_requests.append(payload)
        return self.reference_payload


def test_single_instrument_subscription_and_heartbeat_messages_are_deterministic() -> None:
    transport = _FixtureTransport()
    session = ForwardRecorderSession(ForwardRecorderConfig(instrument="BTC"))

    session.connect(transport, timestamp_ns=100)

    assert transport.sent == [
        b'{"method":"subscribe","subscription":{"coin":"BTC","type":"l2Book"}}\n',
        b'{"method":"subscribe","subscription":{"coin":"BTC","type":"trades"}}\n',
    ]
    assert session.continuity_epoch == 0
    assert session.reference_due(100) is True
    assert session.heartbeat_due(30_000_000_099) is False
    assert session.heartbeat_due(30_000_000_100) is True

    session.send_heartbeat(transport, timestamp_ns=30_000_000_100)

    assert transport.sent[-1] == b'{"method":"ping"}\n'
    assert session.heartbeat_due(60_000_000_099) is False


def test_reference_capture_defaults_to_sixty_seconds_and_persists_before_completion(
    tmp_path: Path,
) -> None:
    payload = b'[{"universe":[{"name":"BTC"}]},[{"funding":"0.0001"}]]'
    transport = _FixtureTransport(reference_payload=payload)
    session = ForwardRecorderSession(ForwardRecorderConfig(instrument="BTC"))
    session.connect(transport, timestamp_ns=0)
    writer = ForwardSegmentWriter(
        tmp_path / "reference.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.FUNDING_REFERENCE,
        collector_schema_version="hyperliquid-info-segment-v1",
        decoder_schema_version="hyperliquid-meta-asset-ctxs-v1",
        continuity_epoch=0,
        source_family=SourceFamily.INFO,
    )

    ordinal = session.capture_reference(
        transport,
        writer,
        receive_ts_ns=1_000,
    )

    assert ordinal == 0
    assert transport.info_requests == [b'{"type":"metaAndAssetCtxs"}\n']
    assert read_segment_records(writer.partial_path) == ((1_000, payload),)
    assert session.reference_due(60_000_000_999) is False
    assert session.reference_due(60_000_001_000) is True
    assert session.reference_gap_exceeded(120_000_001_000) is False
    assert session.reference_gap_exceeded(120_000_001_001) is True

    segment = writer.finalize(acquired_at=datetime(2026, 8, 10, tzinfo=UTC))
    assert segment.raw_object.identity.source_family is SourceFamily.INFO


def test_reconnect_creates_new_continuity_epoch_and_waits_for_authoritative_book(
    tmp_path: Path,
) -> None:
    session = ForwardRecorderSession(ForwardRecorderConfig(instrument="BTC"))
    transport = _FixtureTransport()
    session.connect(transport, timestamp_ns=100)
    session.note_disconnect(timestamp_ns=1_000)
    session.reconnect(transport, timestamp_ns=1_500)

    assert session.continuity_epoch == 1
    assert session.awaiting_authoritative_book is True

    trades_writer = ForwardSegmentWriter(
        tmp_path / "trades.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.TRADES,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-trades-v1",
        continuity_epoch=1,
    )
    session.capture_trades(
        b'{"channel":"trades","data":[]}',
        trades_writer,
        receive_ts_ns=1_600,
    )
    assert session.awaiting_authoritative_book is True

    book_writer = ForwardSegmentWriter(
        tmp_path / "book.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-l2book-v1",
        continuity_epoch=1,
    )
    capture = session.capture_l2_book(
        b'{"channel":"l2Book","data":{"coin":"BTC"}}',
        book_writer,
        receive_ts_ns=1_700,
        exchange_ts_ns=1_650,
    )

    assert capture.ordinal == 0
    assert capture.continuity_record is not None
    assert capture.continuity_record.continuity_epoch == 1
    assert capture.continuity_record.disconnect_ts_ns == 1_000
    assert capture.continuity_record.reconnect_ts_ns == 1_500
    assert capture.continuity_record.first_post_reconnect_exchange_ts_ns == 1_650
    assert capture.continuity_record.first_post_reconnect_receive_ts_ns == 1_700
    assert capture.continuity_record.uncovered_receive_interval_ns == 700
    assert session.awaiting_authoritative_book is False


def test_capture_rejects_writer_from_previous_continuity_epoch(tmp_path: Path) -> None:
    session = ForwardRecorderSession(ForwardRecorderConfig(instrument="BTC"))
    transport = _FixtureTransport()
    session.connect(transport, timestamp_ns=100)
    stale_writer = ForwardSegmentWriter(
        tmp_path / "stale.gtseg",
        instrument="BTC",
        dataset_type=DatasetType.L2_BOOK,
        collector_schema_version="hyperliquid-ws-segment-v1",
        decoder_schema_version="hyperliquid-l2book-v1",
        continuity_epoch=0,
    )
    session.note_disconnect(timestamp_ns=1_000)
    session.reconnect(transport, timestamp_ns=1_500)

    with pytest.raises(ValueError, match="continuity epoch"):
        session.capture_l2_book(
            b"book",
            stale_writer,
            receive_ts_ns=1_700,
            exchange_ts_ns=1_650,
        )
