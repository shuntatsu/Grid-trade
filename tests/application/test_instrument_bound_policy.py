from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.application.passive_policy import transition_passive_policy
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _State:
    value: int


def _snapshot(instrument_id: str = "BTC-PERP") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:instrument-bound-policy",
        instrument_id=instrument_id,
    )


def _limits() -> RiskLimits:
    return RiskLimits(Decimal("1"), Decimal("0.2"), 1_000, 10)


def _risk_state() -> RiskState:
    return RiskState(Decimal("100"), Decimal("100"), 0, _NOW)


def _intent(instrument_id: str) -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id="candidate:g0:buy:l1",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("98"),
        quantity=Decimal("0.01"),
        instrument_id=instrument_id,
    )


def test_policy_rejects_proposed_order_from_another_instrument() -> None:
    with pytest.raises(ValueError, match="proposed order instrument mismatch"):
        transition_passive_policy(
            decision="fixture",
            previous_state=_State(0),
            candidate_state=_State(1),
            snapshot=_snapshot(),
            risk_limits=_limits(),
            risk_state=_risk_state(),
            working_orders=(),
            proposed_ladder=(_intent("ETH-PERP"),),
        )


def test_policy_rejects_working_order_from_another_instrument() -> None:
    intent = _intent("BTC-PERP")
    working = WorkingOrder(
        client_order_id=intent.client_order_id,
        generation=intent.generation,
        level=intent.level,
        side=intent.side,
        price=intent.price,
        quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        instrument_id="ETH-PERP",
    )

    with pytest.raises(ValueError, match="working order instrument mismatch"):
        transition_passive_policy(
            decision="fixture",
            previous_state=_State(0),
            candidate_state=_State(1),
            snapshot=_snapshot(),
            risk_limits=_limits(),
            risk_state=RiskState(Decimal("100"), Decimal("100"), 1, _NOW),
            working_orders=(working,),
            proposed_ladder=(intent,),
        )
