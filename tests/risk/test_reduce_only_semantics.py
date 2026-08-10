from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.risk.controller import assess_passive_ladder_risk

_NOW = datetime(2026, 8, 11, tzinfo=UTC)


def _snapshot(position: str) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal(position),
        source_id="fixture:reduce-only-risk",
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("3"),
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=20,
    )


def _state() -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=0,
        now=_NOW,
    )


def _order(
    *,
    order_id: str,
    side: OrderSide,
    quantity: str,
    reduce_only: bool,
) -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id=order_id,
        generation=1,
        level=1,
        side=side,
        price=Decimal("99" if side is OrderSide.BUY else "101"),
        quantity=Decimal(quantity),
        reduce_only=reduce_only,
    )


def _reason_values(decision: object) -> tuple[str, ...]:
    return tuple(reason.value for reason in decision.reasons)  # type: ignore[attr-defined]


def test_wrong_side_reduce_only_is_rejected_by_hard_risk() -> None:
    order = _order(
        order_id="wrong-side",
        side=OrderSide.BUY,
        quantity="0.2",
        reduce_only=True,
    )

    decision, filtered = assess_passive_ladder_risk(
        _snapshot("1"),
        _limits(),
        _state(),
        (order,),
    )

    assert filtered == ()
    assert not decision.allow_new_risk
    assert "invalid_reduce_only" in _reason_values(decision)


def test_reduce_only_from_flat_is_rejected_by_hard_risk() -> None:
    order = _order(
        order_id="flat-reduce-only",
        side=OrderSide.SELL,
        quantity="0.2",
        reduce_only=True,
    )

    decision, filtered = assess_passive_ladder_risk(
        _snapshot("0"),
        _limits(),
        _state(),
        (order,),
    )

    assert filtered == ()
    assert not decision.allow_new_risk
    assert "invalid_reduce_only" in _reason_values(decision)


def test_cumulative_reduce_only_quantity_cannot_cross_flat() -> None:
    orders = (
        _order(
            order_id="reduce-1",
            side=OrderSide.SELL,
            quantity="0.6",
            reduce_only=True,
        ),
        _order(
            order_id="reduce-2",
            side=OrderSide.SELL,
            quantity="0.6",
            reduce_only=True,
        ),
    )

    decision, filtered = assess_passive_ladder_risk(
        _snapshot("1"),
        _limits(),
        _state(),
        orders,
    )

    assert filtered == (orders[0],)
    assert not decision.allow_new_risk
    assert "invalid_reduce_only" in _reason_values(decision)


def test_multiple_reduce_only_orders_may_exactly_flatten_position() -> None:
    orders = (
        _order(
            order_id="reduce-1",
            side=OrderSide.SELL,
            quantity="0.6",
            reduce_only=True,
        ),
        _order(
            order_id="reduce-2",
            side=OrderSide.SELL,
            quantity="0.4",
            reduce_only=True,
        ),
    )

    decision, filtered = assess_passive_ladder_risk(
        _snapshot("1"),
        _limits(),
        _state(),
        orders,
    )

    assert filtered == orders
    assert decision.allow_new_risk


def test_new_risk_orders_do_not_expand_reduce_only_capacity() -> None:
    orders = (
        _order(
            order_id="new-risk-buy",
            side=OrderSide.BUY,
            quantity="1",
            reduce_only=False,
        ),
        _order(
            order_id="oversize-reduce",
            side=OrderSide.SELL,
            quantity="2",
            reduce_only=True,
        ),
    )

    decision, filtered = assess_passive_ladder_risk(
        _snapshot("1"),
        _limits(),
        _state(),
        orders,
    )

    assert filtered == (orders[0],)
    assert not decision.allow_new_risk
    assert "invalid_reduce_only" in _reason_values(decision)
