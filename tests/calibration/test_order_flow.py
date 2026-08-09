import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration.microstructure_contracts import TopOfBookObservation
from grid_trade.calibration.order_flow import (
    compute_ofi,
    microprice,
    microprice_displacement,
    normalized_ofi,
)


def _book(
    *,
    minute: int,
    bid: str = "99",
    bid_size: str = "5",
    ask: str = "101",
    ask_size: str = "5",
    price_scale: str = "1",
    size_scale: str = "1",
) -> TopOfBookObservation:
    p_scale = Decimal(price_scale)
    q_scale = Decimal(size_scale)
    return TopOfBookObservation(
        timestamp=dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC),
        source_id="fixture",
        instrument_id="AAA-PERP",
        best_bid=Decimal(bid) * p_scale,
        bid_size=Decimal(bid_size) * q_scale,
        best_ask=Decimal(ask) * p_scale,
        ask_size=Decimal(ask_size) * q_scale,
    )


def test_bid_size_increase_is_positive_ofi() -> None:
    previous = _book(minute=0, bid_size="5")
    current = _book(minute=1, bid_size="8")

    assert compute_ofi(previous, current) == Decimal("3")
    assert normalized_ofi(previous, current) > 0


def test_ask_size_increase_is_negative_ofi() -> None:
    previous = _book(minute=0, ask_size="5")
    current = _book(minute=1, ask_size="8")

    assert compute_ofi(previous, current) == Decimal("-3")
    assert normalized_ofi(previous, current) < 0


def test_bid_price_improvement_uses_new_bid_queue() -> None:
    previous = _book(minute=0, bid="99", bid_size="7")
    current = _book(minute=1, bid="100", bid_size="4")

    assert compute_ofi(previous, current) == Decimal("4")


def test_ask_price_improvement_uses_new_ask_queue() -> None:
    previous = _book(minute=0, ask="102", ask_size="7")
    current = _book(minute=1, ask="101", ask_size="4")

    assert compute_ofi(previous, current) == Decimal("-4")


def test_normalized_ofi_is_invariant_to_common_size_scale() -> None:
    previous = _book(minute=0, bid_size="5", ask_size="4")
    current = _book(minute=1, bid_size="8", ask_size="3")
    previous_scaled = _book(minute=0, bid_size="5", ask_size="4", size_scale="100")
    current_scaled = _book(minute=1, bid_size="8", ask_size="3", size_scale="100")

    assert normalized_ofi(previous, current) == normalized_ofi(previous_scaled, current_scaled)


def test_microprice_lies_inside_spread_and_moves_toward_thinner_side() -> None:
    balanced = _book(minute=0, bid_size="5", ask_size="5")
    bid_heavy = _book(minute=1, bid_size="9", ask_size="1")

    assert microprice(balanced) == balanced.mid
    assert bid_heavy.best_bid < microprice(bid_heavy) < bid_heavy.best_ask
    assert microprice(bid_heavy) > bid_heavy.mid


def test_microprice_displacement_is_price_scale_invariant() -> None:
    base = _book(minute=0, bid_size="9", ask_size="1")
    scaled = _book(minute=0, bid_size="9", ask_size="1", price_scale="100")

    assert microprice_displacement(base) == microprice_displacement(scaled)


def test_order_flow_requires_identity_and_time_continuity() -> None:
    previous = _book(minute=1)
    current = _book(minute=0)

    with pytest.raises(ValueError, match="strictly newer"):
        compute_ofi(previous, current)
