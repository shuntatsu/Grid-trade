from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskReason, RiskState
from grid_trade.strategy.dynamic_center import DynamicCenterConfig, DynamicCenterState
from grid_trade.strategy.dynamic_center_transition import transition_dynamic_center
from grid_trade.strategy.fixed_grid import FixedLongGridConfig
from grid_trade.strategy.grid_geometry import build_long_grid_at_center

_NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def _snapshot(*, mid: str = "101", position: str = "0") -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal(position),
        source_id="fixture:s1-transition",
    )


def _grid_config() -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=100,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.1"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def _risk_state(*, open_orders: int = 0) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=open_orders,
        now=_NOW,
    )


def _working_generation(
    state: DynamicCenterState,
    *,
    first_filled: str = "0",
) -> tuple[WorkingOrder, ...]:
    ladder = build_long_grid_at_center(
        state.center,
        _grid_config(),
        generation=state.generation,
        stage="s1",
    )
    return tuple(
        WorkingOrder(
            client_order_id=order.client_order_id,
            generation=order.generation,
            level=order.level,
            side=order.side,
            price=order.price,
            quantity=order.quantity,
            filled_quantity=Decimal(first_filled) if index == 0 else Decimal("0"),
            reduce_only=order.reduce_only,
        )
        for index, order in enumerate(ladder)
    )


def test_projected_position_rejection_does_not_commit_proposed_center() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=2)
    transition = transition_dynamic_center(
        snapshot=_snapshot(position="0.98"),
        state=state,
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        grid_config=_grid_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(),
        working_orders=(),
    )

    assert transition.risk_decision.allow_new_risk is False
    assert RiskReason.MAX_POSITION in transition.risk_decision.reasons
    assert transition.next_state == state
    assert transition.desired_ladder == ()
    assert transition.reconciliation.submit == ()


def test_effective_reanchor_cancels_old_generation_before_submission() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=0)
    working = _working_generation(state)

    first = transition_dynamic_center(
        snapshot=_snapshot(),
        state=state,
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        grid_config=_grid_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )

    assert first.next_state.generation == 1
    assert first.reconciliation.cancel == tuple(sorted(order.client_order_id for order in working))
    assert first.reconciliation.submit == ()

    second = transition_dynamic_center(
        snapshot=_snapshot(),
        state=state,
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        grid_config=_grid_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=0),
        working_orders=(),
    )

    assert second.reconciliation.cancel == ()
    assert second.reconciliation.submit == second.desired_ladder
    assert all(order.generation == 1 for order in second.reconciliation.submit)


def test_partial_fill_old_generation_never_submits_replacement_same_cycle() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=0)
    working = _working_generation(state, first_filled="0.005")

    transition = transition_dynamic_center(
        snapshot=_snapshot(),
        state=state,
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        grid_config=_grid_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )

    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()


def test_no_effective_change_keeps_matching_working_orders_untouched() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=4)
    working = _working_generation(state)

    transition = transition_dynamic_center(
        snapshot=_snapshot(mid="100.02"),
        state=state,
        center_config=DynamicCenterConfig(Decimal("25"), Decimal("50")),
        grid_config=_grid_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=len(working)),
        working_orders=working,
    )

    assert transition.next_state == state
    assert transition.reconciliation.cancel == ()
    assert transition.reconciliation.submit == ()
