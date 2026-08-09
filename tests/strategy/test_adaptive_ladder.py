from decimal import Decimal

import pytest

from grid_trade.domain.orders import OrderSide
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig, build_adaptive_ladder


def _config() -> AdaptiveLadderConfig:
    return AdaptiveLadderConfig(
        levels=3,
        spacing_bps=100,
        order_quantity=Decimal("0.02"),
        tick_size=Decimal("0.01"),
        max_abs_inventory=Decimal("0.10"),
    )


def test_long_target_uses_new_risk_bids_and_reduce_only_asks() -> None:
    ladder = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0.05"),
        target=Decimal("0.05"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=_config(),
        generation=2,
        stage="s3",
    )

    buys = tuple(order for order in ladder if order.side is OrderSide.BUY)
    sells = tuple(order for order in ladder if order.side is OrderSide.SELL)
    assert tuple(order.price for order in buys) == (
        Decimal("99.00"),
        Decimal("98.00"),
        Decimal("97.00"),
    )
    assert tuple(order.price for order in sells) == (
        Decimal("101.00"),
        Decimal("102.00"),
        Decimal("103.00"),
    )
    assert all(not order.reduce_only for order in buys)
    assert all(order.reduce_only for order in sells)
    assert sum((order.quantity for order in buys), Decimal(0)) == Decimal("0.05")
    assert sum((order.quantity for order in sells), Decimal(0)) == Decimal("0.05")


def test_flatten_long_emits_only_reduce_only_sells() -> None:
    ladder = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0.05"),
        target=Decimal(0),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=_config(),
        generation=3,
        stage="s5",
    )
    assert ladder
    assert all(order.side is OrderSide.SELL for order in ladder)
    assert all(order.reduce_only for order in ladder)
    assert sum((order.quantity for order in ladder), Decimal(0)) == Decimal("0.05")


def test_short_target_uses_new_risk_asks_and_reduce_only_bids() -> None:
    ladder = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("-0.04"),
        target=Decimal("-0.05"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=_config(),
        generation=4,
        stage="s5",
    )
    buys = tuple(order for order in ladder if order.side is OrderSide.BUY)
    sells = tuple(order for order in ladder if order.side is OrderSide.SELL)
    assert buys and sells
    assert all(order.reduce_only for order in buys)
    assert all(not order.reduce_only for order in sells)
    assert sum((order.quantity for order in buys), Decimal(0)) == Decimal("0.04")
    assert sum((order.quantity for order in sells), Decimal(0)) == Decimal("0.06")


def test_side_scales_can_suppress_risk_without_amplifying_quantity() -> None:
    ladder = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0.05"),
        target=Decimal("0.05"),
        bid_scale=Decimal("0"),
        ask_scale=Decimal("0.5"),
        config=_config(),
        generation=1,
        stage="s3",
    )
    assert all(order.side is OrderSide.SELL for order in ladder)
    assert all(order.quantity <= Decimal("0.010") for order in ladder)


def test_opposite_sign_target_fails_closed_until_flat_gate() -> None:
    with pytest.raises(ValueError, match="flat-before-reverse"):
        build_adaptive_ladder(
            reference=Decimal("100"),
            position=Decimal("0.01"),
            target=Decimal("-0.02"),
            bid_scale=Decimal("1"),
            ask_scale=Decimal("1"),
            config=_config(),
            generation=0,
            stage="s5",
        )


def test_ladder_cannot_add_beyond_strategy_inventory_cap() -> None:
    ladder = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0.09"),
        target=Decimal("0.05"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=_config(),
        generation=0,
        stage="s3",
    )
    add_long = sum(
        (order.quantity for order in ladder if order.side is OrderSide.BUY and not order.reduce_only),
        Decimal(0),
    )
    assert add_long == Decimal("0.01")
