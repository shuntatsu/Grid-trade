from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.s2_adaptive_grid import (
    continue_s2_adaptive_grid_reconciliation,
    transition_s2_adaptive_grid,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskReason, RiskState
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig, build_long_grid_at_center
from grid_trade.strategy.s2_adaptive_grid import S2GridState
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_NOW = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)


def _snapshot(*, mid: str = "101", vol: str = "0.006", position: str = "0") -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal(vol),
        position_quantity=Decimal(position),
        source_id="fixture:s2-application",
    )


def _grid_config() -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=20,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.1"),
    )


def _center_config() -> DynamicCenterConfig:
    return DynamicCenterConfig(Decimal("1"), Decimal("50"))


def _spacing_config() -> VolatilitySpacingConfig:
    return VolatilitySpacingConfig(
        min_spacing_bps=Decimal("10"),
        max_spacing_bps=Decimal("100"),
        volatility_multiplier=Decimal("0.5"),
        execution_cost_floor_bps=Decimal("12"),
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


def _working(state: S2GridState, *, first_fill: str = "0") -> tuple[WorkingOrder, ...]:
    ladder = build_long_grid_at_center(
        state.center,
        replace(_grid_config(), spacing_bps=state.spacing_bps),
        generation=state.generation,
        stage="s2",
    )
    return tuple(
        WorkingOrder(
            client_order_id=order.client_order_id,
            generation=order.generation,
            level=order.level,
            side=order.side,
            price=order.price,
            quantity=order.quantity,
            filled_quantity=Decimal(first_fill) if index == 0 else Decimal("0"),
            reduce_only=order.reduce_only,
        )
        for index, order in enumerate(ladder)
    )


def test_risk_rejection_does_not_commit_candidate_center_or_spacing() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=2)
    transition = transition_s2_adaptive_grid(
        snapshot=_snapshot(position="0.98"),
        state=state,
        center_config=_center_config(),
        grid_config=_grid_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=0),
        working_orders=(),
    )

    assert transition.risk_decision.allow_new_risk is False
    assert RiskReason.MAX_POSITION in transition.risk_decision.reasons
    assert transition.next_state == state
    assert transition.desired_ladder == ()
    assert transition.reconciliation.submit == ()


def test_reanchor_cancels_old_generation_before_candidate_submission() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=0)
    working = _working(state)
    first = transition_s2_adaptive_grid(
        snapshot=_snapshot(),
        state=state,
        center_config=_center_config(),
        grid_config=_grid_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )

    assert first.candidate_state.generation == 1
    assert first.next_state == state
    assert first.reconciliation.cancel == tuple(sorted(order.client_order_id for order in working))
    assert first.reconciliation.submit == ()

    second = continue_s2_adaptive_grid_reconciliation(
        first,
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=0),
        working_orders=(),
    )

    assert second.decision == first.decision
    assert second.candidate_state == first.candidate_state
    assert second.next_state == first.candidate_state
    assert second.reconciliation.cancel == ()
    assert second.reconciliation.submit == first.desired_ladder


def test_risk_is_rechecked_after_cancel_without_recomputing_candidate() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=0)
    working = _working(state)
    first = transition_s2_adaptive_grid(
        snapshot=_snapshot(),
        state=state,
        center_config=_center_config(),
        grid_config=_grid_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )
    second = continue_s2_adaptive_grid_reconciliation(
        first,
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(
            open_orders=0,
            now=_NOW + timedelta(milliseconds=1_001),
        ),
        working_orders=(),
    )

    assert second.decision == first.decision
    assert second.candidate_state == first.candidate_state
    assert second.risk_decision.allow_new_risk is False
    assert RiskReason.STALE_DATA in second.risk_decision.reasons
    assert second.next_state == state
    assert second.reconciliation.submit == ()


def test_partial_fill_old_generation_never_coexists_with_candidate_submit() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=0)
    working = _working(state, first_fill="0.005")
    transition = transition_s2_adaptive_grid(
        snapshot=_snapshot(),
        state=state,
        center_config=_center_config(),
        grid_config=_grid_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )

    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()
    assert transition.next_state == state
