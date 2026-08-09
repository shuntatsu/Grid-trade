from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.passive_policy import (
    continue_passive_policy_reconciliation,
    transition_passive_policy,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskReason, RiskState

_NOW = datetime(2026, 8, 9, 10, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _State:
    value: int


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:passive-policy",
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def _risk_state(*, open_orders: int, now: datetime = _NOW) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=open_orders,
        now=now,
    )


def _working_order() -> WorkingOrder:
    return WorkingOrder(
        client_order_id="stage:g0:buy:l1",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("99"),
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0"),
    )


def _replacement_intent() -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id="stage:g1:buy:l1",
        generation=1,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("98.5"),
        quantity=Decimal("0.01"),
    )


def _cancel_only_transition():
    return transition_passive_policy(
        decision="candidate",
        previous_state=_State(1),
        candidate_state=_State(2),
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=1),
        working_orders=(_working_order(),),
        proposed_ladder=(_replacement_intent(),),
    )


def test_cancel_only_keeps_previous_state_until_submit_phase() -> None:
    transition = _cancel_only_transition()

    assert transition.previous_state == _State(1)
    assert transition.candidate_state == _State(2)
    assert transition.next_state == _State(1)
    assert transition.reconciliation.cancel == ("stage:g0:buy:l1",)
    assert transition.reconciliation.submit == ()


def test_submit_phase_commits_candidate_state_without_recomputing_policy() -> None:
    first = _cancel_only_transition()
    second = continue_passive_policy_reconciliation(
        first,
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=0),
        working_orders=(),
    )

    assert second.decision == "candidate"
    assert second.previous_state == _State(1)
    assert second.candidate_state == _State(2)
    assert second.next_state == _State(2)
    assert second.reconciliation.cancel == ()
    assert second.reconciliation.submit == (_replacement_intent(),)


def test_risk_failure_after_cancel_restores_previous_state() -> None:
    first = _cancel_only_transition()
    second = continue_passive_policy_reconciliation(
        first,
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(
            open_orders=0,
            now=_NOW + timedelta(milliseconds=1_001),
        ),
        working_orders=(),
    )

    assert second.risk_decision.allow_new_risk is False
    assert RiskReason.STALE_DATA in second.risk_decision.reasons
    assert second.next_state == first.previous_state
    assert second.desired_ladder == ()
    assert second.reconciliation.submit == ()


def test_replacement_budget_counts_old_strategy_order_as_replaced() -> None:
    limits = RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=1,
    )
    transition = transition_passive_policy(
        decision="candidate",
        previous_state=_State(1),
        candidate_state=_State(2),
        snapshot=_snapshot(),
        risk_limits=limits,
        risk_state=_risk_state(open_orders=1),
        working_orders=(_working_order(),),
        proposed_ladder=(_replacement_intent(),),
    )

    assert transition.risk_decision.allow_new_risk is True
    assert transition.reconciliation.cancel == ("stage:g0:buy:l1",)
