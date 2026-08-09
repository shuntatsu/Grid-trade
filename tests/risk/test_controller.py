from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskLimits, RiskReason, RiskState
from grid_trade.risk.controller import evaluate_risk, filter_passive_orders

_NOW = datetime(2026, 8, 9, 7, 0, tzinfo=UTC)


def _snapshot(
    *,
    timestamp: datetime = _NOW,
    position: str = "0",
) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=timestamp,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal(position),
        source_id="fixture:risk",
    )


def _limits(**overrides: object) -> RiskLimits:
    values: dict[str, object] = {
        "max_abs_position": Decimal("1"),
        "max_drawdown_fraction": Decimal("0.10"),
        "max_data_age_ms": 1000,
        "max_open_orders": 10,
    }
    values.update(overrides)
    return RiskLimits(**values)  # type: ignore[arg-type]


def _state(**overrides: object) -> RiskState:
    values: dict[str, object] = {
        "equity": Decimal("100"),
        "peak_equity": Decimal("100"),
        "open_order_count": 0,
        "now": _NOW,
    }
    values.update(overrides)
    return RiskState(**values)  # type: ignore[arg-type]


def _order(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "0.10",
    reduce_only: bool = False,
) -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id=f"risk:{side.value}:{quantity}:{int(reduce_only)}",
        generation=1,
        level=1,
        side=side,
        price=Decimal("99" if side is OrderSide.BUY else "101"),
        quantity=Decimal(quantity),
        reduce_only=reduce_only,
    )


def test_healthy_state_allows_new_risk() -> None:
    decision = evaluate_risk(_snapshot(), _limits(), _state())

    assert decision.allow_new_risk
    assert not decision.cancel_all_passive
    assert not decision.target_flat
    assert decision.reasons == ()


def test_stale_data_fails_closed_and_cancels_passive_orders() -> None:
    snapshot = _snapshot(timestamp=_NOW - timedelta(milliseconds=1001))

    decision = evaluate_risk(snapshot, _limits(), _state())

    assert not decision.allow_new_risk
    assert decision.cancel_all_passive
    assert not decision.target_flat
    assert RiskReason.STALE_DATA in decision.reasons


def test_drawdown_breach_cancels_and_targets_flat() -> None:
    decision = evaluate_risk(
        _snapshot(position="0.5"),
        _limits(max_drawdown_fraction=Decimal("0.10")),
        _state(equity=Decimal("89"), peak_equity=Decimal("100")),
    )

    assert not decision.allow_new_risk
    assert decision.cancel_all_passive
    assert decision.target_flat
    assert RiskReason.DRAWDOWN_BREACH in decision.reasons


def test_open_order_limit_breach_fails_closed() -> None:
    decision = evaluate_risk(
        _snapshot(),
        _limits(max_open_orders=10),
        _state(open_order_count=11),
    )

    assert not decision.allow_new_risk
    assert decision.cancel_all_passive
    assert not decision.target_flat
    assert RiskReason.MAX_OPEN_ORDERS in decision.reasons


def test_position_at_limit_blocks_new_risk_but_preserves_reduce_only() -> None:
    snapshot = _snapshot(position="1")
    limits = _limits(max_abs_position=Decimal("1"))
    decision = evaluate_risk(snapshot, limits, _state())
    orders = (
        _order(side=OrderSide.BUY, quantity="0.10"),
        _order(side=OrderSide.SELL, quantity="0.10", reduce_only=True),
    )

    filtered = filter_passive_orders(snapshot, limits, decision, orders)

    assert not decision.allow_new_risk
    assert RiskReason.MAX_POSITION in decision.reasons
    assert filtered == (orders[1],)


def test_position_beyond_limit_requires_flattening() -> None:
    decision = evaluate_risk(
        _snapshot(position="1.01"),
        _limits(max_abs_position=Decimal("1")),
        _state(),
    )

    assert not decision.allow_new_risk
    assert decision.cancel_all_passive
    assert decision.target_flat
    assert RiskReason.MAX_POSITION in decision.reasons


def test_filter_limits_total_potential_position_if_all_orders_fill() -> None:
    snapshot = _snapshot(position="0.80")
    limits = _limits(max_abs_position=Decimal("1"))
    decision = evaluate_risk(snapshot, limits, _state())
    orders = (
        _order(quantity="0.15"),
        _order(quantity="0.10"),
    )

    assert filter_passive_orders(snapshot, limits, decision, orders) == (orders[0],)


def test_future_market_snapshot_is_rejected_as_causal_violation() -> None:
    with pytest.raises(ValueError, match="future"):
        evaluate_risk(
            _snapshot(timestamp=_NOW + timedelta(milliseconds=1)),
            _limits(),
            _state(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"equity": Decimal("-1")},
        {"peak_equity": Decimal("0")},
        {"equity": Decimal("101"), "peak_equity": Decimal("100")},
        {"open_order_count": -1},
        {"now": datetime(2026, 8, 9, 7, 0)},
    ],
)
def test_risk_state_rejects_invalid_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _state(**overrides)


@given(quantity=st.decimals(min_value="0.0001", max_value="10", places=4))
def test_at_max_long_position_no_risk_increasing_buy_survives(quantity: Decimal) -> None:
    snapshot = _snapshot(position="1")
    limits = _limits(max_abs_position=Decimal("1"))
    decision = evaluate_risk(snapshot, limits, _state())
    order = _order(side=OrderSide.BUY, quantity=str(quantity))

    filtered = filter_passive_orders(snapshot, limits, decision, (order,))

    assert filtered == ()
