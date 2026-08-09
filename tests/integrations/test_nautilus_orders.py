from decimal import Decimal
from types import SimpleNamespace

import pytest

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.integrations.nautilus.orders import (
    build_nautilus_post_only_order,
    require_nautilus_runtime,
)

pytestmark = pytest.mark.research


def _runtime_objects() -> tuple[object, object]:
    from nautilus_trader.common import Clock, OrderFactory
    from nautilus_trader.model import InstrumentId, Price, Quantity, StrategyId, TraderId

    strategy = SimpleNamespace(
        order_factory=OrderFactory(
            TraderId("TRADER-001"),
            StrategyId("GRID-001"),
            Clock.new_test(),
        ),
    )
    instrument = SimpleNamespace(
        id=InstrumentId.from_str("BTC-PERP.HYPERLIQUID"),
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
    )
    return strategy, instrument


def _intent(
    *,
    side: OrderSide = OrderSide.BUY,
    price: str = "99.0",
    quantity: str = "0.010",
    reduce_only: bool = False,
) -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id=f"nautilus:{side.value}:{price}:{quantity}:{int(reduce_only)}",
        generation=1,
        level=1,
        side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        reduce_only=reduce_only,
    )


def test_pinned_nautilus_runtime_is_available() -> None:
    require_nautilus_runtime()


def test_runtime_identity_fails_closed_on_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grid_trade.integrations.nautilus.orders.distribution_version",
        lambda _: "9.9.9",
    )

    with pytest.raises(RuntimeError, match=r"1\.230\.0"):
        require_nautilus_runtime()


def test_builds_buy_gtc_post_only_limit_order_without_submission() -> None:
    from nautilus_trader.model import OrderSide as NautilusOrderSide
    from nautilus_trader.model import Price, Quantity, TimeInForce

    strategy, instrument = _runtime_objects()
    order = build_nautilus_post_only_order(
        strategy=strategy,
        instrument=instrument,
        intent=_intent(),
    )

    assert order.side == NautilusOrderSide.BUY
    assert order.price == Price.from_str("99.0")
    assert order.quantity == Quantity.from_str("0.010")
    assert order.time_in_force == TimeInForce.GTC
    assert order.is_post_only
    assert not order.is_reduce_only


def test_preserves_sell_and_reduce_only_flags() -> None:
    from nautilus_trader.model import OrderSide as NautilusOrderSide

    strategy, instrument = _runtime_objects()
    order = build_nautilus_post_only_order(
        strategy=strategy,
        instrument=instrument,
        intent=_intent(side=OrderSide.SELL, price="101.0", reduce_only=True),
    )

    assert order.side == NautilusOrderSide.SELL
    assert order.is_post_only
    assert order.is_reduce_only


def test_rejects_untick_aligned_price_instead_of_rounding() -> None:
    strategy, instrument = _runtime_objects()

    with pytest.raises(ValueError, match="tick"):
        build_nautilus_post_only_order(
            strategy=strategy,
            instrument=instrument,
            intent=_intent(price="99.05"),
        )


def test_rejects_unlot_aligned_quantity_instead_of_rounding() -> None:
    strategy, instrument = _runtime_objects()

    with pytest.raises(ValueError, match="lot"):
        build_nautilus_post_only_order(
            strategy=strategy,
            instrument=instrument,
            intent=_intent(quantity="0.0105"),
        )
