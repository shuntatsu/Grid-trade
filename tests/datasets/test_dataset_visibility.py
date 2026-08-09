from decimal import Decimal

from grid_trade.datasets.canonical import (
    BookSide,
    BookVisibilityTracker,
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    VisibilityChange,
    VisibleDepthUpdate,
)


def _level(price: str, quantity: str = "1") -> CanonicalBookLevel:
    return CanonicalBookLevel(Decimal(price), Decimal(quantity), 1)


def _snapshot(*, bids: tuple[str, ...], asks: tuple[str, ...]) -> CanonicalBookSnapshot:
    return CanonicalBookSnapshot(
        bids=tuple(_level(price) for price in bids),
        asks=tuple(_level(price) for price in asks),
    )


def _find(
    updates: tuple[VisibleDepthUpdate, ...],
    *,
    side: BookSide,
    price: str,
) -> VisibleDepthUpdate:
    target = Decimal(price)
    return next(update for update in updates if update.side is side and update.price == target)


def test_missing_level_inside_new_top_n_boundary_is_confirmed_zero() -> None:
    tracker = BookVisibilityTracker()
    tracker.apply(
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        )
    )

    updates = tracker.apply(
        _snapshot(
            bids=("100", "98", "97"),
            asks=("101", "102", "103"),
        )
    )

    removed = _find(updates, side=BookSide.BID, price="99")
    assert removed.change is VisibilityChange.CONFIRMED_ZERO
    assert removed.quantity == Decimal(0)
    assert removed.epoch_id == 0


def test_level_beyond_new_deep_boundary_becomes_visibility_lost_not_cancelled() -> None:
    tracker = BookVisibilityTracker()
    tracker.apply(
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        )
    )

    updates = tracker.apply(
        _snapshot(
            bids=("102", "101", "100"),
            asks=("103", "104", "105"),
        )
    )

    lost_bid = _find(updates, side=BookSide.BID, price="98")
    assert lost_bid.change is VisibilityChange.VISIBILITY_LOST
    assert lost_bid.epoch_id == 0

    lost_ask = _find(updates, side=BookSide.ASK, price="103")
    assert lost_ask.change is VisibilityChange.CONFIRMED_ZERO


def test_reentry_after_visibility_loss_starts_new_epoch() -> None:
    tracker = BookVisibilityTracker()
    tracker.apply(
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        )
    )
    tracker.apply(
        _snapshot(
            bids=("102", "101", "100"),
            asks=("103", "104", "105"),
        )
    )

    updates = tracker.apply(
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        )
    )

    reentered = _find(updates, side=BookSide.BID, price="98")
    assert reentered.change is VisibilityChange.REESTABLISHED
    assert reentered.epoch_id == 1


def test_replaying_same_snapshot_sequence_is_deterministic() -> None:
    snapshots = (
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        ),
        _snapshot(
            bids=("102", "101", "100"),
            asks=("103", "104", "105"),
        ),
        _snapshot(
            bids=("100", "99", "98"),
            asks=("101", "102", "103"),
        ),
    )

    first_tracker = BookVisibilityTracker()
    second_tracker = BookVisibilityTracker()

    first = tuple(first_tracker.apply(snapshot) for snapshot in snapshots)
    second = tuple(second_tracker.apply(snapshot) for snapshot in snapshots)

    assert first == second
