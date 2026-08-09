from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide
from grid_trade.strategy.fixed_grid import FixedLongGridConfig, build_fixed_long_grid


def _snapshot(*, bid: str = "99.99", ask: str = "100.01") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime(2026, 8, 9, 7, 0, tzinfo=UTC),
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:s0",
    )


def _config(**overrides: object) -> FixedLongGridConfig:
    values: dict[str, object] = {
        "levels": 3,
        "spacing_bps": 100,
        "order_quantity": Decimal("0.01"),
        "tick_size": Decimal("0.01"),
    }
    values.update(overrides)
    return FixedLongGridConfig(**values)  # type: ignore[arg-type]


def test_builds_expected_three_level_long_grid() -> None:
    orders = build_fixed_long_grid(_snapshot(), _config(), generation=7)

    assert [order.price for order in orders] == [
        Decimal("99.00"),
        Decimal("98.00"),
        Decimal("97.00"),
    ]
    assert [order.client_order_id for order in orders] == [
        "s0:g7:buy:l1",
        "s0:g7:buy:l2",
        "s0:g7:buy:l3",
    ]
    assert all(order.side is OrderSide.BUY for order in orders)
    assert all(order.quantity == Decimal("0.01") for order in orders)
    assert all(not order.reduce_only for order in orders)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("levels", 0),
        ("levels", 51),
        ("spacing_bps", 0),
        ("spacing_bps", -1),
        ("order_quantity", Decimal("0")),
        ("order_quantity", Decimal("-0.01")),
        ("tick_size", Decimal("0")),
        ("tick_size", Decimal("-0.01")),
    ],
)
def test_config_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _config(**{field: value})


def test_generation_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        build_fixed_long_grid(_snapshot(), _config(), generation=-1)


def test_rejects_grid_level_that_rounds_to_non_positive_price() -> None:
    with pytest.raises(ValueError):
        build_fixed_long_grid(
            _snapshot(bid="0.99", ask="1.01"),
            _config(levels=2, spacing_bps=5000, tick_size=Decimal("0.01")),
            generation=0,
        )


def test_rejects_levels_collapsed_by_coarse_tick_size() -> None:
    with pytest.raises(ValueError):
        build_fixed_long_grid(
            _snapshot(),
            _config(levels=2, spacing_bps=1, tick_size=Decimal("1")),
            generation=0,
        )


def test_same_inputs_are_deterministic() -> None:
    snapshot = _snapshot()
    config = _config()

    assert build_fixed_long_grid(snapshot, config, generation=3) == build_fixed_long_grid(
        snapshot,
        config,
        generation=3,
    )


@given(
    spacing_bps=st.integers(min_value=1, max_value=500),
    levels=st.integers(min_value=1, max_value=10),
)
def test_generated_prices_are_tick_aligned_and_strictly_descending(
    spacing_bps: int,
    levels: int,
) -> None:
    config = _config(levels=levels, spacing_bps=spacing_bps, tick_size=Decimal("0.01"))

    try:
        orders = build_fixed_long_grid(_snapshot(), config, generation=1)
    except ValueError:
        return

    prices = [order.price for order in orders]
    assert all(price % config.tick_size == 0 for price in prices)
    assert all(left > right for left, right in pairwise(prices))
