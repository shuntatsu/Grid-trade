from decimal import Decimal

import pytest

from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
)
from grid_trade.research.hftbacktest_adapter import (
    ReceiveTimestampMode,
    canonical_events_to_hftbacktest_fixture,
)

pytestmark = pytest.mark.research

_RAW_HASH = "a" * 64


def _level(price: str, quantity: str) -> CanonicalBookLevel:
    return CanonicalBookLevel(Decimal(price), Decimal(quantity), 1)


def _book(
    *,
    timestamp_ns: int,
    bids: tuple[tuple[str, str], ...],
    asks: tuple[tuple[str, str], ...],
    local_receive_ts_ns: int | None = None,
    ordinal: int,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=local_receive_ts_ns,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=tuple(_level(price, quantity) for price, quantity in bids),
            asks=tuple(_level(price, quantity) for price, quantity in asks),
        ),
    )


def _trade(*, timestamp_ns: int, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.SELL,
            price=Decimal("100"),
            quantity=Decimal("0.5"),
            stable_identity=f"trade-{ordinal}",
        ),
    )


def test_conversion_omits_visibility_loss_but_emits_confirmed_zero() -> None:
    events = (
        _book(
            timestamp_ns=100,
            bids=(("100", "2"), ("99", "1"), ("98", "1")),
            asks=(("101", "2"), ("102", "1"), ("103", "1")),
            ordinal=0,
        ),
        _book(
            timestamp_ns=200,
            bids=(("102", "2"), ("101", "1"), ("100", "1")),
            asks=(("103", "2"), ("104", "1"), ("105", "1")),
            ordinal=1,
        ),
        _trade(timestamp_ns=300, ordinal=2),
    )

    converted = canonical_events_to_hftbacktest_fixture(
        events,
        synthetic_receive_latency_ns=50,
    )

    assert converted.receive_timestamp_mode is ReceiveTimestampMode.SYNTHETIC
    assert converted.synthetic_receive_latency_ns == 50
    assert len(converted.fixture.snapshot) == 6
    assert all(row.local_ts == row.exch_ts + 50 for row in converted.fixture.feed)
    assert any(
        row.kind == "depth_ask" and row.price == Decimal("101") and row.quantity == 0
        for row in converted.fixture.feed
    )
    assert not any(
        row.kind == "depth_bid" and row.price == Decimal("98") and row.quantity == 0
        for row in converted.fixture.feed
    )
    assert any(row.kind == "trade_sell" for row in converted.fixture.feed)


def test_conversion_labels_mixed_observed_and_synthetic_receive_times() -> None:
    events = (
        _book(
            timestamp_ns=100,
            local_receive_ts_ns=125,
            bids=(("100", "1"),),
            asks=(("101", "1"),),
            ordinal=0,
        ),
        _book(
            timestamp_ns=200,
            bids=(("100", "2"),),
            asks=(("101", "1"),),
            ordinal=1,
        ),
    )

    converted = canonical_events_to_hftbacktest_fixture(
        events,
        synthetic_receive_latency_ns=50,
    )

    assert converted.receive_timestamp_mode is ReceiveTimestampMode.MIXED
    assert converted.fixture.snapshot[0].local_ts == 125
    assert converted.fixture.feed[0].local_ts == 250


def test_conversion_rejects_trade_before_initial_book_snapshot() -> None:
    events = (
        _trade(timestamp_ns=100, ordinal=0),
        _book(
            timestamp_ns=200,
            bids=(("100", "1"),),
            asks=(("101", "1"),),
            ordinal=1,
        ),
    )

    with pytest.raises(ValueError, match="initial book snapshot"):
        canonical_events_to_hftbacktest_fixture(events, synthetic_receive_latency_ns=0)


def test_conversion_rejects_negative_synthetic_receive_latency() -> None:
    events = (
        _book(
            timestamp_ns=100,
            bids=(("100", "1"),),
            asks=(("101", "1"),),
            ordinal=0,
        ),
    )

    with pytest.raises(ValueError, match="synthetic_receive_latency_ns"):
        canonical_events_to_hftbacktest_fixture(events, synthetic_receive_latency_ns=-1)
