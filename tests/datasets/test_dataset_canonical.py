from decimal import Decimal

import pytest

from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
    canonical_event_sort_key,
)


def _book(*, bid: str = "100", ask: str = "101") -> CanonicalBookSnapshot:
    return CanonicalBookSnapshot(
        bids=(CanonicalBookLevel(Decimal(bid), Decimal("2"), 3),),
        asks=(CanonicalBookLevel(Decimal(ask), Decimal("3"), 4),),
    )


def _event(
    *,
    event_type: CanonicalEventType,
    exchange_ts_ns: int,
    source_sequence: int | None,
    raw_hash: str,
    ordinal: int,
) -> CanonicalEventEnvelope:
    payload: CanonicalBookSnapshot | CanonicalTrade
    if event_type is CanonicalEventType.BOOK_SNAPSHOT:
        payload = _book()
    else:
        payload = CanonicalTrade(
            side=TradeSide.BUY,
            price=Decimal("100.5"),
            quantity=Decimal("0.25"),
            stable_identity="trade-1",
        )
    return CanonicalEventEnvelope(
        event_type=event_type,
        instrument="BTC",
        exchange_ts_ns=exchange_ts_ns,
        local_receive_ts_ns=None,
        source_sequence=source_sequence,
        raw_object_sha256=raw_hash,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=payload,
    )


def test_book_level_requires_finite_positive_price_and_quantity() -> None:
    with pytest.raises(ValueError, match="price"):
        CanonicalBookLevel(Decimal("NaN"), Decimal("1"), 1)
    with pytest.raises(ValueError, match="quantity"):
        CanonicalBookLevel(Decimal("100"), Decimal("0"), 1)


def test_book_snapshot_requires_ordered_non_crossed_sides() -> None:
    with pytest.raises(ValueError, match="bids"):
        CanonicalBookSnapshot(
            bids=(
                CanonicalBookLevel(Decimal("99"), Decimal("1"), 1),
                CanonicalBookLevel(Decimal("100"), Decimal("1"), 1),
            ),
            asks=(CanonicalBookLevel(Decimal("101"), Decimal("1"), 1),),
        )

    with pytest.raises(ValueError, match="crossed"):
        CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(Decimal("101"), Decimal("1"), 1),),
            asks=(CanonicalBookLevel(Decimal("100"), Decimal("1"), 1),),
        )


def test_equal_snapshots_at_distinct_timestamps_remain_distinct_events() -> None:
    first = _event(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        exchange_ts_ns=100,
        source_sequence=None,
        raw_hash="a" * 64,
        ordinal=0,
    )
    second = _event(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        exchange_ts_ns=101,
        source_sequence=None,
        raw_hash="a" * 64,
        ordinal=1,
    )

    assert first != second
    assert canonical_event_sort_key(first) < canonical_event_sort_key(second)


def test_canonical_order_uses_sequence_then_event_precedence_then_provenance() -> None:
    later_sequence = _event(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        exchange_ts_ns=100,
        source_sequence=2,
        raw_hash="a" * 64,
        ordinal=0,
    )
    earlier_sequence = _event(
        event_type=CanonicalEventType.TRADE,
        exchange_ts_ns=100,
        source_sequence=1,
        raw_hash="f" * 64,
        ordinal=9,
    )
    no_sequence_book = _event(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        exchange_ts_ns=100,
        source_sequence=None,
        raw_hash="b" * 64,
        ordinal=3,
    )
    no_sequence_trade = _event(
        event_type=CanonicalEventType.TRADE,
        exchange_ts_ns=100,
        source_sequence=None,
        raw_hash="a" * 64,
        ordinal=1,
    )

    ordered = sorted(
        (no_sequence_trade, later_sequence, no_sequence_book, earlier_sequence),
        key=canonical_event_sort_key,
    )

    assert ordered == [
        earlier_sequence,
        later_sequence,
        no_sequence_book,
        no_sequence_trade,
    ]


def test_event_payload_type_must_match_declared_event_type() -> None:
    with pytest.raises(ValueError, match="payload"):
        CanonicalEventEnvelope(
            event_type=CanonicalEventType.TRADE,
            instrument="BTC",
            exchange_ts_ns=100,
            local_receive_ts_ns=None,
            source_sequence=None,
            raw_object_sha256="a" * 64,
            raw_record_ordinal=0,
            normalization_schema_version="canonical-v1",
            payload=_book(),
        )
