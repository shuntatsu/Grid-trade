from decimal import Decimal

from hypothesis import given, strategies as st

from grid_trade.datasets.canonical import (
    BookVisibilityTracker,
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
    canonical_event_sort_key,
)


def _trade_event(
    *,
    timestamp_ns: int,
    sequence: int | None,
    digest_char: str,
    ordinal: int,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=sequence,
        raw_object_sha256=digest_char * 64,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.BUY,
            price=Decimal("100"),
            quantity=Decimal("1"),
            stable_identity=f"trade-{digest_char}-{ordinal}",
        ),
    )


_BASE_EVENTS = (
    _trade_event(timestamp_ns=100, sequence=2, digest_char="a", ordinal=0),
    _trade_event(timestamp_ns=100, sequence=1, digest_char="b", ordinal=0),
    _trade_event(timestamp_ns=100, sequence=None, digest_char="c", ordinal=0),
    _trade_event(timestamp_ns=101, sequence=0, digest_char="d", ordinal=0),
)
_EXPECTED_ORDER = tuple(sorted(_BASE_EVENTS, key=canonical_event_sort_key))


@given(st.permutations(_BASE_EVENTS))
def test_canonical_sort_is_input_order_independent(
    permutation: list[CanonicalEventEnvelope],
) -> None:
    assert tuple(sorted(permutation, key=canonical_event_sort_key)) == _EXPECTED_ORDER


def _snapshot(best_bid: int, shift: int) -> CanonicalBookSnapshot:
    bids = tuple(
        CanonicalBookLevel(Decimal(best_bid + shift - depth), Decimal(depth + 1), depth + 1)
        for depth in range(3)
    )
    asks = tuple(
        CanonicalBookLevel(Decimal(best_bid + shift + 10 + depth), Decimal(depth + 1), depth + 1)
        for depth in range(3)
    )
    return CanonicalBookSnapshot(bids=bids, asks=asks)


@given(
    best_bid=st.integers(min_value=10, max_value=1_000_000),
    shifts=st.lists(st.integers(min_value=-5, max_value=5), min_size=1, max_size=20),
)
def test_visibility_replay_is_deterministic_for_generated_snapshot_sequences(
    best_bid: int,
    shifts: list[int],
) -> None:
    snapshots = tuple(_snapshot(best_bid, shift) for shift in shifts)
    first_tracker = BookVisibilityTracker()
    second_tracker = BookVisibilityTracker()

    first = tuple(first_tracker.apply(snapshot) for snapshot in snapshots)
    second = tuple(second_tracker.apply(snapshot) for snapshot in snapshots)

    assert first == second
